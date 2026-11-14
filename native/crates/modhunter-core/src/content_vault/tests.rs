use super::*;
struct Fixture {
    _d: tempfile::TempDir,
    root: PathBuf,
    vault: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("Mod");
        fs::create_dir(&root).unwrap();
        fs::write(root.join("mesh.nif"), "original").unwrap();
        let vault = d.path().join("vault");
        Self { _d: d, root, vault }
    }
    fn req(&self, action: &str, id: Option<&str>, confirm: Option<&str>) -> Request {
        Request {
            action: action.into(),
            root: self.root.clone(),
            vault: self.vault.clone(),
            profile_id: "Skyrim-profile".into(),
            game: "skyrimspecialedition".into(),
            mod_name: "Mod".into(),
            id: id.map(str::to_owned),
            confirm: confirm.map(str::to_owned),
            other: None,
        }
    }
    fn backup(&self) -> String {
        execute(self.req("snapshot", None, Some("BACKUP")), || false).unwrap()["backup"]["id"]
            .as_str()
            .unwrap()
            .into()
    }
    fn updated(&self) -> String {
        let id = self.backup();
        fs::write(self.root.join("mesh.nif"), "new").unwrap();
        fs::write(self.root.join("texture.dds"), "new texture").unwrap();
        execute(self.req("seal", Some(&id), None), || false).unwrap();
        id
    }
}
#[test]
fn snapshot_copies_real_bytes() {
    let f = Fixture::new();
    let id = f.backup();
    assert_eq!(
        fs::read(f.vault.join(id).join("files/mesh.nif")).unwrap(),
        b"original"
    );
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
}
#[test]
fn explicit_backup_required() {
    let f = Fixture::new();
    assert!(execute(f.req("snapshot", None, None), || false).is_err());
}
#[test]
fn unsealed_restore_refused() {
    let f = Fixture::new();
    let id = f.backup();
    assert!(execute(f.req("restore", Some(&id), Some("RESTORE")), || false).is_err());
}
#[test]
fn restore_real_files() {
    let f = Fixture::new();
    let id = f.updated();
    execute(f.req("restore", Some(&id), Some("RESTORE")), || false).unwrap();
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
    assert!(!f.root.join("texture.dds").exists());
    assert!(
        f.root
            .parent()
            .unwrap()
            .join(format!(".modhunter-displaced-{id}/texture.dds"))
            .exists()
    );
}
#[test]
fn preview_does_not_modify_target() {
    let f = Fixture::new();
    let id = f.updated();
    let result = execute(f.req("previewRestore", Some(&id), None), || false).unwrap();
    assert_eq!(result["canRestore"], true);
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"new");
}
#[test]
fn preserve_later_user_edit() {
    let f = Fixture::new();
    let id = f.updated();
    fs::write(f.root.join("mesh.nif"), "user change").unwrap();
    assert!(execute(f.req("restore", Some(&id), Some("RESTORE")), || false).is_err());
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"user change");
}
#[test]
fn preserve_later_added_file() {
    let f = Fixture::new();
    let id = f.updated();
    fs::write(f.root.join("important.txt"), "notes").unwrap();
    assert!(execute(f.req("restore", Some(&id), Some("RESTORE")), || false).is_err());
}
#[test]
fn reject_tampered_backup() {
    let f = Fixture::new();
    let id = f.updated();
    fs::write(f.vault.join(&id).join("files/mesh.nif"), "wrong").unwrap();
    assert!(execute(f.req("restore", Some(&id), Some("RESTORE")), || false).is_err());
}
#[test]
fn profile_change_blocks_restore() {
    let f = Fixture::new();
    let id = f.updated();
    let mut r = f.req("restore", Some(&id), Some("RESTORE"));
    r.profile_id = "other".into();
    assert!(execute(r, || false).is_err());
}
#[test]
fn game_change_blocks_restore() {
    let f = Fixture::new();
    let id = f.updated();
    let mut r = f.req("restore", Some(&id), Some("RESTORE"));
    r.game = "fallout4".into();
    assert!(execute(r, || false).is_err());
}
#[test]
fn cancellation_keeps_target() {
    let f = Fixture::new();
    assert!(execute(f.req("snapshot", None, Some("BACKUP")), || true).is_err());
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
}
#[test]
fn no_nested_vault() {
    let f = Fixture::new();
    let mut r = f.req("snapshot", None, Some("BACKUP"));
    r.vault = f.root.join("vault");
    assert!(execute(r, || false).is_err());
}
#[test]
fn delete_needs_distinct_confirmation() {
    let f = Fixture::new();
    let id = f.updated();
    assert!(execute(f.req("deleteBackup", Some(&id), Some("RESTORE")), || false).is_err());
    execute(
        f.req("deleteBackup", Some(&id), Some("DELETE BACKUP")),
        || false,
    )
    .unwrap();
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"new");
}
#[test]
fn listing_reports_actual_backup() {
    let f = Fixture::new();
    f.updated();
    let result = execute(f.req("list", None, None), || false).unwrap();
    assert_eq!(result["backups"][0]["includesModFiles"], true);
}
#[cfg(unix)]
#[test]
fn links_never_followed() {
    let f = Fixture::new();
    std::os::unix::fs::symlink("/etc/passwd", f.root.join("secret")).unwrap();
    assert!(execute(f.req("snapshot", None, Some("BACKUP")), || false).is_err());
}

#[test]
fn nested_vault_rejection_does_not_create_any_mod_files() {
    let f = Fixture::new();
    let mut req = f.req("snapshot", None, Some("BACKUP"));
    req.vault = f.root.join("nested-vault");
    assert!(execute(req, || false).is_err());
    assert!(!f.root.join("nested-vault").exists());
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 1);
}
