//! Read-only Bethesda plugin facts and conditional ESL eligibility.
//!
//! A flag is not conversion history. A bounded record scan is not an xEdit
//! conflict, external-reference, save, asset, or runtime compatibility audit.
//! Format rules were checked against xEdit's release notes and LOOT/esplugin's
//! public format behavior; this is an independent parser, not copied code.
//! https://github.com/TES5Edit/TES5Edit/releases
//! https://github.com/MaskPlague/ESLifier
//! https://github.com/Ortham/esplugin

mod provenance;
mod records;

use crate::ini::decode_text;
use crate::{CoreError, io_error};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const MAX_MODS: usize = 100_000;
const MAX_PLUGINS: usize = 20_000;
const MAX_ROOT_ENTRIES: usize = 100_000;
const CACHE_LIMIT: usize = 20_000;
const TEXT_LIMIT: usize = 4_000_000;
const DEEP_FILE_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const DEEP_PROFILE_LIMIT: u64 = 16 * 1024 * 1024 * 1024;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginAuditRequest {
    pub game: String,
    #[serde(default)]
    pub game_runtime: Option<String>,
    #[serde(default)]
    pub profile_id: Option<String>,
    #[serde(default)]
    pub profile_name: Option<String>,
    #[serde(default)]
    pub mods: Vec<PluginMod>,
    #[serde(default)]
    pub plugins_path: Option<PathBuf>,
    #[serde(default)]
    pub loadorder_path: Option<PathBuf>,
    #[serde(default)]
    pub overwrite_path: Option<PathBuf>,
    #[serde(default)]
    pub game_data_path: Option<PathBuf>,
    #[serde(default)]
    pub plugin_states: Option<Vec<PluginState>>,
    #[serde(default)]
    pub eslifier_data_paths: Vec<PathBuf>,
    #[serde(default)]
    pub deep: bool,
    #[serde(default)]
    pub workers: Option<usize>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginMod {
    pub id: String,
    pub name: String,
    pub path: PathBuf,
    pub enabled: bool,
    pub priority: i64,
    #[serde(default)]
    pub installed_version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginState {
    pub name: String,
    pub active: bool,
    #[serde(default)]
    pub load_order: Option<i64>,
    #[serde(default)]
    pub origin: Option<String>,
    #[serde(default)]
    pub path: Option<PathBuf>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginAuditStats {
    pub elapsed_ms: f64,
    pub files_read: usize,
    pub bytes_read: u64,
    pub directories_read: usize,
    pub header_cache_hits: usize,
    pub directory_cache_hits: usize,
    pub plugin_copies: usize,
    pub winning_plugins: usize,
    pub deep_scanned: usize,
    pub workers: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginAuditResult {
    pub schema_version: u32,
    pub profile_id: Option<String>,
    pub checked_at: String,
    pub depth: String,
    pub inventory_complete: bool,
    pub activation_source: String,
    pub mods: BTreeMap<String, ModPluginAudit>,
    pub plugins: Vec<WinningPlugin>,
    pub stats: PluginAuditStats,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModPluginAudit {
    pub status: String,
    pub plugins: Vec<PluginCopy>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginCopy {
    pub name: String,
    pub owner_mod_id: String,
    pub owner_mod_name: String,
    pub path: PathBuf,
    pub priority: i64,
    pub installed_version: Option<String>,
    pub enabled: bool,
    pub ghosted: bool,
    pub winner: bool,
    pub winner_known: bool,
    pub winner_by: String,
    pub winner_mod_id: Option<String>,
    pub winner_light: Option<bool>,
    pub light_override: bool,
    pub active: Option<bool>,
    pub load_order: Option<i64>,
    pub header: Option<PluginHeader>,
    pub runtime_support: String,
    pub eligibility: Eligibility,
    pub records: Option<RecordSummary>,
    pub fingerprint: Option<FileFingerprint>,
    pub master_issues: Vec<MasterIssue>,
    pub unresolved_masters: Vec<String>,
    pub provenance: ConversionProvenance,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WinningPlugin {
    #[serde(flatten)]
    pub copy: PluginCopy,
    pub source_copies: Vec<PluginCopy>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginHeader {
    pub flags: u32,
    pub version: f32,
    pub record_header_version: u16,
    pub masters: Vec<String>,
    pub light_flag: bool,
    pub light_by_extension: bool,
    pub light_effective: bool,
    pub master_flag: bool,
    pub medium_flag: bool,
    pub update_flag: bool,
    pub declared_record_and_group_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Eligibility {
    pub status: String,
    pub reason_codes: Vec<String>,
    pub notes: Vec<String>,
    pub save_safety: String,
}
impl Eligibility {
    fn new(status: &str, reason: &str, note: &str) -> Self {
        Self {
            status: status.into(),
            reason_codes: vec![reason.into()],
            notes: vec![note.into()],
            save_safety: "not-assessed".into(),
        }
    }
    fn add(&mut self, reason: &str, note: &str) {
        if !self.reason_codes.iter().any(|r| r == reason) {
            self.reason_codes.push(reason.into());
            self.notes.push(note.into());
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecordSummary {
    pub scope: String,
    pub total_records: usize,
    pub groups: usize,
    pub new_records: usize,
    pub override_or_injected_records: usize,
    pub minimum_new_local_id: Option<u32>,
    pub maximum_new_local_id: Option<u32>,
    pub new_ids_below_0800: usize,
    pub new_ids_above_0fff: usize,
    pub zero_new_local_ids: usize,
    pub null_form_ids: usize,
    pub invalid_master_indices: usize,
    pub duplicate_form_ids: usize,
    pub deleted_records: usize,
    pub compressed_records: usize,
    pub new_record_types: BTreeMap<String, usize>,
    pub scan_complete: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileFingerprint {
    pub size: u64,
    pub modified_ns: Option<String>,
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MasterIssue {
    pub name: String,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversionProvenance {
    pub state: String,
    pub tool: Option<String>,
    pub evidence_path: Option<PathBuf>,
    pub original_path: Option<PathBuf>,
    pub output_path: Option<PathBuf>,
    pub original_sha256: Option<String>,
    pub output_sha256: Option<String>,
    pub reason: String,
}
impl Default for ConversionProvenance {
    fn default() -> Self {
        Self {
            state: "unknown".into(),
            tool: None,
            evidence_path: None,
            original_path: None,
            output_path: None,
            original_sha256: None,
            output_sha256: None,
            reason: "A current light flag does not establish conversion history.".into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Stamp {
    size: u64,
    modified: Option<SystemTime>,
    created: Option<SystemTime>,
    #[cfg(unix)]
    identity: (u64, u64, i64, i64),
}
impl Stamp {
    fn new(meta: &Metadata) -> Self {
        #[cfg(unix)]
        use std::os::unix::fs::MetadataExt;
        Self {
            size: meta.len(),
            modified: meta.modified().ok(),
            created: meta.created().ok(),
            #[cfg(unix)]
            identity: (meta.dev(), meta.ino(), meta.ctime(), meta.ctime_nsec()),
        }
    }
    fn fingerprint(&self) -> FileFingerprint {
        FileFingerprint {
            size: self.size,
            modified_ns: self
                .modified
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_nanos().to_string()),
            sha256: None,
        }
    }
}

#[derive(Clone)]
struct CachedHeader {
    game: String,
    digest: [u8; 32],
    header: PluginHeader,
}
#[derive(Clone)]
struct CachedDirectory {
    files: Vec<PathBuf>,
}

/// Reuse parsed headers only after rereading their bounded TES4 bytes. Directory
/// membership is re-enumerated; filesystem timestamps are never freshness proof.
/// Deep audits always hash fresh bytes.
#[derive(Default)]
pub struct PluginAnalyzer {
    headers: Mutex<HashMap<PathBuf, CachedHeader>>,
    directories: Mutex<HashMap<PathBuf, CachedDirectory>>,
    pool: Mutex<Option<Arc<rayon::ThreadPool>>>,
}

pub fn audit_plugins(request: &PluginAuditRequest) -> Result<PluginAuditResult, CoreError> {
    PluginAnalyzer::new().audit(request)
}

impl PluginAnalyzer {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn clear_cache(&self) {
        self.headers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clear();
        self.directories
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clear();
    }

    pub fn audit(&self, request: &PluginAuditRequest) -> Result<PluginAuditResult, CoreError> {
        self.audit_in_pool(request, None)
    }

    /// Profile imports already own a bounded worker pool. Reuse that pool for
    /// their plugin phase instead of retaining a second identical set of idle
    /// threads for the lifetime of the desktop process.
    pub(crate) fn audit_in_pool(
        &self,
        request: &PluginAuditRequest,
        existing_pool: Option<Arc<rayon::ThreadPool>>,
    ) -> Result<PluginAuditResult, CoreError> {
        let started = Instant::now();
        let workers = request.workers.unwrap_or(8);
        if !(1..=32).contains(&workers)
            || request.mods.len() > MAX_MODS
            || request.eslifier_data_paths.len() > 16
        {
            return Err(CoreError::Invalid("Plugin audit requires 1..32 workers, at most 100000 mods and at most 16 provenance folders".into()));
        }
        let mut ids = HashSet::new();
        for m in &request.mods {
            if m.id.is_empty() || !ids.insert(&m.id) {
                return Err(CoreError::Invalid(
                    "Plugin audit mod IDs must be nonempty and unique".into(),
                ));
            }
        }
        let game = crate::importer::game_slug(&request.game);
        let mut stats = PluginAuditStats {
            workers,
            ..Default::default()
        };
        let mut warnings = Vec::new();
        let (states, activation_source, _activation_known) =
            read_states(request, &game, &mut stats, &mut warnings)?;
        let mut roots = request.mods.clone();
        add_root(
            &mut roots,
            request.game_data_path.as_deref(),
            "__game_data__",
            "Game Data",
            i64::MIN,
        );
        add_root(
            &mut roots,
            request.overwrite_path.as_deref(),
            "__overwrite__",
            "Overwrite",
            i64::MAX,
        );
        let pool = match existing_pool {
            Some(pool) if pool.current_num_threads() == workers => pool,
            Some(_) => {
                return Err(CoreError::Invalid(
                    "Plugin audit worker pool does not match its requested bound".into(),
                ));
            }
            None => self.pool(workers)?,
        };
        let listings: Vec<_> =
            pool.install(|| roots.par_iter().map(|m| self.list_plugins(m)).collect());
        let mut complete = true;
        let mut game_data_scanned = false;
        let mut mods = BTreeMap::new();
        let mut seeds = Vec::new();
        for (owner, listing) in roots.iter().zip(listings) {
            let mut audit = ModPluginAudit {
                status: "no-plugins".into(),
                ..Default::default()
            };
            match listing {
                Ok((files, cache_hit)) => {
                    stats.directories_read += 1;
                    if cache_hit {
                        stats.directory_cache_hits += 1;
                    }
                    if request
                        .game_data_path
                        .as_deref()
                        .is_some_and(|p| path_key(p) == path_key(&owner.path))
                    {
                        game_data_scanned = true;
                    }
                    for path in files {
                        seeds.push((owner.clone(), path));
                        if seeds.len() > MAX_PLUGINS {
                            return Err(CoreError::Invalid("Plugin audit exceeds the 20000-copy limit; nothing was silently truncated".into()));
                        }
                    }
                }
                Err(error) => {
                    complete = false;
                    audit.status = "unknown".into();
                    audit.warnings.push(error.to_string());
                    warnings.push(format!("{}: {error}", owner.name));
                }
            }
            mods.insert(owner.id.clone(), audit);
        }
        let mut deep_bytes = 0_u64;
        if request.deep {
            for (_, path) in &seeds {
                if let Ok(meta) = fs::symlink_metadata(path) {
                    deep_bytes = deep_bytes.saturating_add(meta.len());
                }
            }
            if deep_bytes > DEEP_PROFILE_LIMIT {
                return Err(CoreError::Invalid(
                    "Deep audit exceeds the 16 GiB input limit; select a smaller group of mods"
                        .into(),
                ));
            }
        }
        let scans: Vec<_> = pool.install(|| {
            seeds
                .par_iter()
                .map(|(owner, path)| self.scan_copy(owner, path, request, &game))
                .collect()
        });
        let mut by_name: BTreeMap<String, Vec<PluginCopy>> = BTreeMap::new();
        for (mut copy, scan_stats, stamp) in scans {
            stats.files_read += scan_stats.files_read;
            stats.bytes_read += scan_stats.bytes_read;
            stats.header_cache_hits += scan_stats.header_cache_hits;
            stats.deep_scanned += scan_stats.deep_scanned;
            // A large audit can take time. Recheck earlier files after every
            // worker has finished, before linking their hashes to provenance.
            if request.deep
                && copy
                    .fingerprint
                    .as_ref()
                    .is_some_and(|f| f.sha256.is_some())
            {
                let unchanged = stamp.as_ref().is_some_and(|stamp| {
                    fs::symlink_metadata(&copy.path)
                        .is_ok_and(|m| !is_link(&m) && Stamp::new(&m) == *stamp)
                });
                if !unchanged {
                    if let Some(fingerprint) = copy.fingerprint.as_mut() {
                        fingerprint.sha256 = None;
                    }
                    copy.header = None;
                    copy.records = None;
                    copy.runtime_support = "unknown".into();
                    copy.errors.push("Plugin changed before the complete profile audit finished; refresh the audit.".into());
                    copy.eligibility = Eligibility::new(
                        "unknown",
                        "changed-during-profile-audit",
                        "The profile did not remain stable long enough to use this plugin's record/hash result.",
                    );
                }
            }
            by_name
                .entry(copy.name.to_lowercase())
                .or_default()
                .push(copy);
        }
        stats.plugin_copies = seeds.len();
        for (key, copies) in &mut by_name {
            // A missing text-file entry can be an implicitly enabled base-game
            // master. Only a complete native MO2 state list proves absence.
            resolve_winner(
                copies,
                states.get(key),
                request.plugin_states.is_some(),
                &mut warnings,
            );
        }
        apply_master_facts(
            &mut by_name,
            &states,
            complete && game_data_scanned,
            request.plugin_states.is_some(),
        );
        provenance::apply(request, &mut by_name, &mut stats, &mut warnings);
        let mut plugins = Vec::new();
        for copies in by_name.values_mut() {
            copies.sort_by(|a, b| {
                a.priority
                    .cmp(&b.priority)
                    .then_with(|| a.owner_mod_id.cmp(&b.owner_mod_id))
                    .then_with(|| a.path.cmp(&b.path))
            });
            let selected = copies
                .iter()
                .find(|p| p.winner)
                .or_else(|| copies.last())
                .expect("Each name has at least one scanned copy")
                .clone();
            if selected.winner {
                stats.winning_plugins += 1;
            }
            for copy in copies.iter() {
                mods.entry(copy.owner_mod_id.clone())
                    .or_default()
                    .plugins
                    .push(copy.clone());
            }
            plugins.push(WinningPlugin {
                copy: selected,
                source_copies: copies.clone(),
            });
        }
        for audit in mods.values_mut() {
            audit.plugins.sort_by_key(|p| p.name.to_lowercase());
            if !audit.plugins.is_empty() {
                audit.status = summarize(&audit.plugins);
            }
        }
        stats.elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
        Ok(PluginAuditResult {
            schema_version: 1,
            profile_id: request.profile_id.clone(),
            checked_at: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            depth: if request.deep { "records" } else { "header" }.into(),
            inventory_complete: complete,
            activation_source,
            mods,
            plugins,
            stats,
            warnings,
        })
    }

    fn pool(&self, workers: usize) -> Result<Arc<rayon::ThreadPool>, CoreError> {
        let mut cached = self.pool.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(pool) = cached
            .as_ref()
            .filter(|pool| pool.current_num_threads() == workers)
        {
            return Ok(Arc::clone(pool));
        }
        let pool = Arc::new(
            rayon::ThreadPoolBuilder::new()
                .num_threads(workers)
                .thread_name(|n| format!("modhunter-plugins-{n}"))
                .build()
                .map_err(|e| CoreError::Invalid(format!("Start plugin audit workers: {e}")))?,
        );
        *cached = Some(Arc::clone(&pool));
        Ok(pool)
    }

    fn list_plugins(&self, owner: &PluginMod) -> Result<(Vec<PathBuf>, bool), CoreError> {
        if !owner.path.is_absolute() {
            return Err(CoreError::Invalid(
                "Plugin source folder must be an absolute MO2-owned path".into(),
            ));
        }
        let meta = fs::symlink_metadata(&owner.path)
            .map_err(|e| io_error("Inspect plugin source folder", e))?;
        if is_link(&meta) || !meta.is_dir() {
            return Err(CoreError::Invalid(
                "Linked or non-directory plugin source rejected".into(),
            ));
        }
        let stamp = Stamp::new(&meta);
        // NTFS/FAT timestamp granularity and preserved directory timestamps can
        // hide additions/removals. Re-enumerate this one root; never traverse
        // asset subdirectories or trust metadata as a complete plugin listing.
        let mut files = Vec::new();
        for (i, child) in fs::read_dir(&owner.path)
            .map_err(|e| io_error("Read plugin source folder", e))?
            .enumerate()
        {
            if i >= MAX_ROOT_ENTRIES {
                return Err(CoreError::Invalid(
                    "Plugin source folder exceeds the 100000-entry limit".into(),
                ));
            }
            let child = child.map_err(|e| io_error("Read plugin source entry", e))?;
            if plugin_name(&child.file_name().to_string_lossy()).is_some() {
                files.push(child.path());
            }
        }
        files.sort_by_key(|p| path_key(p));
        let after = fs::symlink_metadata(&owner.path)
            .map_err(|e| io_error("Recheck plugin source folder", e))?;
        if is_link(&after) || Stamp::new(&after) != stamp {
            return Err(CoreError::Invalid(
                "Plugin source folder changed during enumeration; refresh the audit".into(),
            ));
        }
        let mut cache = self.directories.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(cached) = cache.get(&owner.path).filter(|c| c.files == files) {
            return Ok((cached.files.clone(), true));
        }
        if cache.len() >= CACHE_LIMIT {
            cache.clear();
        }
        cache.insert(
            owner.path.clone(),
            CachedDirectory {
                files: files.clone(),
            },
        );
        Ok((files, false))
    }

    fn scan_copy(
        &self,
        owner: &PluginMod,
        path: &Path,
        request: &PluginAuditRequest,
        game: &str,
    ) -> (PluginCopy, PluginAuditStats, Option<Stamp>) {
        let raw_name = path.file_name().unwrap_or_default().to_string_lossy();
        let (name, ghosted) = plugin_name(&raw_name).expect("Only plugin names enter scan_copy");
        let mut stats = PluginAuditStats::default();
        let mut scanned_stamp = None;
        let mut copy = PluginCopy {
            name,
            owner_mod_id: owner.id.clone(),
            owner_mod_name: owner.name.clone(),
            path: path.to_path_buf(),
            priority: owner.priority,
            installed_version: owner.installed_version.clone(),
            enabled: owner.enabled,
            ghosted,
            winner: false,
            winner_known: false,
            winner_by: "unresolved".into(),
            winner_mod_id: None,
            winner_light: None,
            light_override: false,
            active: None,
            load_order: None,
            header: None,
            runtime_support: "unknown".into(),
            eligibility: Eligibility::new(
                "unknown",
                "not-scanned",
                "Plugin bytes have not been read.",
            ),
            records: None,
            fingerprint: None,
            master_issues: Vec::new(),
            unresolved_masters: Vec::new(),
            provenance: ConversionProvenance::default(),
            errors: Vec::new(),
        };
        let read = (|| {
            let (file, stamp) = open_regular(
                path,
                if request.deep {
                    DEEP_FILE_LIMIT
                } else {
                    u64::MAX
                },
            )?;
            scanned_stamp = Some(stamp.clone());
            copy.fingerprint = Some(stamp.fingerprint());
            let cached = (!request.deep)
                .then(|| {
                    self.headers
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .get(path)
                        .filter(|c| c.game == game)
                        .cloned()
                })
                .flatten();
            stats.files_read += 1;
            let parsed = records::scan(
                file,
                &stamp,
                &copy.name,
                game,
                request.deep,
                cached.as_ref().map(|c| (&c.digest, &c.header)),
                &mut stats,
            )?;
            let after =
                fs::symlink_metadata(path).map_err(|e| io_error("Recheck plugin file", e))?;
            if is_link(&after) || Stamp::new(&after) != stamp {
                return Err(CoreError::Invalid(
                    "Plugin changed during analysis; refresh before using the result".into(),
                ));
            }
            if let Some(sha) = parsed.sha256 {
                copy.fingerprint
                    .as_mut()
                    .expect("Fingerprint set above")
                    .sha256 = Some(sha);
            }
            copy.header = Some(parsed.header.clone());
            copy.records = parsed.records;
            let mut cache = self.headers.lock().unwrap_or_else(|e| e.into_inner());
            if cache.len() >= CACHE_LIMIT {
                cache.clear();
            }
            cache.insert(
                path.to_path_buf(),
                CachedHeader {
                    game: game.into(),
                    digest: parsed.header_digest,
                    header: parsed.header,
                },
            );
            Ok::<(), CoreError>(())
        })();
        match read {
            Ok(()) => {
                let (eligibility, runtime) = records::assess(
                    game,
                    request.game_runtime.as_deref(),
                    copy.header
                        .as_ref()
                        .expect("Successful scan provides header"),
                    copy.records.as_ref(),
                );
                copy.eligibility = eligibility;
                copy.runtime_support = runtime;
            }
            Err(error) => {
                copy.errors.push(error.to_string());
                copy.eligibility = Eligibility::new(
                    "unknown",
                    "unreadable-plugin",
                    "The file could not be analyzed completely; no eligibility conclusion is available.",
                );
            }
        }
        (copy, stats, scanned_stamp)
    }
}

fn add_root(roots: &mut Vec<PluginMod>, path: Option<&Path>, id: &str, name: &str, priority: i64) {
    if let Some(path) = path {
        if let Some(existing) = roots
            .iter_mut()
            .find(|m| path_key(&m.path) == path_key(path))
        {
            existing.priority = priority;
            existing.enabled = true;
        } else {
            roots.push(PluginMod {
                id: id.into(),
                name: name.into(),
                path: path.to_path_buf(),
                enabled: true,
                priority,
                installed_version: None,
            });
        }
    }
}

fn plugin_name(name: &str) -> Option<(String, bool)> {
    let lower = name.to_lowercase();
    let ghosted = lower.ends_with(".ghost");
    let base = if ghosted {
        &name[..name.len() - 6]
    } else {
        name
    };
    let lower = base.to_lowercase();
    if !base.is_empty()
        && !base.contains(['/', '\\', ':'])
        && !base.chars().any(char::is_control)
        && [".esp", ".esm", ".esl"]
            .iter()
            .any(|ext| lower.ends_with(ext))
    {
        Some((base.into(), ghosted))
    } else {
        None
    }
}

fn path_key(path: &Path) -> String {
    let text = path.to_string_lossy().replace('\\', "/");
    let text = if let Some(unc) = text.strip_prefix("//?/UNC/") {
        format!("//{unc}")
    } else {
        text.strip_prefix("//?/").unwrap_or(&text).to_owned()
    };
    text.trim_end_matches('/').to_lowercase()
}

fn is_link(meta: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        meta.file_type().is_symlink() || meta.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        meta.file_type().is_symlink()
    }
}

fn open_regular(path: &Path, limit: u64) -> Result<(File, Stamp), CoreError> {
    let before =
        fs::symlink_metadata(path).map_err(|e| io_error("Inspect plugin analysis input", e))?;
    if is_link(&before) || !before.is_file() {
        return Err(CoreError::Invalid(
            "Linked or non-regular plugin analysis input rejected".into(),
        ));
    }
    if before.len() > limit {
        return Err(CoreError::Invalid(format!(
            "Plugin analysis input exceeds the {limit}-byte limit"
        )));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Do not admit writers/deletion while a plugin snapshot is being read.
        // A writer that is already active makes the audit explicitly unknown
        // instead of producing mixed bytes with an unchanged coarse mtime.
        options.custom_flags(0x0020_0000).share_mode(0x0000_0001);
    }
    let file = options
        .open(path)
        .map_err(|e| io_error("Open plugin analysis input", e))?;
    let opened = file
        .metadata()
        .map_err(|e| io_error("Inspect opened plugin analysis input", e))?;
    let stamp = Stamp::new(&before);
    if is_link(&opened) || !opened.is_file() || Stamp::new(&opened) != stamp {
        return Err(CoreError::Invalid(
            "Plugin input changed or became a link during opening".into(),
        ));
    }
    Ok((file, stamp))
}

fn read_small(
    path: &Path,
    limit: usize,
    stats: &mut PluginAuditStats,
) -> Result<Vec<u8>, CoreError> {
    let (file, stamp) = open_regular(path, limit as u64)?;
    let mut bytes = Vec::with_capacity(stamp.size as usize);
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| io_error("Read plugin analysis text", e))?;
    if bytes.len() > limit {
        return Err(CoreError::Invalid(
            "Plugin analysis text grew beyond its read limit".into(),
        ));
    }
    let after =
        fs::symlink_metadata(path).map_err(|e| io_error("Recheck plugin analysis text", e))?;
    if is_link(&after) || Stamp::new(&after) != stamp {
        return Err(CoreError::Invalid(
            "Plugin analysis text changed while reading".into(),
        ));
    }
    stats.files_read += 1;
    stats.bytes_read += bytes.len() as u64;
    Ok(bytes)
}

fn read_states(
    request: &PluginAuditRequest,
    game: &str,
    stats: &mut PluginAuditStats,
    warnings: &mut Vec<String>,
) -> Result<(BTreeMap<String, PluginState>, String, bool), CoreError> {
    if let Some(states) = &request.plugin_states {
        if states.len() > MAX_PLUGINS {
            return Err(CoreError::Invalid(
                "Plugin state list exceeds 20000 entries".into(),
            ));
        }
        let mut result = BTreeMap::new();
        for state in states {
            if plugin_name(&state.name).is_none() {
                return Err(CoreError::Invalid(
                    "Native plugin state contains a path-like or invalid name".into(),
                ));
            }
            if result
                .insert(state.name.to_lowercase(), state.clone())
                .is_some()
            {
                return Err(CoreError::Invalid(
                    "Native plugin states contain duplicate case-insensitive names".into(),
                ));
            }
        }
        return Ok((result, "mo2-native".into(), true));
    }
    let mut states = BTreeMap::new();
    let mut known = false;
    if let Some(path) = &request.plugins_path {
        match read_small(path, TEXT_LIMIT, stats).and_then(|b| decode_text(&b)) {
            Ok(text) => {
                known = true;
                for line in text
                    .trim_start_matches('\u{feff}')
                    .lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty() && !l.starts_with('#'))
                {
                    if states.len() >= MAX_PLUGINS {
                        return Err(CoreError::Invalid(
                            "plugins.txt exceeds 20000 entries".into(),
                        ));
                    }
                    let marked = line.starts_with('*');
                    let name = line.trim_start_matches('*').trim();
                    if plugin_name(name).is_none() {
                        warnings.push("Skipped an invalid plugins.txt entry; activation completeness is unknown.".into());
                        known = false;
                        continue;
                    }
                    let active = marked
                        || !matches!(
                            game,
                            "skyrimspecialedition"
                                | "enderalspecialedition"
                                | "skyrimvr"
                                | "fallout4"
                                | "fallout4vr"
                                | "starfield"
                        );
                    let order = states.len() as i64;
                    let state = PluginState {
                        name: name.into(),
                        active,
                        load_order: Some(order),
                        origin: None,
                        path: None,
                    };
                    if states.insert(name.to_lowercase(), state).is_some() {
                        warnings.push(format!("Duplicate plugins.txt name: {name}"));
                        known = false;
                    }
                }
            }
            Err(error) => warnings.push(format!("Plugin activation could not be read: {error}")),
        }
    }
    if let Some(path) = &request.loadorder_path {
        match read_small(path, TEXT_LIMIT, stats).and_then(|b| decode_text(&b)) {
            Ok(text) => {
                let mut seen = HashSet::new();
                for (order, line) in text
                    .trim_start_matches('\u{feff}')
                    .lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty() && !l.starts_with('#'))
                    .enumerate()
                {
                    if order >= MAX_PLUGINS {
                        return Err(CoreError::Invalid(
                            "loadorder.txt exceeds 20000 entries".into(),
                        ));
                    }
                    let name = line.trim_start_matches('*');
                    if plugin_name(name).is_none() || !seen.insert(name.to_lowercase()) {
                        warnings.push(
                            "Invalid or duplicate loadorder.txt entry; that entry was not applied."
                                .into(),
                        );
                        continue;
                    }
                    if let Some(state) = states.get_mut(&name.to_lowercase()) {
                        state.load_order = Some(order as i64);
                    }
                }
            }
            Err(error) => warnings.push(format!("Plugin order could not be read: {error}")),
        }
    }
    Ok((
        states,
        if known { "plugins.txt" } else { "unknown" }.into(),
        known,
    ))
}

fn resolve_winner(
    copies: &mut [PluginCopy],
    state: Option<&PluginState>,
    activation_known: bool,
    warnings: &mut Vec<String>,
) {
    let mut winner = None;
    let mut resolved = true;
    let mut by = "priority";
    if let Some(native_path) = state.and_then(|s| s.path.as_ref()) {
        by = "mo2-native";
        let matches: Vec<_> = copies
            .iter()
            .enumerate()
            .filter(|(_, c)| !c.ghosted && path_key(&c.path) == path_key(native_path))
            .map(|(i, _)| i)
            .collect();
        if matches.len() == 1 {
            winner = matches.first().copied();
        } else {
            resolved = false;
            warnings.push(format!(
                "MO2's winning path for {} does not match one scanned owned copy.",
                copies[0].name
            ));
        }
    } else {
        let maximum = copies
            .iter()
            .filter(|c| c.enabled && !c.ghosted)
            .map(|c| c.priority)
            .max();
        if let Some(priority) = maximum {
            let matches: Vec<_> = copies
                .iter()
                .enumerate()
                .filter(|(_, c)| c.enabled && !c.ghosted && c.priority == priority)
                .map(|(i, _)| i)
                .collect();
            if matches.len() == 1 {
                winner = matches.first().copied();
            } else {
                resolved = false;
                warnings.push(format!(
                    "Ambiguous equal-priority plugin providers for {}.",
                    copies[0].name
                ));
            }
        }
    }
    let winner_mod = winner.map(|i| copies[i].owner_mod_id.clone());
    let winner_light = winner.and_then(|i| copies[i].header.as_ref().map(|h| h.light_effective));
    for (i, copy) in copies.iter_mut().enumerate() {
        copy.winner = winner == Some(i);
        copy.winner_known = resolved;
        copy.winner_by = if resolved { by } else { "unresolved" }.into();
        copy.winner_mod_id = winner_mod.clone();
        copy.winner_light = winner_light;
        copy.active = if copy.ghosted {
            Some(false)
        } else {
            state
                .map(|s| s.active)
                .or(if activation_known { Some(false) } else { None })
        };
        copy.load_order = state.and_then(|s| s.load_order);
        copy.light_override = copy.enabled
            && !copy.winner
            && winner_light == Some(true)
            && copy.header.as_ref().is_some_and(|h| !h.light_effective);
    }
}

fn apply_master_facts(
    by_name: &mut BTreeMap<String, Vec<PluginCopy>>,
    states: &BTreeMap<String, PluginState>,
    inventory_complete: bool,
    native_states: bool,
) {
    let known: BTreeMap<_, _> = by_name
        .iter()
        .map(|(name, copies)| {
            (
                name.clone(),
                (
                    copies.iter().any(|c| c.winner),
                    copies.iter().all(|c| c.winner_known),
                ),
            )
        })
        .collect();
    for copies in by_name.values_mut() {
        for copy in copies {
            if let Some(header) = &copy.header {
                for master in &header.masters {
                    let key = master.to_lowercase();
                    let issue = match known.get(&key) {
                        Some((false, true)) => Some("inactive"),
                        None if inventory_complete
                            && !(native_states && states.contains_key(&key)) =>
                        {
                            Some("missing")
                        }
                        Some((true, true)) if native_states && !states.contains_key(&key) => {
                            Some("inactive")
                        }
                        _ => match states.get(&key) {
                            Some(s) if !s.active => Some("inactive"),
                            Some(s)
                                if copy.active == Some(true)
                                    && s.load_order
                                        .zip(copy.load_order)
                                        .is_some_and(|(a, b)| a >= b) =>
                            {
                                Some("loads-after")
                            }
                            _ => None,
                        },
                    };
                    if let Some(status) = issue {
                        copy.master_issues.push(MasterIssue {
                            name: master.clone(),
                            status: status.into(),
                        });
                    } else if !known
                        .get(&key)
                        .is_some_and(|(winner, resolved)| *winner && *resolved)
                        || (!native_states && !states.contains_key(&key))
                    {
                        copy.unresolved_masters.push(master.clone());
                    }
                }
            }
            if !copy.unresolved_masters.is_empty() {
                copy.eligibility.add("master-state-unresolved", "Some master files or activation states were not resolved; confirm the full dependency chain in MO2 or xEdit.");
            }
            if !copy.master_issues.is_empty() && copy.active == Some(true) && copy.winner {
                copy.eligibility.status = "blocked".into();
                copy.eligibility.add("master-issues", "Resolve missing, inactive, or late-loading masters before considering plugin changes.");
            }
        }
    }
}

fn summarize(copies: &[PluginCopy]) -> String {
    if copies.iter().any(|c| {
        matches!(
            c.provenance.state.as_str(),
            "source-changed" | "output-changed" | "source-and-output-changed" | "winner-changed"
        )
    }) {
        return "conversion-stale".into();
    }
    if copies.iter().any(|c| c.eligibility.status == "blocked") {
        return "blocked".into();
    }
    if copies
        .iter()
        .any(|c| c.light_override && c.active == Some(true))
    {
        return "light-override".into();
    }
    let first = &copies[0].eligibility.status;
    if copies.iter().all(|c| c.eligibility.status == *first) {
        first.clone()
    } else {
        "mixed".into()
    }
}

#[cfg(test)]
mod worker_pool_tests {
    use super::*;

    #[test]
    fn changing_worker_count_retires_idle_pools_but_preserves_active_work() {
        let engine = PluginAnalyzer::new();
        let active = engine.pool(2).unwrap();
        let retired = Arc::downgrade(&active);
        let next = engine.pool(3).unwrap();
        assert!(Arc::ptr_eq(&next, &engine.pool(3).unwrap()));
        assert_eq!(active.install(rayon::current_num_threads), 2);
        assert_eq!(next.install(rayon::current_num_threads), 3);
        drop(active);
        assert!(retired.upgrade().is_none());
    }

    #[test]
    fn imported_audits_reuse_the_caller_pool_without_retaining_another() {
        let engine = PluginAnalyzer::new();
        let shared = Arc::new(
            rayon::ThreadPoolBuilder::new()
                .num_threads(2)
                .build()
                .unwrap(),
        );
        let request = PluginAuditRequest {
            workers: Some(2),
            ..Default::default()
        };
        let result = engine
            .audit_in_pool(&request, Some(Arc::clone(&shared)))
            .unwrap();
        assert_eq!(result.stats.workers, 2);
        assert!(engine.pool.lock().unwrap().is_none());
        assert_eq!(Arc::strong_count(&shared), 1);
        let wrong = PluginAuditRequest {
            workers: Some(3),
            ..request
        };
        assert!(engine.audit_in_pool(&wrong, Some(shared)).is_err());
    }
}
