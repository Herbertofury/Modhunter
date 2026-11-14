use super::*;
fn binding() -> Binding {
    Binding {
        watch_id: "watch-1".into(),
        watch_generation: 1,
        session_id: Some("session-1".into()),
        session_generation: 1,
        source_origin: "https://mods.example.com".into(),
        association: None,
    }
}
fn start() -> (tempfile::TempDir, Store, Receipt) {
    let d = tempfile::tempdir().unwrap();
    let mut s = Store::open(d.path());
    let r = s.request("mod.zip", binding(), None, 1).unwrap();
    (d, s, r)
}
fn ready(s: &mut Store, r: &Receipt) {
    let p = s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    fs::write(p, b"PK\x03\x04fixture").unwrap();
    s.begin_validation(&r.id, &binding()).unwrap();
    s.validate(&r.id, || false).unwrap();
}
#[test]
fn consent_required() {
    let (_d, mut s, r) = start();
    assert!(s.approve(&r.id, &binding(), "", 2).is_err());
    assert_eq!(s.row(&r.id).unwrap().state, "pending");
}
#[test]
fn source_lease_checked() {
    let (_d, mut s, r) = start();
    let mut b = binding();
    b.watch_generation = 2;
    assert!(s.approve(&r.id, &b, "DOWNLOAD", 2).is_err());
}
#[test]
fn account_lease_checked() {
    let (_d, mut s, r) = start();
    let mut b = binding();
    b.session_generation = 2;
    assert!(s.approve(&r.id, &b, "DOWNLOAD", 2).is_err());
}
#[test]
fn approval_expires() {
    let (_d, mut s, r) = start();
    assert!(s.approve(&r.id, &binding(), "DOWNLOAD", 200000).is_err());
}
#[test]
fn no_website_paths() {
    for n in [
        "../x.zip",
        "a\\b.zip",
        "CON.zip",
        "a.zip.exe",
        "a:evil.zip",
        "x.zip ",
        "foo.html",
    ] {
        assert!(!valid_filename(n), "{n}")
    }
    assert!(valid_filename("A mod 1.2.zip"));
}
#[test]
fn durable_ready_hash() {
    let (d, mut s, r) = start();
    ready(&mut s, &r);
    let reopened = Store::open(d.path());
    assert_eq!(reopened.row(&r.id).unwrap().state, "ready");
    assert!(reopened.verify(&r.id, || false).is_ok());
}
#[test]
fn reject_tampered_archive() {
    let (_d, mut s, r) = start();
    ready(&mut s, &r);
    let path = s.verify(&r.id, || false).unwrap();
    fs::write(path, b"PK\x03\x04changed").unwrap();
    assert!(s.verify(&r.id, || false).is_err());
}
#[test]
fn reject_html_disguised_as_zip() {
    let (_d, mut s, r) = start();
    let p = s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    fs::write(p, b"<!doctype html>Sign in").unwrap();
    s.begin_validation(&r.id, &binding()).unwrap();
    assert!(s.validate(&r.id, || false).is_err());
    assert_ne!(s.row(&r.id).unwrap().state, "ready");
}
#[test]
fn no_late_completion_after_cancel() {
    let (_d, mut s, r) = start();
    s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    s.finish_error(&r.id, "cancelled", "Cancelled").unwrap();
    assert!(s.begin_validation(&r.id, &binding()).is_err());
}
#[test]
fn progress_limits() {
    let (_d, mut s, r) = start();
    s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    assert!(s.progress(&r.id, MAX_BYTES + 1, None).is_err());
    assert!(s.progress(&r.id, 100, Some(99)).is_err());
}
#[test]
fn restart_incomplete_is_not_ready() {
    let (d, mut s, r) = start();
    s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    assert_eq!(
        Store::open(d.path()).row(&r.id).unwrap().state,
        "interrupted"
    );
}
#[test]
fn corrupt_store_preserved() {
    let d = tempfile::tempdir().unwrap();
    fs::write(d.path().join("receipts.json"), "{broken").unwrap();
    let mut s = Store::open(d.path());
    assert!(s.request("a.zip", binding(), None, 1).is_err());
    assert_eq!(
        fs::read_to_string(d.path().join("receipts.json")).unwrap(),
        "{broken"
    );
}
#[test]
fn removing_requires_confirmation() {
    let (_d, mut s, r) = start();
    assert!(s.remove(&r.id, "").is_err());
    s.remove(&r.id, "REMOVE").unwrap();
    assert!(s.row(&r.id).is_err());
}
#[test]
fn cancellation_during_hash() {
    let (_d, mut s, r) = start();
    let p = s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    fs::write(p, b"PK\x03\x04fixture").unwrap();
    s.begin_validation(&r.id, &binding()).unwrap();
    assert!(s.validate(&r.id, || true).is_err());
}
#[cfg(unix)]
#[test]
fn symlink_archive_rejected() {
    let (d, mut s, r) = start();
    let p = s.approve(&r.id, &binding(), "DOWNLOAD", 2).unwrap();
    let other = d.path().join("other");
    fs::write(&other, b"PK\x03\x04fixture").unwrap();
    std::os::unix::fs::symlink(other, p).unwrap();
    s.begin_validation(&r.id, &binding()).unwrap();
    assert!(s.validate(&r.id, || false).is_err());
}
