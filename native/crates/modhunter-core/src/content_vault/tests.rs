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
            selected: Vec::new(),
            remove: Vec::new(),
            expected_digest: None,
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

fn incoming(f: &Fixture) -> PathBuf {
    let p = f._d.path().join("incoming");
    fs::create_dir(&p).unwrap();
    fs::write(p.join("mesh.nif"), "updated").unwrap();
    fs::write(p.join("new.dds"), "optional").unwrap();
    p
}
fn reviewed(f: &Fixture, p: PathBuf) -> Request {
    let mut preview = f.req("compare", None, None);
    preview.other = Some(p.clone());
    let digest = execute(preview, || false).unwrap()["digest"]
        .as_str()
        .unwrap()
        .to_owned();
    let id = f.backup();
    let mut req = f.req("apply", Some(&id), Some("INSTALL"));
    req.other = Some(p);
    req.selected = vec!["mesh.nif".into()];
    req.expected_digest = Some(digest);
    req
}
#[test]
fn reviewed_selected_files_apply_and_undo() {
    let f = Fixture::new();
    let p = incoming(&f);
    let req = reviewed(&f, p);
    let id = req.id.clone().unwrap();
    let result = execute(req, || false).unwrap();
    assert_eq!(result["backup"]["undoAvailable"], true);
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"updated");
    assert!(!f.root.join("new.dds").exists());
    execute(f.req("restore", Some(&id), Some("RESTORE")), || false).unwrap();
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
}
#[test]
fn altered_incoming_invalidates_review() {
    let f = Fixture::new();
    let p = incoming(&f);
    let req = reviewed(&f, p.clone());
    fs::write(p.join("mesh.nif"), "other").unwrap();
    assert!(execute(req, || false).is_err());
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
}
#[test]
fn changes_after_backup_block_apply() {
    let f = Fixture::new();
    let p = incoming(&f);
    let req = reviewed(&f, p);
    fs::write(f.root.join("user.cfg"), "keep").unwrap();
    assert!(execute(req, || false).is_err());
    assert_eq!(fs::read(f.root.join("user.cfg")).unwrap(), b"keep");
}
#[test]
fn installer_archive_cannot_use_plain_apply() {
    let f = Fixture::new();
    let p = incoming(&f);
    fs::create_dir(p.join("fomod")).unwrap();
    fs::write(p.join("fomod/moduleconfig.xml"), "choices").unwrap();
    let req = reviewed(&f, p);
    assert!(execute(req, || false).is_err());
}
#[test]
fn archive_cannot_replace_mo2_metadata() {
    let f = Fixture::new();
    let p = incoming(&f);
    fs::write(p.join("meta.ini"), "spoof").unwrap();
    let mut req = reviewed(&f, p);
    req.selected = vec!["meta.ini".into()];
    assert!(execute(req, || false).is_err());
}

#[test]
fn canonical_extended_paths_are_supported_without_skipping_child_checks() {
    let f = Fixture::new();
    let canonical = fs::canonicalize(&f.root).unwrap();
    assert_eq!(checked_root(&canonical).unwrap(), canonical);
    let mut request = f.req("snapshot", None, Some("BACKUP"));
    request.root = canonical;
    assert!(execute(request, || false).unwrap()["backup"]["includesModFiles"] == true);
}

fn interrupted_apply(f: &Fixture, phase: u8) -> (String, PathBuf, PathBuf) {
    let id = f.backup();
    let (dir, mut r) = load(&f.vault, &id).unwrap();
    let stage = f.root.parent().unwrap().join(".modhunter-apply-fixture");
    fs::create_dir(&stage).unwrap();
    fs::write(stage.join("mesh.nif"), "updated content").unwrap();
    r.after = Some(tree(&stage, &|| false).unwrap());
    let displaced = f
        .root
        .parent()
        .unwrap()
        .join(format!(".modhunter-applied-{id}"));
    r.state = "applying".into();
    r.staged = Some(stage.clone());
    r.displaced = Some(displaced.clone());
    atomic_json(&dir.join("receipt.json"), &r).unwrap();
    if phase >= 1 {
        fs::rename(&f.root, &displaced).unwrap();
    }
    if phase >= 2 {
        fs::rename(&stage, &f.root).unwrap();
    }
    (id, stage, displaced)
}
#[test]
fn interrupted_swaps_recover_by_verified_identity_at_every_phase() {
    for phase in 0..3 {
        let f = Fixture::new();
        let (id, _, _) = interrupted_apply(&f, phase);
        let result = execute(f.req("recover", Some(&id), Some("RECOVER")), || false).unwrap();
        assert_eq!(result["backup"]["state"], "sealed");
        assert_eq!(
            fs::read(f.root.join("mesh.nif")).unwrap(),
            b"updated content"
        );
        execute(f.req("restore", Some(&id), Some("RESTORE")), || false).unwrap();
        assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"original");
    }
}
#[test]
fn ambiguous_interrupted_swap_preserves_all_versions() {
    let f = Fixture::new();
    let (id, stage, displaced) = interrupted_apply(&f, 1);
    fs::write(stage.join("mesh.nif"), "later staging edit").unwrap();
    assert!(execute(f.req("recover", Some(&id), Some("RECOVER")), || false).is_err());
    assert_eq!(fs::read(displaced.join("mesh.nif")).unwrap(), b"original");
    assert_eq!(
        fs::read(stage.join("mesh.nif")).unwrap(),
        b"later staging edit"
    );
    assert!(!f.root.exists());
}
#[test]
fn recovery_requires_separate_explicit_consent() {
    let f = Fixture::new();
    let (id, stage, _) = interrupted_apply(&f, 1);
    assert!(execute(f.req("recover", Some(&id), None), || false).is_err());
    assert!(stage.exists());
    assert!(!f.root.exists());
}
