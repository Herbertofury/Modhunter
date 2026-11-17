//! Read-only MO2 application and instance discovery.
//!
//! An application owns its plugin directory. Global instances are shared
//! configurations, not separate plugin destinations. Portable instances are
//! identified by an adjacent ModOrganizer.ini; portable.txt only locks the
//! application to that instance (upstream InstanceManager, MO2 2.5.2+).
#![cfg_attr(not(windows), allow(dead_code))]

use modhunter_core::ini::{Ini, decode_text};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{self, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

#[cfg(windows)]
pub mod windows;

const INI_LIMIT: u64 = 4 * 1024 * 1024;
const VERSION_LIMIT: u64 = 64 * 1024;

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum ScanMode {
    #[default]
    Known,
    Deep,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanSpec {
    pub mode: ScanMode,
    #[serde(default)]
    pub roots: Vec<PathBuf>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Inventory {
    pub installations: Vec<Installation>,
    pub instances: Vec<Instance>,
    pub associations: Vec<Association>,
    pub issues: Vec<Issue>,
    pub scope: String,
    pub complete: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Installation {
    pub id: String,
    pub directory: PathBuf,
    pub executable: PathBuf,
    pub version: Option<String>,
    pub architecture: Option<String>,
    pub portable_locked: bool,
    pub plugin: PluginObservation,
    pub running_pids: Vec<u32>,
    pub sources: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginObservation {
    pub version: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Instance {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub config_path: PathBuf,
    pub base_directory: Option<PathBuf>,
    pub game_name: Option<String>,
    pub game_path: Option<PathBuf>,
    pub selected_profile: Option<String>,
    pub profiles_directory: Option<PathBuf>,
    pub mods_directory: Option<PathBuf>,
    pub profiles: Vec<Profile>,
    pub sources: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub name: String,
    pub path: PathBuf,
    pub modlist_path: PathBuf,
    pub selected: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Association {
    pub installation_id: String,
    pub instance_id: String,
    pub kind: String,
    pub evidence: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Issue {
    pub path: Option<PathBuf>,
    pub kind: String,
    pub message: String,
}

impl Issue {
    pub fn new(path: Option<PathBuf>, kind: &str, message: impl Into<String>) -> Self {
        Self {
            path,
            kind: kind.into(),
            message: message.into(),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanProgress {
    pub directories_visited: u64,
    pub installations_found: usize,
    pub instances_found: usize,
    pub current_path: Option<PathBuf>,
    pub elapsed_ms: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AvailableRoot {
    pub path: PathBuf,
    pub label: String,
    pub kind: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunningProcess {
    pub pid: u32,
    pub name: String,
    /// None means a named MO2 process could not be inspected. It is not proof
    /// that the process belongs to a different installation.
    pub executable: Option<PathBuf>,
}

#[derive(Clone, Debug)]
pub struct SeedPath {
    pub path: PathBuf,
    pub source: String,
}

impl SeedPath {
    pub fn new(path: impl Into<PathBuf>, source: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            source: source.into(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct ShortcutSeed {
    pub path: PathBuf,
    pub executable: PathBuf,
    /// Some("") is an explicit portable-instance choice. None means the
    /// shortcut does not specify an instance, not the last-used instance.
    pub instance_name: Option<String>,
}

/// Public deterministic inputs allow native smoke tests to inspect only
/// disposable roots, without consulting the user's registry or processes.
#[derive(Clone, Debug, Default)]
pub struct KnownSeeds {
    pub applications: Vec<SeedPath>,
    pub instance_configs: Vec<SeedPath>,
    pub global_instance_roots: Vec<PathBuf>,
    /// Known-location scanning checks these directories and their immediate
    /// children. Only ScanMode::Deep enables unrestricted recursive traversal.
    pub search_roots: Vec<SeedPath>,
    pub shortcuts: Vec<ShortcutSeed>,
    pub processes: Vec<RunningProcess>,
    pub issues: Vec<Issue>,
}

impl KnownSeeds {
    pub fn for_roots(roots: &[PathBuf]) -> Self {
        Self {
            search_roots: roots
                .iter()
                .map(|path| SeedPath::new(path, "selectedFolder"))
                .collect(),
            ..Self::default()
        }
    }
}

pub fn known_seeds(remembered_roots: &[PathBuf], imported_hints: &[PathBuf]) -> KnownSeeds {
    #[cfg(windows)]
    let mut seeds = windows::known_seeds();
    #[cfg(not(windows))]
    let mut seeds = KnownSeeds::default();

    seeds.search_roots.extend(
        remembered_roots
            .iter()
            .map(|path| SeedPath::new(path, "rememberedFolder")),
    );
    for path in imported_hints {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if name.eq_ignore_ascii_case("ModOrganizer.ini") {
            seeds
                .instance_configs
                .push(SeedPath::new(path, "importedProfile"));
        } else if name.eq_ignore_ascii_case("modlist.txt") {
            if let Some(instance) = path.parent().and_then(Path::parent).and_then(Path::parent) {
                seeds.instance_configs.push(SeedPath::new(
                    instance.join("ModOrganizer.ini"),
                    "importedProfile",
                ));
            }
        } else if name.eq_ignore_ascii_case("ModOrganizer.exe") {
            seeds
                .applications
                .push(SeedPath::new(path, "importedApplication"));
        } else {
            seeds
                .search_roots
                .push(SeedPath::new(path, "importedLocation"));
        }
    }
    seeds
}

pub fn available_roots() -> Vec<AvailableRoot> {
    #[cfg(windows)]
    {
        windows::available_roots()
    }
    #[cfg(not(windows))]
    {
        Vec::new()
    }
}

pub fn running_processes() -> Result<Vec<RunningProcess>, String> {
    #[cfg(windows)]
    {
        windows::running_processes()
    }
    #[cfg(not(windows))]
    {
        Ok(Vec::new())
    }
}

/// Complete means that work in the declared scope finished. Inaccessible and
/// deliberately unfollowed linked paths remain explicit in issues. It does
/// not mean every directory on the computer was searched or no paths skipped.
pub fn discover(
    spec: &ScanSpec,
    seeds: KnownSeeds,
    cancelled: &AtomicBool,
    mut on_progress: impl FnMut(ScanProgress, &Inventory),
) -> Inventory {
    let mut scanner = Scanner {
        cancelled,
        progress: ScanProgress::default(),
        inventory: Inventory {
            issues: seeds.issues.clone(),
            scope: match spec.mode {
                ScanMode::Known => "Known application locations, current-user global instances, \
                    imported references, and remembered folders with their immediate children. \
                    Other folders require an explicit deep scan."
                    .into(),
                ScanMode::Deep => format!(
                    "Complete recursive search of the explicitly selected roots: {}. \
                     Known application/instance references are also inspected directly. \
                     Reparse points are not followed; skipped locations are listed.",
                    spec.roots
                        .iter()
                        .map(|path| path.display().to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            },
            ..Inventory::default()
        },
        started: Instant::now(),
        last_report: Instant::now(),
        inspected: HashSet::new(),
        walked: HashSet::new(),
        installation_indexes: HashMap::new(),
        instance_indexes: HashMap::new(),
        instance_directories: HashMap::new(),
        directory_aliases: HashMap::new(),
        global_roots: HashSet::new(),
        explicit_roots: if spec.mode == ScanMode::Deep {
            spec.roots.clone()
        } else {
            Vec::new()
        },
        on_progress: &mut on_progress,
    };
    scanner.report(true);
    let mut global_roots = Vec::new();
    for root in &seeds.global_instance_roots {
        if scanner.stopped() {
            break;
        }
        if let Some(path) = scanner.regular_directory(root) {
            scanner.global_roots.insert(path_key(&path));
            global_roots.push(path);
        }
    }
    for root in global_roots {
        if scanner.stopped() {
            break;
        }
        scanner.walk(&root, Some(1), "globalInstances");
    }
    for seed in &seeds.applications {
        if scanner.stopped() {
            break;
        }
        let directory = if seed.path.file_name().is_some_and(|name| {
            name.to_string_lossy()
                .eq_ignore_ascii_case("ModOrganizer.exe")
        }) {
            seed.path.parent()
        } else {
            Some(seed.path.as_path())
        };
        if let Some(directory) = directory {
            scanner.walk(directory, Some(0), &seed.source);
        }
    }
    for seed in &seeds.instance_configs {
        if scanner.stopped() {
            break;
        }
        scanner.inspect_instance(&seed.path, &seed.source);
        if let Some(directory) = seed.path.parent() {
            scanner.walk(directory, Some(0), &seed.source);
        }
    }
    for shortcut in &seeds.shortcuts {
        if scanner.stopped() {
            break;
        }
        if let Some(directory) = shortcut.executable.parent() {
            scanner.walk(directory, Some(0), "shortcut");
        }
    }
    for process in &seeds.processes {
        if scanner.stopped() {
            break;
        }
        if let Some(executable) = &process.executable
            && let Some(directory) = executable.parent()
        {
            scanner.walk(directory, Some(0), "runningProcess");
        }
    }
    match spec.mode {
        ScanMode::Known => {
            for seed in &seeds.search_roots {
                if scanner.stopped() {
                    break;
                }
                scanner.walk(&seed.path, Some(1), &seed.source);
            }
        }
        ScanMode::Deep => {
            if spec.roots.is_empty() {
                scanner.issue(
                    None,
                    "missingRoot",
                    "Select a folder or drive for a deep scan.",
                );
            }
            for root in &spec.roots {
                if scanner.stopped() {
                    break;
                }
                scanner.walk(root, None, "deepScan");
            }
        }
    }
    scanner.associate(&seeds);
    scanner.inventory.complete = !scanner.stopped();
    if scanner.stopped() {
        scanner.issue(
            None,
            "cancelled",
            "Search was cancelled. These findings are partial; previously completed findings remain available.",
        );
    }
    scanner
        .inventory
        .installations
        .sort_by(|a, b| a.directory.cmp(&b.directory).then(a.id.cmp(&b.id)));
    scanner
        .inventory
        .instances
        .sort_by(|a, b| a.name.cmp(&b.name).then(a.config_path.cmp(&b.config_path)));
    scanner.inventory.associations.sort_by(|a, b| {
        a.installation_id
            .cmp(&b.installation_id)
            .then(a.instance_id.cmp(&b.instance_id))
    });
    scanner.report(true);
    scanner.inventory
}

struct Scanner<'a, F> {
    cancelled: &'a AtomicBool,
    progress: ScanProgress,
    inventory: Inventory,
    started: Instant,
    last_report: Instant,
    inspected: HashSet<String>,
    // (directory identity, recursion depth). A prior shallow inspection must
    // never prevent the same directory from being visited by a later deep root.
    walked: HashSet<(String, Option<usize>)>,
    installation_indexes: HashMap<String, usize>,
    instance_indexes: HashMap<String, usize>,
    instance_directories: HashMap<String, usize>,
    directory_aliases: HashMap<String, String>,
    global_roots: HashSet<String>,
    explicit_roots: Vec<PathBuf>,
    on_progress: F,
}

impl<F: FnMut(ScanProgress, &Inventory)> Scanner<'_, F> {
    fn stopped(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    fn issue(&mut self, path: Option<PathBuf>, kind: &str, message: impl Into<String>) {
        self.inventory.issues.push(Issue::new(path, kind, message));
    }

    fn report(&mut self, force: bool) {
        if !force && self.last_report.elapsed() < Duration::from_millis(150) {
            return;
        }
        self.last_report = Instant::now();
        self.progress.installations_found = self.inventory.installations.len();
        self.progress.instances_found = self.inventory.instances.len();
        self.progress.elapsed_ms = self.started.elapsed().as_millis().min(u64::MAX as u128) as u64;
        (self.on_progress)(self.progress.clone(), &self.inventory);
    }

    fn regular_directory(&mut self, path: &Path) -> Option<PathBuf> {
        if !self.allowed_location(path) {
            return None;
        }
        match fs::symlink_metadata(path) {
            Ok(metadata) if linked(&metadata) => {
                self.issue(
                    Some(path.into()),
                    "linkedPath",
                    "Linked or reparse-point directory was not followed. Select its real target separately.",
                );
                None
            }
            Ok(metadata) if metadata.is_dir() => match fs::canonicalize(path) {
                Ok(canonical) => {
                    self.directory_aliases
                        .insert(path_key(path), path_key(&canonical));
                    Some(canonical)
                }
                Err(error) => {
                    self.issue(Some(path.into()), "unreadable", error.to_string());
                    None
                }
            },
            Ok(_) => {
                self.issue(
                    Some(path.into()),
                    "notDirectory",
                    "Search location is not a directory.",
                );
                None
            }
            Err(error) => {
                self.issue(
                    Some(path.into()),
                    if error.kind() == io::ErrorKind::NotFound {
                        "unavailable"
                    } else {
                        "unreadable"
                    },
                    error.to_string(),
                );
                None
            }
        }
    }

    fn allowed_location(&mut self, path: &Path) -> bool {
        #[cfg(windows)]
        if windows::is_network_path(path) {
            let key = path_key(path);
            if !self.explicit_roots.iter().any(|root| {
                let root = path_key(root);
                key == root
                    || key
                        .strip_prefix(&root)
                        .is_some_and(|tail| tail.starts_with('\\'))
            }) {
                self.issue(
                    Some(path.into()),
                    "networkDeferred",
                    "Network metadata was not accessed automatically. Select this folder or drive explicitly for a deep scan.",
                );
                return false;
            }
        }
        #[cfg(not(windows))]
        let _ = path;
        true
    }

    fn record_directory_source(&mut self, directory: &Path, source: &str) {
        if let Some(&index) = self
            .installation_indexes
            .get(&identity("application", directory))
        {
            add_source(&mut self.inventory.installations[index].sources, source);
        }
        if let Some(&index) = self.instance_directories.get(&path_key(directory)) {
            add_source(&mut self.inventory.instances[index].sources, source);
        }
    }

    fn walk(&mut self, root: &Path, depth: Option<usize>, source: &str) {
        let Some(root) = self.regular_directory(root) else {
            return;
        };
        let mut pending = vec![(root, depth)];
        while let Some((directory, remaining)) = pending.pop() {
            if self.stopped() {
                break;
            }
            let key = path_key(&directory);
            self.record_directory_source(&directory, source);
            if self.walked.contains(&(key.clone(), None))
                || !self.walked.insert((key.clone(), remaining))
            {
                continue;
            }
            self.progress.current_path = Some(directory.clone());
            self.progress.directories_visited += 1;
            let entries = match fs::read_dir(&directory) {
                Ok(entries) => entries,
                Err(error) => {
                    self.issue(Some(directory.clone()), "unreadable", error.to_string());
                    self.report(false);
                    continue;
                }
            };
            let inspect = self.inspected.insert(key);
            let mut executable = None;
            let mut config = None;
            for entry in entries {
                if self.stopped() {
                    break;
                }
                let entry = match entry {
                    Ok(entry) => entry,
                    Err(error) => {
                        self.issue(
                            Some(directory.clone()),
                            "unreadableEntry",
                            error.to_string(),
                        );
                        continue;
                    }
                };
                let path = entry.path();
                let kind = match entry.file_type() {
                    Ok(kind) => kind,
                    Err(error) => {
                        self.issue(Some(path), "unreadableEntry", error.to_string());
                        continue;
                    }
                };
                if inspect {
                    let name = entry.file_name();
                    if name
                        .to_string_lossy()
                        .eq_ignore_ascii_case("ModOrganizer.exe")
                    {
                        executable = Some(path.clone());
                    } else if name
                        .to_string_lossy()
                        .eq_ignore_ascii_case("ModOrganizer.ini")
                    {
                        config = Some(path.clone());
                    }
                }
                if kind.is_dir() || kind.is_symlink() {
                    if remaining == Some(0) {
                        continue;
                    }
                    let Some(path) = self.regular_directory(&path) else {
                        continue;
                    };
                    pending.push((path, remaining.map(|value| value - 1)));
                }
            }
            // Keep a candidate found before cancellation; do not convert an
            // interrupted directory into a completed search.
            if let Some(path) = executable {
                self.inspect_application(&path, source);
            }
            if let Some(path) = config {
                self.inspect_instance(&path, source);
            }
            self.report(false);
        }
    }

    fn inspect_application(&mut self, executable: &Path, source: &str) {
        let executable = match regular_file(executable) {
            Ok(path) => path,
            Err(error) => {
                self.issue(
                    Some(executable.into()),
                    "invalidApplication",
                    error.to_string(),
                );
                return;
            }
        };
        let Some(directory) = executable.parent() else {
            return;
        };
        let id = identity("application", directory);
        if let Some(&index) = self.installation_indexes.get(&id) {
            add_source(&mut self.inventory.installations[index].sources, source);
            return;
        }
        let architecture = match executable_architecture(&executable) {
            Ok(value) => value,
            Err(error) => {
                self.issue(
                    Some(executable.clone()),
                    "unreadableExecutable",
                    error.to_string(),
                );
                None
            }
        };
        if architecture.is_none() {
            self.issue(
                Some(executable.clone()),
                "unknownExecutable",
                "A file named ModOrganizer.exe was found, but a supported Windows executable header could not be established.",
            );
        }
        #[cfg(windows)]
        let version = windows::file_version(&executable);
        #[cfg(not(windows))]
        let version = None;
        let portable_locked = fs::symlink_metadata(directory.join("portable.txt")).is_ok();
        let plugin = self.plugin_observation(directory);
        self.installation_indexes
            .insert(id.clone(), self.inventory.installations.len());
        self.inventory.installations.push(Installation {
            id,
            directory: directory.into(),
            executable,
            version,
            architecture,
            portable_locked,
            plugin,
            running_pids: Vec::new(),
            sources: vec![source.into()],
        });
        self.report(true);
    }

    fn plugin_observation(&mut self, application: &Path) -> PluginObservation {
        let directory = application.join("plugins").join("mo2_update_arsenal");
        match fs::symlink_metadata(&directory) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return PluginObservation {
                    version: None,
                    status: "missing".into(),
                };
            }
            Ok(metadata) if metadata.is_dir() && !linked(&metadata) => {}
            Ok(_) => {
                self.issue(
                    Some(directory),
                    "invalidPlugin",
                    "Installed plugin path is linked or not a directory.",
                );
                return PluginObservation {
                    version: None,
                    status: "invalid".into(),
                };
            }
            Err(error) => {
                self.issue(Some(directory), "unreadablePlugin", error.to_string());
                return PluginObservation {
                    version: None,
                    status: "unknown".into(),
                };
            }
        }
        let path = directory.join("version.py");
        let bytes = match read_metadata(&path, VERSION_LIMIT) {
            Ok(bytes) => bytes,
            Err(error) => {
                let status = if error.kind() == io::ErrorKind::PermissionDenied {
                    "unknown"
                } else {
                    "invalid"
                };
                self.issue(Some(path), "pluginVersion", error.to_string());
                return PluginObservation {
                    version: None,
                    status: status.into(),
                };
            }
        };
        let version = decode_text(&bytes)
            .ok()
            .and_then(|text| plugin_version(&text));
        let status = match version.as_deref() {
            Some(env!("CARGO_PKG_VERSION")) => "matching",
            Some(_) => "different",
            None => "invalid",
        };
        if version.is_none() {
            self.issue(
                Some(path),
                "pluginVersion",
                "The installed plugin has no single valid VERSION assignment.",
            );
        }
        PluginObservation {
            version,
            status: status.into(),
        }
    }

    fn inspect_instance(&mut self, config: &Path, source: &str) {
        if !self.allowed_location(config) {
            return;
        }
        let config = match regular_file(config) {
            Ok(path) => path,
            Err(error) => {
                self.issue(Some(config.into()), "invalidInstance", error.to_string());
                return;
            }
        };
        let Some(directory) = config.parent() else {
            return;
        };
        let id = identity("instance", &config);
        if let Some(&index) = self.instance_indexes.get(&id) {
            add_source(&mut self.inventory.instances[index].sources, source);
            return;
        }
        let mut instance = Instance {
            id: id.clone(),
            name: directory
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
            kind: if directory
                .parent()
                .is_some_and(|parent| self.global_roots.contains(&path_key(parent)))
            {
                "global"
            } else {
                "external"
            }
            .into(),
            config_path: config.clone(),
            base_directory: None,
            game_name: None,
            game_path: None,
            selected_profile: None,
            profiles_directory: None,
            mods_directory: None,
            profiles: Vec::new(),
            sources: vec![source.into()],
        };
        let settings = read_metadata(&config, INI_LIMIT)
            .map_err(|error| error.to_string())
            .and_then(|bytes| decode_text(&bytes).map_err(|error| error.to_string()))
            .and_then(|text| Ini::parse(&text).map_err(|error| error.to_string()));
        match settings {
            Ok(settings) => {
                instance.game_name = nonempty(settings.general("gameName"));
                instance.selected_profile = nonempty(settings.general("selected_profile"));
                if let Some(raw) = nonempty(settings.general("gamePath")) {
                    match configured_path(&raw, directory, directory) {
                        Ok(path) => instance.game_path = Some(path),
                        Err(error) => self.issue(Some(config.clone()), "gamePath", error),
                    }
                }
                let base = configured_path(
                    settings.get("Settings", "base_directory").filter(|s| !s.is_empty()).unwrap_or("."),
                    directory,
                    directory,
                );
                match base {
                    Ok(base) => {
                        instance.base_directory = Some(base.clone());
                        for (field, default, profiles) in [
                            ("profiles_directory", "%BASE_DIR%/profiles", true),
                            ("mod_directory", "%BASE_DIR%/mods", false),
                        ] {
                            let value = configured_path(
                                settings.get("Settings", field).filter(|s| !s.is_empty()).unwrap_or(default),
                                &base,
                                directory,
                            );
                            match value {
                                Ok(path) if profiles => {
                                    instance.profiles = self.profiles(&path, instance.selected_profile.as_deref());
                                    instance.profiles_directory = Some(path);
                                }
                                Ok(path) => instance.mods_directory = Some(path),
                                Err(error) => self.issue(Some(config.clone()), "configuredPath", error),
                            }
                        }
                    }
                    Err(error) => self.issue(Some(config.clone()), "baseDirectory", error),
                }
            }
            Err(error) => self.issue(
                Some(config),
                "invalidInstance",
                format!("Instance metadata could not be read; the discovered instance is retained: {error}"),
            ),
        }
        self.instance_indexes
            .insert(id, self.inventory.instances.len());
        if let Some(directory) = instance.config_path.parent() {
            self.instance_directories
                .insert(path_key(directory), self.inventory.instances.len());
        }
        self.inventory.instances.push(instance);
        self.report(true);
    }

    fn profiles(&mut self, directory: &Path, selected: Option<&str>) -> Vec<Profile> {
        let Some(directory) = self.regular_directory(directory) else {
            return Vec::new();
        };
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) => {
                self.issue(Some(directory), "unreadableProfiles", error.to_string());
                return Vec::new();
            }
        };
        let mut profiles = Vec::new();
        for entry in entries {
            if self.stopped() {
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) => {
                    self.issue(
                        Some(directory.clone()),
                        "unreadableProfile",
                        error.to_string(),
                    );
                    continue;
                }
            };
            let kind = match entry.file_type() {
                Ok(kind) => kind,
                Err(error) => {
                    self.issue(Some(entry.path()), "unreadableProfile", error.to_string());
                    continue;
                }
            };
            if !kind.is_dir() && !kind.is_symlink() {
                continue;
            }
            let Some(path) = self.regular_directory(&entry.path()) else {
                continue;
            };
            let name = entry.file_name().to_string_lossy().into_owned();
            let modlist_path = path.join("modlist.txt");
            if let Err(error) = regular_file(&modlist_path) {
                self.issue(
                    Some(modlist_path.clone()),
                    "profileModlist",
                    error.to_string(),
                );
            }
            profiles.push(Profile {
                selected: selected.is_some_and(|value| names_equal(value, &name)),
                name,
                path,
                modlist_path,
            });
        }
        profiles.sort_by(|a, b| a.name.cmp(&b.name).then(a.path.cmp(&b.path)));
        profiles
    }

    fn associate(&mut self, seeds: &KnownSeeds) {
        let mut pairs = HashMap::<(String, String), Association>::new();
        // Resolve only directories already inspected by the scanner. Besides
        // avoiding repeated filesystem work for every application/process
        // pair, this keeps association from probing deferred network paths.
        let mut process_directories = HashMap::<String, Vec<u32>>::new();
        for process in &seeds.processes {
            if let Some(directory) = process.executable.as_ref().and_then(|p| p.parent())
                && let Some(key) = self.directory_aliases.get(&path_key(directory))
            {
                process_directories
                    .entry(key.clone())
                    .or_default()
                    .push(process.pid);
            }
        }
        let applications: HashMap<_, _> = self
            .inventory
            .installations
            .iter()
            .map(|app| (path_key(&app.directory), app.id.clone()))
            .collect();
        for instance in &mut self.inventory.instances {
            if let Some(directory) = instance.config_path.parent()
                && let Some(application_id) = applications.get(&path_key(directory))
            {
                instance.kind = "portable".into();
                pairs.insert(
                    (application_id.clone(), instance.id.clone()),
                    Association {
                        installation_id: application_id.clone(),
                        instance_id: instance.id.clone(),
                        kind: "portable".into(),
                        evidence: "ModOrganizer.ini is beside this application's ModOrganizer.exe."
                            .into(),
                    },
                );
            }
        }
        for app in &mut self.inventory.installations {
            app.running_pids = process_directories
                .remove(&path_key(&app.directory))
                .unwrap_or_default();
            app.running_pids.sort_unstable();
            app.running_pids.dedup();
            if !app.portable_locked {
                for instance in self
                    .inventory
                    .instances
                    .iter()
                    .filter(|instance| instance.kind == "global")
                {
                    pairs.entry((app.id.clone(), instance.id.clone())).or_insert_with(|| Association {
                        installation_id: app.id.clone(),
                        instance_id: instance.id.clone(),
                        kind: "availableGlobal".into(),
                        evidence: "Shared current-user global instance available to unlocked MO2 applications; game-plugin compatibility has not been executed.".into(),
                    });
                }
            }
        }
        for shortcut in &seeds.shortcuts {
            let Some(name) = &shortcut.instance_name else {
                continue;
            };
            let Some(directory_key) = shortcut
                .executable
                .parent()
                .and_then(|p| self.directory_aliases.get(&path_key(p)))
            else {
                continue;
            };
            let Some(app) = self
                .inventory
                .installations
                .iter()
                .find(|app| path_key(&app.directory) == *directory_key)
            else {
                continue;
            };
            if app.portable_locked && !name.is_empty() {
                continue;
            }
            let matches: Vec<_> = self
                .inventory
                .instances
                .iter()
                .filter(|instance| {
                    if name.is_empty() {
                        instance.kind == "portable"
                            && instance
                                .config_path
                                .parent()
                                .is_some_and(|p| path_key(p) == *directory_key)
                    } else {
                        instance.kind == "global" && names_equal(&instance.name, name)
                    }
                })
                .collect();
            if matches.len() == 1 {
                let instance = matches[0];
                let key = (app.id.clone(), instance.id.clone());
                if pairs
                    .get(&key)
                    .is_none_or(|association| association.kind != "portable")
                {
                    pairs.insert(
                        key,
                        Association {
                            installation_id: app.id.clone(),
                            instance_id: instance.id.clone(),
                            kind: "shortcut".into(),
                            evidence: format!(
                                "{} explicitly selects this MO2 instance.",
                                shortcut.path.display()
                            ),
                        },
                    );
                }
            } else if matches.len() > 1 {
                self.inventory.issues.push(Issue::new(
                    Some(shortcut.path.clone()),
                    "ambiguousShortcut",
                    "Several global instance configurations match this shortcut; no exclusive association was inferred.",
                ));
            }
        }
        self.inventory.associations = pairs.into_values().collect();
    }
}

pub(crate) fn linked(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn regular_file(path: &Path) -> io::Result<PathBuf> {
    let metadata = fs::symlink_metadata(path)?;
    if linked(&metadata) || !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Linked or non-regular file was not followed.",
        ));
    }
    fs::canonicalize(path)
}

fn read_metadata(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    regular_file(path)?;
    let file = fs::File::open(path)?;
    if file.metadata()?.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Metadata exceeds the documented read bound; this location needs review.",
        ));
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Metadata grew beyond the read bound.",
        ));
    }
    Ok(bytes)
}

fn plugin_version(text: &str) -> Option<String> {
    let mut found = None;
    for line in text.lines() {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if key.trim() != "VERSION" {
            continue;
        }
        if found.is_some() {
            return None;
        }
        let value = value.trim();
        let quote = value.chars().next()?;
        if !matches!(quote, '\'' | '"') || !value.ends_with(quote) || value.len() < 2 {
            return None;
        }
        let value = &value[1..value.len() - 1];
        let components: Vec<_> = value.split('.').collect();
        if components.len() != 3
            || components
                .iter()
                .any(|part| part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()))
        {
            return None;
        }
        found = Some(value.to_owned());
    }
    found
}

fn configured_path(raw: &str, base: &Path, instance: &Path) -> Result<PathBuf, String> {
    if raw
        .chars()
        .any(|character| matches!(character, '\0' | '\r' | '\n'))
    {
        return Err("Configured directory contains invalid control characters.".into());
    }
    let expanded = raw.replace("%BASE_DIR%", &base.to_string_lossy());
    if expanded.contains('%') {
        return Err(
            "Unsupported directory placeholder; select the exact configured location.".into(),
        );
    }
    #[cfg(windows)]
    let expanded = expanded.replace('/', "\\");
    #[cfg(not(windows))]
    let expanded = expanded.replace('\\', "/");
    let path = PathBuf::from(expanded);
    let path = if path.is_absolute() {
        path
    } else {
        instance.join(path)
    };
    Ok(path)
}

fn nonempty(value: &str) -> Option<String> {
    (!value.is_empty()).then(|| value.to_owned())
}

fn add_source(sources: &mut Vec<String>, source: &str) {
    if !sources.iter().any(|value| value == source) {
        sources.push(source.into());
    }
}

fn names_equal(left: &str, right: &str) -> bool {
    #[cfg(windows)]
    {
        left.to_lowercase() == right.to_lowercase()
    }
    #[cfg(not(windows))]
    {
        left == right
    }
}

pub(crate) fn path_key(path: &Path) -> String {
    #[cfg(windows)]
    {
        let text = path.to_string_lossy().replace('/', "\\");
        let text = text
            .strip_prefix("\\\\?\\UNC\\")
            .map(|rest| format!("\\\\{rest}"))
            .unwrap_or_else(|| text.strip_prefix("\\\\?\\").unwrap_or(&text).to_owned());
        text.trim_end_matches('\\').to_lowercase()
    }
    #[cfg(not(windows))]
    {
        path.to_string_lossy().into_owned()
    }
}

fn identity(kind: &str, path: &Path) -> String {
    let mut hash = Sha256::new();
    hash.update(kind.as_bytes());
    hash.update([0]);
    hash.update(path_key(path).as_bytes());
    format!("{kind}-{:x}", hash.finalize())
}

fn executable_architecture(path: &Path) -> io::Result<Option<String>> {
    let mut file = fs::File::open(path)?;
    let length = file.metadata()?.len();
    if length < 64 {
        return Ok(None);
    }
    let mut header = [0_u8; 64];
    file.read_exact(&mut header)?;
    if &header[..2] != b"MZ" {
        return Ok(None);
    }
    let offset = u32::from_le_bytes(header[60..64].try_into().expect("four bytes")) as u64;
    if !(64..=16 * 1024 * 1024).contains(&offset) || offset.saturating_add(6) > length {
        return Ok(None);
    }
    file.seek(SeekFrom::Start(offset))?;
    let mut pe = [0_u8; 6];
    file.read_exact(&mut pe)?;
    if &pe[..4] != b"PE\0\0" {
        return Ok(None);
    }
    Ok(match u16::from_le_bytes([pe[4], pe[5]]) {
        0x8664 => Some("x64".into()),
        0x014c => Some("x86".into()),
        0xaa64 => Some("arm64".into()),
        _ => None,
    })
}

#[cfg(test)]
mod tests;
