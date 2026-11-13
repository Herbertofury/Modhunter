use crate::ini::{Ini, decode_text};
use crate::plugin_analysis::{PluginAnalyzer, PluginAuditRequest, PluginMod};
use crate::{CancellationCheck, CoreError, check_cancelled, io_error};
use chrono::{SecondsFormat, Utc};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime};
use url::{Host, Url};

const META_LIMIT: usize = 256_000;
const MODLIST_LIMIT: usize = 12_000_000;
const MAX_ENTRIES: usize = 100_000;
const CACHE_LIMIT: usize = 50_000;
const COLORS: [&str; 5] = ["mint", "gold", "sky", "rose", "green"];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportRequest {
    pub modlist_path: PathBuf,
    #[serde(default)]
    pub ini_path: Option<PathBuf>,
    #[serde(default)]
    pub mods_path: Option<PathBuf>,
    #[serde(default)]
    pub workers: Option<usize>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportStats {
    pub elapsed_ms: f64,
    pub files_read: usize,
    pub bytes_read: usize,
    pub metadata_cache_hits: usize,
    pub directories_read: usize,
    pub workers: usize,
    pub mods: usize,
    pub foreign_mods: usize,
    pub plugin_copies: usize,
    pub plugin_header_cache_hits: usize,
    pub warnings: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportResult {
    pub catalog: Value,
    pub stats: ImportStats,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileStamp {
    len: u64,
    modified: Option<SystemTime>,
    created: Option<SystemTime>,
    #[cfg(unix)]
    identity: (u64, u64, i64, i64),
}
impl FileStamp {
    fn from(meta: &Metadata) -> Self {
        #[cfg(unix)]
        use std::os::unix::fs::MetadataExt;
        Self {
            len: meta.len(),
            modified: meta.modified().ok(),
            created: meta.created().ok(),
            #[cfg(unix)]
            identity: (meta.dev(), meta.ino(), meta.ctime(), meta.ctime_nsec()),
        }
    }
}
#[derive(Clone)]
struct CachedMeta {
    digest: [u8; 32],
    parsed: Arc<Ini>,
}

/// One instance lives for the desktop process lifetime. Disk work runs in a
/// bounded Rayon pool; the UI must invoke import from its blocking-work pool.
#[derive(Default)]
pub struct ProfileImporter {
    cache: Mutex<HashMap<PathBuf, CachedMeta>>,
    // Retain only the latest worker configuration. In-flight imports own an
    // Arc, so changing the setting retires idle pools without stopping work.
    pool: Mutex<Option<Arc<rayon::ThreadPool>>>,
    plugins: PluginAnalyzer,
}

#[derive(Debug, Clone)]
struct Entry {
    name: String,
    enabled: bool,
    foreign: bool,
    not_in_profile: bool,
}
struct ItemRead {
    item: Value,
    stats: ImportStats,
    warnings: Vec<String>,
}

impl ProfileImporter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn clear_cache(&self) {
        self.cache.lock().unwrap_or_else(|e| e.into_inner()).clear();
        self.plugins.clear_cache();
    }

    pub fn import(&self, request: &ImportRequest) -> Result<ImportResult, CoreError> {
        self.import_with_analysis(request, false, &[])
    }

    /// Explicit deep audits freshly hash plugin bytes. Ordinary imports retain
    /// the cheap, cached header-only path and make no conversion safety claim.
    pub fn import_with_analysis(
        &self,
        request: &ImportRequest,
        deep: bool,
        eslifier_data_paths: &[PathBuf],
    ) -> Result<ImportResult, CoreError> {
        self.import_with_analysis_cancellable(request, deep, eslifier_data_paths, &|| false)
    }

    /// Stop superseded imports and audits without publishing incomplete facts.
    pub fn import_with_analysis_cancellable(
        &self,
        request: &ImportRequest,
        deep: bool,
        eslifier_data_paths: &[PathBuf],
        cancelled: CancellationCheck<'_>,
    ) -> Result<ImportResult, CoreError> {
        check_cancelled(cancelled)?;
        let started = Instant::now();
        let workers = request.workers.unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(usize::from)
                .unwrap_or(4)
                .min(8)
        });
        if !(1..=32).contains(&workers) {
            return Err(CoreError::Invalid(
                "Import workers must be between 1 and 32".into(),
            ));
        }
        if request
            .modlist_path
            .file_name()
            .is_none_or(|s| !s.to_string_lossy().eq_ignore_ascii_case("modlist.txt"))
        {
            return Err(CoreError::Invalid(
                "Select the modlist.txt file inside the MO2 profile to import".into(),
            ));
        }
        let selected = fs::canonicalize(&request.modlist_path)
            .map_err(|e| io_error("Open selected MO2 modlist.txt", e))?;
        let profile_dir = selected
            .parent()
            .ok_or_else(|| CoreError::Invalid("The profile path has no parent directory".into()))?;
        let profiles_dir = profile_dir.parent().ok_or_else(|| {
            CoreError::Invalid("The profile path has no profiles directory".into())
        })?;
        let inferred_instance = profiles_dir.parent().ok_or_else(|| {
            CoreError::Invalid("The profile path has no instance directory".into())
        })?;
        let mut stats = ImportStats {
            workers,
            ..Default::default()
        };
        let mut warnings = Vec::new();
        let ini_path = match &request.ini_path {
            Some(p) => Some(
                fs::canonicalize(p).map_err(|e| io_error("Open selected ModOrganizer.ini", e))?,
            ),
            None => find_ini(inferred_instance)?,
        };
        let settings = if let Some(p) = &ini_path {
            let bytes = read_bounded(p, META_LIMIT)?;
            stats.files_read += 1;
            stats.bytes_read += bytes.len();
            Ini::parse(&decode_text(&bytes)?)?
        } else {
            warnings.push("ModOrganizer.ini was not found; game identity remains unknown. Select the instance INI for a split-path setup.".into());
            Ini::default()
        };
        let instance_dir = ini_path
            .as_ref()
            .and_then(|p| p.parent())
            .unwrap_or(inferred_instance);
        let base_dir = match settings
            .get("settings", "base_directory")
            .filter(|s| !s.is_empty())
        {
            Some(raw) => configured_path(raw, instance_dir, instance_dir)?,
            None => instance_dir.to_path_buf(),
        };
        let mods_path = if let Some(p) = &request.mods_path {
            p.clone()
        } else {
            configured_path(
                settings
                    .get("settings", "mod_directory")
                    .filter(|s| !s.is_empty())
                    .unwrap_or("%BASE_DIR%/mods"),
                &base_dir,
                instance_dir,
            )?
        };
        let mods_dir = fs::canonicalize(&mods_path).map_err(|e| io_error("MO2 mods directory was not found. Select ModOrganizer.ini or provide the configured mods directory", e))?;
        if !mods_dir.is_dir() {
            return Err(CoreError::Invalid(
                "Configured MO2 mods path is not a directory".into(),
            ));
        }
        if let Some(raw) = settings
            .get("settings", "profiles_directory")
            .filter(|s| !s.is_empty())
        {
            let expected = configured_path(raw, &base_dir, instance_dir)?;
            let expected = fs::canonicalize(&expected)
                .map_err(|e| io_error("Configured MO2 profiles directory was not found", e))?;
            if path_key(&expected) != path_key(profiles_dir) {
                return Err(CoreError::Invalid("The selected modlist.txt does not belong to the profiles directory in ModOrganizer.ini".into()));
            }
        }
        let bytes = read_bounded(&selected, MODLIST_LIMIT)?;
        stats.files_read += 1;
        stats.bytes_read += bytes.len();
        let text = decode_text(&bytes)?;
        let (mut entries, parse_warnings) = parse_modlist(&text)?;
        warnings.extend(parse_warnings);
        let game = game_slug(first_nonempty(&[
            settings.general("gamenexusname"),
            settings.general("gamename"),
            settings.general("game"),
        ]));
        let runtime = match settings.general("gameversion") {
            "" => "unknown",
            s => s,
        };
        let profile_name = profile_dir
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        let profile_id = format!(
            "offline-{}",
            identity(&format!(
                "{}|{}",
                path_key(instance_dir),
                path_key(profile_dir)
            ))
        );

        let mut dirs = HashMap::new();
        stats.directories_read += 1;
        let listing =
            fs::read_dir(&mods_dir).map_err(|e| io_error("Read MO2 mods directory", e))?;
        for child in listing {
            check_cancelled(cancelled)?;
            let child = child.map_err(|e| io_error("Read entry in MO2 mods directory", e))?;
            let name = child.file_name().to_string_lossy().into_owned();
            if !safe_name(&name) {
                warnings.push(format!(
                    "Skipped an invalid installed mod directory name: {name}"
                ));
                continue;
            }
            let meta = fs::symlink_metadata(child.path())
                .map_err(|e| io_error(format!("Inspect installed mod {name}"), e))?;
            if is_link(&meta) {
                warnings.push(format!(
                    "Skipped linked/reparse mod directory {name}; use MO2 live mode to inspect it."
                ));
                continue;
            }
            if !meta.is_dir() {
                continue;
            }
            let key = name.to_lowercase();
            if dirs.insert(key, name.clone()).is_some() {
                return Err(CoreError::Invalid(format!(
                    "Ambiguous installed mod names differ only by case: {name}"
                )));
            }
        }
        let present: HashSet<_> = entries.iter().map(|e| e.name.to_lowercase()).collect();
        let mut extras: Vec<_> = dirs
            .iter()
            .filter(|(k, _)| !present.contains(*k))
            .map(|(_, n)| n.clone())
            .collect();
        extras.sort_by_key(|n| n.to_lowercase());
        for name in extras {
            entries.push(Entry {
                name,
                enabled: false,
                foreign: false,
                not_in_profile: true,
            });
        }
        if entries.len() > MAX_ENTRIES {
            return Err(CoreError::Invalid(format!(
                "Profile exceeds the supported {MAX_ENTRIES} entries; no rows were silently truncated"
            )));
        }
        let pool = {
            let mut cached = self.pool.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(p) = cached
                .as_ref()
                .filter(|p| p.current_num_threads() == workers)
            {
                Arc::clone(p)
            } else {
                let p = Arc::new(
                    rayon::ThreadPoolBuilder::new()
                        .num_threads(workers)
                        .thread_name(|n| format!("modhunter-import-{n}"))
                        .build()
                        .map_err(|e| {
                            CoreError::Invalid(format!("Could not start import workers: {e}"))
                        })?,
                );
                *cached = Some(Arc::clone(&p));
                p
            }
        };
        let rows: Result<Vec<ItemRead>, CoreError> = pool.install(|| {
            entries
                .par_iter()
                .enumerate()
                .map(|(priority, e)| {
                    check_cancelled(cancelled)?;
                    let row = self.read_item(e, priority, &mods_dir, &dirs, &profile_id, &game);
                    check_cancelled(cancelled)?;
                    Ok(row)
                })
                .collect()
        });
        let rows = rows?;
        let mut groups = Vec::<Value>::new();
        let mut items = Vec::<Value>::new();
        groups.push(make_group(
            &profile_id,
            "Imported load order",
            "unsorted",
            0,
            0,
        ));
        let mut group_index = 0;
        let mut extra_group_created = false;
        for (entry, mut row) in entries.iter().zip(rows) {
            check_cancelled(cancelled)?;
            stats.files_read += row.stats.files_read;
            stats.bytes_read += row.stats.bytes_read;
            stats.metadata_cache_hits += row.stats.metadata_cache_hits;
            stats.directories_read += row.stats.directories_read;
            warnings.extend(row.warnings);
            if entry.not_in_profile && !extra_group_created {
                extra_group_created = true;
                groups.push(make_group(
                    &profile_id,
                    "Installed but not in this profile",
                    "not-in-profile",
                    groups.len(),
                    row.item["priority"].as_u64().unwrap_or_default() as usize,
                ));
                group_index = groups.len() - 1;
            }
            if entry.name.to_lowercase().ends_with("_separator") {
                if !entry.not_in_profile {
                    let name = &entry.name[..entry.name.len() - "_separator".len()];
                    groups.push(make_group(
                        &profile_id,
                        name,
                        &entry.name,
                        groups.len(),
                        row.item["priority"].as_u64().unwrap_or_default() as usize,
                    ));
                    group_index = groups.len() - 1;
                }
                continue;
            }
            let group = &mut groups[group_index];
            group["count"] = json!(group["count"].as_u64().unwrap_or_default() + 1);
            row.item["group"] = group["name"].clone();
            row.item["groupId"] = group["id"].clone();
            if entry.foreign {
                stats.foreign_mods += 1;
            }
            items.push(row.item);
        }
        // Avoid an empty synthetic section before the first true separator.
        if groups
            .first()
            .is_some_and(|g| g["count"].as_u64() == Some(0))
            && groups.len() > 1
        {
            groups.remove(0);
        }
        let plugin_mods = items
            .iter()
            .filter_map(|item| {
                if item["foreign"] == true {
                    return None;
                }
                let name = item["mo2InternalName"].as_str()?;
                let actual = dirs.get(&name.to_lowercase())?;
                Some(PluginMod {
                    id: item["id"].as_str()?.into(),
                    name: name.into(),
                    path: mods_dir.join(actual),
                    enabled: item["enabled"].as_bool().unwrap_or(false),
                    priority: item["priority"].as_i64().unwrap_or(0),
                    installed_version: item["installedVersion"].as_str().map(str::to_owned),
                })
            })
            .collect();
        let overwrite = configured_path(
            settings
                .get("settings", "overwrite_directory")
                .filter(|s| !s.is_empty())
                .unwrap_or("%BASE_DIR%/overwrite"),
            &base_dir,
            instance_dir,
        )?;
        let overwrite_path = optional_analysis_path(overwrite);
        let game_data_path = settings
            .get("general", "gamepath")
            .filter(|s| !s.is_empty())
            .and_then(|raw| match configured_path(raw, &base_dir, instance_dir) {
                Ok(path) => optional_analysis_path(path.join("Data")),
                Err(error) => {
                    warnings.push(format!(
                        "Game Data path could not be resolved for plugin analysis: {error}"
                    ));
                    None
                }
            });
        let plugins_path = profile_dir.join("plugins.txt");
        let loadorder_path = profile_dir.join("loadorder.txt");
        let analysis = self.plugins.audit_in_pool_cancellable(
            &PluginAuditRequest {
                game: game.clone(),
                game_runtime: Some(runtime.into()),
                profile_id: Some(profile_id.clone()),
                profile_name: Some(profile_name.clone()),
                mods: plugin_mods,
                plugins_path: optional_analysis_path(plugins_path),
                loadorder_path: optional_analysis_path(loadorder_path),
                overwrite_path,
                game_data_path,
                plugin_states: None,
                eslifier_data_paths: eslifier_data_paths.to_vec(),
                deep,
                workers: Some(workers),
            },
            Some(pool),
            cancelled,
        )?;
        stats.files_read += analysis.stats.files_read;
        stats.bytes_read += analysis.stats.bytes_read as usize;
        stats.directories_read += analysis.stats.directories_read;
        stats.plugin_copies = analysis.stats.plugin_copies;
        stats.plugin_header_cache_hits = analysis.stats.header_cache_hits;
        warnings.extend(analysis.warnings.iter().cloned());
        for item in &mut items {
            check_cancelled(cancelled)?;
            let id = item["id"].as_str().unwrap_or_default();
            let row_scanned = analysis.mods.contains_key(id);
            item["localAnalysis"] = if let Some(audit) = analysis.mods.get(id) {
                serde_json::to_value(audit)?
            } else {
                json!({"status":"unknown","plugins":[],"warnings":["This row has no directly owned plugin folder available for analysis."]})
            };
            item["localAnalysis"]["depth"] = json!(analysis.depth);
            item["localAnalysis"]["inventoryComplete"] =
                json!(row_scanned && analysis.inventory_complete);
            item["localAnalysis"]["checkedAt"] = if deep {
                json!(analysis.checked_at)
            } else {
                Value::Null
            };
        }
        let plugin_analysis = json!({
            "schemaVersion":analysis.schema_version,"profileId":analysis.profile_id,"checkedAt":analysis.checked_at,
            "depth":analysis.depth,"inventoryComplete":analysis.inventory_complete,"activationSource":analysis.activation_source,
            "plugins":analysis.plugins,"stats":analysis.stats,"warnings":analysis.warnings,
        });
        let verified_links = items
            .iter()
            .filter(|i| i["source"]["verified"] == true)
            .count();
        stats.mods = items.len();
        stats.warnings = warnings.len();
        let stamp = Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true);
        let catalog = json!({
            "schemaVersion":2,"catalogId":format!("offline-mo2-{profile_id}"),"profileId":profile_id,
            "profileName":profile_name,"game":game,"gameRuntime":runtime,
            "title":format!("Imported MO2 / {profile_name}"),"generatedAt":stamp,
            "build":{"count":items.len(),"groups":groups.len(),"verifiedLinks":verified_links,"unresolvedLinks":items.len()-verified_links},
            "groups":groups,"items":items,
            "pluginAnalysis":plugin_analysis,
            "sources":[{"kind":"offline-modlist","name":"User-selected MO2 profile · Rust engine","timestamp":stamp}],
            "offline":true,"importedAt":stamp
        });
        stats.elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
        check_cancelled(cancelled)?;
        Ok(ImportResult {
            catalog,
            stats,
            warnings,
        })
    }

    fn read_item(
        &self,
        e: &Entry,
        priority: usize,
        mods_dir: &Path,
        dirs: &HashMap<String, String>,
        profile_id: &str,
        game: &str,
    ) -> ItemRead {
        let mut stats = ImportStats::default();
        let mut warnings = Vec::new();
        let actual = dirs.get(&e.name.to_lowercase());
        let separator = e.name.to_lowercase().ends_with("_separator");
        let mut meta = Arc::new(Ini::default());
        let mut flags = Vec::<&str>::new();
        if !e.foreign && !separator {
            if let Some(actual) = actual {
                let dir = mods_dir.join(actual);
                match self.read_meta(&dir.join("meta.ini"), &mut stats) {
                    Ok(Some(m)) => meta = m,
                    Ok(None) => {}
                    Err(ex) => warnings.push(format!(
                        "{}: metadata unavailable ({ex}); retained as a read-only row",
                        e.name
                    )),
                }
                match inspect_dlls(&dir, &mut stats) {
                    Ok(true) => flags.push("DLL"),
                    Ok(false) => {}
                    Err(ex) => warnings.push(format!(
                        "{}: native-plugin inspection failed ({ex})",
                        e.name
                    )),
                }
            } else {
                warnings.push(format!(
                    "{} is listed in this profile but its mod directory is absent or linked",
                    e.name
                ));
            }
        }
        let per_mod_game_raw =
            first_nonempty(&[meta.general("gamenexusname"), meta.general("gamename")]);
        let per_mod_game = game_slug(per_mod_game_raw);
        let mismatched_game = !per_mod_game_raw.is_empty() && per_mod_game != game;
        let effective_game = if !per_mod_game_raw.is_empty() {
            per_mod_game.as_str()
        } else {
            game
        };
        let mod_id = if e.foreign {
            0
        } else {
            first_nonempty(&[
                meta.general("modid"),
                meta.general("nexusid"),
                meta.general("mod_id"),
            ])
            .parse::<u64>()
            .ok()
            .filter(|n| *n <= 9_007_199_254_740_991)
            .unwrap_or_default()
        };
        let notes = meta.general("notes");
        let custom_url = match meta.general("hascustomurl").to_ascii_lowercase().as_str() {
            "false" | "0" => "",
            _ => first_nonempty(&[
                meta.general("url"),
                meta.general("customurl"),
                meta.general("modurl"),
                meta.general("homepage"),
                meta.general("website"),
            ]),
        };
        let notes_url = notes
            .split_whitespace()
            .find(|s| s.starts_with("https://"))
            .map(|s| s.trim_matches(|c| "<>\"')]".contains(c)))
            .unwrap_or("");
        let authoritative_nexus = mod_id > 0 && effective_game != "unknown";
        let notes_advisory = custom_url.is_empty() && !authoritative_nexus && !notes_url.is_empty();
        let mut source = if e.foreign {
            unknown_source("Foreign / game-managed entry")
        } else {
            source_for(
                if notes_advisory {
                    notes_url
                } else {
                    custom_url
                },
                mod_id,
                effective_game,
            )
        };
        if notes_advisory && !e.foreign && source["verified"] == true {
            // Notes frequently link dependencies or troubleshooting pages. Such
            // links remain useful to inspect but do not establish mod identity.
            source["verified"] = json!(false);
            source["kind"] = json!("advisory");
            source["label"] = json!("Unverified link from MO2 notes");
        }
        let version = match meta.general("version") {
            "" => "?",
            s => s,
        };
        let audit = if mismatched_game {
            json!({"verdict":"check","note":"MO2 metadata identifies a different game; verify the source before updating","evidence":"MO2 per-mod gameName"})
        } else if !flags.is_empty() {
            json!({"verdict":"check","note":"Native DLL detected; verify the exact game runtime and dependencies","evidence":"Imported MO2 profile metadata"})
        } else {
            json!({})
        };
        ItemRead {
            item: json!({
                "id":format!("live-{}", identity(&format!("{profile_id}|{}", name_identity(&e.name)))),
                "mo2InternalName":e.name,"name":e.name,"installedVersion":version,
                "latestKnownVersion":meta.general("newestversion"),"enabled":e.enabled,"nexusId":mod_id,
                "priority":priority,"flags":flags,"groupId":"","group":"Imported load order",
                "screenshotNote":notes.chars().take(4000).collect::<String>(),"author":meta.general("author"),
                "source":source,"screenshot":0,"audit":audit,"offlineImported":true,
                "foreign":e.foreign,"notInProfile":e.not_in_profile,
                "missingFromDisk":!e.foreign && actual.is_none(),"sourceGame":effective_game,
                "updateEligible":!e.foreign && actual.is_some() && !mismatched_game,
                "repository":if meta.general("repository").is_empty() {"Nexus"} else {meta.general("repository")}
            }),
            stats,
            warnings,
        }
    }

    fn read_meta(
        &self,
        path: &Path,
        stats: &mut ImportStats,
    ) -> Result<Option<Arc<Ini>>, CoreError> {
        let meta = match fs::symlink_metadata(path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                self.cache
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .remove(path);
                return Ok(None);
            }
            Err(e) => return Err(io_error("Inspect meta.ini", e)),
        };
        if is_link(&meta) || !meta.is_file() {
            return Err(CoreError::Invalid(
                "Linked or non-file meta.ini rejected".into(),
            ));
        }
        let stamp = FileStamp::from(&meta);
        // Mod managers can preserve timestamps, and Windows may report the
        // same metadata for a quick equal-length rewrite. Verify bounded fresh
        // bytes before reusing parsed versions, game IDs or source links.
        let bytes = read_bounded(path, META_LIMIT)?;
        stats.files_read += 1;
        stats.bytes_read += bytes.len();
        let digest: [u8; 32] = Sha256::digest(&bytes).into();
        let after =
            fs::symlink_metadata(path).map_err(|e| io_error("Recheck meta.ini after import", e))?;
        if is_link(&after) || FileStamp::from(&after) != stamp {
            return Err(CoreError::Invalid(
                "meta.ini changed during import; refresh once MO2 finishes writing".into(),
            ));
        }
        if let Some(cached) = self
            .cache
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(path)
            .filter(|c| c.digest == digest)
            .cloned()
        {
            stats.metadata_cache_hits += 1;
            return Ok(Some(cached.parsed));
        }
        let parsed = Arc::new(Ini::parse(&decode_text(&bytes)?)?);
        let mut cache = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        if cache.len() >= CACHE_LIMIT && !cache.contains_key(path) {
            cache.clear();
        }
        cache.insert(
            path.to_path_buf(),
            CachedMeta {
                digest,
                parsed: Arc::clone(&parsed),
            },
        );
        Ok(Some(parsed))
    }
}

fn find_ini(instance: &Path) -> Result<Option<PathBuf>, CoreError> {
    let path = instance.join("ModOrganizer.ini");
    match fs::symlink_metadata(&path) {
        Ok(_) => Ok(Some(
            fs::canonicalize(path).map_err(|e| io_error("Resolve ModOrganizer.ini", e))?,
        )),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(io_error("Inspect ModOrganizer.ini", e)),
    }
}

fn configured_path(raw: &str, base: &Path, instance: &Path) -> Result<PathBuf, CoreError> {
    if raw.chars().any(|c| c == '\0' || c == '\n' || c == '\r') {
        return Err(CoreError::Invalid(
            "Configured MO2 directory contains invalid characters".into(),
        ));
    }
    let expanded = raw.replace("%BASE_DIR%", &base.to_string_lossy());
    if expanded.contains('%') {
        return Err(CoreError::Invalid(
            "Unsupported MO2 directory placeholder; select the exact configured path".into(),
        ));
    }
    #[cfg(windows)]
    let expanded = expanded.replace('/', "\\");
    #[cfg(not(windows))]
    let expanded = expanded.replace('\\', "/");
    let path = PathBuf::from(expanded);
    Ok(if path.is_absolute() {
        path
    } else {
        instance.join(path)
    })
}

fn optional_analysis_path(path: PathBuf) -> Option<PathBuf> {
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        // Other errors go to the analyzer, where they become explicit warnings.
        _ => Some(path),
    }
}

fn read_bounded(path: &Path, limit: usize) -> Result<Vec<u8>, CoreError> {
    let before = fs::symlink_metadata(path).map_err(|e| io_error("Inspect MO2 file", e))?;
    if is_link(&before) || !before.is_file() {
        return Err(CoreError::Invalid(
            "Linked or non-regular MO2 file rejected".into(),
        ));
    }
    if before.len() > limit as u64 {
        return Err(CoreError::Invalid(format!(
            "MO2 file exceeds the {limit}-byte import limit"
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
        // A live writer must finish before these bytes can establish installed
        // versions or source identity. Other readers remain permitted.
        options.custom_flags(0x0020_0000).share_mode(0x0000_0001);
    }
    let file: File = options
        .open(path)
        .map_err(|e| io_error("Read MO2 file", e))?;
    let opened = file
        .metadata()
        .map_err(|e| io_error("Inspect opened MO2 file", e))?;
    if is_link(&opened) || !opened.is_file() || FileStamp::from(&opened) != FileStamp::from(&before)
    {
        return Err(CoreError::Invalid(
            "MO2 file changed or became a link while opening it".into(),
        ));
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| io_error("Read MO2 file contents", e))?;
    if bytes.len() > limit {
        return Err(CoreError::Invalid(format!(
            "MO2 file exceeds the {limit}-byte import limit"
        )));
    }
    Ok(bytes)
}

fn inspect_dlls(mod_dir: &Path, stats: &mut ImportStats) -> Result<bool, CoreError> {
    for (root, leaf) in [
        ("SKSE", "Plugins"),
        ("skse", "plugins"),
        ("F4SE", "Plugins"),
        ("f4se", "plugins"),
        ("SFSE", "Plugins"),
        ("sfse", "plugins"),
        ("OBSE", "Plugins"),
        ("NVSE", "Plugins"),
    ] {
        let parent = mod_dir.join(root);
        let meta = match fs::symlink_metadata(&parent) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(io_error("Inspect native-plugin folder", e)),
        };
        if is_link(&meta) || !meta.is_dir() {
            continue;
        }
        let folder = parent.join(leaf);
        let meta = match fs::symlink_metadata(&folder) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(io_error("Inspect native-plugin directory", e)),
        };
        if is_link(&meta) || !meta.is_dir() {
            continue;
        }
        stats.directories_read += 1;
        for child in
            fs::read_dir(folder).map_err(|e| io_error("Read native-plugin directory", e))?
        {
            let child = child.map_err(|e| io_error("Read native-plugin entry", e))?;
            if !child
                .file_name()
                .to_string_lossy()
                .to_lowercase()
                .ends_with(".dll")
            {
                continue;
            }
            let metadata = fs::symlink_metadata(child.path())
                .map_err(|e| io_error("Inspect native plugin", e))?;
            if metadata.is_file() && !is_link(&metadata) {
                return Ok(true);
            }
        }
    }
    Ok(false)
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

fn parse_modlist(text: &str) -> Result<(Vec<Entry>, Vec<String>), CoreError> {
    let mut entries = Vec::new();
    let mut seen = HashSet::new();
    let mut warnings = Vec::new();
    for raw in text.trim_start_matches('\u{feff}').lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (name, enabled, foreign) = match line.as_bytes()[0] {
            b'+' => (&line[1..], true, false),
            b'-' => (&line[1..], false, false),
            b'*' => (&line[1..], true, true),
            _ => (line, true, false),
        };
        let name = name.trim();
        let valid = if foreign {
            !name.is_empty() && name.len() < 1024 && !name.chars().any(char::is_control)
        } else {
            safe_name(name)
        };
        if !valid {
            warnings.push("Skipped an invalid path-like entry in modlist.txt".into());
            continue;
        }
        if !seen.insert(name.to_lowercase()) {
            warnings.push(format!("Ignored duplicate profile entry {name}"));
            continue;
        }
        entries.push(Entry {
            name: name.to_owned(),
            enabled,
            foreign,
            not_in_profile: false,
        });
        if entries.len() > MAX_ENTRIES {
            return Err(CoreError::Invalid(format!(
                "Profile exceeds {MAX_ENTRIES} entries"
            )));
        }
    }
    // MO2 writes this file from highest priority to lowest priority.
    entries.reverse();
    Ok((entries, warnings))
}

fn safe_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() < 1024
        && name != "."
        && name != ".."
        && !name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\' | ':' | '\0'))
}
fn path_key(p: &Path) -> String {
    #[cfg(windows)]
    {
        windows_path_key(&p.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        p.to_string_lossy().into_owned()
    }
}
/// Match the existing Electron import identity on Windows. Rust canonicalize
/// produces extended-length paths, while Node path.resolve does not.
#[cfg(any(windows, test))]
fn windows_path_key(raw: &str) -> String {
    let native = raw.replace('/', "\\");
    let normalized = if let Some(unc) = native.strip_prefix("\\\\?\\UNC\\") {
        format!("\\\\{unc}")
    } else {
        native.strip_prefix("\\\\?\\").unwrap_or(&native).to_owned()
    };
    normalized.to_lowercase()
}
fn name_identity(name: &str) -> String {
    // Existing progress exports key rows by the exact MO2 internal name.
    name.to_owned()
}
fn identity(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))[..16].to_owned()
}
fn first_nonempty<'a>(values: &[&'a str]) -> &'a str {
    values.iter().copied().find(|v| !v.is_empty()).unwrap_or("")
}
fn make_group(profile: &str, name: &str, key: &str, index: usize, priority: usize) -> Value {
    json!({"id":format!("g-{}", identity(&format!("{profile}|{key}"))),"name":name,"startPriority":priority,"count":0,"color":COLORS[index % COLORS.len()]})
}

pub fn game_slug(raw: &str) -> String {
    let normalized: String = raw
        .to_ascii_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect();
    match normalized.as_str() {
        "skyrimspecialedition" | "skyrimse" | "skyrimanniversaryedition" => "skyrimspecialedition",
        "skyrim" => "skyrim",
        "skyrimvr" => "skyrimvr",
        "fallout4" => "fallout4",
        "fallout4vr" => "fallout4vr",
        "falloutnewvegas" | "newvegas" => "newvegas",
        "fallout3" => "fallout3",
        "oblivion" => "oblivion",
        "oblivionremastered" => "oblivionremastered",
        "morrowind" => "morrowind",
        "starfield" => "starfield",
        "enderal" => "enderal",
        "enderalspecialedition" => "enderalspecialedition",
        _ => "unknown",
    }
    .into()
}

fn unknown_source(label: &str) -> Value {
    json!({"type":"unknown","verified":false,"kind":"unresolved","label":label,"url":""})
}
fn source_for(raw: &str, mod_id: u64, game: &str) -> Value {
    if let Ok(mut url) = Url::parse(raw.trim()) {
        let allowed_host = match url.host() {
            Some(Host::Domain(host)) => {
                host != "localhost"
                    && !host.ends_with(".localhost")
                    && host.contains('.')
                    && !host.ends_with(".local")
            }
            _ => false,
        };
        if url.scheme() == "https"
            && allowed_host
            && url.username().is_empty()
            && url.password().is_none()
            && url.port().is_none()
        {
            sanitize_source_query(&mut url);
            url.set_fragment(None);
            let provider = crate::source_adapters::provider_of(url.as_str());
            return json!({"type":provider,"verified":true,"kind":"project","label":format!("{} · MO2 URL", provider.to_uppercase()),"url":url.as_str()});
        }
    }
    if mod_id > 0 && game != "unknown" {
        json!({"type":"nexus","verified":true,"kind":"project","label":"Nexus / Files","url":format!("https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files")})
    } else {
        unknown_source("Source unlinked")
    }
}

fn sanitize_source_query(url: &mut Url) {
    let Some(query) = url.query() else {
        return;
    };
    // Preserve raw surviving components. Re-encoding all pairs changes forum
    // route queries such as index.php?/topic/123/ into a different destination.
    let kept = query
        .split('&')
        .filter(|component| {
            let key = url::form_urlencoded::parse(component.as_bytes())
                .next()
                .map(|(key, _)| key.to_ascii_lowercase())
                .unwrap_or_default();
            !key.starts_with("utm_")
                && !key.starts_with("x-amz-")
                && !key.starts_with("x-goog-")
                && !key.starts_with("__cf_chl_")
                && !matches!(
                    key.as_str(),
                    "token"
                        | "private_token"
                        | "auth_token"
                        | "oauth_token"
                        | "download_key"
                        | "access_token"
                        | "refresh_token"
                        | "id_token"
                        | "apikey"
                        | "api_key"
                        | "api-key"
                        | "key"
                        | "auth"
                        | "authorization"
                        | "password"
                        | "passwd"
                        | "secret"
                        | "client_secret"
                        | "session"
                        | "sessionid"
                        | "session_id"
                        | "sid"
                        | "signature"
                        | "sig"
                        | "fbclid"
                        | "gclid"
                        | "dclid"
                        | "msclkid"
                        | "igshid"
                        | "ref_src"
                        | "ref_url"
                )
        })
        .collect::<Vec<_>>()
        .join("&");
    url.set_query(if kept.is_empty() { None } else { Some(&kept) });
}

#[cfg(test)]
mod identity_tests {
    use super::*;

    #[test]
    fn windows_profile_and_row_hashes_match_electron_exports() {
        let instance = windows_path_key(r"\\?\C:\Modding\MO2");
        let profile = windows_path_key(r"\\?\C:\Modding\MO2\profiles\Héro");
        let profile_id = format!("offline-{}", identity(&format!("{instance}|{profile}")));
        assert_eq!(profile_id, "offline-897a014303c869c0");
        assert_eq!(
            format!(
                "live-{}",
                identity(&format!("{profile_id}|{}", name_identity("SkyUI")))
            ),
            "live-645f8b0c2bca2404"
        );
        assert_ne!(name_identity("SkyUI"), name_identity("skyui"));
    }

    #[test]
    fn windows_unc_and_slash_forms_share_identity() {
        assert_eq!(
            windows_path_key(r"\\?\UNC\SERVER\Share\MO2"),
            windows_path_key(r"\\server\share\mo2")
        );
        assert_eq!(
            windows_path_key("C:/Modding/MO2"),
            windows_path_key(r"C:\modding\mo2")
        );
    }
}

#[cfg(test)]
mod worker_pool_tests {
    use super::*;

    #[test]
    fn changing_import_workers_releases_the_previous_idle_pool() {
        let fixture = tempfile::tempdir().unwrap();
        let profile = fixture.path().join("profiles/Test");
        fs::create_dir_all(&profile).unwrap();
        fs::create_dir(fixture.path().join("mods")).unwrap();
        fs::write(profile.join("modlist.txt"), "# Empty disposable profile\n").unwrap();
        let mut request = ImportRequest {
            modlist_path: profile.join("modlist.txt"),
            ini_path: None,
            mods_path: None,
            workers: Some(2),
        };
        let engine = ProfileImporter::new();
        engine.import(&request).unwrap();
        let retired = Arc::downgrade(engine.pool.lock().unwrap().as_ref().unwrap());
        request.workers = Some(3);
        let result = engine.import(&request).unwrap();
        assert_eq!(result.stats.workers, 3);
        assert!(retired.upgrade().is_none());
    }
}
