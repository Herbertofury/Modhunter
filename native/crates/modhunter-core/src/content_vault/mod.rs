//! Actual-file recovery snapshots. A profile-text backup is not a mod-content backup.
//! Snapshots and inspection are read-only to the target. Restore needs a sealed
//! post-install tree, matching ownership, explicit approval and no later edits.
use crate::acquisition::{atomic_json, file_hash, reject_link};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    fs,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
type Result<T> = std::result::Result<T, String>;
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
const MAX_FILES: usize = 200_000;
const MAX_TREE_BYTES: u64 = 256 * 1024 * 1024 * 1024;
const MAX_MANIFEST: u64 = 64 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub action: String,
    pub root: PathBuf,
    pub vault: PathBuf,
    pub profile_id: String,
    pub game: String,
    pub mod_name: String,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub confirm: Option<String>,
    #[serde(default)]
    pub other: Option<PathBuf>,
    #[serde(default)]
    pub selected: Vec<String>,
    #[serde(default)]
    pub remove: Vec<String>,
    #[serde(default)]
    pub expected_digest: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Entry {
    bytes: u64,
    sha256: String,
}
type Tree = BTreeMap<String, Entry>;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Record {
    schema: u32,
    id: String,
    root: PathBuf,
    profile_id: String,
    game: String,
    mod_name: String,
    state: String,
    created_at: i64,
    before: Tree,
    after: Option<Tree>,
    #[serde(default)]
    displaced: Option<PathBuf>,
    #[serde(default)]
    staged: Option<PathBuf>,
}
pub fn checked_root(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute() || path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err("Use an absolute path without parent traversal.".into());
    }
    let mut walked = PathBuf::new();
    for part in path.components() {
        walked.push(part);
        // A Windows prefix (notably \\?\C:) is not an addressable path until
        // RootDir is appended. Check the complete root and every child, rather
        // than sending a volume prefix to GetFileAttributes/CreateFile.
        if !matches!(part, Component::Prefix(_)) {
            reject_link(&walked)?;
        }
    }
    let result = fs::canonicalize(path).map_err(err)?;
    if !result.is_dir() || result.parent().is_none() {
        return Err("Select a real mod directory, not a filesystem root.".into());
    }
    Ok(result)
}
fn relative_file(root: &Path, name: &str) -> Result<PathBuf> {
    if name.is_empty() || name.len() > 32760 || name.contains(['\\', ':', '\0']) {
        return Err("Unsafe relative file identity.".into());
    }
    let path = Path::new(name);
    if !path.components().all(|c| matches!(c, Component::Normal(_))) {
        return Err("Invalid manifest path.".into());
    }
    Ok(root.join(path))
}
fn tree(root: &Path, cancel: &impl Fn() -> bool) -> Result<Tree> {
    let mut out = Tree::new();
    let mut stack = vec![root.to_path_buf()];
    let mut bytes = 0u64;
    let mut names = HashSet::new();
    while let Some(dir) = stack.pop() {
        if cancel() {
            return Err("File operation cancelled.".into());
        }
        reject_link(&dir)?;
        for child in fs::read_dir(&dir).map_err(err)? {
            let child = child.map_err(err)?;
            let path = child.path();
            reject_link(&path)?;
            let kind = child.file_type().map_err(err)?;
            if kind.is_dir() {
                stack.push(path);
                if stack.len() > MAX_FILES {
                    return Err("Too many subdirectories.".into());
                }
                continue;
            }
            if !kind.is_file() {
                return Err(
                    "Only regular files are supported; no devices or special files.".into(),
                );
            }
            let name = path
                .strip_prefix(root)
                .map_err(err)?
                .to_str()
                .ok_or("Non-UTF-8 file name needs manual review.")?
                .replace('\\', "/");
            relative_file(root, &name)?;
            if !names.insert(name.to_lowercase()) {
                return Err("Case-colliding files need manual review.".into());
            }
            let (size, sha256) = file_hash(&path, cancel)?;
            bytes = bytes.checked_add(size).ok_or("Tree too large.")?;
            if bytes > MAX_TREE_BYTES || out.len() >= MAX_FILES {
                return Err("Mod snapshot exceeds its configured bound.".into());
            }
            out.insert(
                name,
                Entry {
                    bytes: size,
                    sha256,
                },
            );
        }
    }
    Ok(out)
}
fn copy_tree(from: &Path, to: &Path, expected: &Tree, cancel: &impl Fn() -> bool) -> Result<()> {
    for (name, entry) in expected {
        if cancel() {
            return Err("File operation cancelled.".into());
        }
        let src = relative_file(from, name)?;
        let dest = relative_file(to, name)?;
        reject_link(&src)?;
        fs::create_dir_all(dest.parent().ok_or("Missing destination parent.")?).map_err(err)?;
        checked_root(dest.parent().unwrap())?;
        let mut input = fs::File::open(&src).map_err(err)?;
        let mut output = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&dest)
            .map_err(err)?;
        let mut buf = [0; 128 * 1024];
        let mut copied = 0;
        loop {
            if cancel() {
                return Err("File operation cancelled.".into());
            }
            let n = input.read(&mut buf).map_err(err)?;
            if n == 0 {
                break;
            }
            copied += n as u64;
            if copied > entry.bytes {
                return Err("Source file grew during backup.".into());
            }
            output.write_all(&buf[..n]).map_err(err)?;
        }
        output.sync_all().map_err(err)?;
        let (size, hash) = file_hash(&dest, cancel)?;
        if size != entry.bytes || hash != entry.sha256 {
            return Err("Source changed while copying; snapshot not approved.".into());
        }
    }
    if tree(from, cancel)? != *expected {
        return Err("Source tree changed during copy; retry after activity stops.".into());
    }
    Ok(())
}
fn validate_id(id: &str) -> Result<()> {
    if !id.starts_with("backup-")
        || id.len() > 100
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
    {
        Err("Invalid backup identity.".into())
    } else {
        Ok(())
    }
}
fn load(vault: &Path, id: &str) -> Result<(PathBuf, Record)> {
    validate_id(id)?;
    let dir = vault.join(id);
    checked_root(&dir)?;
    let path = dir.join("receipt.json");
    reject_link(&path)?;
    let mut data = Vec::new();
    fs::File::open(path)
        .map_err(err)?
        .take(MAX_MANIFEST + 1)
        .read_to_end(&mut data)
        .map_err(err)?;
    if data.len() as u64 > MAX_MANIFEST {
        return Err("Backup manifest exceeds limit.".into());
    }
    let record: Record = serde_json::from_slice(&data).map_err(err)?;
    if record.schema != 1 || record.id != id || record.before.len() > MAX_FILES {
        return Err("Invalid backup manifest.".into());
    }
    for (name, entry) in &record.before {
        relative_file(&dir, name)?;
        if entry.sha256.len() != 64 || !entry.sha256.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("Invalid backup hash.".into());
        }
    }
    Ok((dir, record))
}
fn review_digest(request: &Request, installed: &Tree, incoming: &Tree) -> Result<String> {
    let body=serde_json::to_vec(&json!({"profile":request.profile_id,"game":request.game,"mod":request.mod_name,"installed":installed,"incoming":incoming})).map_err(err)?;
    Ok(format!("{:x}", Sha256::digest(body)))
}
fn copy_selected(
    source: &Path,
    dest: &Path,
    selected: &Tree,
    cancel: &impl Fn() -> bool,
) -> Result<()> {
    for (name, expected) in selected {
        if cancel() {
            return Err("Operation cancelled.".into());
        }
        let src = relative_file(source, name)?;
        let dst = relative_file(dest, name)?;
        reject_link(&src)?;
        fs::create_dir_all(dst.parent().ok_or("Missing selected parent.")?).map_err(err)?;
        let mut input = fs::File::open(&src).map_err(err)?;
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&dst)
            .map_err(err)?;
        let mut buf = [0u8; 128 * 1024];
        loop {
            if cancel() {
                return Err("Operation cancelled.".into());
            }
            let n = input.read(&mut buf).map_err(err)?;
            if n == 0 {
                break;
            }
            output.write_all(&buf[..n]).map_err(err)?;
        }
        output.sync_all().map_err(err)?;
        let (bytes, hash) = file_hash(&dst, cancel)?;
        if bytes != expected.bytes || hash != expected.sha256 {
            return Err("Selected content changed while copying.".into());
        }
    }
    Ok(())
}
fn public(r: &Record) -> Value {
    json!({"id":r.id,"state":r.state,"profileId":r.profile_id,"game":r.game,"modName":r.mod_name,"createdAt":r.created_at,"files":r.before.len(),"bytes":r.before.values().map(|e|e.bytes).sum::<u64>(),"includesModFiles":true,"undoAvailable":r.state=="sealed"})
}

pub fn execute(request: Request, cancel: impl Fn() -> bool) -> Result<Value> {
    let root = if request.action == "recover" && !request.root.exists() {
        let parent = checked_root(request.root.parent().ok_or("Missing target parent.")?)?;
        let name = request.root.file_name().ok_or("Missing target name.")?;
        parent.join(name)
    } else {
        checked_root(&request.root)?
    };
    if request.profile_id.is_empty()
        || request.profile_id.len() > 1024
        || request.game.is_empty()
        || request.mod_name.is_empty()
    {
        return Err("An exact profile, game and target mod are required.".into());
    }
    // Do not create a vault through a symlink or inside the managed mod.
    if !request.vault.exists() {
        let parent = checked_root(request.vault.parent().ok_or("Missing vault parent.")?)?;
        let target = parent.join(request.vault.file_name().ok_or("Missing vault name.")?);
        if target.starts_with(&root) || root.starts_with(&target) {
            return Err("Backup vault and mod content must be separate.".into());
        }
        fs::create_dir(&request.vault).map_err(err)?;
    }
    let vault = checked_root(&request.vault)?;
    if root.starts_with(&vault) || vault.starts_with(&root) {
        return Err("Backup vault and mod content must be separate.".into());
    }
    let marker = vault.join("modhunter-content-vault.json");
    if marker.exists() {
        reject_link(&marker)?;
        if fs::read(&marker).map_err(err)?
            != b"{\"schema\":1,\"owner\":\"Modhunter-content-vault\"}"
        {
            return Err("Unrecognized backup directory owner.".into());
        }
    } else {
        if fs::read_dir(&vault).map_err(err)?.next().is_some() {
            return Err("Choose an empty, app-owned backup directory.".into());
        }
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&marker)
            .map_err(err)?;
        f.write_all(b"{\"schema\":1,\"owner\":\"Modhunter-content-vault\"}")
            .map_err(err)?;
        f.sync_all().map_err(err)?;
    }
    // A process-held lock serializes snapshot/restore operations across CLI runs.
    let lock_path = vault.join(".lock");
    let _lock = VaultLock::acquire(&lock_path)?;
    if request.action == "list" || request.action == "storage" {
        let mut rows = Vec::new();
        let mut warnings = 0;
        for child in fs::read_dir(&vault).map_err(err)? {
            let child = child.map_err(err)?;
            let id = child.file_name().to_string_lossy().into_owned();
            if !id.starts_with("backup-") {
                continue;
            }
            match load(&vault, &id) {
                Ok((_, r))
                    if r.root == root
                        && r.profile_id == request.profile_id
                        && r.game == request.game =>
                {
                    rows.push(public(&r))
                }
                Ok(_) => {}
                Err(_) => warnings += 1,
            }
        }
        return Ok(
            json!({"ok":true,"backups":rows,"unreadableBackups":warnings,"includesModFiles":true}),
        );
    }
    if request.action == "snapshot" {
        if request.confirm.as_deref() != Some("BACKUP") {
            return Err("Confirm BACKUP before copying mod files.".into());
        }
        let before = tree(&root, &cancel)?;
        let temp = tempfile::Builder::new()
            .prefix("backup-")
            .tempdir_in(&vault)
            .map_err(err)?;
        let id = temp
            .path()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let files = temp.path().join("files");
        fs::create_dir(&files).map_err(err)?;
        copy_tree(&root, &files, &before, &cancel)?;
        let record = Record {
            schema: 1,
            id,
            root,
            profile_id: request.profile_id,
            game: request.game,
            mod_name: request.mod_name,
            state: "prepared".into(),
            created_at: chrono::Utc::now().timestamp_millis(),
            before,
            after: None,
            displaced: None,
            staged: None,
        };
        atomic_json(&temp.path().join("receipt.json"), &record)?;
        let _ = temp.keep();
        return Ok(json!({"ok":true,"backup":public(&record)}));
    }
    if request.action == "compare" {
        let other = checked_root(
            request
                .other
                .as_ref()
                .ok_or("Missing incoming-content directory.")?,
        )?;
        let installed = tree(&root, &cancel)?;
        let incoming = tree(&other, &cancel)?;
        let mut entries = Vec::new();
        for (name, e) in &incoming {
            entries.push(json!({"path":name,"bytes":e.bytes,"state":match installed.get(name){None=>"add",Some(old) if old==e=>"unchanged",Some(_)=>"replace"}}))
        }
        for name in installed.keys().filter(|n| !incoming.contains_key(*n)) {
            entries.push(json!({"path":name,"state":"notInArchive","removalAuthorized":false}))
        }
        return Ok(
            json!({"ok":true,"identical":incoming==installed,"entries":entries,
                "digest":review_digest(&request, &installed, &incoming)?,
                "requiresInstaller":incoming.keys().any(|n| n.to_lowercase().ends_with("fomod/moduleconfig.xml")),
                "scope":"Unpacked trees only. FOMOD-selected actions require the installer's choices."}),
        );
    }
    if request.action == "duplicates" {
        let files = tree(&root, &cancel)?;
        let mut by_hash: BTreeMap<(u64, String), Vec<String>> = BTreeMap::new();
        for (name, e) in files {
            by_hash.entry((e.bytes, e.sha256)).or_default().push(name)
        }
        let groups:Vec<_>=by_hash.into_iter().filter(|(_,names)|names.len()>1).map(|((bytes,hash),names)|json!({"sha256":hash,"bytes":bytes,"paths":names,"deletionAuthorized":false})).collect();
        return Ok(
            json!({"ok":true,"groups":groups,"scope":"Content equality only. Distinct paths can be required by game assets; nothing is removed."}),
        );
    }
    let (dir, mut record) = load(
        &vault,
        request.id.as_deref().ok_or("Missing backup identity.")?,
    )?;
    if record.root != root
        || record.profile_id != request.profile_id
        || record.game != request.game
        || record.mod_name != request.mod_name
    {
        return Err("Backup belongs to a different target mod, game or profile.".into());
    }
    match request.action.as_str() {
        "apply" => {
            if request.confirm.as_deref() != Some("INSTALL") || record.state != "prepared" {
                return Err("Confirm INSTALL for the exact reviewed content backup.".into());
            }
            let other = checked_root(
                request
                    .other
                    .as_ref()
                    .ok_or("Missing reviewed incoming tree.")?,
            )?;
            let installed = tree(&root, &cancel)?;
            let incoming = tree(&other, &cancel)?;
            if incoming
                .keys()
                .any(|n| n.to_lowercase().ends_with("fomod/moduleconfig.xml"))
            {
                return Err("This archive requires interactive installer choices. Use MO2's installer instead.".into());
            }
            if record.before != installed || tree(&dir.join("files"), &cancel)? != installed {
                return Err("Mod or backup changed after review; installation stopped.".into());
            }
            if request.expected_digest.as_deref()
                != Some(&review_digest(&request, &installed, &incoming)?)
            {
                return Err("Incoming content or target changed after file review.".into());
            }
            if request.selected.len() > MAX_FILES || request.remove.len() > MAX_FILES {
                return Err("Too many selected file actions.".into());
            }
            let mut selected = Tree::new();
            let mut removals = HashSet::new();
            for name in &request.selected {
                let entry = incoming
                    .get(name)
                    .ok_or("Selected incoming file no longer exists.")?;
                if name.eq_ignore_ascii_case("meta.ini") {
                    return Err("An archive cannot replace MO2's mod metadata.".into());
                }
                if selected.insert(name.clone(), entry.clone()).is_some() {
                    return Err("Duplicate file selection.".into());
                }
            }
            for name in &request.remove {
                if !installed.contains_key(name)
                    || selected.contains_key(name)
                    || !removals.insert(name)
                    || name.eq_ignore_ascii_case("meta.ini")
                {
                    return Err("Invalid obsolete-file selection.".into());
                }
            }
            if selected.is_empty() && removals.is_empty() {
                return Err("No file changes were approved.".into());
            }
            let parent = root.parent().ok_or("Missing target parent.")?;
            let stage = tempfile::Builder::new()
                .prefix(".modhunter-apply-")
                .tempdir_in(parent)
                .map_err(err)?;
            copy_tree(&root, stage.path(), &installed, &cancel)?;
            for name in selected.keys().chain(request.remove.iter()) {
                let path = relative_file(stage.path(), name)?;
                if path.exists() {
                    reject_link(&path)?;
                    fs::remove_file(path).map_err(err)?;
                }
            }
            copy_selected(&other, stage.path(), &selected, &cancel)?;
            let expected = tree(stage.path(), &cancel)?;
            if tree(&root, &cancel)? != installed || tree(&other, &cancel)? != incoming || cancel()
            {
                return Err(
                    "Files changed or installation cancelled before the atomic swap.".into(),
                );
            }
            let displaced = parent.join(format!(".modhunter-applied-{}", record.id));
            if displaced.exists() {
                return Err("An earlier transaction needs recovery; no files changed.".into());
            }
            record.state = "applying".into();
            record.after = Some(expected);
            record.displaced = Some(displaced.clone());
            record.staged = Some(stage.path().into());
            atomic_json(&dir.join("receipt.json"), &record)?;
            let staged = stage.keep();
            fs::rename(&root, &displaced).map_err(err)?;
            if let Err(e) = fs::rename(&staged, &root) {
                let _ = fs::rename(&displaced, &root);
                return Err(format!("Install swap failed; both copies preserved: {e}"));
            }
            record.state = "sealed".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(
                json!({"ok":true,"backup":public(&record),"message":"Reviewed files installed and real previous bytes retained. Refresh MO2 before launching the game."}),
            )
        }
        "recover" => {
            if request.confirm.as_deref() != Some("RECOVER")
                || !matches!(record.state.as_str(), "applying" | "restoring")
            {
                return Err("Confirm RECOVER for an interrupted file transaction.".into());
            }
            let parent = root.parent().ok_or("Missing target parent.")?;
            let stage = record
                .staged
                .as_ref()
                .ok_or("Missing staged tree identity.")?;
            let displaced = record
                .displaced
                .as_ref()
                .ok_or("Missing displaced tree identity.")?;
            let prefix = if record.state == "applying" {
                ".modhunter-apply-"
            } else {
                ".modhunter-restore-"
            };
            if stage.parent() != Some(parent)
                || !stage
                    .file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with(prefix))
                || displaced
                    != &parent.join(format!(
                        "{}{}",
                        if record.state == "applying" {
                            ".modhunter-applied-"
                        } else {
                            ".modhunter-displaced-"
                        },
                        record.id
                    ))
            {
                return Err("Recovery paths do not belong to this transaction.".into());
            }
            let after = record
                .after
                .as_ref()
                .ok_or("Missing post-install tree identity.")?;
            let (old, new) = if record.state == "applying" {
                (&record.before, after)
            } else {
                (after, &record.before)
            };
            if tree(&dir.join("files"), &cancel)? != record.before {
                return Err("Backup changed; recovery stopped.".into());
            }
            let current = if root.exists() {
                Some(tree(&root, &cancel)?)
            } else {
                None
            };
            let saved = if displaced.exists() {
                Some(tree(displaced, &cancel)?)
            } else {
                None
            };
            let staged = if stage.exists() {
                Some(tree(stage, &cancel)?)
            } else {
                None
            };
            if current.as_ref() == Some(new) && saved.as_ref() == Some(old) && staged.is_none() {
                // The directory swap finished before receipt persistence.
            } else if current.is_none()
                && saved.as_ref() == Some(old)
                && staged.as_ref() == Some(new)
            {
                fs::rename(stage, &root).map_err(err)?;
            } else if current.as_ref() == Some(old)
                && saved.is_none()
                && staged.as_ref() == Some(new)
            {
                fs::rename(&root, displaced).map_err(err)?;
                if let Err(e) = fs::rename(stage, &root) {
                    let _ = fs::rename(displaced, &root);
                    return Err(err(e));
                }
            } else {
                return Err("Recovery found changed or ambiguous files; every copy is preserved for review.".into());
            }
            record.state = if record.state == "applying" {
                "sealed"
            } else {
                "restored"
            }
            .into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(
                json!({"ok":true,"backup":public(&record),"message":"Interrupted transaction reconciled from verified file identities."}),
            )
        }
        "seal" => {
            if record.state != "prepared" {
                return Err("Only a new backup may receive its post-install identity.".into());
            }
            if tree(&dir.join("files"), &cancel)? != record.before {
                return Err("Backup contents changed before sealing.".into());
            }
            record.after = Some(tree(&root, &cancel)?);
            record.state = "sealed".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(json!({"ok":true,"backup":public(&record)}))
        }
        "previewRestore" | "restore" => {
            if record.state != "sealed" {
                return Err("Backup has no completed post-install identity; preserve it for manual recovery.".into());
            }
            let current = tree(&root, &cancel)?;
            if record.after.as_ref() != Some(&current) {
                return Err("Installed content changed after this update. Restore is blocked to preserve later edits.".into());
            }
            if tree(&dir.join("files"), &cancel)? != record.before {
                return Err("Backup is missing or changed; restore is blocked.".into());
            }
            if request.action == "previewRestore" {
                return Ok(
                    json!({"ok":true,"backup":public(&record),"canRestore":true,"replacesFiles":true}),
                );
            }
            if request.confirm.as_deref() != Some("RESTORE") {
                return Err("Confirm RESTORE after reviewing the exact target.".into());
            }
            let parent = root.parent().ok_or("Missing target parent.")?;
            let stage = tempfile::Builder::new()
                .prefix(".modhunter-restore-")
                .tempdir_in(parent)
                .map_err(err)?;
            copy_tree(&dir.join("files"), stage.path(), &record.before, &cancel)?;
            if tree(&root, &cancel)? != current {
                return Err("Target changed during restore preparation.".into());
            }
            let displaced = parent.join(format!(".modhunter-displaced-{}", record.id));
            if displaced.exists() {
                return Err(
                    "An earlier restore is pending recovery; original files retained.".into(),
                );
            }
            record.state = "restoring".into();
            record.displaced = Some(displaced.clone());
            record.staged = Some(stage.path().into());
            atomic_json(&dir.join("receipt.json"), &record)?;
            // No cancellation after the swap begins. Both trees remain available
            // until the durable commit; errors never delete either copy.
            let staged = stage.keep();
            fs::rename(&root, &displaced).map_err(err)?;
            if let Err(e) = fs::rename(&staged, &root) {
                let _ = fs::rename(&displaced, &root);
                return Err(format!("Restore swap failed; preserved both trees: {e}"));
            }
            record.state = "restored".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(
                json!({"ok":true,"backup":public(&record),"message":"Previous mod content restored. The displaced post-update tree is retained for recovery; rescan MO2."}),
            )
        }
        "deleteBackup" => {
            if request.confirm.as_deref() != Some("DELETE BACKUP")
                || !matches!(record.state.as_str(), "sealed" | "restored")
            {
                return Err("Confirm DELETE BACKUP for a completed record. Incomplete recovery records are retained.".into());
            }
            if tree(&dir.join("files"), &cancel)? != record.before {
                return Err("Backup changed; not removing unexpected files.".into());
            }
            if fs::read_dir(&dir).map_err(err)?.count() != 2 {
                return Err("Unexpected backup contents; preserve for review.".into());
            }
            fs::remove_dir_all(&dir).map_err(err)?;
            Ok(json!({"ok":true,"deleted":record.id,"undoAvailable":false}))
        }
        _ => Err("Unknown content-vault operation.".into()),
    }
}

// Advisory locks survive process crashes without stale lockfile ownership.
struct VaultLock(fs::File);
impl VaultLock {
    fn acquire(path: &Path) -> Result<Self> {
        if path.exists() {
            reject_link(path)?
        }
        let f = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)
            .map_err(err)?;
        f.try_lock()
            .map_err(|_| "Another backup or restore is running.".to_string())?;
        Ok(Self(f))
    }
}
impl Drop for VaultLock {
    fn drop(&mut self) {
        let _ = self.0.unlock();
    }
}
#[cfg(test)]
mod tests;
