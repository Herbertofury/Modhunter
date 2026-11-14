//! Actual-file recovery snapshots. A profile-text backup is not a mod-content backup.
//! Snapshots and inspection are read-only to the target. Restore needs a sealed
//! post-install tree, matching ownership, explicit approval and no later edits.
use crate::acquisition::{atomic_json, file_hash, reject_link};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
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
    #[serde(default)]
    pub retention_days: Option<u64>,
    #[serde(default)]
    pub retention_count: Option<usize>,
    #[serde(default)]
    pub retention_bytes: Option<u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Entry {
    pub bytes: u64,
    pub sha256: String,
}
pub(crate) type Tree = BTreeMap<String, Entry>;
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
    #[serde(default)]
    new_target: bool,
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
#[derive(PartialEq, Eq)]
struct Stamp {
    bytes: u64,
    modified: Option<std::time::SystemTime>,
    created: Option<std::time::SystemTime>,
    directory: bool,
    identity: (u64, u64),
}
fn inventory(root: &Path, cancel: &impl Fn() -> bool) -> Result<BTreeMap<String, Stamp>> {
    let mut out = BTreeMap::new();
    let mut names = HashSet::new();
    let mut stack = vec![root.to_path_buf()];
    let mut bytes = 0u64;
    while let Some(dir) = stack.pop() {
        if cancel() {
            return Err("File operation cancelled.".into());
        }
        reject_link(&dir)?;
        for child in fs::read_dir(dir).map_err(err)? {
            let child = child.map_err(err)?;
            let path = child.path();
            reject_link(&path)?;
            let name = path
                .strip_prefix(root)
                .map_err(err)?
                .to_str()
                .ok_or("Non-UTF-8 path needs manual review.")?
                .replace('\\', "/");
            crate::archive_review::safe_name(&name)?;
            if !names.insert(name.to_uppercase()) {
                return Err("Case-colliding content needs manual review.".into());
            }
            let meta = child.metadata().map_err(err)?;
            let directory = meta.is_dir();
            if directory {
                stack.push(path);
            } else if meta.is_file() {
                bytes = bytes.checked_add(meta.len()).ok_or("Tree too large.")?;
            } else {
                return Err("Only regular files and directories are supported.".into());
            }
            #[cfg(unix)]
            let identity = {
                use std::os::unix::fs::MetadataExt;
                (meta.dev(), meta.ino())
            };
            #[cfg(not(unix))]
            let identity = (0, 0);
            out.insert(
                name,
                Stamp {
                    bytes: if directory { 0 } else { meta.len() },
                    modified: meta.modified().ok(),
                    created: meta.created().ok(),
                    directory,
                    identity,
                },
            );
            if out.len() > MAX_FILES || bytes > MAX_TREE_BYTES {
                return Err("Complete mod tree exceeds its configured bound.".into());
            }
        }
    }
    Ok(out)
}
pub(crate) fn tree(root: &Path, cancel: &impl Fn() -> bool) -> Result<Tree> {
    let before = inventory(root, cancel)?;
    let mut out = Tree::new();
    for (name, stamp) in before.iter().filter(|(_, s)| !s.directory) {
        let (bytes, sha256) = file_hash(&root.join(name), cancel)?;
        if bytes != stamp.bytes {
            return Err("File size changed during complete-tree inspection.".into());
        }
        out.insert(name.clone(), Entry { bytes, sha256 });
    }
    if inventory(root, cancel)? != before {
        return Err(
            "Content inventory changed during inspection; no partial result was accepted.".into(),
        );
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
fn copy_one(
    source: &Path,
    destination: &Path,
    expected: &Entry,
    cancel: &impl Fn() -> bool,
) -> Result<()> {
    reject_link(source)?;
    fs::create_dir_all(destination.parent().ok_or("Missing selected parent.")?).map_err(err)?;
    checked_root(destination.parent().unwrap())?;
    let mut input = fs::File::open(source).map_err(err)?;
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(err)?;
    let mut buffer = [0u8; 128 * 1024];
    let mut copied = 0u64;
    loop {
        if cancel() {
            return Err("Operation cancelled.".into());
        }
        let n = input.read(&mut buffer).map_err(err)?;
        if n == 0 {
            break;
        }
        copied += n as u64;
        if copied > expected.bytes {
            return Err("Source grew during copy.".into());
        }
        output.write_all(&buffer[..n]).map_err(err)?;
    }
    output.sync_all().map_err(err)?;
    if file_hash(destination, cancel)? != (expected.bytes, expected.sha256.clone()) {
        return Err("Selected content changed while copying.".into());
    }
    Ok(())
}
fn copy_selected(
    source: &Path,
    dest: &Path,
    selected: &Tree,
    cancel: &impl Fn() -> bool,
) -> Result<()> {
    for (name, expected) in selected {
        copy_one(
            &relative_file(source, name)?,
            &relative_file(dest, name)?,
            expected,
            cancel,
        )?;
    }
    Ok(())
}
// Remove only a redundant, fully verified transaction copy. Restored newer
// bytes are moved into the owned receipt directory and remain available until
// explicit backup retention removes that completed record.
fn tidy_record(dir: &Path, root: &Path, record: &mut Record) -> Result<()> {
    let Some(displaced) = record.displaced.clone() else {
        return Ok(());
    };
    if record.new_target || !matches!(record.state.as_str(), "sealed" | "restored") {
        return Err("Incomplete transactions retain every copy.".into());
    }
    let after = record
        .after
        .as_ref()
        .ok_or("Missing completed content identity.")?;
    let parent = checked_root(root.parent().ok_or("Missing target parent.")?)?;
    let expected_path = parent.join(format!(
        "{}{}",
        if record.state == "sealed" {
            ".modhunter-applied-"
        } else {
            ".modhunter-displaced-"
        },
        record.id
    ));
    let displaced = checked_root(displaced.parent().ok_or("Missing displaced parent.")?)?
        .join(displaced.file_name().ok_or("Missing displaced name.")?);
    let retained = dir.join("post-update-files");
    if displaced == retained && record.state == "restored" {
        if tree(&retained, &|| false)? != *after {
            return Err("Retained post-update content changed; preserve it.".into());
        }
        return Ok(());
    }
    if displaced != expected_path {
        return Err("Displaced copy is not owned by this transaction.".into());
    }
    if tree(&dir.join("files"), &|| false)? != record.before {
        return Err("Backup changed; redundant-copy cleanup stopped.".into());
    }
    let expected_current = if record.state == "sealed" {
        after
    } else {
        &record.before
    };
    if tree(root, &|| false)? != *expected_current {
        return Err("Current mod changed; displaced copy retained.".into());
    }
    if displaced.exists() {
        let expected = if record.state == "sealed" {
            &record.before
        } else {
            after
        };
        if tree(&displaced, &|| false)? != *expected {
            return Err("Displaced content changed; every copy was retained.".into());
        }
        if record.state == "restored" {
            if retained.exists() {
                if tree(&retained, &|| false)? != *after {
                    return Err("An unexpected post-update copy exists; preserve both.".into());
                }
            } else if fs::rename(&displaced, &retained).is_err() {
                let temp = tempfile::Builder::new()
                    .prefix("post-update-")
                    .tempdir_in(dir)
                    .map_err(err)?;
                copy_tree(&displaced, temp.path(), after, &|| false)?;
                fs::rename(temp.path(), &retained).map_err(err)?;
            }
            if tree(&retained, &|| false)? != *after {
                return Err("Post-update retention verification failed.".into());
            }
        }
        if displaced.exists() {
            if tree(&displaced, &|| false)? != *expected {
                return Err("Displaced content changed before cleanup.".into());
            }
            fs::remove_dir_all(&displaced).map_err(err)?;
        }
    } else if record.state == "restored"
        && (!retained.exists() || tree(&retained, &|| false)? != *after)
    {
        return Err("Post-update recovery copy is missing.".into());
    }
    record.displaced = if record.state == "restored" {
        Some(retained)
    } else {
        None
    };
    record.staged = None;
    atomic_json(&dir.join("receipt.json"), record)
}
fn record_material(dir: &Path, record: &Record, cancel: &impl Fn() -> bool) -> Result<u64> {
    if tree(&dir.join("files"), cancel)? != record.before {
        return Err("Backup content changed; preserve the record.".into());
    }
    let mut expected = BTreeSet::from(["files".to_owned(), "receipt.json".to_owned()]);
    let mut bytes = record.before.values().map(|e| e.bytes).sum::<u64>();
    if let Some(displaced) = &record.displaced {
        let retained = dir.join("post-update-files");
        if *displaced != retained || record.state != "restored" {
            return Err("This backup still has transaction copies requiring cleanup.".into());
        }
        let after = record
            .after
            .as_ref()
            .ok_or("Missing retained content identity.")?;
        if tree(&retained, cancel)? != *after {
            return Err("Retained post-update content changed; preserve it.".into());
        }
        expected.insert("post-update-files".into());
        bytes = bytes.saturating_add(after.values().map(|e| e.bytes).sum::<u64>());
    }
    let mut names = BTreeSet::new();
    for child in fs::read_dir(dir).map_err(err)? {
        let child = child.map_err(err)?;
        reject_link(&child.path())?;
        names.insert(child.file_name().to_string_lossy().into_owned());
    }
    if names != expected {
        return Err(
            "Backup contains unexpected material; no retention deletion authorized.".into(),
        );
    }
    Ok(bytes)
}
fn public(r: &Record) -> Value {
    json!({"id":r.id,"state":r.state,"profileId":r.profile_id,"game":r.game,"modName":r.mod_name,"createdAt":r.created_at,"files":r.before.len(),"bytes":r.before.values().map(|e|e.bytes).sum::<u64>(),"includesModFiles":true,"undoAvailable":matches!(r.state.as_str(),"sealed"|"sealedNew"),"newTarget":r.new_target,"canRemoveNewTarget":r.new_target && r.state=="sealedNew","canDeleteBackup":matches!(r.state.as_str(),"sealed"|"restored"|"sealedNew"|"restoredNew")})
}

pub fn execute(request: Request, cancel: impl Fn() -> bool) -> Result<Value> {
    let root = if matches!(
        request.action.as_str(),
        "recover"
            | "prepareNew"
            | "verifyPrepared"
            | "restoreNew"
            | "recoverNew"
            | "list"
            | "storage"
            | "deleteBackup"
            | "deleteNewBackup"
    ) && !request.root.exists()
    {
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
    if matches!(
        request.action.as_str(),
        "retentionPreview" | "retentionApply"
    ) {
        return retention(&request, &root, &vault, &cancel);
    }
    if matches!(request.action.as_str(), "list" | "listProfile" | "storage") {
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
                    if (r.root == root
                        || (request.action == "listProfile"
                            && r.root.parent() == Some(root.as_path())))
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
    if request.action == "prepareNew" {
        if request.confirm.as_deref() != Some("INSTALL") || root.exists() {
            return Err("Confirm INSTALL for an absent, explicit new mod target.".into());
        }
        let temp = tempfile::Builder::new()
            .prefix("backup-")
            .tempdir_in(&vault)
            .map_err(err)?;
        fs::create_dir(temp.path().join("files")).map_err(err)?;
        let record = Record {
            schema: 1,
            id: temp
                .path()
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            root,
            profile_id: request.profile_id,
            game: request.game,
            mod_name: request.mod_name,
            state: "preparedNew".into(),
            created_at: chrono::Utc::now().timestamp_millis(),
            before: Tree::new(),
            after: None,
            displaced: None,
            staged: None,
            new_target: true,
        };
        atomic_json(&temp.path().join("receipt.json"), &record)?;
        let _ = temp.keep();
        return Ok(json!({"ok":true,"backup":public(&record),"newTarget":true}));
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
            new_target: false,
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
        let installed_index: BTreeMap<_, _> = installed
            .iter()
            .filter(|(n, _)| !n.eq_ignore_ascii_case("meta.ini"))
            .map(|(n, e)| (n.to_uppercase(), (n, e)))
            .collect();
        let incoming_index: BTreeMap<_, _> = incoming
            .iter()
            .filter(|(n, _)| !n.eq_ignore_ascii_case("meta.ini"))
            .map(|(n, e)| (n.to_uppercase(), (n, e)))
            .collect();
        let absent: Vec<_> = installed_index
            .iter()
            .filter(|(n, _)| !incoming_index.contains_key(*n))
            .map(|(_, v)| *v)
            .collect();
        let mut entries = Vec::new();
        for (name, e) in incoming
            .iter()
            .filter(|(n, _)| !n.eq_ignore_ascii_case("meta.ini"))
        {
            let old = installed_index.get(&name.to_uppercase());
            let state = match old {
                None => "add",
                Some((_, old)) if *old == e => "unchanged",
                Some(_) => "replace",
            };
            let lower = name.to_ascii_lowercase();
            let optional = lower.starts_with("docs/")
                || lower.starts_with("documentation/")
                || ["readme", "license", "changelog"]
                    .iter()
                    .any(|p| lower.starts_with(p) && !lower.contains('/'));
            let hints: Vec<_> = if state == "add" {
                absent
                    .iter()
                    .filter(|(_, old)| *old == e)
                    .map(|(n, _)| *n)
                    .collect()
            } else {
                Vec::new()
            };
            entries.push(json!({"path":name,"installedPath":old.map(|(n,_)|*n),"bytes":e.bytes,"state":state,"selectable":true,"selectedDefault":!optional,"optional":optional,"removalAuthorized":false,"renameHints":hints}));
        }
        for (name, e) in absent {
            entries.push(json!({"path":name,"installedPath":name,"bytes":e.bytes,"state":"notInArchive","selectable":false,"selectedDefault":false,"optional":false,"removalAuthorized":false,"renameHints":[]}));
        }
        let identical = installed_index.len() == incoming_index.len()
            && incoming_index
                .iter()
                .all(|(name, (_, e))| installed_index.get(name).is_some_and(|(_, old)| old == e));
        return Ok(
            json!({"ok":true,"identical":identical,"entries":entries,"digest":review_digest(&request,&installed,&incoming)?,"requiresInstaller":incoming.keys().any(|n|n.to_uppercase().ends_with("FOMOD/MODULECONFIG.XML")),"scope":"Complete unpacked trees. Missing paths and equal-content rename hints do not authorize deletion; FOMOD-selected paths require native installer choices."}),
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
        "verifyPrepared" => {
            if record.new_target && record.state == "preparedNew" {
                if root.exists() {
                    return Err("New target appeared before native installation.".into());
                }
                return Ok(json!({"ok":true,"verified":true,"backup":public(&record)}));
            }
            if record.state != "prepared"
                || record.new_target
                || tree(&root, &cancel)? != record.before
                || tree(&dir.join("files"), &cancel)? != record.before
            {
                return Err("Prepared backup or target changed before native installation.".into());
            }
            if let Some(other) = request.other.as_ref() {
                let other = checked_root(other)?;
                let incoming = tree(&other, &cancel)?;
                if request.expected_digest.as_deref()
                    != Some(&review_digest(&request, &record.before, &incoming)?)
                {
                    return Err(
                        "Prepared incoming content no longer matches the approved comparison."
                            .into(),
                    );
                }
            }
            Ok(json!({"ok":true,"verified":true,"backup":public(&record)}))
        }
        "sealNew" => {
            if !record.new_target || record.state != "preparedNew" {
                return Err("Only this pending new target may be sealed.".into());
            }
            record.after = Some(tree(&root, &cancel)?);
            record.state = "sealedNew".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(json!({"ok":true,"backup":public(&record)}))
        }
        "previewRestoreNew" | "restoreNew" | "recoverNew" => {
            if !record.new_target || !matches!(record.state.as_str(), "sealedNew" | "restoringNew")
            {
                return Err("New target has no completed identity for reversible removal.".into());
            }
            let after = record
                .after
                .as_ref()
                .ok_or("New target has no final file identity.")?;
            let profiles = request
                .other
                .as_ref()
                .ok_or("Complete saved-profile ownership is required for new-target removal.")?;
            let profile_proof = crate::duplicate_report::disabled_profile_proof(
                profiles,
                &record.mod_name,
                &cancel,
            )?;
            let saved = dir.join("new-target-files");
            if request.action == "recoverNew" {
                if request.confirm.as_deref() != Some("RECOVER")
                    || record.state != "restoringNew"
                    || root.exists()
                    || !saved.exists()
                    || tree(&saved, &cancel)? != *after
                {
                    return Err("New-target recovery found changed or ambiguous files; all copies preserved.".into());
                }
            } else {
                if tree(&root, &cancel)? != *after || saved.exists() {
                    return Err("New mod changed or recovery copy exists; removal blocked.".into());
                }
                if request.action == "previewRestoreNew" {
                    return Ok(
                        json!({"ok":true,"canRestore":true,"backup":public(&record),"movesNewTargetToVault":true}),
                    );
                }
                if request.confirm.as_deref() != Some("RESTORE") {
                    return Err(
                        "Confirm RESTORE after checking every profile using this new target."
                            .into(),
                    );
                }
                if crate::duplicate_report::disabled_profile_proof(
                    profiles,
                    &record.mod_name,
                    &cancel,
                )? != profile_proof
                    || tree(&root, &cancel)? != *after
                {
                    return Err(
                        "Profile ownership or new-target bytes changed before removal.".into(),
                    );
                }
                record.state = "restoringNew".into();
                atomic_json(&dir.join("receipt.json"), &record)?;
                if let Err(error) = fs::rename(&root, &saved) {
                    record.state = "sealedNew".into();
                    atomic_json(&dir.join("receipt.json"), &record)?;
                    return Err(format!(
                        "New target was not moved; original files remain intact. A same-volume recovery vault is required: {error}"
                    ));
                }
            }
            record.state = "restoredNew".into();
            record.displaced = Some(saved);
            atomic_json(&dir.join("receipt.json"), &record)?;
            Ok(
                json!({"ok":true,"backup":public(&record),"newTargetRemoved":true,"retainedCreatedFiles":true,"message":"The unchanged new target was moved into its owned recovery record; refresh MO2."}),
            )
        }
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
                .any(|n| n.to_uppercase().ends_with("FOMOD/MODULECONFIG.XML"))
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
                    || selected
                        .keys()
                        .any(|selected| selected.to_uppercase() == name.to_uppercase())
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
            let selected_identities: HashSet<_> =
                selected.keys().map(|n| n.to_uppercase()).collect();
            let retained: Tree = installed
                .iter()
                .filter(|(n, _)| {
                    !selected_identities.contains(&n.to_uppercase()) && !removals.contains(n)
                })
                .map(|(n, e)| (n.clone(), e.clone()))
                .collect();
            // A retained sibling fixes its parent's spelling. Incoming paths
            // sharing that parent use the same directory, while exact reviewed
            // file names and their bytes remain bound by the approval digest.
            let mut parent_names = BTreeMap::<String, String>::new();
            for name in retained.keys() {
                let mut part = String::new();
                for component in name.split('/').take(name.split('/').count() - 1) {
                    if !part.is_empty() {
                        part.push('/');
                    }
                    part.push_str(component);
                    parent_names.insert(part.to_uppercase(), part.clone());
                }
            }
            let mut selected_destinations = Tree::new();
            for (name, entry) in &selected {
                let mut spelling = Vec::new();
                let mut raw = String::new();
                let count = name.split('/').count();
                for (index, component) in name.split('/').enumerate() {
                    if !raw.is_empty() {
                        raw.push('/');
                    }
                    raw.push_str(component);
                    if index + 1 < count {
                        let chosen = parent_names
                            .entry(raw.to_uppercase())
                            .or_insert_with(|| {
                                let mut path = spelling.join("/");
                                if !path.is_empty() {
                                    path.push('/');
                                }
                                path.push_str(component);
                                path
                            })
                            .clone();
                        spelling = chosen.split('/').map(str::to_owned).collect();
                    } else {
                        spelling.push(component.to_owned());
                    }
                }
                selected_destinations.insert(spelling.join("/"), entry.clone());
            }
            copy_selected(&root, stage.path(), &retained, &cancel)?;
            let destinations: BTreeMap<_, _> = selected_destinations
                .keys()
                .map(|n| (n.to_uppercase(), n))
                .collect();
            for (source, entry) in &selected {
                let destination = destinations
                    .get(&source.to_uppercase())
                    .ok_or("Missing selected destination.")?;
                copy_one(
                    &other.join(source),
                    &stage.path().join(destination),
                    entry,
                    &cancel,
                )?;
            }
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
            let cleanup_warning = tidy_record(&dir, &root, &mut record).err();
            Ok(
                json!({"ok":true,"backup":public(&record),"cleanupWarning":cleanup_warning,"message":"Reviewed files installed and real previous bytes retained. Refresh MO2 before launching the game."}),
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
            // Receipts can contain ordinary Windows paths while checked_root
            // returns extended canonical paths. Normalize the existing parent,
            // not a staged/displaced leaf that may already have been renamed.
            // Parent traversal and reparse points still pass checked_root.
            let stage = checked_root(stage.parent().ok_or("Missing staged parent.")?)?
                .join(stage.file_name().ok_or("Missing staged name.")?);
            let displaced = checked_root(displaced.parent().ok_or("Missing displaced parent.")?)?
                .join(displaced.file_name().ok_or("Missing displaced name.")?);
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
                    != parent.join(format!(
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
                Some(tree(&displaced, &cancel)?)
            } else {
                None
            };
            let staged = if stage.exists() {
                Some(tree(&stage, &cancel)?)
            } else {
                None
            };
            if current.as_ref() == Some(new) && saved.as_ref() == Some(old) && staged.is_none() {
                // The directory swap finished before receipt persistence.
            } else if current.is_none()
                && saved.as_ref() == Some(old)
                && staged.as_ref() == Some(new)
            {
                fs::rename(&stage, &root).map_err(err)?;
            } else if current.as_ref() == Some(old)
                && saved.is_none()
                && staged.as_ref() == Some(new)
            {
                fs::rename(&root, &displaced).map_err(err)?;
                if let Err(e) = fs::rename(&stage, &root) {
                    let _ = fs::rename(&displaced, &root);
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
            let cleanup_warning = tidy_record(&dir, &root, &mut record).err();
            Ok(
                json!({"ok":true,"backup":public(&record),"cleanupWarning":cleanup_warning,"message":"Interrupted transaction reconciled from verified file identities."}),
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
            let cleanup_warning = tidy_record(&dir, &root, &mut record).err();
            Ok(
                json!({"ok":true,"backup":public(&record),"cleanupWarning":cleanup_warning,"message":"Previous mod content restored. Post-update bytes remain in the owned recovery record; rescan MO2."}),
            )
        }
        "cleanupDisplaced" => {
            if request.confirm.as_deref() != Some("CLEANUP") {
                return Err(
                    "Confirm CLEANUP for this completed transaction's verified redundant copies."
                        .into(),
                );
            }
            tidy_record(&dir, &root, &mut record)?;
            Ok(json!({"ok":true,"backup":public(&record),"originalAndRecoveryBytesPreserved":true}))
        }
        "deleteBackup" | "deleteNewBackup" => {
            if request.confirm.as_deref() != Some("DELETE BACKUP")
                || !matches!(
                    record.state.as_str(),
                    "sealed" | "restored" | "sealedNew" | "restoredNew"
                )
            {
                return Err("Confirm DELETE BACKUP for a completed record. Incomplete recovery records are retained.".into());
            }
            if request.action == "deleteNewBackup" && !record.new_target {
                return Err("This is not a new-target recovery record.".into());
            }
            if record.new_target {
                let after = record
                    .after
                    .as_ref()
                    .ok_or("Missing final new-target identity.")?;
                if !record.before.is_empty() || !tree(&dir.join("files"), &cancel)?.is_empty() {
                    return Err("Unexpected new-target backup material was preserved.".into());
                }
                let expected = if record.state == "sealedNew" {
                    if tree(&root, &cancel)? != *after {
                        return Err(
                            "New target changed since installation; preserve its recovery record."
                                .into(),
                        );
                    }
                    BTreeSet::from(["files".to_owned(), "receipt.json".to_owned()])
                } else if record.state == "restoredNew" {
                    let saved = dir.join("new-target-files");
                    if record.displaced.as_ref() != Some(&saved) || tree(&saved, &cancel)? != *after
                    {
                        return Err(
                            "Retained new-target content changed; no deletion was authorized."
                                .into(),
                        );
                    }
                    BTreeSet::from([
                        "files".to_owned(),
                        "receipt.json".to_owned(),
                        "new-target-files".to_owned(),
                    ])
                } else {
                    return Err("New-target transaction is incomplete; keep every copy.".into());
                };
                let actual: BTreeSet<_> = fs::read_dir(&dir)
                    .map_err(err)?
                    .map(|r| {
                        r.map(|e| e.file_name().to_string_lossy().into_owned())
                            .map_err(err)
                    })
                    .collect::<Result<_>>()?;
                if actual != expected {
                    return Err("Unexpected owned-record contents were preserved.".into());
                }
            } else {
                record_material(&dir, &record, &cancel)?;
            }
            fs::remove_dir_all(&dir).map_err(err)?;
            Ok(
                json!({"ok":true,"deleted":record.id,"undoAvailable":false,"retainedBackupBytesDeleted":true}),
            )
        }
        _ => Err("Unknown content-vault operation.".into()),
    }
}

fn retention(
    request: &Request,
    root: &Path,
    vault: &Path,
    cancel: &impl Fn() -> bool,
) -> Result<Value> {
    if request.retention_count == Some(0)
        || request.retention_days == Some(0)
        || request.retention_bytes == Some(0)
    {
        return Err("Retention limits must be positive; omit a limit to disable it.".into());
    }
    let mut records = Vec::new();
    for entry in fs::read_dir(vault).map_err(err)? {
        if cancel() {
            return Err("Retention review cancelled.".into());
        }
        let entry = entry.map_err(err)?;
        let id = entry.file_name().to_string_lossy().into_owned();
        if !id.starts_with("backup-") {
            continue;
        }
        let (dir, record) = load(vault, &id)?;
        if record.root == root
            && record.profile_id == request.profile_id
            && record.game == request.game
            && record.mod_name == request.mod_name
            && !record.new_target
            && matches!(record.state.as_str(), "sealed" | "restored")
        {
            record_material(&dir, &record, cancel)?;
            records.push(record);
        }
    }
    records.sort_by(|a, b| {
        b.created_at
            .cmp(&a.created_at)
            .then_with(|| a.id.cmp(&b.id))
    });
    let now = chrono::Utc::now().timestamp_millis();
    let mut kept_bytes = 0u64;
    let mut selected = Vec::new();
    let mut kept = Vec::new();
    for (i, record) in records.iter().enumerate() {
        let bytes = record_material(&vault.join(&record.id), record, cancel)?;
        let expired = request.retention_days.is_some_and(|days| {
            now.saturating_sub(record.created_at) as u64 > days.saturating_mul(86_400_000)
        });
        let over_count = request.retention_count.is_some_and(|count| i >= count);
        let over_bytes = request
            .retention_bytes
            .is_some_and(|max| kept_bytes.saturating_add(bytes) > max);
        if i > 0 && (expired || over_count || over_bytes) {
            selected.push(public(record));
        } else {
            kept_bytes = kept_bytes.saturating_add(bytes);
            kept.push(public(record));
        }
    }
    let digest=format!("{:x}",Sha256::digest(serde_json::to_vec(&json!({"root":root,"profileId":request.profile_id,"game":request.game,"records":records,"eligible":selected.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"days":request.retention_days,"count":request.retention_count,"bytes":request.retention_bytes})).map_err(err)?));
    if request.action == "retentionPreview" {
        return Ok(
            json!({"ok":true,"eligible":selected,"retained":kept,"digest":digest,"newestBackupRetained":true,"incompleteRecordsRetained":true}),
        );
    }
    if request.confirm.as_deref() != Some("DELETE BACKUPS")
        || request.expected_digest.as_deref() != Some(&digest)
    {
        return Err("Confirm DELETE BACKUPS for the unchanged exact retention preview.".into());
    }
    let ids: Vec<_> = selected
        .iter()
        .map(|r| r["id"].as_str().unwrap().to_owned())
        .collect();
    if !request.selected.is_empty() && request.selected != ids {
        return Err("Retention selection differs from the complete reviewed plan.".into());
    }
    let mut deleted = Vec::new();
    for (index, id) in ids.iter().enumerate() {
        if cancel() {
            return Ok(
                json!({"ok":false,"state":"cancelled","deleted":deleted,"remaining":&ids[index..],"partial":!deleted.is_empty()}),
            );
        }
        let removal = (|| -> Result<()> {
            let (dir, mut record) = load(vault, id)?;
            record_material(&dir, &record, cancel)?;
            record.state = "deleting".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            fs::remove_dir_all(dir).map_err(err)
        })();
        if let Err(error) = removal {
            return Ok(
                json!({"ok":false,"state":"failed","deleted":deleted,"remaining":&ids[index..],"partial":!deleted.is_empty(),"error":error,"partialRemoval":id}),
            );
        }
        deleted.push(id.clone());
    }
    Ok(
        json!({"ok":true,"state":"completed","deleted":deleted,"retained":kept,"incompleteRecordsRetained":true}),
    )
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
