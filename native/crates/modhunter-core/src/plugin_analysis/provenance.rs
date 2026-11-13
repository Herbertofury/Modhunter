//! Inspect selected ESLifier records, without creating or trusting a new
//! conversion baseline. Paths from JSON are never opened: only already-scanned
//! MO2-owned copies can supply current bytes. Records are mutable tool state,
//! not signed evidence of a successful or safe conversion.
//!
//! Format references: MaskPlague/ESLifier src/data_holder.py,
//! src/compact_form_ids.py and src/main/main_page.py (upstream main).

use super::{
    ConversionProvenance, PluginAuditRequest, PluginAuditStats, PluginCopy, is_link, path_key,
    read_small,
};
use crate::{CancellationCheck, CoreError, check_cancelled};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

const JSON_LIMIT: usize = 8 * 1024 * 1024;

pub(super) fn apply(
    request: &PluginAuditRequest,
    plugins: &mut BTreeMap<String, Vec<PluginCopy>>,
    stats: &mut PluginAuditStats,
    warnings: &mut Vec<String>,
    cancelled: CancellationCheck<'_>,
) -> Result<(), CoreError> {
    check_cancelled(cancelled)?;
    if request.eslifier_data_paths.is_empty() {
        return Ok(());
    }
    if !request.deep {
        for copies in plugins.values_mut() {
            check_cancelled(cancelled)?;
            for copy in copies {
                copy.provenance.reason = "Selected ESLifier records need a deep audit with fresh original and output SHA-256 hashes.".into();
            }
        }
        return Ok(());
    }
    let mut observed: HashMap<String, ConversionProvenance> = HashMap::new();
    for directory in &request.eslifier_data_paths {
        check_cancelled(cancelled)?;
        match inspect_directory(directory, request, plugins, stats, cancelled) {
            Ok(results) => {
                for (name, next) in results {
                    if let Some(previous) = observed.get_mut(&name) {
                        if previous.state != next.state
                            || previous.original_path != next.original_path
                            || previous.output_path != next.output_path
                            || previous.original_sha256 != next.original_sha256
                            || previous.output_sha256 != next.output_sha256
                        {
                            *previous = ConversionProvenance { reason: "Selected ESLifier folders contain conflicting records for this plugin; choose the folder for this profile.".into(), ..Default::default() };
                        }
                    } else {
                        observed.insert(name, next);
                    }
                }
            }
            Err(CoreError::Cancelled) => return Err(CoreError::Cancelled),
            Err(error) => warnings.push(format!(
                "ESLifier records in {} were not used: {error}",
                directory.display()
            )),
        }
    }
    for (name, provenance) in observed {
        check_cancelled(cancelled)?;
        if let Some(copies) = plugins.get_mut(&name) {
            for copy in copies {
                copy.provenance = provenance.clone();
            }
        }
    }
    Ok(())
}

fn inspect_directory(
    directory: &Path,
    request: &PluginAuditRequest,
    plugins: &BTreeMap<String, Vec<PluginCopy>>,
    stats: &mut PluginAuditStats,
    cancelled: CancellationCheck<'_>,
) -> Result<HashMap<String, ConversionProvenance>, CoreError> {
    check_cancelled(cancelled)?;
    let meta = fs::symlink_metadata(directory)
        .map_err(|e| crate::io_error("Inspect selected ESLifier data folder", e))?;
    if !directory.is_absolute() || is_link(&meta) || !meta.is_dir() {
        return invalid("ESLifier data must be an absolute regular directory, not a link");
    }
    let settings = json(directory, "settings.json", stats)?;
    check_cancelled(cancelled)?;
    if settings.get("mod_manager_mode").and_then(Value::as_i64) != Some(2) {
        return invalid("ESLifier settings are not for Mod Organizer 2 mode");
    }
    if let Some(profile) = request.profile_name.as_deref()
        && settings
            .get("mo2_profile")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .is_some_and(|s| s != profile)
    {
        return invalid("ESLifier records belong to a different MO2 profile");
    }
    let output_parent = settings
        .get("output_folder_path")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| CoreError::Invalid("ESLifier settings have no output_folder_path".into()))?;
    let output_name = settings
        .get("output_folder_name")
        .and_then(Value::as_str)
        .unwrap_or("ESLifier Output");
    if output_name.is_empty()
        || output_name == "."
        || output_name == ".."
        || output_name.contains(['/', '\\', ':'])
        || output_name.chars().any(char::is_control)
    {
        return invalid("ESLifier output folder name is not one directory component");
    }
    let output_root = PathBuf::from(output_parent).join(output_name);
    if !request
        .mods
        .iter()
        .any(|m| path_key(&m.path) == path_key(&output_root))
        && !request
            .overwrite_path
            .as_deref()
            .is_some_and(|p| path_key(p) == path_key(&output_root))
    {
        return invalid("Recorded ESLifier output folder is not a supplied MO2-owned source");
    }
    let originals = json(directory, "original_files.json", stats)?;
    check_cancelled(cancelled)?;
    let outputs = json(directory, "new_file_hashes.json", stats)?;
    check_cancelled(cancelled)?;
    let originals = originals
        .as_object()
        .ok_or_else(|| CoreError::Invalid("original_files.json must be an object".into()))?;
    let outputs = outputs
        .as_object()
        .ok_or_else(|| CoreError::Invalid("new_file_hashes.json must be an object".into()))?;
    let flagged =
        optional_json(directory, "esl_flagged.json", stats)?.unwrap_or(Value::Array(Vec::new()));
    check_cancelled(cancelled)?;
    let compacted = optional_json(directory, "compacted_and_patched.json", stats)?
        .unwrap_or(Value::Object(Default::default()));
    let mut marked = HashSet::new();
    let Some(flagged) = flagged.as_array() else {
        return invalid("esl_flagged.json must be a list");
    };
    for entry in flagged {
        check_cancelled(cancelled)?;
        if let Some(name) = entry.as_str() {
            marked.insert(name.to_lowercase());
        }
    }
    let Some(compacted) = compacted.as_object() else {
        return invalid("compacted_and_patched.json must be an object");
    };
    marked.extend(compacted.keys().map(|s| s.to_lowercase()));
    let mut result = HashMap::new();
    for (name, copies) in plugins {
        check_cancelled(cancelled)?;
        if !marked.contains(name) {
            continue;
        }
        let Some(original) = originals
            .get(name)
            .and_then(Value::as_array)
            .filter(|a| a.len() == 2)
        else {
            continue;
        };
        let Some(source_path) = original[0].as_str().map(PathBuf::from) else {
            continue;
        };
        let Some(source_hash) = valid_hash(&original[1]) else {
            continue;
        };
        let Some(output) = outputs
            .get(name)
            .and_then(Value::as_array)
            .filter(|a| a.len() == 2)
        else {
            continue;
        };
        let Some(output_hash) = valid_hash(&output[0]) else {
            continue;
        };
        let Some(altered) = output[1].as_bool() else {
            continue;
        };
        let source = copies
            .iter()
            .find(|c| path_key(&c.path) == path_key(&source_path));
        let actual_output = copies.iter().find(|c| {
            c.path
                .parent()
                .is_some_and(|p| path_key(p) == path_key(&output_root))
                && c.name.eq_ignore_ascii_case(name)
                && !c.ghosted
        });
        let (Some(source), Some(actual_output)) = (source, actual_output) else {
            continue;
        };
        if path_key(&source.path) == path_key(&actual_output.path) {
            continue;
        }
        let Some(source_current) = source
            .fingerprint
            .as_ref()
            .and_then(|f| f.sha256.as_deref())
        else {
            continue;
        };
        let Some(output_current) = actual_output
            .fingerprint
            .as_ref()
            .and_then(|f| f.sha256.as_deref())
        else {
            continue;
        };
        let mut evidence = ConversionProvenance {
            state: "unknown".into(),
            tool: Some("ESLifier".into()),
            evidence_path: Some(directory.to_path_buf()),
            original_path: Some(source.path.clone()),
            output_path: Some(actual_output.path.clone()),
            original_sha256: Some(source_hash.clone()),
            output_sha256: Some(output_hash.clone()),
            reason: String::new(),
        };
        let source_changed = source_current != source_hash;
        let output_changed = altered || output_current != output_hash;
        if !actual_output.winner_known {
            evidence.reason =
                "The actual winning file is unresolved; recorded conversion status is unknown."
                    .into();
        } else if !actual_output.winner {
            evidence.state = "winner-changed".into();
            evidence.reason =
                "The recorded ESLifier output is no longer the winning file in this profile."
                    .into();
        } else if source_changed && output_changed {
            evidence.state = "source-and-output-changed".into();
            evidence.reason = "Fresh source and output bytes differ from the selected ESLifier records, or the tool marked the output externally altered. Re-audit with ESLifier.".into();
        } else if source_changed {
            evidence.state = "source-changed".into();
            evidence.reason = "The original plugin's SHA-256 changed while the winning output still matches the selected ESLifier record. Re-audit that conversion after the source update.".into();
        } else if output_changed {
            evidence.state = "output-changed".into();
            evidence.reason = "The output SHA-256 differs from the selected ESLifier record, or ESLifier marks it externally altered. Do not treat it as an unchanged conversion.".into();
        } else if !actual_output
            .header
            .as_ref()
            .is_some_and(|h| h.light_effective)
        {
            evidence.reason = "Recorded hashes match, but the current output is not light. Tool lists alone do not prove successful flagging.".into();
        } else {
            evidence.state = "current".into();
            evidence.reason = "Fresh original and winning light-output hashes match the selected mutable ESLifier records. This is recorded-output consistency, not proof of save or conversion safety.".into();
            if request.profile_name.is_none() {
                evidence.reason.push_str(" Profile-name matching was not supplied; ownership was checked against this audit's sources.");
            }
        }
        result.insert(name.clone(), evidence);
    }
    Ok(result)
}

fn json(directory: &Path, name: &str, stats: &mut PluginAuditStats) -> Result<Value, CoreError> {
    let bytes = read_small(&directory.join(name), JSON_LIMIT, stats)?;
    let bytes = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(&bytes);
    serde_json::from_slice(bytes)
        .map_err(|error| CoreError::Invalid(format!("Invalid ESLifier {name}: {error}")))
}
fn optional_json(
    directory: &Path,
    name: &str,
    stats: &mut PluginAuditStats,
) -> Result<Option<Value>, CoreError> {
    match fs::symlink_metadata(directory.join(name)) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(crate::io_error("Inspect ESLifier record", error)),
        Ok(_) => json(directory, name, stats).map(Some),
    }
}
fn valid_hash(value: &Value) -> Option<String> {
    value
        .as_str()
        .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .map(str::to_ascii_lowercase)
}
fn invalid<T>(message: &str) -> Result<T, CoreError> {
    Err(CoreError::Invalid(message.into()))
}
