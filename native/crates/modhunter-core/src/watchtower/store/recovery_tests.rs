//! Recovery must remain newer than every snapshot previously sent to the UI.
use super::*;

fn readonly_at(path: &Path, revision: u64) -> WatchStore {
    let store = WatchStore::open(path);
    let mut inner = store.inner.lock().unwrap();
    // Models a valid document whose startup recovery could not be persisted.
    inner.document.revision = revision;
    inner.read_only = true;
    inner.warning = "Fixture startup recovery failed".into();
    drop(inner);
    store
}

#[test]
fn resetting_recovery_advances_revision_and_preserves_original_bytes() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("watchtower.json");
    let original = b"{incomplete original";
    std::fs::write(&path, original).unwrap();
    let store = readonly_at(&path, 42);
    let before = store.snapshot();
    assert!(
        store
            .command("recover", &json!({"confirm":true}), 100)
            .is_err()
    );
    assert_eq!(store.snapshot(), before);
    let result = store
        .command("recover", &json!({"mode":"reset","confirm":"RESET"}), 100)
        .unwrap();
    assert_eq!(store.snapshot()["revision"], 43);
    assert_eq!(store.snapshot()["readOnly"], false);
    assert_eq!(read_document(&path).unwrap().revision, 43);
    assert_eq!(
        std::fs::read(result["backupPath"].as_str().unwrap()).unwrap(),
        original
    );
}

#[test]
fn retrying_an_older_repaired_file_keeps_revision_monotonic() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("watchtower.json");
    write_document(&path, &Document::default()).unwrap();
    let store = readonly_at(&path, 42);
    store
        .command("recover", &json!({"mode":"retry"}), 100)
        .unwrap();
    assert_eq!(store.snapshot()["revision"], 43);
    assert_eq!(read_document(&path).unwrap().revision, 43);
    assert_eq!(store.snapshot()["readOnly"], false);
}

#[test]
fn exhausted_revision_cannot_move_or_reset_the_original_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("watchtower.json");
    let original = b"unreadable fixture";
    std::fs::write(&path, original).unwrap();
    let store = readonly_at(&path, u64::MAX);
    let before = store.snapshot();
    assert!(
        store
            .command("recover", &json!({"mode":"reset","confirm":"RESET"}), 100)
            .is_err()
    );
    assert_eq!(store.snapshot(), before);
    assert_eq!(std::fs::read(&path).unwrap(), original);
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
}
