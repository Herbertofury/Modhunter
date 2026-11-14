//! Complete content equality reports and reversible, profile-checked quarantine.
//! A matching hash is never permission to remove an individual game asset.
use crate::acquisition::{atomic_json, file_hash, reject_link};
use crate::content_vault::{Tree, checked_root, tree};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, String>;
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
const OWNER: &[u8] = b"{\"schema\":1,\"owner\":\"Modhunter-duplicate-vault\"}";
const MAX_MODS: usize = 20_000;
const MAX_FILES: usize = 1_000_000;
const MAX_PROFILES: usize = 2_000;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProfileOwner {
    pub id: String,
    pub enabled: bool,
    #[serde(default)]
    pub priority: Option<i64>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ModOwner {
    pub name: String,
    pub path: PathBuf,
    pub enabled: bool,
    #[serde(default)]
    pub profiles: Vec<ProfileOwner>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub action: String,
    pub mods_root: PathBuf,
    pub profiles_root: PathBuf,
    pub vault: PathBuf,
    pub profile_id: String,
    pub game: String,
    pub mods: Vec<ModOwner>,
    pub context_digest: String,
    #[serde(default)]
    pub expected_digest: Option<String>,
    #[serde(default)]
    pub mod_name: Option<String>,
    #[serde(default)]
    pub retained_mod_name: Option<String>,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub confirm: Option<String>,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct SavedMod {
    name: String,
    enabled: bool,
    priority: i64,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Profile {
    sha256: String,
    bytes: u64,
    mods: BTreeMap<String, SavedMod>,
}
type Profiles = BTreeMap<String, Profile>;
fn digest(value: &impl Serialize) -> Result<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).map_err(err)?)
    ))
}
fn valid_leaf(name: &str) -> Result<()> {
    crate::archive_review::safe_name(name)?;
    if name.contains('/') {
        return Err("Expected one exact directory name.".into());
    }
    Ok(())
}
fn directories(root: &Path, max: usize) -> Result<BTreeMap<String, PathBuf>> {
    let mut out = BTreeMap::new();
    let mut aliases = BTreeSet::new();
    for entry in fs::read_dir(root).map_err(err)? {
        let entry = entry.map_err(err)?;
        let path = entry.path();
        reject_link(&path)?;
        if !entry.file_type().map_err(err)?.is_dir() {
            return Err("Managed root contains an unexpected non-directory entry; complete ownership cannot be established.".into());
        }
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "Non-UTF-8 directory requires manual review.")?;
        valid_leaf(&name)?;
        if !aliases.insert(name.to_uppercase()) {
            return Err("Managed directories have case-colliding identities.".into());
        }
        out.insert(name, checked_root(&path)?);
        if out.len() > max {
            return Err("Complete directory inventory exceeds its bound.".into());
        }
    }
    Ok(out)
}
fn profiles(root: &Path, cancel: &impl Fn() -> bool) -> Result<Profiles> {
    let dirs = directories(root, MAX_PROFILES)?;
    if dirs.is_empty() {
        return Err("No saved profiles were available to verify ownership.".into());
    }
    let mut out = Profiles::new();
    let mut total = 0u64;
    for (name, dir) in &dirs {
        if cancel() {
            return Err("Complete profile inspection cancelled.".into());
        }
        let path = dir.join("modlist.txt");
        reject_link(&path)?;
        let mut data = Vec::new();
        fs::File::open(&path)
            .map_err(err)?
            .take(16 * 1024 * 1024 + 1)
            .read_to_end(&mut data)
            .map_err(err)?;
        total += data.len() as u64;
        if data.len() > 16 * 1024 * 1024 || total > 64 * 1024 * 1024 {
            return Err("Complete profile ownership exceeds its bound.".into());
        }
        let text = std::str::from_utf8(&data)
            .map_err(|_| "Profile modlist must be valid UTF-8.")?
            .trim_start_matches('\u{feff}');
        let mut mods = BTreeMap::new();
        for (priority, line) in text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .enumerate()
        {
            if !matches!(line.as_bytes().first(), Some(b'+' | b'-' | b'*')) {
                return Err("Profile contains an ambiguous ownership prefix.".into());
            }
            let (mark, name) = line.split_at(1);
            if !matches!(mark, "+" | "-" | "*") || name.is_empty() {
                return Err("Profile contains an ambiguous mod ownership line.".into());
            }
            valid_leaf(name)?;
            let value = SavedMod {
                name: name.into(),
                enabled: mark == "+",
                priority: priority as i64,
            };
            if mods.insert(name.to_uppercase(), value).is_some() {
                return Err("Profile contains repeated or case-colliding mod names.".into());
            }
        }
        let sha256 = format!("{:x}", Sha256::digest(&data));
        if file_hash(&path, cancel)? != (data.len() as u64, sha256.clone()) {
            return Err("Profile changed while ownership was inspected.".into());
        }
        out.insert(
            name.clone(),
            Profile {
                sha256,
                bytes: data.len() as u64,
                mods,
            },
        );
    }
    if directories(root, MAX_PROFILES)? != dirs {
        return Err("Saved profile inventory changed during inspection.".into());
    }
    Ok(out)
}
pub(crate) fn disabled_profile_proof(
    root: &Path,
    name: &str,
    cancel: &impl Fn() -> bool,
) -> Result<String> {
    valid_leaf(name)?;
    let all = profiles(&checked_root(root)?, cancel)?;
    if all
        .values()
        .any(|p| p.mods.get(&name.to_uppercase()).is_some_and(|m| m.enabled))
    {
        return Err(
            "This mod is enabled in a saved profile; reversible removal is blocked.".into(),
        );
    }
    digest(&all)
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Physical {
    identity: String,
    allocated_bytes: u64,
    hard_links: u64,
}
#[cfg(unix)]
fn physical(path: &Path) -> Result<Physical> {
    use std::os::unix::fs::MetadataExt;
    let m = fs::metadata(path).map_err(err)?;
    Ok(Physical {
        identity: format!("{}:{}", m.dev(), m.ino()),
        allocated_bytes: m.blocks().saturating_mul(512),
        hard_links: m.nlink(),
    })
}
#[cfg(windows)]
fn physical(path: &Path) -> Result<Physical> {
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    #[derive(Default)]
    struct Time {
        low: u32,
        high: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        created: Time,
        accessed: Time,
        written: Time,
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct Standard {
        allocation: i64,
        end: i64,
        links: u32,
        delete: u8,
        directory: u8,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFileInformationByHandle(h: *mut std::ffi::c_void, info: *mut Info) -> i32;
        fn GetFileInformationByHandleEx(
            h: *mut std::ffi::c_void,
            class: i32,
            info: *mut std::ffi::c_void,
            size: u32,
        ) -> i32;
    }
    let file = fs::File::open(path).map_err(err)?;
    let mut info = Info::default();
    let mut standard = Standard::default();
    // Both fixed-layout structures are initialized, and the borrowed handle stays open.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                1,
                (&mut standard as *mut Standard).cast(),
                std::mem::size_of::<Standard>() as u32,
            )
        } == 0
    {
        return Err(err(std::io::Error::last_os_error()));
    }
    Ok(Physical {
        identity: format!("{}:{}:{}", info.volume, info.index_high, info.index_low),
        allocated_bytes: standard.allocation.max(0) as u64,
        hard_links: info.links as u64,
    })
}
#[cfg(not(any(unix, windows)))]
fn physical(_: &Path) -> Result<Physical> {
    Err("Physical file identity is unavailable on this platform.".into())
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Mod {
    name: String,
    path: PathBuf,
    enabled: bool,
    profiles: Vec<ProfileOwner>,
    files: Tree,
    physical: BTreeMap<String, Physical>,
    content_digest: String,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Snapshot {
    mods_root: PathBuf,
    profiles_root: PathBuf,
    profile_id: String,
    game: String,
    context_digest: String,
    profiles: Profiles,
    mods: BTreeMap<String, Mod>,
}
fn content(files: &Tree) -> Tree {
    files
        .iter()
        .filter(|(n, _)| !n.eq_ignore_ascii_case("meta.ini"))
        .map(|(n, e)| (n.to_uppercase(), e.clone()))
        .collect()
}
fn snapshot(request: &Request, cancel: &impl Fn() -> bool) -> Result<Snapshot> {
    if request.profile_id.is_empty()
        || request.game.is_empty()
        || request.context_digest.is_empty()
        || request.context_digest.len() > 4096
        || request.mods.len() > MAX_MODS
    {
        return Err(
            "An exact native context and complete bounded mod inventory are required.".into(),
        );
    }
    let mods_root = checked_root(&request.mods_root)?;
    let profiles_root = checked_root(&request.profiles_root)?;
    if mods_root.starts_with(&profiles_root) || profiles_root.starts_with(&mods_root) {
        return Err("Mods and profiles must have distinct roots.".into());
    }
    let dirs = directories(&mods_root, MAX_MODS)?;
    let saved = profiles(&profiles_root, cancel)?;
    let mut mods = BTreeMap::new();
    let mut file_count = 0usize;
    let mut total_bytes = 0u64;
    for claim in &request.mods {
        if cancel() {
            return Err("Duplicate inspection cancelled; no partial report accepted.".into());
        }
        valid_leaf(&claim.name)?;
        let path = dirs
            .get(&claim.name)
            .ok_or("Native mod inventory does not match the complete disk directory list.")?;
        if checked_root(&claim.path)? != *path || mods.contains_key(&claim.name) {
            return Err("Duplicate or mismatched native mod ownership.".into());
        }
        let mut claims = BTreeMap::new();
        for owner in &claim.profiles {
            if claims.insert(owner.id.clone(), owner).is_some() {
                return Err("Duplicate saved-profile ownership claim.".into());
            }
        }
        if claims.len() != saved.len() {
            return Err("Native ownership must account for every saved profile.".into());
        }
        let mut owners = Vec::new();
        for (id, profile) in &saved {
            let row = profile.mods.get(&claim.name.to_uppercase());
            let enabled = row.is_some_and(|m| m.enabled);
            let claim = claims
                .get(id)
                .ok_or("Native ownership omitted a saved profile.")?;
            if claim.enabled != enabled
                || claim
                    .priority
                    .is_some_and(|p| row.is_none_or(|r| r.priority != p))
            {
                return Err("Saved profile changed since native ownership was captured.".into());
            }
            owners.push(ProfileOwner {
                id: id.clone(),
                enabled,
                priority: row.map(|r| r.priority),
            });
        }
        let files = tree(path, cancel)?;
        file_count += files.len();
        total_bytes = total_bytes.saturating_add(files.values().map(|e| e.bytes).sum::<u64>());
        if file_count > MAX_FILES || total_bytes > 1024 * 1024 * 1024 * 1024 {
            return Err("Complete duplicate inventory exceeds its configured bound.".into());
        }
        let mut allocation = BTreeMap::new();
        for name in files.keys() {
            allocation.insert(name.clone(), physical(&path.join(name))?);
        }
        let content_digest = digest(&content(&files))?;
        mods.insert(
            claim.name.clone(),
            Mod {
                name: claim.name.clone(),
                path: path.clone(),
                enabled: claim.enabled,
                profiles: owners,
                files,
                physical: allocation,
                content_digest,
            },
        );
    }
    if dirs.len() != mods.len() {
        return Err(
            "Unmanaged directories or missing native mods prevent a complete ownership report."
                .into(),
        );
    }
    // Invalidate the entire report if any earlier tree, physical identity, saved
    // profile or top-level directory changed while another mod was being hashed.
    for m in mods.values() {
        if tree(&m.path, cancel)? != m.files {
            return Err("Mod content changed during scan; no partial success was accepted.".into());
        }
        for (name, expected) in &m.physical {
            if physical(&m.path.join(name))? != *expected {
                return Err("Physical file identity changed during scan.".into());
            }
        }
    }
    if profiles(&profiles_root, cancel)? != saved || directories(&mods_root, MAX_MODS)? != dirs {
        return Err("Ownership changed during scan; no partial success was accepted.".into());
    }
    Ok(Snapshot {
        mods_root,
        profiles_root,
        profile_id: request.profile_id.clone(),
        game: request.game.clone(),
        context_digest: request.context_digest.clone(),
        profiles: saved,
        mods,
    })
}
fn owner(m: &Mod) -> Value {
    json!({"name":m.name,"path":m.path,"enabled":m.enabled,"profiles":m.profiles})
}
fn report(s: &Snapshot) -> Result<Value> {
    type FileOwners<'a> = BTreeMap<(u64, String), Vec<(&'a Mod, &'a String, &'a Physical)>>;
    let mut files = FileOwners::new();
    let mut identical = BTreeMap::<String, Vec<&Mod>>::new();
    let mut logical = 0u64;
    let mut unique_physical = BTreeMap::<String, u64>::new();
    for m in s.mods.values() {
        if !content(&m.files).is_empty() {
            identical
                .entry(m.content_digest.clone())
                .or_default()
                .push(m);
        }
        for (name, e) in m
            .files
            .iter()
            .filter(|(n, _)| !n.eq_ignore_ascii_case("meta.ini"))
        {
            let p = &m.physical[name];
            logical = logical.saturating_add(e.bytes);
            unique_physical.insert(p.identity.clone(), p.allocated_bytes);
            files
                .entry((e.bytes, e.sha256.clone()))
                .or_default()
                .push((m, name, p));
        }
    }
    let mut logical_duplicates = 0u64;
    let mut duplicate_groups = Vec::new();
    for ((bytes, sha256), rows) in files.into_iter().filter(|(_, r)| r.len() > 1) {
        logical_duplicates =
            logical_duplicates.saturating_add(bytes.saturating_mul(rows.len() as u64 - 1));
        let mut physical = BTreeMap::new();
        let mut paths = Vec::new();
        for (m, name, p) in &rows {
            physical.insert(&p.identity, p.allocated_bytes);
            paths.push(json!({"modName":m.name,"path":name,"owner":owner(m),"physicalIdentity":p.identity,"allocatedBytes":p.allocated_bytes,"hardLinks":p.hard_links}));
        }
        duplicate_groups.push(json!({"bytes":bytes,"sha256":sha256,"paths":paths,"logicalDuplicateBytes":bytes.saturating_mul(rows.len() as u64-1),"distinctPhysicalFiles":physical.len(),"physicalAllocatedBytes":physical.values().sum::<u64>(),"alreadyHardlinked":physical.len()<rows.len(),"deletionAuthorized":false}));
    }
    let complete:Vec<_>=identical.into_iter().filter(|(_,m)|m.len()>1).map(|(d,mods)|json!({"contentDigest":d,"mods":mods.iter().map(|m|owner(m)).collect::<Vec<_>>(),"files":content(&mods[0].files).len(),"logicalBytesPerMod":content(&mods[0].files).values().map(|e|e.bytes).sum::<u64>(),"metadataExcluded":["meta.ini"],"deletionAuthorized":false})).collect();
    Ok(
        json!({"ok":true,"complete":true,"digest":digest(s)?,"contextDigest":s.context_digest,"mods":s.mods.values().map(owner).collect::<Vec<_>>(),"fileGroups":duplicate_groups,"groups":complete,"completeContentGroups":complete,"logicalBytes":logical,"logicalDuplicateBytes":logical_duplicates,"physicalAllocatedBytes":unique_physical.values().sum::<u64>(),"distinctPhysicalFiles":unique_physical.len(),"deletionAuthorized":false,"scope":"Complete file hashes and every saved profile are included. Root meta.ini is excluded from content equality. Hard links share physical allocation. Quarantine preserves bytes and does not promise freed disk space."}),
    )
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Record {
    schema: u32,
    id: String,
    state: String,
    mods_root: PathBuf,
    profiles_root: PathBuf,
    profile_id: String,
    game: String,
    mod_name: String,
    retained_mod_name: String,
    files: Tree,
    retained_files: Tree,
    profile_digest: String,
    context_digest: String,
    created_at: i64,
}
fn public(r: &Record) -> Value {
    json!({"id":r.id,"state":r.state,"modName":r.mod_name,"retainedModName":r.retained_mod_name,"profileId":r.profile_id,"game":r.game,"createdAt":r.created_at,"files":r.files.len(),"bytes":r.files.values().map(|e|e.bytes).sum::<u64>(),"canRestore":r.state=="quarantined","canDelete":matches!(r.state.as_str(),"quarantined"|"restored"),"retainedBytes":r.state=="quarantined"})
}
fn vault(request: &Request) -> Result<(PathBuf, fs::File)> {
    let mods = checked_root(&request.mods_root)?;
    let profiles = checked_root(&request.profiles_root)?;
    let parent = checked_root(request.vault.parent().ok_or("Missing quarantine parent.")?)?;
    let path = parent.join(
        request
            .vault
            .file_name()
            .ok_or("Missing quarantine name.")?,
    );
    if path.starts_with(&mods)
        || mods.starts_with(&path)
        || path.starts_with(&profiles)
        || profiles.starts_with(&path)
    {
        return Err("Quarantine must be separate from mods and profiles.".into());
    }
    if !path.exists() {
        fs::create_dir(&path).map_err(err)?;
    }
    let path = checked_root(&path)?;
    let marker = path.join("modhunter-duplicate-vault.json");
    if marker.exists() {
        reject_link(&marker)?;
        if fs::read(&marker).map_err(err)? != OWNER {
            return Err("Quarantine directory has an unknown owner.".into());
        }
    } else {
        if fs::read_dir(&path).map_err(err)?.next().is_some() {
            return Err("Quarantine must start in an empty owned directory.".into());
        }
        let mut f = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&marker)
            .map_err(err)?;
        f.write_all(OWNER).map_err(err)?;
        f.sync_all().map_err(err)?;
    }
    let lockpath = path.join(".lock");
    if lockpath.exists() {
        reject_link(&lockpath)?;
    }
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(lockpath)
        .map_err(err)?;
    lock.try_lock()
        .map_err(|_| "Another duplicate maintenance operation is running.")?;
    Ok((path, lock))
}
fn load(vault: &Path, id: &str) -> Result<(PathBuf, Record)> {
    if !id.starts_with("quarantine-")
        || id.len() > 100
        || !id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        return Err("Invalid quarantine identity.".into());
    }
    let dir = checked_root(&vault.join(id))?;
    let receipt = dir.join("receipt.json");
    reject_link(&receipt)?;
    let mut data = Vec::new();
    fs::File::open(receipt)
        .map_err(err)?
        .take(128 * 1024 * 1024 + 1)
        .read_to_end(&mut data)
        .map_err(err)?;
    if data.len() > 128 * 1024 * 1024 {
        return Err("Quarantine receipt exceeds its bound.".into());
    }
    let r: Record = serde_json::from_slice(&data).map_err(err)?;
    if r.schema != 1
        || r.id != id
        || r.files.len() > MAX_FILES
        || r.retained_files.len() > MAX_FILES
    {
        return Err("Invalid quarantine record.".into());
    }
    valid_leaf(&r.mod_name)?;
    valid_leaf(&r.retained_mod_name)?;
    Ok((dir, r))
}
fn selected<'a>(request: &Request, s: &'a Snapshot) -> Result<(&'a Mod, &'a Mod)> {
    let chosen = s
        .mods
        .get(
            request
                .mod_name
                .as_deref()
                .ok_or("Choose the exact duplicate mod.")?,
        )
        .ok_or("Selected mod no longer exists.")?;
    let retained = s
        .mods
        .get(
            request
                .retained_mod_name
                .as_deref()
                .ok_or("Choose the retained counterpart.")?,
        )
        .ok_or("Retained counterpart no longer exists.")?;
    if chosen.name == retained.name
        || content(&chosen.files).is_empty()
        || content(&chosen.files) != content(&retained.files)
    {
        return Err("Quarantine requires a distinct complete equal-content counterpart.".into());
    }
    if chosen.enabled || chosen.profiles.iter().any(|p| p.enabled) {
        return Err("Selected mod must be disabled in the live context and every saved profile before quarantine.".into());
    }
    Ok((chosen, retained))
}
pub fn execute(request: Request, cancel: impl Fn() -> bool) -> Result<Value> {
    if matches!(
        request.action.as_str(),
        "scan" | "previewQuarantine" | "quarantine"
    ) {
        let s = snapshot(&request, &cancel)?;
        if request.action == "scan" {
            return report(&s);
        }
        let (chosen, retained) = selected(&request, &s)?;
        let token = digest(&s)?;
        if request.action == "previewQuarantine" {
            return Ok(
                json!({"ok":true,"canQuarantine":true,"digest":token,"mod":owner(chosen),"retained":owner(retained),"reversible":true,"reclaimsDiskSpace":false}),
            );
        }
        if request.confirm.as_deref() != Some("QUARANTINE")
            || request.expected_digest.as_deref() != Some(&token)
        {
            return Err("Confirm QUARANTINE for the unchanged complete ownership report.".into());
        }
        let (vault, _lock) = vault(&request)?;
        if digest(&snapshot(&request, &cancel)?)? != token {
            return Err("Ownership or content changed before quarantine.".into());
        }
        let temp = tempfile::Builder::new()
            .prefix("quarantine-")
            .tempdir_in(&vault)
            .map_err(err)?;
        let mut record = Record {
            schema: 1,
            id: temp
                .path()
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            state: "preparing".into(),
            mods_root: s.mods_root.clone(),
            profiles_root: s.profiles_root.clone(),
            profile_id: request.profile_id,
            game: request.game,
            mod_name: chosen.name.clone(),
            retained_mod_name: retained.name.clone(),
            files: chosen.files.clone(),
            retained_files: retained.files.clone(),
            profile_digest: digest(&s.profiles)?,
            context_digest: request.context_digest,
            created_at: chrono::Utc::now().timestamp_millis(),
        };
        atomic_json(&temp.path().join("receipt.json"), &record)?;
        let dir = temp.keep();
        if let Err(error) = fs::rename(&chosen.path, dir.join("files")) {
            record.state = "notMoved".into();
            atomic_json(&dir.join("receipt.json"), &record)?;
            return Err(format!(
                "Quarantine could not move the mod; original bytes remain untouched. The vault must support a same-volume rename: {error}"
            ));
        }
        record.state = "quarantined".into();
        atomic_json(&dir.join("receipt.json"), &record)?;
        return Ok(
            json!({"ok":true,"state":"quarantined","record":public(&record),"quarantine":public(&record),"rescanRequired":true,"filesDeleted":false}),
        );
    }
    let (vault, _lock) = vault(&request)?;
    if request.action == "list" {
        let mut records = Vec::new();
        for child in fs::read_dir(&vault).map_err(err)? {
            let name = child
                .map_err(err)?
                .file_name()
                .to_string_lossy()
                .into_owned();
            if name.starts_with("quarantine-") {
                let (_, r) = load(&vault, &name)?;
                if r.mods_root == checked_root(&request.mods_root)?
                    && r.profiles_root == checked_root(&request.profiles_root)?
                    && r.profile_id == request.profile_id
                    && r.game == request.game
                {
                    records.push(public(&r));
                }
            }
        }
        return Ok(json!({"ok":true,"records":records,"quarantines":records}));
    }
    if !matches!(
        request.action.as_str(),
        "previewRestore" | "restore" | "recover" | "previewDrop" | "drop"
    ) {
        return Err("Unknown duplicate-report operation.".into());
    }
    let (dir, mut r) = load(
        &vault,
        request
            .id
            .as_deref()
            .ok_or("Missing quarantine identity.")?,
    )?;
    let s = snapshot(&request, &cancel)?;
    if r.mods_root != s.mods_root
        || r.profiles_root != s.profiles_root
        || r.profile_id != request.profile_id
        || r.game != request.game
    {
        return Err("Quarantine belongs to another native context.".into());
    }
    if matches!(request.action.as_str(), "previewDrop" | "drop") {
        if !matches!(r.state.as_str(), "quarantined" | "restored") {
            return Err("Incomplete quarantine records retain every copy.".into());
        }
        let expected = if r.state == "quarantined" {
            if s.mods.keys().any(|n| n.eq_ignore_ascii_case(&r.mod_name))
                || tree(&dir.join("files"), &cancel)? != r.files
            {
                return Err(
                    "Quarantined bytes or original ownership changed; deletion blocked.".into(),
                );
            }
            let counterpart = s
                .mods
                .get(&r.retained_mod_name)
                .ok_or("Retained counterpart is missing.")?;
            if counterpart.files != r.retained_files {
                return Err(
                    "Retained counterpart changed; quarantined bytes remain protected.".into(),
                );
            }
            disabled_profile_proof(&s.profiles_root, &r.mod_name, &cancel)?;
            BTreeSet::from(["files".to_owned(), "receipt.json".to_owned()])
        } else {
            BTreeSet::from(["receipt.json".to_owned()])
        };
        let mut actual = BTreeSet::new();
        for child in fs::read_dir(&dir).map_err(err)? {
            let child = child.map_err(err)?;
            reject_link(&child.path())?;
            actual.insert(child.file_name().to_string_lossy().into_owned());
        }
        if actual != expected {
            return Err("Unexpected quarantine material was preserved.".into());
        }
        let token = digest(&json!({"snapshot":s,"record":r}))?;
        if request.action == "previewDrop" {
            return Ok(
                json!({"ok":true,"canDelete":true,"digest":token,"record":public(&r),"undoWillBeLost":true}),
            );
        }
        if request.confirm.as_deref() != Some("DELETE QUARANTINE")
            || request.expected_digest.as_deref() != Some(&token)
        {
            return Err("Confirm DELETE QUARANTINE for the exact current deletion preview.".into());
        }
        if digest(&snapshot(&request, &cancel)?)? != digest(&s)? {
            return Err("Ownership changed before deleting quarantine bytes.".into());
        }
        if r.state == "quarantined" && tree(&dir.join("files"), &cancel)? != r.files {
            return Err("Quarantined content changed before removal.".into());
        }
        r.state = "deleting".into();
        atomic_json(&dir.join("receipt.json"), &r)?;
        fs::remove_dir_all(&dir).map_err(err)?;
        return Ok(
            json!({"ok":true,"deleted":r.id,"undoAvailable":false,"retainedCounterpartPreserved":true}),
        );
    }
    if request.action == "recover" {
        if request.confirm.as_deref() != Some("RECOVER")
            || !matches!(r.state.as_str(), "preparing" | "restoring")
        {
            return Err("Confirm RECOVER for an interrupted quarantine transaction.".into());
        }
        let target = r.mods_root.join(&r.mod_name);
        let files = dir.join("files");
        if r.state == "preparing"
            && !target.exists()
            && files.exists()
            && tree(&files, &cancel)? == r.files
        {
            r.state = "quarantined".into();
        } else if r.state == "restoring"
            && target.exists()
            && !files.exists()
            && tree(&target, &cancel)? == r.files
        {
            r.state = "restored".into();
        } else {
            return Err(
                "Recovery found changed or ambiguous copies; all bytes were preserved.".into(),
            );
        }
        atomic_json(&dir.join("receipt.json"), &r)?;
        return Ok(json!({"ok":true,"record":public(&r),"rescanRequired":true}));
    }
    if r.state != "quarantined"
        || s.mods.keys().any(|n| n.eq_ignore_ascii_case(&r.mod_name))
        || tree(&dir.join("files"), &cancel)? != r.files
    {
        return Err("Restore target already exists or quarantined bytes changed.".into());
    }
    let counterpart = s
        .mods
        .get(&r.retained_mod_name)
        .ok_or("Retained counterpart disappeared; review manually.")?;
    if counterpart.files != r.retained_files {
        return Err(
            "Retained counterpart changed since quarantine; restore needs manual review.".into(),
        );
    }
    let profile_proof = disabled_profile_proof(&s.profiles_root, &r.mod_name, &cancel)?;
    let token = digest(&json!({"snapshot":s,"record":r,"profileProof":profile_proof}))?;
    if request.action == "previewRestore" {
        return Ok(json!({"ok":true,"canRestore":true,"record":public(&r),"digest":token}));
    }
    if request.confirm.as_deref() != Some("RESTORE")
        || request.expected_digest.as_deref() != Some(&token)
    {
        return Err("Confirm RESTORE for the unchanged quarantine preview.".into());
    }
    if disabled_profile_proof(&s.profiles_root, &r.mod_name, &cancel)? != profile_proof
        || tree(&counterpart.path, &cancel)? != r.retained_files
        || tree(&dir.join("files"), &cancel)? != r.files
        || directories(&s.mods_root, MAX_MODS)?.contains_key(&r.mod_name)
    {
        return Err("Ownership changed before restoring quarantined content.".into());
    }
    r.state = "restoring".into();
    atomic_json(&dir.join("receipt.json"), &r)?;
    if let Err(error) = fs::rename(dir.join("files"), r.mods_root.join(&r.mod_name)) {
        r.state = "quarantined".into();
        atomic_json(&dir.join("receipt.json"), &r)?;
        return Err(err(error));
    }
    r.state = "restored".into();
    atomic_json(&dir.join("receipt.json"), &r)?;
    Ok(
        json!({"ok":true,"state":"restored","record":public(&r),"rescanRequired":true,"filesDeleted":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture {
        _temp: tempfile::TempDir,
        mods: PathBuf,
        profiles: PathBuf,
        vault: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let t = tempfile::tempdir().unwrap();
            let mods = t.path().join("mods");
            let profiles = t.path().join("profiles");
            let vault = t.path().join("quarantine");
            fs::create_dir(&mods).unwrap();
            fs::create_dir_all(profiles.join("Default")).unwrap();
            for name in ["A", "B"] {
                fs::create_dir(mods.join(name)).unwrap();
                fs::write(mods.join(name).join("asset.nif"), "shared bytes").unwrap();
                fs::write(mods.join(name).join("meta.ini"), name).unwrap();
            }
            fs::write(profiles.join("Default/modlist.txt"), "-A\n+B\n").unwrap();
            Self {
                _temp: t,
                mods,
                profiles,
                vault,
            }
        }
        fn request(&self, action: &str) -> Request {
            let saved = profiles(&self.profiles, &|| false).unwrap();
            let mut mods = Vec::new();
            for (name, path) in directories(&self.mods, MAX_MODS).unwrap() {
                let owners = saved
                    .iter()
                    .map(|(id, p)| ProfileOwner {
                        id: id.clone(),
                        enabled: p.mods.get(&name.to_uppercase()).is_some_and(|r| r.enabled),
                        priority: p.mods.get(&name.to_uppercase()).map(|r| r.priority),
                    })
                    .collect();
                mods.push(ModOwner {
                    name: name.clone(),
                    path,
                    enabled: name == "B",
                    profiles: owners,
                });
            }
            Request {
                action: action.into(),
                mods_root: self.mods.clone(),
                profiles_root: self.profiles.clone(),
                vault: self.vault.clone(),
                profile_id: "native-profile-id".into(),
                game: "skyrimspecialedition".into(),
                mods,
                context_digest: "native-epoch-inventory".into(),
                expected_digest: None,
                mod_name: Some("A".into()),
                retained_mod_name: Some("B".into()),
                id: None,
                confirm: None,
            }
        }
        fn quarantine(&self) -> String {
            let preview = execute(self.request("previewQuarantine"), || false).unwrap();
            let mut r = self.request("quarantine");
            r.confirm = Some("QUARANTINE".into());
            r.expected_digest = preview["digest"].as_str().map(str::to_owned);
            execute(r, || false).unwrap()["record"]["id"]
                .as_str()
                .unwrap()
                .to_owned()
        }
    }
    #[test]
    fn complete_equal_content_has_every_owner_and_ignores_metadata() {
        let f = Fixture::new();
        let r = execute(f.request("scan"), || false).unwrap();
        assert_eq!(r["complete"], true);
        assert_eq!(r["groups"].as_array().unwrap().len(), 1);
        assert_eq!(r["groups"][0]["mods"].as_array().unwrap().len(), 2);
        assert_eq!(r["fileGroups"].as_array().unwrap().len(), 1);
        assert_eq!(r["fileGroups"][0]["deletionAuthorized"], false);
        assert_eq!(r["logicalDuplicateBytes"], 12);
    }
    #[test]
    fn complete_equality_uses_case_insensitive_path_identity() {
        let f = Fixture::new();
        fs::rename(f.mods.join("B/asset.nif"), f.mods.join("B/ASSET.NIF")).unwrap();
        assert_eq!(
            execute(f.request("scan"), || false).unwrap()["groups"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }
    #[test]
    fn empty_or_metadata_only_mods_are_not_duplicates() {
        let f = Fixture::new();
        for name in ["A", "B"] {
            fs::remove_file(f.mods.join(name).join("asset.nif")).unwrap();
        }
        assert!(
            execute(f.request("scan"), || false).unwrap()["groups"]
                .as_array()
                .unwrap()
                .is_empty()
        );
    }
    #[test]
    fn incomplete_inventory_rejects_entire_scan() {
        let f = Fixture::new();
        let mut r = f.request("scan");
        r.mods.pop();
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn added_unmanaged_directory_invalidates_native_inventory() {
        let f = Fixture::new();
        let r = f.request("scan");
        fs::create_dir(f.mods.join("Unmanaged")).unwrap();
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn saved_profile_changes_invalidate_claims() {
        let f = Fixture::new();
        let r = f.request("scan");
        fs::write(f.profiles.join("Default/modlist.txt"), "+A\n+B\n").unwrap();
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn enabled_in_another_saved_profile_blocks_quarantine() {
        let f = Fixture::new();
        fs::create_dir(f.profiles.join("Other")).unwrap();
        fs::write(f.profiles.join("Other/modlist.txt"), "+A\n-B\n").unwrap();
        assert!(execute(f.request("previewQuarantine"), || false).is_err());
    }
    #[test]
    fn live_unsaved_enable_blocks_quarantine() {
        let f = Fixture::new();
        let mut r = f.request("previewQuarantine");
        r.mods[0].enabled = true;
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn stale_native_context_and_changed_counterpart_block_mutation() {
        let f = Fixture::new();
        let preview = execute(f.request("previewQuarantine"), || false).unwrap();
        let mut r = f.request("quarantine");
        r.expected_digest = preview["digest"].as_str().map(str::to_owned);
        r.confirm = Some("QUARANTINE".into());
        r.context_digest = "different context".into();
        assert!(execute(r, || false).is_err());
        fs::write(f.mods.join("B/asset.nif"), "different").unwrap();
        assert!(execute(f.request("previewQuarantine"), || false).is_err());
        assert!(f.mods.join("A").exists());
    }
    #[test]
    fn quarantine_and_restore_keep_original_content_and_metadata() {
        let f = Fixture::new();
        let id = f.quarantine();
        assert!(!f.mods.join("A").exists());
        assert_eq!(
            fs::read(f.vault.join(&id).join("files/meta.ini")).unwrap(),
            b"A"
        );
        let mut p = f.request("previewRestore");
        p.id = Some(id.clone());
        let preview = execute(p, || false).unwrap();
        let mut r = f.request("restore");
        r.id = Some(id);
        r.confirm = Some("RESTORE".into());
        r.expected_digest = preview["digest"].as_str().map(str::to_owned);
        execute(r, || false).unwrap();
        assert_eq!(
            fs::read(f.mods.join("A/asset.nif")).unwrap(),
            b"shared bytes"
        );
        assert_eq!(fs::read(f.mods.join("A/meta.ini")).unwrap(), b"A");
    }
    #[test]
    fn later_enabled_profile_blocks_restore() {
        let f = Fixture::new();
        let id = f.quarantine();
        fs::write(f.profiles.join("Default/modlist.txt"), "+A\n+B\n").unwrap();
        let mut r = f.request("previewRestore");
        r.id = Some(id);
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn changed_quarantine_or_reappeared_target_blocks_restore() {
        let f = Fixture::new();
        let id = f.quarantine();
        fs::write(
            f.vault.join(&id).join("files/asset.nif"),
            "user edited retained copy",
        )
        .unwrap();
        let mut r = f.request("previewRestore");
        r.id = Some(id.clone());
        assert!(execute(r, || false).is_err());
        fs::create_dir(f.mods.join("A")).unwrap();
        fs::write(f.mods.join("A/user.nif"), "new target").unwrap();
        let mut r = f.request("previewRestore");
        r.id = Some(id);
        assert!(execute(r, || false).is_err());
        assert_eq!(fs::read(f.mods.join("A/user.nif")).unwrap(), b"new target");
    }
    #[test]
    fn cancellation_never_returns_partial_scan_success() {
        let f = Fixture::new();
        assert!(execute(f.request("scan"), || true).is_err());
        assert!(f.mods.join("A/asset.nif").exists());
    }
    #[test]
    fn interrupted_move_reconciles_only_exact_owned_copies() {
        let f = Fixture::new();
        let id = f.quarantine();
        let (dir, mut r) = load(&f.vault, &id).unwrap();
        r.state = "preparing".into();
        atomic_json(&dir.join("receipt.json"), &r).unwrap();
        let mut recover = f.request("recover");
        recover.id = Some(id);
        recover.confirm = Some("RECOVER".into());
        assert_eq!(
            execute(recover, || false).unwrap()["record"]["state"],
            "quarantined"
        );
    }
    #[test]
    fn hardlinks_count_logical_copies_and_shared_allocation_separately() {
        let f = Fixture::new();
        fs::remove_file(f.mods.join("B/asset.nif")).unwrap();
        fs::hard_link(f.mods.join("A/asset.nif"), f.mods.join("B/asset.nif")).unwrap();
        let r = execute(f.request("scan"), || false).unwrap();
        assert_eq!(r["fileGroups"][0]["distinctPhysicalFiles"], 1);
        assert_eq!(r["fileGroups"][0]["alreadyHardlinked"], true);
        assert_eq!(r["logicalDuplicateBytes"], 12);
        assert_eq!(
            r["physicalAllocatedBytes"],
            r["fileGroups"][0]["physicalAllocatedBytes"]
        );
    }
    #[test]
    fn malformed_non_ascii_profile_prefix_is_rejected_without_panic() {
        let f = Fixture::new();
        let r = f.request("scan");
        fs::write(f.profiles.join("Default/modlist.txt"), "☃A\n").unwrap();
        assert!(execute(r, || false).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn linked_mod_directory_is_not_an_owned_duplicate() {
        let f = Fixture::new();
        let r = f.request("scan");
        fs::remove_dir_all(f.mods.join("A")).unwrap();
        std::os::unix::fs::symlink(f.mods.join("B"), f.mods.join("A")).unwrap();
        assert!(execute(r, || false).is_err());
    }
    #[test]
    fn completed_quarantine_deletion_requires_unchanged_preview_and_retained_counterpart() {
        let f = Fixture::new();
        let id = f.quarantine();
        let mut p = f.request("previewDrop");
        p.id = Some(id.clone());
        let preview = execute(p, || false).unwrap();
        let mut r = f.request("drop");
        r.id = Some(id.clone());
        r.confirm = Some("DELETE QUARANTINE".into());
        r.expected_digest = Some("stale".into());
        assert!(execute(r, || false).is_err());
        let mut r = f.request("drop");
        r.id = Some(id.clone());
        r.confirm = Some("DELETE QUARANTINE".into());
        r.expected_digest = preview["digest"].as_str().map(str::to_owned);
        execute(r, || false).unwrap();
        assert!(!f.vault.join(id).exists());
        assert_eq!(
            fs::read(f.mods.join("B/asset.nif")).unwrap(),
            b"shared bytes"
        );
    }
    #[test]
    fn quarantine_deletion_preserves_changed_counterpart_and_unknown_material() {
        let f = Fixture::new();
        let id = f.quarantine();
        fs::write(f.vault.join(&id).join("notes.txt"), "preserve").unwrap();
        let mut p = f.request("previewDrop");
        p.id = Some(id.clone());
        assert!(execute(p, || false).is_err());
        fs::remove_file(f.vault.join(&id).join("notes.txt")).unwrap();
        fs::write(f.mods.join("B/asset.nif"), "different").unwrap();
        let mut p = f.request("previewDrop");
        p.id = Some(id.clone());
        assert!(execute(p, || false).is_err());
        assert_eq!(
            fs::read(f.vault.join(id).join("files/asset.nif")).unwrap(),
            b"shared bytes"
        );
    }
}
