use super::types::*;
use super::url_policy::{canonical_page_url, page_origin};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

mod commands;
#[cfg(test)]
#[path = "store/recovery_tests.rs"]
mod recovery_tests;
#[cfg(test)]
mod tests;

struct Inner {
    document: Document,
    warning: String,
    read_only: bool,
}

/// Serializes mutations while allowing UI snapshots to read the prior complete
/// state during disk IO. No browser session contents are accepted or persisted.
pub struct WatchStore {
    path: PathBuf,
    inner: Mutex<Inner>,
    write_guard: Mutex<()>,
    writable: bool,
}

impl WatchStore {
    pub fn open(path: impl Into<PathBuf>) -> Self {
        Self::open_internal(path.into(), true)
    }

    /// Opens a spectator store when another desktop process owns the data file.
    /// This mode cannot recover, rotate account generations, or write to disk.
    pub fn open_read_only(path: impl Into<PathBuf>) -> Self {
        Self::open_internal(path.into(), false)
    }

    fn open_internal(path: PathBuf, writable: bool) -> Self {
        let loaded = read_document(&path);
        let (mut document, mut warning, mut read_only) = match loaded {
            Ok(document) => (document, String::new(), false),
            Err(error) => (
                Document::default(),
                format!(
                    "Watchtower could not load its saved data. The original file has been left untouched. {error}"
                ),
                true,
            ),
        };
        if writable
            && !read_only
            && let Err(error) = recover_runtime_state(&mut document).and_then(|changed| {
                if changed {
                    write_document(&path, &document)
                } else {
                    Ok(())
                }
            })
        {
            warning = format!(
                "Saved watches were read, but recovery could not be saved. Checking is paused to preserve the original file. {error}"
            );
            read_only = true;
        }
        if !writable {
            read_only = true;
            warning = format!(
                "Another Modhunter instance owns Watchtower. This window is read-only. {warning}"
            )
            .trim()
            .to_owned();
        }
        Self {
            path,
            inner: Mutex::new(Inner {
                document,
                warning,
                read_only,
            }),
            write_guard: Mutex::new(()),
            writable,
        }
    }

    pub fn snapshot(&self) -> Value {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let doc = &inner.document;
        let mut watches = Vec::with_capacity(doc.watches.len());
        let (mut total, mut new, mut changed, mut unread, mut checking, mut paused, mut errors) =
            (0, 0, 0, 0, 0, 0, 0);
        for watch in &doc.watches {
            let row = summary(watch, &doc.settings);
            if watch.removed_at.is_none() {
                total += 1;
                if row["needsCheck"] == true {
                    new += 1;
                }
                if row["hasChanges"] == true {
                    changed += 1;
                }
                if row["unread"] == true {
                    unread += 1;
                }
                if watch.state == "checking" {
                    checking += 1;
                }
                if !watch.enabled || row["muted"] == true {
                    paused += 1;
                }
                if is_error(&watch.state) {
                    errors += 1;
                }
            }
            watches.push(row);
        }
        json!({"ok":true,"schemaVersion":SCHEMA_VERSION,"revision":doc.revision,
            "settings":doc.settings,"watches":watches,"sessions":doc.sessions,
            "seenHistory":doc.seen_history,"history":doc.seen_history,
            "readOnly":inner.read_only,"recoveryWarning":inner.warning,
            "counts":{"total":total,"all":doc.watches.len(),"new":new,"changed":changed,
                "unread":unread,"checking":checking,"paused":paused,"errors":errors,
                "removed":doc.watches.len()-total,"sessions":doc.sessions.len()}})
    }

    pub fn session(&self, id: &str) -> Option<Session> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .sessions
            .iter()
            .find(|s| s.id == id)
            .cloned()
    }

    pub fn watch(&self, id: &str) -> Option<Watch> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .watches
            .iter()
            .find(|w| w.id == id)
            .cloned()
    }

    /// Scheduler lookup without cloning observation previews or change history.
    pub fn watch_location(&self, id: &str) -> Option<(String, Option<String>)> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .watches
            .iter()
            .find(|w| w.id == id)
            .map(|w| (w.url.clone(), w.session_id.clone()))
    }

    /// Checks pause, mute, removal, and session requirements without considering
    /// the active operation or due time. Useful for cancelling an existing queue.
    pub fn watch_eligible(&self, id: &str) -> bool {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        !inner.read_only
            && inner
                .document
                .watches
                .iter()
                .find(|w| w.id == id)
                .is_some_and(|watch| {
                    eligible(watch, &inner.document.settings)
                        && (!watch.requires_session || watch.session_id.is_some())
                })
    }

    /// Includes persisted operations whose browser worker no longer exists.
    pub fn checking_ids(&self) -> Vec<String> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .watches
            .iter()
            .filter(|watch| watch.state == "checking")
            .map(|watch| watch.id.clone())
            .collect()
    }

    pub fn latest_hash(&self, id: &str) -> Option<String> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .watches
            .iter()
            .find(|w| w.id == id)
            .and_then(|w| w.latest.as_ref())
            .map(|o| o.hash.clone())
    }

    pub fn settings(&self) -> WatchSettings {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .document
            .settings
            .clone()
    }

    pub fn command(&self, action: &str, payload: &Value, now_ms: u64) -> Result<Value, String> {
        if !payload.is_object() {
            return Err("Watchtower commands require an object.".into());
        }
        match action {
            "state" => Ok(self.snapshot()),
            "detail" => {
                let id = required(payload, "id")?;
                let inner = self.inner.lock().map_err(lock_error)?;
                let watch = inner
                    .document
                    .watches
                    .iter()
                    .find(|w| w.id == id)
                    .ok_or("This page watch no longer exists.")?;
                let mut value = serde_json::to_value(watch).map_err(|e| e.to_string())?;
                add_flags(&mut value, watch, &inner.document.settings);
                Ok(
                    json!({"ok":true,"revision":inner.document.revision,"watch":value,
                    "seenHistory":inner.document.seen_history.iter().filter(|e|e.watch_id==id).collect::<Vec<_>>() }),
                )
            }
            "export" => {
                let inner = self.inner.lock().map_err(lock_error)?;
                if payload.get("format").and_then(Value::as_str) == Some("csv") {
                    return Ok(commands::export_csv(&inner.document));
                }
                Ok(
                    json!({"ok":true,"document":commands::export_document(&inner.document, payload.get("includeHistory").and_then(Value::as_bool).unwrap_or(false),now_ms)}),
                )
            }
            "import"
                if payload
                    .get("preview")
                    .and_then(Value::as_bool)
                    .unwrap_or(true) =>
            {
                let inner = self.inner.lock().map_err(lock_error)?;
                commands::import_preview(&inner.document, payload)
            }
            "recover" => self.recover(payload, now_ms),
            _ => self.mutate(|doc| commands::apply(doc, action, payload, now_ms)),
        }
    }

    fn mutate<T>(
        &self,
        operation: impl FnOnce(&mut Document) -> Result<T, String>,
    ) -> Result<T, String> {
        self.mutate_if(operation, |_| true)
    }

    fn mutate_if<T>(
        &self,
        operation: impl FnOnce(&mut Document) -> Result<T, String>,
        changed: impl FnOnce(&T) -> bool,
    ) -> Result<T, String> {
        let _write = self.write_guard.lock().map_err(lock_error)?;
        let mut draft = {
            let inner = self.inner.lock().map_err(lock_error)?;
            if inner.read_only {
                return Err("Saved Watchtower data needs recovery before it can be changed. The original file is preserved.".into());
            }
            inner.document.clone()
        };
        let result = operation(&mut draft)?;
        if !changed(&result) {
            return Ok(result);
        }
        draft.revision = draft
            .revision
            .checked_add(1)
            .ok_or("Watchtower revision limit reached.")?;
        validate_document(&draft)?;
        write_document(&self.path, &draft)?;
        self.inner.lock().map_err(lock_error)?.document = draft;
        Ok(result)
    }

    pub fn check_candidates(&self, now_ms: u64, due_only: bool) -> Vec<String> {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if inner.read_only {
            return Vec::new();
        }
        let doc = &inner.document;
        let mut watches: Vec<_> = doc
            .watches
            .iter()
            .filter(|w| {
                eligible(w, &doc.settings)
                    && w.state != "checking"
                    && (!w.requires_session || w.session_id.is_some())
                    && (!due_only || now_ms >= w.next_check_at.max(w.retry_at))
            })
            .collect();
        watches.sort_by_key(|w| (w.next_check_at, w.created_at, w.id.clone()));
        watches.into_iter().map(|w| w.id.clone()).collect()
    }

    pub fn begin_check(&self, id: &str, now_ms: u64) -> Result<CheckJob, String> {
        self.mutate(|doc| {
            let index = doc
                .watches
                .iter()
                .position(|w| w.id == id)
                .ok_or("This page watch no longer exists.")?;
            let watch = &doc.watches[index];
            if !eligible(watch, &doc.settings) {
                return Err("This page watch is paused, muted, or removed.".into());
            }
            if watch.state == "checking" {
                return Err("This page already has a check in progress.".into());
            }
            if watch.requires_session && watch.session_id.is_none() {
                return Err("Choose a sign-in session for this page before checking it.".into());
            }
            let session = watch
                .session_id
                .as_ref()
                .map(|id| {
                    doc.sessions
                        .iter()
                        .find(|s| &s.id == id)
                        .cloned()
                        .ok_or_else(|| "The selected sign-in session is missing.".to_owned())
                })
                .transpose()?;
            if session
                .as_ref()
                .is_some_and(|s| page_origin(&watch.url).ok().as_deref() != Some(&s.origin))
            {
                return Err("The sign-in session does not belong to this page's origin.".into());
            }
            let operation_id = sequence(doc)?;
            let watch = &mut doc.watches[index];
            let now_ms = now_ms.max(watch.added_at);
            watch.operation_id = operation_id;
            watch.state = "checking".into();
            watch.last_attempt_at = Some(now_ms);
            watch.updated_at = now_ms;
            Ok(CheckJob {
                watch_id: watch.id.clone(),
                operation_id,
                generation: watch.generation,
                started_at: now_ms,
                url: watch.url.clone(),
                title: watch.title.clone(),
                selector: watch.selector.clone(),
                ignore_selectors: watch.ignore_selectors.clone(),
                session_id: watch.session_id.clone(),
                session_storage_generation: session.as_ref().map_or(0, |s| s.storage_generation),
                session,
            })
        })
    }

    pub fn is_current(&self, job: &CheckJob) -> bool {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        !inner.read_only && current(&inner.document, job)
    }

    pub fn complete_check(
        &self,
        job: &CheckJob,
        observed: PageObservation,
        now_ms: u64,
    ) -> Result<bool, String> {
        let now_ms = now_ms.max(job.started_at);
        if !self.is_current(job) {
            return Ok(false);
        }
        if observed.status != "ok" {
            return self.fail_check(job, &observed.status, &observed.message, now_ms);
        }
        if observed.truncated || observed.text.len() > MAX_CAPTURE_BYTES {
            return self.fail_check(job,"tooLarge","The selected page content exceeded the capture limit. Choose a smaller page region; the previous baseline is unchanged.",now_ms);
        }
        if observed.http_status.is_some_and(|s| s >= 400) {
            return self.fail_check(job,if observed.http_status==Some(401){"signInRequired"}else{"unavailable"},"The page returned an unsuccessful HTTP response; the previous baseline is unchanged.",now_ms);
        }
        let final_url=match canonical_page_url(&observed.final_url) {
            Ok(url) if page_origin(&url).ok()==page_origin(&job.url).ok()=>url,
            _=>return self.fail_check(job,"unavailable","The page left its tracked origin or did not report a valid final URL. Review the original page before changing its address.",now_ms),
        };
        if login_redirect(&job.url, &final_url) {
            return self.fail_check(job,"signInRequired","The page redirected to sign-in. Open the site's session and sign in before checking again.",now_ms);
        }
        let normalized = normalize_text(&observed.text);
        if normalized.is_empty() {
            return self.fail_check(job,"selectorMissing","The selected page region contained no readable text. Check the selector or finish signing in; the previous baseline is unchanged.",now_ms);
        }
        let title = bounded_text(&observed.title, 512);
        let lower_title = title.to_ascii_lowercase();
        if [
            "just a moment",
            "attention required!",
            "verify you are human",
            "checking your browser",
        ]
        .iter()
        .any(|p| lower_title.starts_with(p))
        {
            return self.fail_check(job,"challenge","The site is showing a verification challenge. Open its browser session to complete it.",now_ms);
        }
        let hash = format!("{:x}", Sha256::digest(normalized.as_bytes()));
        self.mutate_if(
            |doc| {
                if !current(doc, job) {
                    return Ok(false);
                }
                let limit = doc.settings.history_limit;
                let watch = doc
                    .watches
                    .iter_mut()
                    .find(|w| w.id == job.watch_id)
                    .ok_or("Missing watch")?;
                let previous = watch.latest.as_ref();
                let changed = previous.is_some_and(|p| p.hash != hash);
                let id = previous
                    .filter(|p| p.hash == hash)
                    .map(|p| p.id.clone())
                    .unwrap_or_else(|| format!("observation-{}", job.operation_id));
                let observation = Observation {
                    id: id.clone(),
                    hash: hash.clone(),
                    title: title.clone(),
                    final_url: final_url.clone(),
                    started_at: job.started_at,
                    checked_at: now_ms,
                    text_bytes: normalized.len(),
                    text_preview: bounded_text(&normalized, MAX_PREVIEW_BYTES),
                    preview_truncated: normalized.len() > MAX_PREVIEW_BYTES,
                };
                if previous.is_none() || changed {
                    let entry = change_entry(previous, &observation, watch.context_generation);
                    watch.history.push(entry);
                    if watch.history.len() > limit {
                        watch.history.drain(..watch.history.len() - limit);
                    }
                }
                let first = previous.is_none();
                if first {
                    watch.seen_hash = Some(hash);
                    watch.seen_observation_id = Some(id);
                    watch.seen_event_id = None;
                }
                watch.latest = Some(observation);
                watch.last_success_at = Some(now_ms);
                watch.updated_at = now_ms;
                watch.fresh_check_required = false;
                watch.last_error.clear();
                watch.consecutive_errors = 0;
                watch.retry_at = 0;
                watch.next_check_at =
                    now_ms.saturating_add(u64::from(watch.interval_minutes) * 60_000);
                watch.state = if first {
                    "baseline"
                } else if has_changes(watch) {
                    "changed"
                } else {
                    "unchanged"
                }
                .into();
                Ok(true)
            },
            |changed| *changed,
        )
    }

    pub fn fail_check(
        &self,
        job: &CheckJob,
        status: &str,
        message: &str,
        now_ms: u64,
    ) -> Result<bool, String> {
        let now_ms = now_ms.max(job.started_at);
        if !self.is_current(job) {
            return Ok(false);
        }
        let status = match status {
            "loginRequired" => "signInRequired",
            "verificationRequired" => "challenge",
            "incomplete" => "tooLarge",
            "empty" => "selectorMissing",
            "blocked" | "timeout" | "redirected" | "loading" => "unavailable",
            s if matches!(
                s,
                "signInRequired"
                    | "challenge"
                    | "unavailable"
                    | "selectorMissing"
                    | "tooLarge"
                    | "cancelled"
                    | "interrupted"
            ) =>
            {
                s
            }
            _ => "error",
        };
        self.mutate_if(
            |doc| {
                if !current(doc, job) {
                    return Ok(false);
                }
                let watch = doc
                    .watches
                    .iter_mut()
                    .find(|w| w.id == job.watch_id)
                    .ok_or("Missing watch")?;
                watch.state = status.into();
                watch.last_error = bounded_text(
                    if message.is_empty() {
                        "The page check did not produce a complete observation."
                    } else {
                        message
                    },
                    2048,
                );
                watch.updated_at = now_ms;
                if status != "cancelled" {
                    watch.consecutive_errors = watch.consecutive_errors.saturating_add(1);
                }
                let backoff = 60_000_u64
                    .saturating_mul(1_u64 << watch.consecutive_errors.saturating_sub(1).min(6))
                    .min(3_600_000);
                watch.retry_at = if status == "cancelled" {
                    0
                } else {
                    now_ms.saturating_add(backoff)
                };
                watch.next_check_at = now_ms
                    .saturating_add(u64::from(watch.interval_minutes) * 60_000)
                    .max(watch.retry_at);
                Ok(true)
            },
            |changed| *changed,
        )
    }

    fn recover(&self, payload: &Value, now_ms: u64) -> Result<Value, String> {
        let _write = self.write_guard.lock().map_err(lock_error)?;
        if !self.writable {
            return Err("This Modhunter instance does not own Watchtower. Close the other instance before recovering its data.".into());
        }
        let prior_revision = {
            let inner = self.inner.lock().map_err(lock_error)?;
            if !inner.read_only {
                return Ok(json!({"ok":true,"recovered":false}));
            }
            inner.document.revision
        };
        if payload.get("mode").and_then(Value::as_str) != Some("reset") {
            let mut document = read_document(&self.path)?;
            recover_runtime_state(&mut document)?;
            // Recovery is a new committed state, even when the repaired file is
            // an older backup. Observers must never mistake it for a late reply.
            document.revision = document
                .revision
                .max(prior_revision)
                .checked_add(1)
                .ok_or("Watchtower revision limit reached.")?;
            write_document(&self.path, &document)?;
            *self.inner.lock().map_err(lock_error)? = Inner {
                document,
                warning: String::new(),
                read_only: false,
            };
            return Ok(json!({"ok":true,"recovered":true}));
        }
        if payload.get("confirm").and_then(Value::as_str) != Some("RESET") {
            return Err(
                "Confirm RESET to preserve the old file and start an empty Watchtower.".into(),
            );
        }
        let backup = self.path.with_file_name(format!(
            "{}.recovery-{now_ms}.json",
            self.path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("watchtower")
        ));
        if backup.exists() {
            return Err(
                "A recovery backup already exists for this time. Retry without replacing it."
                    .into(),
            );
        }
        let document = Document {
            revision: prior_revision
                .checked_add(1)
                .ok_or("Watchtower revision limit reached.")?,
            ..Document::default()
        };
        let had_original = self.path.exists();
        if had_original {
            std::fs::rename(&self.path, &backup)
                .map_err(|e| format!("Could not preserve the original Watchtower file: {e}"))?;
        }
        if let Err(error) = write_document(&self.path, &document) {
            if had_original {
                let _ = std::fs::rename(&backup, &self.path);
            }
            return Err(error);
        }
        *self.inner.lock().map_err(lock_error)? = Inner {
            document,
            warning: String::new(),
            read_only: false,
        };
        Ok(json!({"ok":true,"recovered":true,"backupPath":if had_original{Some(backup)}else{None}}))
    }
}

fn lock_error<T>(_: std::sync::PoisonError<T>) -> String {
    "Watchtower state is unavailable; restart the app before changing it.".into()
}

fn recover_runtime_state(document: &mut Document) -> Result<bool, String> {
    let mut changed = false;
    let mut reset_sessions = HashSet::new();
    for session in &mut document.sessions {
        if !session.remember {
            session.storage_generation = session
                .storage_generation
                .checked_add(1)
                .ok_or("Session generation limit reached.")?;
            session.state = "reset".into();
            session.last_opened_at = None;
            session.last_confirmed_at = None;
            reset_sessions.insert(session.id.clone());
            changed = true;
        }
    }
    for watch in &mut document.watches {
        if watch.state == "checking"
            || watch
                .session_id
                .as_ref()
                .is_some_and(|id| reset_sessions.contains(id))
        {
            watch.generation = watch
                .generation
                .checked_add(1)
                .ok_or("Page generation limit reached.")?;
            if watch.state == "checking" {
                watch.state = "interrupted".into();
                watch.last_error="The previous check ended before a complete result was saved. Check again when ready.".into();
            } else if watch.enabled && watch.removed_at.is_none() {
                watch.state = "signInRequired".into();
                watch.last_error =
                    "This session did not remember browser data. Open it to sign in again.".into();
            }
            changed = true;
        }
    }
    if changed {
        document.revision = document
            .revision
            .checked_add(1)
            .ok_or("Watchtower revision limit reached.")?;
    }
    Ok(changed)
}

fn read_document(path: &Path) -> Result<Document, String> {
    let metadata = match std::fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Document::default()),
        Err(e) => return Err(e.to_string()),
    };
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() > MAX_STORE_BYTES as u64
    {
        return Err("The saved state is not a bounded regular file.".into());
    }
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .and_then(|f| f.take(MAX_STORE_BYTES as u64 + 1).read_to_end(&mut bytes))
        .map_err(|e| e.to_string())?;
    if bytes.len() > MAX_STORE_BYTES {
        return Err("Saved Watchtower data exceeds its storage limit.".into());
    }
    let document: Document =
        serde_json::from_slice(&bytes).map_err(|e| format!("Invalid saved JSON: {e}"))?;
    validate_document(&document)?;
    Ok(document)
}

fn write_document(path: &Path, document: &Document) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or("Invalid Watchtower storage directory.")?;
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("Could not create the Watchtower data directory: {e}"))?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut temporary, document).map_err(|e| e.to_string())?;
    if temporary
        .as_file()
        .metadata()
        .map_err(|e| e.to_string())?
        .len()
        > MAX_STORE_BYTES as u64
    {
        return Err("Watchtower's saved data exceeds 64 MiB. Export history or reduce retained history before adding more content.".into());
    }
    temporary
        .flush()
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| {
        format!(
            "Could not atomically save Watchtower; the previous state remains available: {}",
            e.error
        )
    })?;
    Ok(())
}

fn validate_document(doc: &Document) -> Result<(), String> {
    if doc.schema_version != SCHEMA_VERSION || doc.next_id == 0 || doc.next_id == u64::MAX {
        return Err("Unsupported Watchtower data schema or sequence.".into());
    }
    commands::validate_settings(&doc.settings)?;
    let mut sessions = std::collections::HashMap::new();
    let mut largest_id = 0;
    for session in &doc.sessions {
        largest_id = largest_id.max(record_id(&session.id, "session-")?);
        if sessions.insert(session.id.as_str(), session).is_some()
            || session.origin != page_origin(&session.origin)?
            || !session.origin.starts_with("https://")
            || session.label.len() > 200
            || session.storage_generation == 0
            || !matches!(
                session.state.as_str(),
                "notOpened" | "opened" | "confirmed" | "reset"
            )
        {
            return Err("Invalid or duplicate saved sign-in session metadata.".into());
        }
    }
    let mut ids = HashSet::new();
    let mut page_keys = HashSet::new();
    for watch in &doc.watches {
        largest_id = largest_id
            .max(record_id(&watch.id, "watch-")?)
            .max(watch.operation_id);
        // Removing two accounts can leave two intentionally separate, paused
        // watches awaiting reassignment. Keep both records and their history.
        let duplicate = !page_keys.insert((&watch.url, &watch.session_id, watch.requires_session));
        if !ids.insert(watch.id.as_str())
            || watch.url != canonical_page_url(&watch.url)?
            || (duplicate && !(watch.requires_session && watch.session_id.is_none()))
            || watch.title.len() > 512
            || watch.folder.len() > 200
            || watch.notes.len() > 8192
            || watch.tags.len() > 32
            || watch.tags.iter().any(|t| t.len() > 80)
            || watch.selector.len() > 2048
            || watch.ignore_selectors.len() > 20
            || watch.ignore_selectors.iter().any(|s| s.len() > 2048)
            || !(1..=10080).contains(&watch.interval_minutes)
            || watch.generation == 0
            || watch.history.len() > 100
            || watch.last_error.len() > 2048
            || (watch.requires_session && watch.session_id.is_none() && watch.enabled)
            || !matches!(
                watch.state.as_str(),
                "new"
                    | "checking"
                    | "baseline"
                    | "unchanged"
                    | "changed"
                    | "paused"
                    | "removed"
                    | "cancelled"
                    | "interrupted"
                    | "signInRequired"
                    | "challenge"
                    | "unavailable"
                    | "selectorMissing"
                    | "tooLarge"
                    | "error"
            )
        {
            return Err("Saved watches contain invalid, duplicate, or excessive fields.".into());
        }
        if let Some(association) = &watch.association {
            if association.profile_id.is_empty()
                || association.item_id.is_empty()
                || association.profile_id.len() > 1000
                || association.item_id.len() > 1000
                || association.name.len() > 512
                || association.source_url.len() > 4096
            {
                return Err("Invalid saved linked-mod association.".into());
            }
            if !association.source_url.is_empty() {
                canonical_page_url(&association.source_url)?;
            }
        }
        if let Some(observation) = &watch.latest {
            largest_id = largest_id.max(record_id(&observation.id, "observation-")?);
            if observation.text_preview.len() > MAX_PREVIEW_BYTES
                || observation.text_bytes > MAX_CAPTURE_BYTES
                || observation.text_preview.len() > observation.text_bytes
                || observation.title.len() > 512
                || !valid_hash(&observation.hash)
                || observation.final_url != canonical_page_url(&observation.final_url)?
                || page_origin(&observation.final_url)? != page_origin(&watch.url)?
            {
                return Err("Invalid or incomplete saved page observation.".into());
            }
        }
        for entry in &watch.history {
            largest_id = largest_id.max(record_id(&entry.observation_id, "observation-")?);
            if !valid_hash(&entry.hash)
                || entry.title.len() > 512
                || entry.summary.len() > 2048
                || entry.final_url != canonical_page_url(&entry.final_url)?
                || entry.added.len() > 12
                || entry.removed.len() > 12
                || entry
                    .added
                    .iter()
                    .chain(&entry.removed)
                    .any(|s| s.len() > 240)
                || !matches!(entry.kind.as_str(), "baseline" | "changed")
            {
                return Err("Invalid saved page change history.".into());
            }
        }
        if watch.seen_hash.as_ref().is_some_and(|h| !valid_hash(h)) {
            return Err("Invalid saved seen identity.".into());
        }
        if let Some(id) = &watch.seen_observation_id {
            largest_id = largest_id.max(record_id(id, "observation-")?);
        }
        if let Some(id) = &watch.seen_event_id {
            largest_id = largest_id.max(record_id(id, "seen-")?);
        }
        if let Some(id) = &watch.session_id {
            let session = sessions
                .get(id.as_str())
                .ok_or("A saved watch references a missing session.")?;
            if session.origin != page_origin(&watch.url)? || !watch.requires_session {
                return Err("A saved page and sign-in origin do not match.".into());
            }
        }
    }
    if doc.seen_history.len() > 500 {
        return Err("Saved seen history exceeds its retention bound.".into());
    }
    let mut events = HashSet::new();
    for event in &doc.seen_history {
        largest_id = largest_id
            .max(record_id(&event.id, "seen-")?)
            .max(record_id(&event.observation_id, "observation-")?);
        if !events.insert(&event.id)
            || !ids.contains(event.watch_id.as_str())
            || !valid_hash(&event.hash)
            || event.title.len() > 512
            || event.previous_hash.as_ref().is_some_and(|h| !valid_hash(h))
        {
            return Err("Invalid saved seen history.".into());
        }
        if let Some(id) = &event.previous_observation_id {
            largest_id = largest_id.max(record_id(id, "observation-")?);
        }
        if let Some(id) = &event.previous_event_id {
            largest_id = largest_id.max(record_id(id, "seen-")?);
        }
    }
    if largest_id >= doc.next_id {
        return Err(
            "The saved Watchtower sequence would reuse an existing operation identity.".into(),
        );
    }
    Ok(())
}

fn record_id(id: &str, prefix: &str) -> Result<u64, String> {
    let suffix = id
        .strip_prefix(prefix)
        .ok_or("Invalid saved Watchtower record identity.")?;
    // Existing numeric session IDs remain valid. New sessions add a fresh
    // namespace so losing/resetting metadata cannot reattach an old cookie jar.
    let counter = if prefix == "session-" {
        if let Some((counter, nonce)) = suffix.split_once('-') {
            if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err("Invalid saved sign-in session namespace.".into());
            }
            counter
        } else {
            suffix
        }
    } else {
        suffix
    };
    let number = Some(counter)
        .filter(|s| !s.is_empty() && s.len() <= 20 && s.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|n| *n > 0);
    number.ok_or_else(|| "Invalid saved Watchtower record identity.".into())
}

fn new_session_id(doc: &mut Document) -> Result<String, String> {
    use std::hash::BuildHasher;
    // RandomState uses fresh randomized keys without adding a browser or
    // credentials dependency. Two domain-separated hashes form an opaque
    // namespace; this is an identity, never an authentication secret.
    let random = std::collections::hash_map::RandomState::new();
    let first = random.hash_one(("modhunter-watchtower-session", 0_u8));
    let second = random.hash_one(("modhunter-watchtower-session", 1_u8));
    Ok(format!(
        "session-{}-{first:016x}{second:016x}",
        sequence(doc)?
    ))
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}
fn sequence(doc: &mut Document) -> Result<u64, String> {
    let id = doc.next_id;
    doc.next_id = doc
        .next_id
        .checked_add(1)
        .ok_or("Watchtower sequence limit reached.")?;
    Ok(id)
}
fn required<'a>(payload: &'a Value, key: &str) -> Result<&'a str, String> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("Missing {key}."))
}
fn needs_check(w: &Watch) -> bool {
    w.fresh_check_required || w.latest.as_ref().is_none_or(|o| o.started_at < w.added_at)
}
fn has_changes(w: &Watch) -> bool {
    w.latest
        .as_ref()
        .is_some_and(|o| w.seen_hash.as_deref() != Some(&o.hash))
}
fn muted(w: &Watch, s: &WatchSettings) -> bool {
    s.muted_folders.contains(&w.folder)
        || page_origin(&w.url)
            .ok()
            .is_some_and(|origin| s.muted_origins.contains(&origin))
}
fn eligible(w: &Watch, s: &WatchSettings) -> bool {
    w.enabled && w.removed_at.is_none() && !muted(w, s)
}
fn is_error(state: &str) -> bool {
    matches!(
        state,
        "signInRequired"
            | "challenge"
            | "unavailable"
            | "selectorMissing"
            | "tooLarge"
            | "error"
            | "interrupted"
    )
}
fn current(doc: &Document, job: &CheckJob) -> bool {
    doc.watches
        .iter()
        .find(|w| w.id == job.watch_id)
        .is_some_and(|w| {
            w.state == "checking"
                && w.operation_id == job.operation_id
                && w.generation == job.generation
                && w.added_at <= job.started_at
                && w.url == job.url
                && w.session_id == job.session_id
                && eligible(w, &doc.settings)
                && job.session_id.as_ref().is_none_or(|id| {
                    doc.sessions.iter().any(|s| {
                        &s.id == id && s.storage_generation == job.session_storage_generation
                    })
                })
        })
}
fn add_flags(value: &mut Value, w: &Watch, s: &WatchSettings) {
    value["needsCheck"] = json!(needs_check(w));
    value["hasChanges"] = json!(has_changes(w));
    value["unread"] = json!(needs_check(w) || has_changes(w));
    value["muted"] = json!(muted(w, s));
}
fn summary(w: &Watch, s: &WatchSettings) -> Value {
    // Polling the full list never serializes retained page bodies or history.
    // Those are only materialized by the explicit detail command.
    let latest = w.latest.as_ref().map(|o| {
        json!({"id":o.id,"hash":o.hash,"title":o.title,
        "finalUrl":o.final_url,"startedAt":o.started_at,"checkedAt":o.checked_at,
        "textBytes":o.text_bytes,"previewTruncated":o.preview_truncated})
    });
    let mut value = json!({"id":w.id,"url":w.url,"title":w.title,"folder":w.folder,"tags":w.tags,
        "notes":w.notes,"enabled":w.enabled,"sessionId":w.session_id,"requiresSession":w.requires_session,
        "selector":w.selector,"ignoreSelectors":w.ignore_selectors,"intervalMinutes":w.interval_minutes,
        "association":w.association,"createdAt":w.created_at,"updatedAt":w.updated_at,"addedAt":w.added_at,
        "removedAt":w.removed_at,"generation":w.generation,"operationId":w.operation_id,"state":w.state,
        "lastAttemptAt":w.last_attempt_at,"lastSuccessAt":w.last_success_at,"nextCheckAt":w.next_check_at,
        "retryAt":w.retry_at,"consecutiveErrors":w.consecutive_errors,"lastError":w.last_error,
        "latest":latest,"seenHash":w.seen_hash,"seenObservationId":w.seen_observation_id,"seenEventId":w.seen_event_id});
    add_flags(&mut value, w, s);
    value
}
fn bounded_text(value: &str, max: usize) -> String {
    let mut end = value.len().min(max);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_owned()
}
fn normalize_text(value: &str) -> String {
    value
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}
fn login_redirect(original: &str, final_url: &str) -> bool {
    let Ok(original) = url::Url::parse(original) else {
        return true;
    };
    let Ok(final_url) = url::Url::parse(final_url) else {
        return true;
    };
    original.path() != final_url.path()
        && final_url.path_segments().is_some_and(|segments| {
            segments.into_iter().any(|s| {
                matches!(
                    s.to_ascii_lowercase().as_str(),
                    "login" | "signin" | "sign-in" | "log-in"
                )
            })
        })
}
fn change_entry(
    previous: Option<&Observation>,
    current: &Observation,
    context_generation: u64,
) -> ChangeEntry {
    let previous_lines: BTreeSet<_> = previous
        .map(|p| p.text_preview.lines().collect())
        .unwrap_or_default();
    let current_lines: BTreeSet<_> = current.text_preview.lines().collect();
    let added: Vec<_> = current_lines
        .difference(&previous_lines)
        .take(12)
        .map(|s| bounded_text(s, 240))
        .collect();
    let removed: Vec<_> = previous_lines
        .difference(&current_lines)
        .take(12)
        .map(|s| bounded_text(s, 240))
        .collect();
    let summary = if previous.is_none() {
        "First complete observation established a baseline.".into()
    } else if added.is_empty() && removed.is_empty() {
        "Page content changed outside the stored preview, in ordering, or in repeated text. Open the original page to review the full change.".into()
    } else {
        format!(
            "{} added and {} removed preview lines.{}",
            added.len(),
            removed.len(),
            if current.preview_truncated || previous.is_some_and(|p| p.preview_truncated) {
                " The digest covers the complete selected content; this preview is shortened."
            } else {
                ""
            }
        )
    };
    ChangeEntry {
        observation_id: current.id.clone(),
        hash: current.hash.clone(),
        context_generation,
        checked_at: current.checked_at,
        title: current.title.clone(),
        final_url: current.final_url.clone(),
        kind: if previous.is_none() {
            "baseline"
        } else {
            "changed"
        }
        .into(),
        summary,
        added,
        removed,
    }
}
