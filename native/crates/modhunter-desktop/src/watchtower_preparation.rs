//! Cancellation ownership before a browser produces a native download. The
//! registry mutex also guards publication, so cancellation cannot race a late
//! resolver result into the accepted offer list.
use super::download_scope::SelectedDownloadScope;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

#[derive(Clone)]
pub struct Ticket {
    id: u64,
    generation: u64,
    pub cancel: Arc<AtomicBool>,
}
struct Owner {
    ticket: Ticket,
    windows: BTreeSet<String>,
    scope: Option<Arc<Mutex<SelectedDownloadScope>>>,
}
#[derive(Default)]
pub struct Registry {
    next: u64,
    generation: u64,
    owners: BTreeMap<u64, Owner>,
}
pub struct Cancelled {
    pub operations: usize,
    pub windows: BTreeSet<String>,
}
impl Registry {
    pub fn begin(&mut self) -> Result<Ticket, String> {
        if self.owners.len() >= 128 || self.generation == u64::MAX {
            return Err("Too many browser preparations are active. Close an earlier file window or cancel preparation.".into());
        }
        self.next = self
            .next
            .checked_add(1)
            .ok_or("Browser preparation identities are exhausted.")?;
        let ticket = Ticket {
            id: self.next,
            generation: self.generation,
            cancel: Arc::new(AtomicBool::new(false)),
        };
        self.owners.insert(
            ticket.id,
            Owner {
                ticket: ticket.clone(),
                windows: BTreeSet::new(),
                scope: None,
            },
        );
        Ok(ticket)
    }
    pub fn current(&self, ticket: &Ticket) -> bool {
        ticket.generation == self.generation
            && !ticket.cancel.load(Ordering::Acquire)
            && self
                .owners
                .get(&ticket.id)
                .is_some_and(|owner| Arc::ptr_eq(&owner.ticket.cancel, &ticket.cancel))
    }
    pub fn window(&mut self, ticket: &Ticket, label: String) -> Result<(), String> {
        if !self.current(ticket) {
            return Err(
                "Browser preparation was cancelled. Start a new explicit file request.".into(),
            );
        }
        self.owners
            .get_mut(&ticket.id)
            .expect("checked owner")
            .windows
            .insert(label);
        Ok(())
    }
    pub fn scope(
        &mut self,
        ticket: &Ticket,
        scope: Arc<Mutex<SelectedDownloadScope>>,
    ) -> Result<(), String> {
        if !self.current(ticket) {
            scope.lock().unwrap_or_else(|e| e.into_inner()).invalidate();
            return Err("The selected file preparation was cancelled.".into());
        }
        self.owners
            .get_mut(&ticket.id)
            .expect("checked owner")
            .scope = Some(scope);
        Ok(())
    }
    pub fn publish<T>(
        &mut self,
        ticket: &Ticket,
        apply: impl FnOnce() -> Result<T, String>,
    ) -> Result<T, String> {
        if !self.current(ticket) {
            return Err(
                "Browser preparation was cancelled. Its late result was not accepted.".into(),
            );
        }
        let result = apply()?;
        self.finish(ticket);
        Ok(result)
    }
    pub fn finish(&mut self, ticket: &Ticket) {
        if self
            .owners
            .get(&ticket.id)
            .is_some_and(|owner| Arc::ptr_eq(&owner.ticket.cancel, &ticket.cancel))
        {
            self.owners.remove(&ticket.id);
        }
    }
    pub fn abandon(&mut self, ticket: &Ticket) -> BTreeSet<String> {
        if !self
            .owners
            .get(&ticket.id)
            .is_some_and(|owner| Arc::ptr_eq(&owner.ticket.cancel, &ticket.cancel))
        {
            return BTreeSet::new();
        }
        let owner = self.owners.remove(&ticket.id).expect("checked owner");
        owner.ticket.cancel.store(true, Ordering::Release);
        if let Some(scope) = owner.scope {
            scope.lock().unwrap_or_else(|e| e.into_inner()).invalidate()
        }
        owner.windows
    }
    pub fn cancel_all(&mut self) -> Cancelled {
        let owners = std::mem::take(&mut self.owners);
        self.generation = self.generation.saturating_add(1);
        let mut result = Cancelled {
            operations: owners.len(),
            windows: BTreeSet::new(),
        };
        for owner in owners.into_values() {
            owner.ticket.cancel.store(true, Ordering::Release);
            if let Some(scope) = owner.scope {
                scope.lock().unwrap_or_else(|e| e.into_inner()).invalidate()
            }
            result.windows.extend(owner.windows);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Barrier, atomic::AtomicUsize};
    #[test]
    fn cancellation_wins_before_a_resolver_can_publish_its_completed_browser_reply() {
        let registry = Arc::new(Mutex::new(Registry::default()));
        let ticket = registry.lock().unwrap().begin().unwrap();
        let browser_ready = Arc::new(Barrier::new(2));
        let publish_allowed = Arc::new(Barrier::new(2));
        let writes = Arc::new(AtomicUsize::new(0));
        let worker = {
            let registry = registry.clone();
            let browser_ready = browser_ready.clone();
            let publish_allowed = publish_allowed.clone();
            let writes = writes.clone();
            std::thread::spawn(move || {
                browser_ready.wait();
                publish_allowed.wait();
                registry.lock().unwrap().publish(&ticket, || {
                    writes.fetch_add(1, Ordering::AcqRel);
                    Ok(())
                })
            })
        };
        browser_ready.wait();
        assert_eq!(registry.lock().unwrap().cancel_all().operations, 1);
        let newer = registry.lock().unwrap().begin().unwrap();
        publish_allowed.wait();
        assert!(worker.join().unwrap().is_err());
        assert_eq!(writes.load(Ordering::Acquire), 0);
        assert!(registry.lock().unwrap().current(&newer));
    }
    #[test]
    fn cancellation_invalidates_direct_and_folder_preblob_scopes_but_retains_completed_work() {
        let mut registry = Registry::default();
        let complete = registry.begin().unwrap();
        let retained = registry
            .publish(&complete, || Ok("complete prior offer"))
            .unwrap();
        let direct = registry.begin().unwrap();
        let direct_scope = Arc::new(Mutex::new(
            SelectedDownloadScope::new(
                url::Url::parse("https://mega.nz/file/file1234#key").unwrap(),
            )
            .unwrap(),
        ));
        registry.scope(&direct, direct_scope.clone()).unwrap();
        registry.window(&direct, "selected-direct".into()).unwrap();
        let folder = registry.begin().unwrap();
        let file = super::super::download_scope::MegaFolderFile {
            public_id: "pub12345".into(),
            root_id: "root1234".into(),
            file_id: "file1234".into(),
            parent_id: "root1234".into(),
            name: "Release.zip".into(),
            bytes: 64,
            modified_at: 1,
        };
        let folder_scope = Arc::new(Mutex::new(
            SelectedDownloadScope::new_mega_folder_file(
                url::Url::parse(
                    "https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/file1234",
                )
                .unwrap(),
                file,
                "owned_observer".into(),
                None,
            )
            .unwrap(),
        ));
        registry.scope(&folder, folder_scope.clone()).unwrap();
        registry.window(&folder, "selected-folder".into()).unwrap();
        let cancelled = registry.cancel_all();
        assert_eq!(cancelled.operations, 2);
        assert_eq!(
            cancelled.windows,
            BTreeSet::from(["selected-direct".into(), "selected-folder".into()])
        );
        assert!(!direct_scope.lock().unwrap().live());
        assert!(!folder_scope.lock().unwrap().live());
        assert!(direct.cancel.load(Ordering::Acquire) && folder.cancel.load(Ordering::Acquire));
        assert!(!complete.cancel.load(Ordering::Acquire));
        assert_eq!(retained, "complete prior offer");
        assert!(
            registry
                .window(&direct, "late-created-view".into())
                .is_err()
        );
    }
}
