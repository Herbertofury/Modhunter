//! Explicit desktop reset. Only registered Tauri application directories enter
//! this transaction. Every reset stages whole owned directories before removing
//! individually verified files; changed or locked material remains recoverable.
use crate::{acquisition::reject_link, content_vault::checked_root};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, String>;
pub const APPLICATION: &str = "com.modhunter.desktop";
pub const CONFIRMATION: &str = "REMOVE MODHUNTER DATA";
const OWNER_FILE: &str = "modhunter-app-owner.json";
const COORDINATOR: &str = "com.modhunter.desktop-reset";
const MAX_ENTRIES: usize = 200_000;
const MAX_PLAN: usize = 64 * 1024 * 1024;

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn json_bytes(value: &impl Serialize) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(err)
}
fn read<T: for<'a> Deserialize<'a>>(path: &Path, limit: usize) -> Result<T> {
    reject_link(path)?;
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(err)?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(err)?;
    if bytes.len() > limit {
        return Err("Reset ownership or inventory record exceeds its limit.".into());
    }
    serde_json::from_slice(&bytes).map_err(err)
}
fn create_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(err)?;
    file.write_all(&json_bytes(value)?).map_err(err)?;
    file.sync_all().map_err(err)
}

/// These paths must come from the native application path resolver. No renderer
/// command accepts a replacement path. The helper independently resolves them.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AppPaths {
    pub config: PathBuf,
    pub data: PathBuf,
    pub local_data: PathBuf,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RootSpec {
    path: PathBuf,
    roles: Vec<String>,
}

fn target(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute()
        || path.file_name().and_then(|n| n.to_str()) != Some(APPLICATION)
        || path
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err("Reset accepts only the exact Modhunter application data directory.".into());
    }
    let parent = checked_root(
        path.parent()
            .ok_or("Application directory has no parent.")?,
    )?;
    let result = parent.join(APPLICATION);
    if result.exists() {
        checked_root(&result)
    } else {
        Ok(result)
    }
}
fn specifications(paths: &AppPaths) -> Result<Vec<RootSpec>> {
    let mut roots: BTreeMap<PathBuf, Vec<String>> = BTreeMap::new();
    for (role, path) in [
        ("configuration", &paths.config),
        ("applicationData", &paths.data),
        ("browserData", &paths.local_data),
    ] {
        roots.entry(target(path)?).or_default().push(role.into());
    }
    let specs: Vec<RootSpec> = roots
        .into_iter()
        .map(|(path, roles)| RootSpec { path, roles })
        .collect();
    for a in &specs {
        for b in &specs {
            if a.path != b.path && a.path.starts_with(&b.path) {
                return Err("Application reset roots must not contain each other.".into());
            }
        }
    }
    Ok(specs)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Owner {
    schema: u32,
    application: String,
    purpose: String,
    path: PathBuf,
    identity: String,
}

#[cfg(unix)]
fn identity(path: &Path) -> Result<String> {
    use std::os::unix::fs::MetadataExt;
    reject_link(path)?;
    let m = fs::symlink_metadata(path).map_err(err)?;
    Ok(format!("{}:{}", m.dev(), m.ino()))
}
#[cfg(windows)]
fn identity(path: &Path) -> Result<String> {
    use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
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
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFileInformationByHandle(handle: *mut std::ffi::c_void, info: *mut Info) -> i32;
    }
    reject_link(path)?;
    // A metadata-only handle works for WebView2 databases even while they hold
    // exclusive data handles. Reparse points are opened themselves, never followed.
    let file = OpenOptions::new()
        .read(true)
        .access_mode(0)
        .share_mode(7)
        .custom_flags(0x0200_0000 | 0x0020_0000)
        .open(path)
        .map_err(err)?;
    let mut info = Info::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Err(err(std::io::Error::last_os_error()));
    }
    if info.attributes & 0x400 != 0 {
        return Err("Reset refuses a reparse point.".into());
    }
    Ok(format!(
        "{}:{}:{}",
        info.volume, info.index_high, info.index_low
    ))
}
#[cfg(not(any(unix, windows)))]
fn identity(_: &Path) -> Result<String> {
    Err("Application reset is unsupported on this platform.".into())
}

fn owner(path: &Path, purpose: &str) -> Result<Owner> {
    Ok(Owner {
        schema: 1,
        application: APPLICATION.into(),
        purpose: purpose.into(),
        path: path.to_path_buf(),
        identity: identity(path)?,
    })
}
fn verify_owner(path: &Path, purpose: &str, marker: &str) -> Result<Owner> {
    checked_root(path)?;
    let expected = owner(path, purpose)?;
    let actual: Owner = read(&path.join(marker), 16 * 1024)?;
    if actual != expected {
        return Err("Application data ownership changed; reset did not touch it.".into());
    }
    Ok(actual)
}
fn claim(path: &Path, purpose: &str, marker: &str) -> Result<()> {
    if !path.exists() {
        fs::create_dir(path).map_err(err)?;
    }
    checked_root(path)?;
    if !path.join(marker).exists() {
        match create_json(&path.join(marker), &owner(path, purpose)?) {
            Ok(()) => (),
            Err(_) if path.join(marker).exists() => (),
            Err(error) => return Err(error),
        }
    }
    verify_owner(path, purpose, marker).map(|_| ())
}
fn approved_top(name: &str, directory: bool, roles: &[String]) -> bool {
    if !directory {
        if matches!(
            name,
            OWNER_FILE
                | "watchtower.json"
                | "watchtower-notifications.json"
                | "watchtower.lock"
                | "maintenance.json"
                | "native-preferences.json"
        ) {
            return true;
        }
        return name
            .strip_prefix("watchtower.json.recovery-")
            .and_then(|n| n.strip_suffix(".json"))
            .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    }
    (roles.iter().any(|r| r == "applicationData")
        && matches!(
            name,
            "watchtower-acquisitions"
                | "watchtower-prepared"
                | "watchtower-site-profiles"
                | "watchtower-temporary-profiles"
        ))
        || (roles.iter().any(|r| r == "browserData") && name == "EBWebView")
}
fn check_top(spec: &RootSpec, path: &Path) -> Result<()> {
    for entry in fs::read_dir(path).map_err(err)? {
        let entry = entry.map_err(err)?;
        reject_link(&entry.path())?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "Application data has a non-Unicode path.")?;
        let metadata = entry.metadata().map_err(err)?;
        if !(metadata.is_file() || metadata.is_dir())
            || !approved_top(&name, metadata.is_dir(), &spec.roles)
        {
            return Err(format!(
                "Unrecognized application data entry '{name}'. Reset preserves it; inspect this directory first."
            ));
        }
    }
    Ok(())
}

fn coordinate(paths: &AppPaths) -> Result<PathBuf> {
    let local = target(&paths.local_data)?;
    let directory = local
        .parent()
        .ok_or("Missing reset coordination parent.")?
        .join(COORDINATOR);
    if directory.exists() {
        verify_owner(&directory, "reset-coordination", OWNER_FILE)?;
    } else {
        claim(&directory, "reset-coordination", OWNER_FILE)?;
    }
    Ok(directory)
}
fn lease(directory: &Path, exclusive: bool) -> Result<File> {
    verify_owner(directory, "reset-coordination", OWNER_FILE)?;
    let path = directory.join("application.lock");
    if !path.exists() {
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => file.sync_all().map_err(err)?,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(err(error)),
        }
    }
    reject_link(&path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        if exclusive {
            options.write(true).share_mode(0);
        } else {
            options.share_mode(1);
        }
    }
    let file = options.open(&path).map_err(err)?;
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        if unsafe {
            libc::flock(
                file.as_raw_fd(),
                (if exclusive {
                    libc::LOCK_EX
                } else {
                    libc::LOCK_SH
                }) | libc::LOCK_NB,
            )
        } != 0
        {
            return Err("Another Modhunter process still owns its application data.".into());
        }
    }
    Ok(file)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Entry {
    directory: bool,
    identity: String,
    bytes: u64,
    modified: u64,
}
type Tree = BTreeMap<String, Entry>;
fn entry(path: &Path) -> Result<Entry> {
    reject_link(path)?;
    let m = fs::symlink_metadata(path).map_err(err)?;
    if !(m.is_file() || m.is_dir()) {
        return Err("Reset refuses a special filesystem object.".into());
    }
    let modified = m
        .modified()
        .map_err(err)?
        .duration_since(UNIX_EPOCH)
        .map_err(err)?
        .as_nanos();
    if modified > u64::MAX as u128 {
        return Err("Application file has an unsupported modification time.".into());
    }
    Ok(Entry {
        directory: m.is_dir(),
        identity: identity(path)?,
        bytes: if m.is_file() { m.len() } else { 0 },
        modified: modified as u64,
    })
}
fn tree(root: &Path) -> Result<Tree> {
    checked_root(root)?;
    let root_identity = identity(root)?;
    let mut result = Tree::new();
    let mut pending = vec![(root.to_path_buf(), String::new())];
    let mut path_bytes = 0usize;
    while let Some((directory, relative)) = pending.pop() {
        reject_link(&directory)?;
        for child in fs::read_dir(&directory).map_err(err)? {
            let child = child.map_err(err)?;
            let name = child
                .file_name()
                .into_string()
                .map_err(|_| "Application data has a non-Unicode filename.")?;
            if name.is_empty()
                || name.contains(['/', '\\', ':', '\0'])
                || name.chars().any(char::is_control)
            {
                return Err("Application data has an unsafe filename.".into());
            }
            let relative = if relative.is_empty() {
                name
            } else {
                format!("{relative}/{name}")
            };
            path_bytes = path_bytes
                .checked_add(relative.len())
                .ok_or("Reset path inventory overflow.")?;
            if result.len() >= MAX_ENTRIES || path_bytes > 32 * 1024 * 1024 {
                return Err("Application data exceeds the complete reset inventory limit.".into());
            }
            let metadata = entry(&child.path())?;
            if metadata.directory {
                pending.push((child.path(), relative.clone()));
            }
            result.insert(relative, metadata);
        }
    }
    if identity(root)? != root_identity {
        return Err("Application directory changed during inventory.".into());
    }
    Ok(result)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RootInventory {
    spec: RootSpec,
    owner: Owner,
    entries: Tree,
}
fn inventory(specs: &[RootSpec]) -> Result<Vec<RootInventory>> {
    let scan = || -> Result<Vec<RootInventory>> {
        specs
            .iter()
            .map(|spec| {
                let owner = verify_owner(&spec.path, "application-data", OWNER_FILE)?;
                check_top(spec, &spec.path)?;
                Ok(RootInventory {
                    spec: spec.clone(),
                    owner,
                    entries: tree(&spec.path)?,
                })
            })
            .collect()
    };
    let result = scan()?;
    if scan()? != result {
        return Err("Application data changed during inventory. Wait for current work to stop, then review reset again.".into());
    }
    Ok(result)
}
fn summary(roots: &[RootInventory]) -> Value {
    json!(roots.iter().map(|root| {
        let bytes:u64 = root.entries.values().map(|entry| entry.bytes).sum();
        json!({"roles":root.spec.roles,"path":root.spec.path,"files":root.entries.values().filter(|e|!e.directory).count(),
            "directories":root.entries.values().filter(|e|e.directory).count(),"bytes":bytes})
    }).collect::<Vec<_>>())
}

pub struct Coordinator {
    paths: AppPaths,
    specs: Vec<RootSpec>,
    directory: PathBuf,
    _lease: File,
}
impl Coordinator {
    /// Acquires a shared lifetime lease before any normal application writes.
    /// A pending helper prevents a new instance from recreating/resetting roots.
    pub fn open(paths: AppPaths) -> Result<Self> {
        let specs = specifications(&paths)?;
        let directory = coordinate(&paths)?;
        let pending = directory.join("active.json");
        if pending.exists() {
            return Err(format!(
                "A Modhunter reset is pending. Close other Modhunter windows and inspect {} before restarting.",
                directory.display()
            ));
        }
        let lock = lease(&directory, false)?;
        if pending.exists() {
            return Err(
                "A reset started while Modhunter was opening. Wait for its completion report."
                    .into(),
            );
        }
        Ok(Self {
            paths,
            specs,
            directory,
            _lease: lock,
        })
    }
    /// Only known historical application filenames may be adopted. Unknown
    /// files do not become deletion authority merely by being under AppData.
    pub fn adopt_roots(&self) -> Result<()> {
        for spec in &self.specs {
            if !spec.path.exists() {
                fs::create_dir(&spec.path).map_err(err)?;
            }
            checked_root(&spec.path)?;
            check_top(spec, &spec.path)?;
            claim(&spec.path, "application-data", OWNER_FILE)?;
        }
        Ok(())
    }
    pub fn preview(&self) -> Result<Value> {
        if self.directory.join("active.json").exists() {
            return Err("A reset is already scheduled.".into());
        }
        let roots = inventory(&self.specs)?;
        let hash = digest(&json_bytes(&roots)?);
        Ok(
            json!({"digest":hash,"roots":summary(&roots),"requiresExit":true,"confirmation":CONFIRMATION,
            "scope":"Modhunter preferences, page watches, site sign-ins, embedded browser data, downloads and prepared archive reviews. MO2, games, user browsers and MO2 recovery vaults are outside this reset.",
            "retention":"Changed or locked files stay in an owned staging directory named in the completion report.",
            "inventory":"complete filesystem metadata and directory identities"}),
        )
    }
    pub fn schedule(
        &self,
        expected_digest: &str,
        confirm: &str,
        executable: &Path,
        pid: u32,
    ) -> Result<Scheduled> {
        if confirm != CONFIRMATION {
            return Err(format!(
                "Type {CONFIRMATION} to approve the reviewed reset."
            ));
        }
        if self.directory.join("active.json").exists() {
            return Err("A reset is already scheduled.".into());
        }
        let roots = inventory(&self.specs)?;
        if expected_digest != digest(&json_bytes(&roots)?) {
            return Err("Application data changed after the reset preview. Review the current inventory again.".into());
        }
        let executable = fs::canonicalize(executable).map_err(err)?;
        reject_link(&executable)?;
        let executable_sha256 = hash_file(&executable)?;
        let random = tempfile::Builder::new()
            .prefix("reset-")
            .tempdir_in(&self.directory)
            .map_err(err)?;
        let job = random.keep();
        let id = job
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or("Invalid reset identity.")?
            .to_owned();
        let plan = Plan {
            schema: 1,
            application: APPLICATION.into(),
            id: id.clone(),
            created_at: now(),
            pid,
            paths: self.paths.clone(),
            coordinator: verify_owner(&self.directory, "reset-coordination", OWNER_FILE)?,
            executable,
            executable_sha256,
            approved_digest: expected_digest.into(),
            roots,
        };
        let bytes = json_bytes(&plan)?;
        if bytes.len() > MAX_PLAN {
            return Err(
                "Reset inventory exceeds the durable plan limit; nothing was scheduled.".into(),
            );
        }
        create_json(&job.join("plan.json"), &plan)?;
        let active = Active {
            schema: 1,
            id: id.clone(),
            plan_sha256: digest(&bytes),
        };
        create_json(&self.directory.join("active.json"), &active)?;
        Ok(Scheduled {
            id,
            plan: job.join("plan.json"),
            completion_report: job.join("completion.json"),
            ready: job.join("ready.json"),
        })
    }
    pub fn cancel_schedule(&self, scheduled: &Scheduled, reason: &str) -> Result<()> {
        let pending = Pending::load(
            &self.paths,
            &scheduled.plan,
            &std::env::current_exe().map_err(err)?,
        )?;
        pending.failed(reason).map(|_| ())
    }
    pub fn last_report(&self) -> Result<Option<Value>> {
        let path = self.directory.join("last-completion.json");
        if !path.exists() {
            return Ok(None);
        }
        read(&path, 1024 * 1024).map(Some)
    }
}

fn hash_file(path: &Path) -> Result<String> {
    let mut file = File::open(path).map_err(err)?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buffer).map_err(err)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Active {
    schema: u32,
    id: String,
    plan_sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Plan {
    schema: u32,
    application: String,
    id: String,
    created_at: u64,
    pid: u32,
    paths: AppPaths,
    coordinator: Owner,
    executable: PathBuf,
    executable_sha256: String,
    approved_digest: String,
    roots: Vec<RootInventory>,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Scheduled {
    pub id: String,
    pub plan: PathBuf,
    pub completion_report: PathBuf,
    pub ready: PathBuf,
}
pub struct Pending {
    plan: Plan,
    directory: PathBuf,
    job: PathBuf,
    active: Active,
}
impl Pending {
    pub fn load(paths: &AppPaths, plan_file: &Path, executable: &Path) -> Result<Self> {
        let specs = specifications(paths)?;
        let directory = coordinate(paths)?;
        let active: Active = read(&directory.join("active.json"), 16 * 1024)?;
        if active.schema != 1
            || !active.id.starts_with("reset-")
            || active.id.len() > 100
            || !active
                .id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err("Invalid pending reset identity.".into());
        }
        let job = directory.join(&active.id);
        checked_root(&job)?;
        let expected_file = job.join("plan.json");
        if fs::canonicalize(plan_file).map_err(err)?
            != fs::canonicalize(&expected_file).map_err(err)?
        {
            return Err(
                "Reset helper received a plan outside the native application coordinator.".into(),
            );
        }
        let plan: Plan = read(&expected_file, MAX_PLAN)?;
        if digest(&json_bytes(&plan)?) != active.plan_sha256
            || plan.schema != 1
            || plan.application != APPLICATION
            || plan.id != active.id
            || plan.coordinator != verify_owner(&directory, "reset-coordination", OWNER_FILE)?
            || plan
                .roots
                .iter()
                .map(|r| r.spec.clone())
                .collect::<Vec<_>>()
                != specs
            || specifications(&plan.paths)? != specs
            || plan.approved_digest != digest(&json_bytes(&plan.roots)?)
            || fs::canonicalize(executable).map_err(err)? != plan.executable
            || hash_file(executable)? != plan.executable_sha256
        {
            return Err("Reset helper ownership or reviewed inventory no longer matches.".into());
        }
        Ok(Self {
            plan,
            directory,
            job,
            active,
        })
    }
    pub fn parent_pid(&self) -> u32 {
        self.plan.pid
    }
    pub fn ready(&self) -> Result<()> {
        self.current()?;
        create_json(
            &self.job.join("ready.json"),
            &json!({"schema":1,"id":self.plan.id,"helperPid":std::process::id(),"planSha256":self.active.plan_sha256}),
        )
    }
    fn current(&self) -> Result<()> {
        if verify_owner(&self.directory, "reset-coordination", OWNER_FILE)? != self.plan.coordinator
            || read::<Active>(&self.directory.join("active.json"), 16 * 1024)? != self.active
        {
            return Err("Reset ownership changed; no further cleanup is authorized.".into());
        }
        checked_root(&self.job)?;
        Ok(())
    }
    fn complete(&self, mut result: Value) -> Result<Value> {
        self.current()?;
        result["schema"] = json!(1);
        result["application"] = json!(APPLICATION);
        result["id"] = json!(self.plan.id);
        result["completedAt"] = json!(now());
        result["completionReport"] = json!(self.job.join("completion.json"));
        result["approvedDigest"] = json!(self.plan.approved_digest);
        create_json(&self.job.join("completion.json"), &result)?;
        crate::acquisition::atomic_json(&self.directory.join("last-completion.json"), &result)?;
        self.current()?;
        fs::remove_file(self.directory.join("active.json")).map_err(err)?;
        Ok(result)
    }
    pub fn failed(&self, reason: &str) -> Result<Value> {
        self.complete(json!({"state":"notCompleted","message":reason,"roots":[],"removedBytes":0,"retainedData":true}))
    }
    /// Call only after the initiating process exited. The exclusive lease also
    /// waits for every other native instance and prevents a restart race.
    pub fn finish(&self, wait: Duration) -> Result<Value> {
        self.current()?;
        let deadline = Instant::now() + wait;
        let _lease = loop {
            match lease(&self.directory,true) {
                Ok(file)=>break file,
                Err(_) if Instant::now()<deadline=>thread::sleep(Duration::from_millis(50)),
                Err(_)=>return self.failed("Another Modhunter process or a locked coordinator prevented reset. No application directory was changed."),
            }
        };
        self.current()?;
        let current = match inventory(
            &self
                .plan
                .roots
                .iter()
                .map(|r| r.spec.clone())
                .collect::<Vec<_>>(),
        ) {
            Ok(value) => value,
            Err(error) => return self.failed(&error),
        };
        if current
            .iter()
            .zip(&self.plan.roots)
            .any(|(current, approved)| current.owner != approved.owner)
        {
            return self.failed(
                "Application directory ownership changed after approval. No directory was changed.",
            );
        }
        let mut rows = Vec::new();
        for (index, (current, approved)) in current.iter().zip(&self.plan.roots).enumerate() {
            self.current()?;
            rows.push(self.reset_root(index, current, approved));
        }
        let all_reset = rows.iter().all(|row| row["activeDataReset"] == true);
        let all_removed = rows.iter().all(|row| row["state"] == "removed");
        self.complete(json!({"state":if all_removed {"removed"} else if all_reset {"resetWithRetainedData"} else {"partiallyReset"},
            "activeDataReset":all_reset,"retainedData":!all_removed,"roots":rows,
            "message":if all_removed {"Reviewed Modhunter application data was removed."} else {"Inspect the root results. Changed or locked data was retained; no MO2, game, user-browser or recovery-vault path was included."}}))
    }
    fn reset_root(&self, index: usize, current: &RootInventory, approved: &RootInventory) -> Value {
        let path = &current.spec.path;
        let stage = path
            .parent()
            .unwrap()
            .join(format!("{APPLICATION}.{}-{index}", self.plan.id));
        let mut result = json!({"roles":current.spec.roles,"path":path,"stagedPath":stage,"activeDataReset":false,"state":"retained","bytes":current.entries.values().map(|e|e.bytes).sum::<u64>()});
        let attempt = || -> Result<&'static str> {
            if stage.exists() {
                return Err("A reset staging path already exists; it was preserved.".into());
            }
            if verify_owner(path, "application-data", OWNER_FILE)? != current.owner
                || tree(path)? != current.entries
            {
                return Err("Application data changed immediately before staging.".into());
            }
            fs::rename(path, &stage).map_err(err)?;
            result_stage_identity(&stage, &current.owner)?;
            if tree(&stage)? != current.entries {
                return Ok("retainedChangedDuringReset");
            }
            if current.entries != approved.entries {
                return Ok("retainedChangesAfterApproval");
            }
            remove_verified(&stage, &current.entries)?;
            Ok("removed")
        };
        match attempt() {
            Ok(state) => {
                result["state"] = json!(state);
                result["activeDataReset"] = json!(true);
            }
            Err(error) => {
                result["error"] = json!(error);
                result["activeDataReset"] = json!(!path.exists() && stage.exists());
            }
        }
        result
    }
}
fn result_stage_identity(stage: &Path, expected: &Owner) -> Result<()> {
    checked_root(stage)?;
    if identity(stage)? != expected.identity
        || read::<Owner>(&stage.join(OWNER_FILE), 16 * 1024)? != *expected
    {
        return Err(
            "Staged application directory ownership changed; its contents were retained.".into(),
        );
    }
    Ok(())
}
fn checked_child(root: &Path, name: &str) -> Result<PathBuf> {
    let mut path = root.to_path_buf();
    for part in name.split('/') {
        path.push(part);
        reject_link(&path)?;
    }
    if !fs::canonicalize(&path)
        .map_err(err)?
        .starts_with(fs::canonicalize(root).map_err(err)?)
    {
        return Err("Reset child escaped its owned staging root.".into());
    }
    Ok(path)
}
fn remove_verified(root: &Path, entries: &Tree) -> Result<()> {
    if tree(root)? != *entries {
        return Err("Staged files changed before removal; they were retained.".into());
    }
    for (name, expected) in entries
        .iter()
        .filter(|(name, e)| !e.directory && name.as_str() != OWNER_FILE)
    {
        let path = checked_child(root, name)?;
        if entry(&path)? != *expected {
            return Err(
                "A staged file changed before removal; remaining files were retained.".into(),
            );
        }
        fs::remove_file(path).map_err(err)?;
    }
    let mut directories: Vec<_> = entries
        .iter()
        .filter(|(_, entry)| entry.directory)
        .collect();
    directories.sort_by(|(a, _), (b, _)| {
        b.split('/')
            .count()
            .cmp(&a.split('/').count())
            .then_with(|| b.cmp(a))
    });
    for (name, expected) in directories {
        let path = checked_child(root, name)?;
        if identity(&path)? != expected.identity {
            return Err("A staged subdirectory changed; it was preserved.".into());
        }
        fs::remove_dir(path).map_err(err)?;
    }
    let remaining = fs::read_dir(root)
        .map_err(err)?
        .collect::<std::io::Result<Vec<_>>>()
        .map_err(err)?;
    if remaining.len() != 1 || remaining[0].file_name() != OWNER_FILE {
        return Err("New staged data appeared during removal; it was retained.".into());
    }
    let marker = checked_child(root, OWNER_FILE)?;
    if entries.get(OWNER_FILE) != Some(&entry(&marker)?) {
        return Err("Application ownership marker changed during removal.".into());
    }
    fs::remove_file(marker).map_err(err)?;
    fs::remove_dir(root).map_err(err)
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture {
        _temp: tempfile::TempDir,
        paths: AppPaths,
    }
    impl Fixture {
        fn new() -> Self {
            let temp = tempfile::tempdir().unwrap();
            let roaming = temp.path().join("roaming");
            let local = temp.path().join("local");
            fs::create_dir(&roaming).unwrap();
            fs::create_dir(&local).unwrap();
            let paths = AppPaths {
                config: roaming.join(APPLICATION),
                data: roaming.join(APPLICATION),
                local_data: local.join(APPLICATION),
            };
            Self { _temp: temp, paths }
        }
        fn open(&self) -> Coordinator {
            let c = Coordinator::open(self.paths.clone()).unwrap();
            c.adopt_roots().unwrap();
            c
        }
        fn schedule(&self, c: &Coordinator) -> Scheduled {
            let preview = c.preview().unwrap();
            c.schedule(
                preview["digest"].as_str().unwrap(),
                CONFIRMATION,
                &std::env::current_exe().unwrap(),
                std::process::id(),
            )
            .unwrap()
        }
        fn pending(&self, s: &Scheduled) -> Pending {
            Pending::load(&self.paths, &s.plan, &std::env::current_exe().unwrap()).unwrap()
        }
    }
    #[test]
    fn exact_roots_deduplicate_and_omit_sibling_recovery() {
        let f = Fixture::new();
        let c = f.open();
        let recovery = f.paths.data.parent().unwrap().join("Modhunter-Recovery");
        fs::create_dir(&recovery).unwrap();
        fs::write(recovery.join("saved.bin"), b"do not remove").unwrap();
        fs::write(f.paths.config.join("watchtower.json"), b"{}").unwrap();
        let preview = c.preview().unwrap();
        assert_eq!(preview["roots"].as_array().unwrap().len(), 2);
        let s = f.schedule(&c);
        let p = f.pending(&s);
        drop(c);
        let result = p.finish(Duration::ZERO).unwrap();
        assert_eq!(result["state"], "removed");
        assert!(!f.paths.config.exists());
        assert!(!f.paths.local_data.exists());
        assert_eq!(
            fs::read(recovery.join("saved.bin")).unwrap(),
            b"do not remove"
        );
        assert!(s.completion_report.exists());
        let reopened = f.open();
        assert!(reopened.last_report().unwrap().is_some());
    }
    #[test]
    fn wrong_root_and_unowned_unknown_files_are_preserved() {
        let f = Fixture::new();
        let mut paths = f.paths.clone();
        paths.data = f.paths.data.parent().unwrap().to_path_buf();
        assert!(Coordinator::open(paths).is_err());
        fs::create_dir(&f.paths.config).unwrap();
        fs::write(f.paths.config.join("personal-save.bin"), b"mine").unwrap();
        let c = Coordinator::open(f.paths.clone()).unwrap();
        assert!(c.adopt_roots().is_err());
        assert!(!f.paths.config.join(OWNER_FILE).exists());
        assert_eq!(
            fs::read(f.paths.config.join("personal-save.bin")).unwrap(),
            b"mine"
        );
    }
    #[test]
    fn changed_inventory_and_missing_confirmation_do_not_schedule() {
        let f = Fixture::new();
        let c = f.open();
        let preview = c.preview().unwrap();
        let exe = std::env::current_exe().unwrap();
        assert!(
            c.schedule(preview["digest"].as_str().unwrap(), "RESET", &exe, 1)
                .is_err()
        );
        fs::write(f.paths.config.join("watchtower.json"), b"changed").unwrap();
        assert!(
            c.schedule(preview["digest"].as_str().unwrap(), CONFIRMATION, &exe, 1)
                .is_err()
        );
        assert!(!c.directory.join("active.json").exists());
    }
    #[test]
    fn restart_and_live_process_lease_prevent_cleanup() {
        let f = Fixture::new();
        let c = f.open();
        let s = f.schedule(&c);
        assert!(Coordinator::open(f.paths.clone()).is_err());
        let p = f.pending(&s);
        let result = p.finish(Duration::ZERO).unwrap();
        assert_eq!(result["state"], "notCompleted");
        assert!(f.paths.config.join(OWNER_FILE).exists());
        drop(c);
        assert!(f.open().preview().is_ok());
    }
    #[test]
    fn shutdown_changes_are_staged_and_reported_without_deleting_bytes() {
        let f = Fixture::new();
        let c = f.open();
        fs::write(f.paths.config.join("watchtower.json"), b"before").unwrap();
        let s = f.schedule(&c);
        let p = f.pending(&s);
        fs::write(f.paths.config.join("watchtower.json"), b"after shutdown").unwrap();
        drop(c);
        let result = p.finish(Duration::ZERO).unwrap();
        assert_eq!(result["state"], "resetWithRetainedData");
        assert_eq!(result["activeDataReset"], true);
        let row = result["roots"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["state"] == "retainedChangesAfterApproval")
            .unwrap();
        assert_eq!(
            fs::read(Path::new(row["stagedPath"].as_str().unwrap()).join("watchtower.json"))
                .unwrap(),
            b"after shutdown"
        );
        assert!(!f.paths.config.exists());
    }
    #[test]
    fn changed_owner_and_new_unknown_files_stop_all_root_mutation() {
        let f = Fixture::new();
        let c = f.open();
        let s = f.schedule(&c);
        let p = f.pending(&s);
        fs::write(f.paths.local_data.join("unknown.bin"), b"keep").unwrap();
        drop(c);
        let result = p.finish(Duration::ZERO).unwrap();
        assert_eq!(result["state"], "notCompleted");
        assert!(f.paths.config.join(OWNER_FILE).exists());
        assert!(f.paths.local_data.join("unknown.bin").exists());
    }
    #[test]
    fn replacement_directory_and_copied_marker_do_not_authorize_reset() {
        let f = Fixture::new();
        let c = f.open();
        let s = f.schedule(&c);
        let p = f.pending(&s);
        let marker = fs::read(f.paths.config.join(OWNER_FILE)).unwrap();
        let moved = f.paths.config.with_extension("moved");
        fs::rename(&f.paths.config, &moved).unwrap();
        fs::create_dir(&f.paths.config).unwrap();
        fs::write(f.paths.config.join(OWNER_FILE), marker).unwrap();
        drop(c);
        assert_eq!(p.finish(Duration::ZERO).unwrap()["state"], "notCompleted");
        assert!(moved.join(OWNER_FILE).exists());
    }
    #[cfg(unix)]
    #[test]
    fn links_and_outside_data_are_never_adopted() {
        use std::os::unix::fs::symlink;
        let f = Fixture::new();
        let c = f.open();
        let external = f._temp.path().join("external");
        fs::create_dir(&external).unwrap();
        fs::write(external.join("save.bin"), b"keep").unwrap();
        symlink(&external, f.paths.config.join("watchtower-site-profiles")).unwrap();
        assert!(c.preview().is_err());
        assert_eq!(fs::read(external.join("save.bin")).unwrap(), b"keep");
    }
    #[cfg(windows)]
    #[test]
    fn locked_files_are_retained_with_a_completion_report() {
        use std::os::windows::fs::OpenOptionsExt;
        let f = Fixture::new();
        let c = f.open();
        let path = f.paths.config.join("watchtower.json");
        fs::write(&path, b"locked").unwrap();
        let lock = OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&path)
            .unwrap();
        let s = f.schedule(&c);
        let p = f.pending(&s);
        drop(c);
        let result = p.finish(Duration::ZERO).unwrap();
        assert_ne!(result["state"], "removed");
        assert!(s.completion_report.exists());
        assert!(
            result["roots"]
                .as_array()
                .unwrap()
                .iter()
                .any(|row| row.get("error").is_some())
        );
        drop(lock);
    }
}
