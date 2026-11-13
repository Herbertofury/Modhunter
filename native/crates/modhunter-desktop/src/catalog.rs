//! Share one encoded immutable catalog across local dashboard tabs. An Arc's
//! identity is a version here: callers publish a new Arc for changed facts.
#![cfg_attr(not(windows), allow(dead_code))]
use serde_json::Value;
use std::sync::{Arc, Mutex, Weak};

#[derive(Default)]
pub struct CatalogCache {
    encoded: Mutex<Option<(Weak<Value>, Arc<str>)>>,
}

impl CatalogCache {
    pub fn encode(&self, catalog: &Arc<Value>) -> Result<Arc<str>, serde_json::Error> {
        // This lock protects only background serialization. It never holds
        // the desktop model or blocks ordinary toolbar state snapshots.
        let mut cached = self.encoded.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((source, encoded)) = cached.as_ref()
            && Weak::ptr_eq(source, &Arc::downgrade(catalog))
        {
            return Ok(Arc::clone(encoded));
        }
        let encoded: Arc<str> = serde_json::to_string(catalog.as_ref())?
            .replace('<', "\\u003c")
            .replace('\u{2028}', "\\u2028")
            .replace('\u{2029}', "\\u2029")
            .into();
        // Do not retain a strong catalog reference: invalidating ESLifier
        // facts must not deep-clone a large profile solely for this cache.
        // Arc::make_mut disassociates a Weak identity when the value changes.
        *cached = Some((Arc::downgrade(catalog), Arc::clone(&encoded)));
        Ok(encoded)
    }
}

pub fn injection_script(encoded: &str, revision: u64) -> String {
    format!(
        "{}({encoded},{revision});",
        include_str!("../../../ui/catalog_injection.js")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn tabs_share_encoding_and_copy_on_write_changes_invalidate_it() {
        let cache = CatalogCache::default();
        let mut catalog = Arc::new(json!({"items":[{"name":"A <test>\u{2028}\u{2029}"}]}));
        let first = cache.encode(&catalog).unwrap();
        assert_eq!(Arc::strong_count(&catalog), 1);
        assert!(!first.contains('<'));
        assert!(!first.contains('\u{2028}'));
        assert_eq!(serde_json::from_str::<Value>(&first).unwrap(), *catalog);
        for _ in 0..12 {
            assert!(Arc::ptr_eq(&first, &cache.encode(&catalog).unwrap()));
        }
        Arc::make_mut(&mut catalog)["items"][0]["name"] = json!("Changed");
        let changed = cache.encode(&catalog).unwrap();
        assert!(!Arc::ptr_eq(&first, &changed));
        assert_eq!(serde_json::from_str::<Value>(&changed).unwrap(), *catalog);
        assert!(first.contains("test"));
    }

    #[test]
    fn simultaneous_tabs_obtain_one_shared_encoding() {
        let cache = CatalogCache::default();
        let catalog = Arc::new(json!({"items":(0..2500).collect::<Vec<_>>()}));
        std::thread::scope(|scope| {
            let workers: Vec<_> = (0..8)
                .map(|_| scope.spawn(|| cache.encode(&catalog).unwrap()))
                .collect();
            let values: Vec<_> = workers.into_iter().map(|w| w.join().unwrap()).collect();
            assert!(values.iter().all(|value| Arc::ptr_eq(&values[0], value)));
        });
    }
}
