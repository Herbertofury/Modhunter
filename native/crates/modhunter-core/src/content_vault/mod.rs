//! Actual-file recovery snapshots. A profile-text backup is not a mod-content backup.
//! Snapshots and inspection are read-only to the target. Restore needs a sealed
//! post-install tree, matching ownership, explicit approval and no later edits.
use crate::acquisition::{atomic_json, file_hash, reject_link};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
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
        reject_link(&walked)?;
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
fn public(r: &Record) -> Value {
    json!({"id":r.id,"state":r.state,"profileId":r.profile_id,"game":r.game,"modName":r.mod_name,"createdAt":r.created_at,"files":r.before.len(),"bytes":r.before.values().map(|e|e.bytes).sum::<u64>(),"includesModFiles":true,"undoAvailable":r.state=="sealed"})
}

pub fn execute(request: Request, cancel: impl Fn() -> bool) -> Result<Value> {
    let root = checked_root(&request.root)?;
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
            json!({"ok":true,"identical":incoming==installed,"entries":entries,"scope":"Unpacked trees only. FOMOD-selected actions require the installer's choices."}),
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
