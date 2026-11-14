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
            retention_days: None,
            retention_count: None,
            retention_bytes: None,
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
    assert_eq!(
        fs::read(f.vault.join(&id).join("post-update-files/texture.dds")).unwrap(),
        b"new texture"
    );
    assert!(
        !f.root
            .parent()
            .unwrap()
            .join(format!(".modhunter-displaced-{id}"))
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

fn incoming_files(f: &Fixture, files: &[(&str, &[u8])]) -> PathBuf {
    let path = f._d.path().join("incoming");
    fs::create_dir(&path).unwrap();
    for (name, data) in files {
        let dest = path.join(name);
        fs::create_dir_all(dest.parent().unwrap()).unwrap();
        fs::write(dest, data).unwrap();
    }
    path
}
fn profile_root(f: &Fixture, enabled: bool) -> PathBuf {
    let root = f._d.path().join("profiles");
    fs::create_dir_all(root.join("Saved")).unwrap();
    fs::write(
        root.join("Saved/modlist.txt"),
        if enabled { "+Mod\n" } else { "-Mod\n" },
    )
    .unwrap();
    root
}
#[test]
fn compare_returns_every_file_and_preserves_explicit_nonremoval() {
    let f = Fixture::new();
    let dir = incoming_files(&f, &[]);
    for n in 0..300 {
        fs::write(dir.join(format!("{n:04}.nif")), format!("file {n}")).unwrap();
    }
    let mut req = f.req("compare", None, None);
    req.other = Some(dir);
    let reply = execute(req, || false).unwrap();
    assert_eq!(reply["entries"].as_array().unwrap().len(), 301);
    let absent = reply["entries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["path"] == "mesh.nif")
        .unwrap();
    assert_eq!(absent["state"], "notInArchive");
    assert_eq!(absent["removalAuthorized"], false);
}
#[test]
fn equality_ignores_metadata_and_case_but_approval_digest_keeps_exact_spelling() {
    let f = Fixture::new();
    fs::write(f.root.join("meta.ini"), "installed native metadata").unwrap();
    let dir = incoming_files(
        &f,
        &[("MESH.NIF", b"original"), ("meta.ini", b"archive metadata")],
    );
    let mut req = f.req("compare", None, None);
    req.other = Some(dir.clone());
    let first = execute(req, || false).unwrap();
    assert_eq!(first["identical"], true);
    fs::rename(dir.join("MESH.NIF"), dir.join("different.nif")).unwrap();
    let mut req = f.req("compare", None, None);
    req.other = Some(dir);
    let next = execute(req, || false).unwrap();
    assert_ne!(first["digest"], next["digest"]);
    assert_eq!(next["entries"][0]["renameHints"], json!(["mesh.nif"]));
}
#[test]
fn optional_document_defaults_do_not_hide_files() {
    let f = Fixture::new();
    let dir = incoming_files(
        &f,
        &[("meshes/changed.nif", b"new"), ("readme.txt", b"docs")],
    );
    let mut r = f.req("compare", None, None);
    r.other = Some(dir);
    let reply = execute(r, || false).unwrap();
    let doc = reply["entries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["path"] == "readme.txt")
        .unwrap();
    assert_eq!(doc["optional"], true);
    assert_eq!(doc["selectedDefault"], false);
    assert_eq!(doc["selectable"], true);
}
#[test]
fn verify_prepared_detects_target_backup_and_incoming_changes() {
    let f = Fixture::new();
    let dir = incoming_files(&f, &[("mesh.nif", b"new")]);
    let mut c = f.req("compare", None, None);
    c.other = Some(dir.clone());
    let digest = execute(c, || false).unwrap()["digest"]
        .as_str()
        .unwrap()
        .to_owned();
    let id = f.backup();
    let mut r = f.req("verifyPrepared", Some(&id), None);
    r.other = Some(dir.clone());
    r.expected_digest = Some(digest.clone());
    assert!(execute(r, || false).is_ok());
    fs::write(dir.join("mesh.nif"), "changed").unwrap();
    let mut r = f.req("verifyPrepared", Some(&id), None);
    r.other = Some(dir);
    r.expected_digest = Some(digest);
    assert!(execute(r, || false).is_err());
    fs::write(f.root.join("mesh.nif"), "later edit").unwrap();
    assert!(execute(f.req("verifyPrepared", Some(&id), None), || false).is_err());
}
#[test]
fn case_changed_files_replace_identity_and_share_retained_parent_spelling() {
    let f = Fixture::new();
    fs::create_dir(f.root.join("Meshes")).unwrap();
    fs::write(f.root.join("Meshes/A.nif"), "old").unwrap();
    fs::write(f.root.join("Meshes/keep.nif"), "keep").unwrap();
    let dir = incoming_files(
        &f,
        &[("meshes/a.NIF", b"new"), ("meshes/new.nif", b"addition")],
    );
    let mut c = f.req("compare", None, None);
    c.other = Some(dir.clone());
    let digest = execute(c, || false).unwrap()["digest"]
        .as_str()
        .unwrap()
        .to_owned();
    let id = f.backup();
    let mut r = f.req("apply", Some(&id), Some("INSTALL"));
    r.other = Some(dir);
    r.expected_digest = Some(digest);
    r.selected = vec!["meshes/a.NIF".into(), "meshes/new.nif".into()];
    let reply = execute(r, || false).unwrap();
    assert!(reply["cleanupWarning"].is_null());
    assert_eq!(fs::read(f.root.join("Meshes/a.NIF")).unwrap(), b"new");
    assert_eq!(fs::read(f.root.join("Meshes/keep.nif")).unwrap(), b"keep");
    assert_eq!(
        tree(&f.root, &|| false)
            .unwrap()
            .keys()
            .filter(|n| n.to_lowercase() == "meshes/a.nif")
            .count(),
        1
    );
    assert!(
        !f.root
            .parent()
            .unwrap()
            .join(format!(".modhunter-applied-{id}"))
            .exists()
    );
}
#[test]
fn new_target_is_absent_until_native_install_then_undo_retains_every_byte() {
    let f = Fixture::new();
    fs::remove_dir_all(&f.root).unwrap();
    let profiles = profile_root(&f, false);
    let prepare = execute(f.req("prepareNew", None, Some("INSTALL")), || false).unwrap();
    let id = prepare["backup"]["id"].as_str().unwrap();
    assert!(!f.root.exists());
    assert_eq!(prepare["backup"]["newTarget"], true);
    execute(f.req("verifyPrepared", Some(id), None), || false).unwrap();
    fs::create_dir(&f.root).unwrap();
    fs::write(f.root.join("new.nif"), "native installed bytes").unwrap();
    execute(f.req("sealNew", Some(id), None), || false).unwrap();
    let mut preview = f.req("previewRestoreNew", Some(id), None);
    preview.other = Some(profiles.clone());
    execute(preview, || false).unwrap();
    let mut undo = f.req("restoreNew", Some(id), Some("RESTORE"));
    undo.other = Some(profiles);
    let reply = execute(undo, || false).unwrap();
    assert_eq!(reply["retainedCreatedFiles"], true);
    assert!(!f.root.exists());
    assert_eq!(
        fs::read(f.vault.join(id).join("new-target-files/new.nif")).unwrap(),
        b"native installed bytes"
    );
    execute(f.req("list", None, None), || false).unwrap();
}
#[test]
fn new_target_undo_requires_complete_disabled_profile_ownership() {
    let f = Fixture::new();
    fs::remove_dir_all(&f.root).unwrap();
    let profiles = profile_root(&f, true);
    let prep = execute(f.req("prepareNew", None, Some("INSTALL")), || false).unwrap();
    let id = prep["backup"]["id"].as_str().unwrap();
    fs::create_dir(&f.root).unwrap();
    fs::write(f.root.join("mesh.nif"), "new").unwrap();
    execute(f.req("sealNew", Some(id), None), || false).unwrap();
    assert!(execute(f.req("restoreNew", Some(id), Some("RESTORE")), || false).is_err());
    let mut undo = f.req("restoreNew", Some(id), Some("RESTORE"));
    undo.other = Some(profiles);
    assert!(execute(undo, || false).is_err());
    assert!(f.root.exists());
}
#[test]
fn new_target_later_edit_blocks_undo() {
    let f = Fixture::new();
    fs::remove_dir_all(&f.root).unwrap();
    let profiles = profile_root(&f, false);
    let prep = execute(f.req("prepareNew", None, Some("INSTALL")), || false).unwrap();
    let id = prep["backup"]["id"].as_str().unwrap();
    fs::create_dir(&f.root).unwrap();
    fs::write(f.root.join("mesh.nif"), "new").unwrap();
    execute(f.req("sealNew", Some(id), None), || false).unwrap();
    fs::write(f.root.join("mesh.nif"), "user edit").unwrap();
    let mut undo = f.req("restoreNew", Some(id), Some("RESTORE"));
    undo.other = Some(profiles);
    assert!(execute(undo, || false).is_err());
    assert_eq!(fs::read(f.root.join("mesh.nif")).unwrap(), b"user edit");
}
#[test]
fn retention_preserves_newest_and_incomplete_records_and_needs_exact_digest() {
    let f = Fixture::new();
    let first = f.backup();
    execute(f.req("seal", Some(&first), None), || false).unwrap();
    let second = f.backup();
    execute(f.req("seal", Some(&second), None), || false).unwrap();
    let pending = f.backup();
    let (_, mut old) = load(&f.vault, &first).unwrap();
    old.created_at = 1;
    atomic_json(&f.vault.join(&first).join("receipt.json"), &old).unwrap();
    let mut req = f.req("retentionPreview", None, None);
    req.retention_count = Some(1);
    let preview = execute(req, || false).unwrap();
    assert_eq!(preview["eligible"].as_array().unwrap().len(), 1);
    assert_eq!(preview["eligible"][0]["id"], first);
    let mut stale = f.req("retentionApply", None, Some("DELETE BACKUPS"));
    stale.retention_count = Some(1);
    stale.expected_digest = Some("stale".into());
    assert!(execute(stale, || false).is_err());
    assert!(f.vault.join(&first).exists());
    let mut apply = f.req("retentionApply", None, Some("DELETE BACKUPS"));
    apply.retention_count = Some(1);
    apply.expected_digest = preview["digest"].as_str().map(str::to_owned);
    execute(apply, || false).unwrap();
    assert!(!f.vault.join(first).exists());
    assert!(f.vault.join(second).exists());
    assert!(f.vault.join(pending).exists());
}
#[test]
fn retention_refuses_unexpected_backup_material() {
    let f = Fixture::new();
    let id = f.updated();
    fs::write(f.vault.join(id).join("unowned.txt"), "preserve").unwrap();
    let mut r = f.req("retentionPreview", None, None);
    r.retention_count = Some(1);
    assert!(execute(r, || false).is_err());
}
#[test]
fn changed_displaced_copy_is_never_anonymous_cleanup_authority() {
    let f = Fixture::new();
    let (id, _stage, displaced) = interrupted_apply(&f, 2);
    let reply = execute(f.req("recover", Some(&id), Some("RECOVER")), || false).unwrap();
    assert!(reply["cleanupWarning"].is_null());
    assert!(!displaced.exists());
    let (dir, mut record) = load(&f.vault, &id).unwrap();
    fs::create_dir(&displaced).unwrap();
    fs::write(displaced.join("mesh.nif"), "user copy").unwrap();
    record.displaced = Some(displaced.clone());
    atomic_json(&dir.join("receipt.json"), &record).unwrap();
    assert!(
        execute(
            f.req("cleanupDisplaced", Some(&id), Some("CLEANUP")),
            || false
        )
        .is_err()
    );
    assert_eq!(fs::read(displaced.join("mesh.nif")).unwrap(), b"user copy");
}

#[test]
fn profile_inventory_includes_missing_targets_without_cross_profile_leaks() {
    let mut f = Fixture::new();
    let mods = f._d.path().join("mods");
    fs::create_dir(&mods).unwrap();
    let moved = mods.join("Mod");
    fs::rename(&f.root, &moved).unwrap();
    f.root = moved;
    let id = f.updated();
    fs::remove_dir_all(&f.root).unwrap();
    let mut request = f.req("listProfile", None, None);
    request.root = mods.clone();
    request.mod_name = "__profile_inventory__".into();
    let rows = execute(request, || false).unwrap();
    assert_eq!(rows["backups"].as_array().unwrap().len(), 1);
    assert_eq!(rows["backups"][0]["id"], id);
    let mut other = f.req("listProfile", None, None);
    other.root = mods;
    other.mod_name = "__profile_inventory__".into();
    other.profile_id = "different-profile".into();
    assert!(
        execute(other, || false).unwrap()["backups"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[test]
fn completed_new_target_retention_is_explicit_and_preserves_current_mod() {
    let f = Fixture::new();
    fs::remove_dir_all(&f.root).unwrap();
    let value = execute(f.req("prepareNew", None, Some("INSTALL")), || false).unwrap();
    let id = value["backup"]["id"].as_str().unwrap();
    assert!(
        execute(
            f.req("deleteBackup", Some(id), Some("DELETE BACKUP")),
            || false
        )
        .is_err()
    );
    fs::create_dir(&f.root).unwrap();
    fs::write(f.root.join("new.nif"), "native installed").unwrap();
    execute(f.req("sealNew", Some(id), None), || false).unwrap();
    assert!(execute(f.req("deleteBackup", Some(id), None), || false).is_err());
    execute(
        f.req("deleteBackup", Some(id), Some("DELETE BACKUP")),
        || false,
    )
    .unwrap();
    assert!(!f.vault.join(id).exists());
    assert_eq!(
        fs::read(f.root.join("new.nif")).unwrap(),
        b"native installed"
    );
}
#[test]
fn restored_new_target_bytes_need_exact_record_before_explicit_deletion() {
    let f = Fixture::new();
    fs::remove_dir_all(&f.root).unwrap();
    let profiles = profile_root(&f, false);
    let value = execute(f.req("prepareNew", None, Some("INSTALL")), || false).unwrap();
    let id = value["backup"]["id"].as_str().unwrap();
    fs::create_dir(&f.root).unwrap();
    fs::write(f.root.join("new.nif"), "native installed").unwrap();
    execute(f.req("sealNew", Some(id), None), || false).unwrap();
    let mut restore = f.req("restoreNew", Some(id), Some("RESTORE"));
    restore.other = Some(profiles);
    execute(restore, || false).unwrap();
    fs::write(f.vault.join(id).join("unexpected.txt"), "preserve").unwrap();
    assert!(
        execute(
            f.req("deleteBackup", Some(id), Some("DELETE BACKUP")),
            || false
        )
        .is_err()
    );
    fs::remove_file(f.vault.join(id).join("unexpected.txt")).unwrap();
    execute(
        f.req("deleteBackup", Some(id), Some("DELETE BACKUP")),
        || false,
    )
    .unwrap();
    assert!(!f.vault.join(id).exists());
    assert!(!f.root.exists());
}
