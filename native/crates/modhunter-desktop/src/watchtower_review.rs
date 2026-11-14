//! File reviews are native capabilities: the renderer receives identifiers and
//! complete file facts, never a filesystem destination or bridge credential.
use super::*;
use modhunter_core::{
    CoreError,
    acquisition::{Receipt, atomic_json, reject_link},
    archive_review, content_vault,
};
use std::collections::BTreeSet;

const MAX_REVIEWS: usize = 32;
const MAX_ENTRIES: usize = 500_000;
const REVIEW_AGE: u64 = 60 * 60 * 1000;
#[derive(Clone)]
struct Review {
    id: String,
    receipt: Receipt,
    archive: PathBuf,
    acquisitions: PathBuf,
    stage: PathBuf,
    inspected: Value,
    compared: Value,
    context: Value,
    endpoint: String,
    epoch: u64,
    created: u64,
    selected: BTreeSet<String>,
    submitted: bool,
    state: String,
    job_id: Option<String>,
    stage_release_allowed: bool,
    cancel: Arc<AtomicBool>,
}
struct Reviews {
    root: PathBuf,
    rows: Mutex<BTreeMap<String, Review>>,
    preparing: Mutex<BTreeMap<String, Arc<AtomicBool>>>,
    exclusions: Mutex<BTreeMap<String, BTreeSet<String>>>,
    lifecycle: Mutex<BTreeSet<String>>,
    cleanup_errors: Mutex<BTreeMap<String, String>>,
    sequence: AtomicU64,
}
pub(super) fn setup(app: &tauri::AppHandle, root: PathBuf) -> Result<()> {
    std::fs::create_dir_all(&root).map_err(err)?;
    content_vault::checked_root(&root)?;
    let preferences = root.join("optional-exclusions.json");
    let exclusions = if preferences.exists() {
        reject_link(&preferences)?;
        if std::fs::metadata(&preferences).map_err(err)?.len() > 8 * 1024 * 1024 {
            return Err("Saved file exclusions exceed their storage limit.".into());
        }
        serde_json::from_slice(&std::fs::read(&preferences).map_err(err)?).map_err(err)?
    } else {
        BTreeMap::new()
    };
    app.manage(Reviews {
        root,
        rows: Mutex::new(BTreeMap::new()),
        preparing: Mutex::new(BTreeMap::new()),
        exclusions: Mutex::new(exclusions),
        lifecycle: Mutex::new(BTreeSet::new()),
        cleanup_errors: Mutex::new(BTreeMap::new()),
        sequence: AtomicU64::new(1),
    });
    Ok(())
}
fn public_context(context: &Value) -> Value {
    let mut result = context.clone();
    if let Some(object) = result.as_object_mut() {
        object.remove("nativeContext");
    }
    result
}
fn summary(row: &Review) -> Value {
    json!({"id":row.id,"downloadId":row.receipt.id,"filename":row.receipt.filename,"createdAt":row.created,
        "state":row.state,"submitted":row.submitted,"jobId":row.job_id,"cancelRequested":row.cancel.load(Ordering::Acquire),
        "context":public_context(&row.context),"selectedCount":row.selected.len(),"fileCount":row.compared["entries"].as_array().map_or(0,Vec::len),
        "requiresInstaller":row.inspected["requiresInstaller"],"identical":row.compared["identical"],
        "canDiscard":!row.submitted||known_finished(&row.state)||row.stage_release_allowed,"reconciled":row.stage_release_allowed})
}
fn detail(row: &Review) -> Value {
    let mut result = summary(row);
    result["entries"] = row.compared["entries"].clone();
    result["selected"] = json!(row.selected);
    result["archive"] = json!({"bytes":row.receipt.bytes,"sha256":row.receipt.sha256});
    result["layout"] = row.inspected["layout"].clone();
    result["scope"] = row.compared["scope"].clone();
    result
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let Some(state) = app.try_state::<Reviews>() else {
        return json!({"rows":[],"preparing":[]});
    };
    let preparing = state
        .preparing
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    let mut rows = state
        .rows
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .values()
        .map(summary)
        .collect::<Vec<_>>();
    let mut errors = Vec::new();
    match retained_journals(&state) {
        Ok(saved) => {
            for (_, journal) in saved {
                if rows.iter().any(|row| row["id"] == journal["reviewId"]) {
                    continue;
                }
                if let Some(error) = journal["invalidReason"].as_str() {
                    errors.push(error.to_owned());
                }
                rows.push(retained_summary(&state, &journal));
            }
        }
        Err(error) => errors.push(error),
    }
    json!({"rows":rows,"preparing":preparing,"error":errors.join(" ")})
}
fn known_finished(state: &str) -> bool {
    matches!(state, "complete" | "failed" | "cancelled")
}
fn journal(row: &Review) -> Result<()> {
    atomic_json(
        &row.stage.join("native-review.json"),
        &json!({"schema":1,"owner":"Modhunter-content-review","reviewId":row.id,
        "downloadId":row.receipt.id,"submitted":row.submitted,"state":row.state,"jobId":row.job_id,
        "profileId":row.context["profileId"],"itemId":row.context["target"]["itemId"],"contextId":row.context["contextId"],
        "filename":row.receipt.filename,"createdAt":row.created,"context":public_context(&row.context),
        "manifestDigest":row.inspected["manifestDigest"],"nativeLease":row.context["nativeLease"],"nativeGeneration":row.context["nativeGeneration"],
        "stageReleaseAllowed":row.stage_release_allowed}),
    )
}
fn valid_review_id(id: &str) -> bool {
    id.starts_with("review-")
        && id.len() <= 100
        && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}
fn readable_journal(path: &Path, id: &str) -> Result<Value> {
    reject_link(path)?;
    if std::fs::metadata(path).map_err(err)?.len() > 16 * 1024 {
        return Err("A retained review journal exceeds its bound.".into());
    }
    let value: Value = serde_json::from_slice(&std::fs::read(path).map_err(err)?).map_err(err)?;
    if value["owner"] != "Modhunter-content-review"
        || value["schema"] != 1
        || value["reviewId"] != id
        || !valid_review_id(id)
        || !value["submitted"].is_boolean()
        || value["state"]
            .as_str()
            .is_none_or(|s| s.is_empty() || s.len() > 100)
        || value["downloadId"]
            .as_str()
            .is_none_or(|s| s.is_empty() || s.len() > 200)
    {
        return Err("Unrecognized retained review journal; its evidence was preserved.".into());
    }
    Ok(value)
}
fn can_discard_saved(saved: &Value) -> bool {
    saved.get("invalidReason").is_none()
        && (saved["submitted"] == false
            || known_finished(saved["state"].as_str().unwrap_or("unknown"))
            || reconciled_saved(saved))
}
fn reconciled_saved(saved: &Value) -> bool {
    saved["stageReleaseAllowed"] == true
        && matches!(saved["state"].as_str(), Some("unknown" | "interrupted"))
        && saved["jobId"].as_str().is_some_and(|id| !id.is_empty())
}
fn retained_summary(state: &Reviews, saved: &Value) -> Value {
    let id = saved["reviewId"].as_str().unwrap_or("");
    let busy = state
        .lifecycle
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .contains(id);
    let cleanup = state
        .cleanup_errors
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(id)
        .cloned();
    let reason=saved["invalidReason"].as_str().map(str::to_owned).or(cleanup)
        .unwrap_or_else(||if busy {"This review is currently preparing or submitting.".into()} else if can_discard_saved(saved) {
            "This saved review is available only for cleanup. Prepare a fresh review to install content.".into()
        } else {"Reconnect to the originating MO2 instance and refresh native operations to reconcile this outcome. Its evidence cannot be discarded yet.".into()});
    json!({"id":id,"downloadId":saved["downloadId"],"filename":saved["filename"].as_str().unwrap_or("Retained file review"),
        "createdAt":saved["createdAt"],"state":saved["state"],"submitted":saved["submitted"],"jobId":saved["jobId"],
        "context":saved["context"],"recoveryOnly":true,"canDiscard":can_discard_saved(saved)&&!busy,"reason":reason,
        "selectedCount":0,"fileCount":0,"cancelRequested":false,"reconciled":reconciled_saved(saved)})
}
fn retained_journals(state: &Reviews) -> Result<Vec<(PathBuf, Value)>> {
    content_vault::checked_root(&state.root)?;
    let mut result = Vec::new();
    for entry in std::fs::read_dir(&state.root).map_err(err)? {
        let entry = entry.map_err(err)?;
        if !entry.file_name().to_string_lossy().starts_with("review-") {
            continue;
        }
        reject_link(&entry.path())?;
        if !entry.file_type().map_err(err)?.is_dir() {
            continue;
        }
        let path = entry.path().join("native-review.json");
        let id = entry
            .file_name()
            .into_string()
            .map_err(|_| "Retained review has a non-Unicode identity.")?;
        let value = match readable_journal(&path, &id) {
            Ok(value) => value,
            Err(error) => {
                json!({"reviewId":id,"submitted":true,"state":"unrecognized","invalidReason":format!("Retained review {id}: {error}")})
            }
        };
        result.push((path, value));
        if result.len() > 2000 {
            return Err(
                "Too many retained review stages. Inspect storage before continuing.".into(),
            );
        }
    }
    Ok(result)
}
pub(super) fn ensure_removable(app: &tauri::AppHandle, download: &str) -> Result<()> {
    let state = app.state::<Reviews>();
    let preparing = state.preparing.lock().map_err(err)?;
    if preparing.contains_key(download) {
        return Err("Cancel the archive review before removing its download.".into());
    }
    if state.rows.lock().map_err(err)?.values().any(|row| {
        row.receipt.id == download
            && (!row.submitted || (!known_finished(&row.state) && !row.stage_release_allowed))
    }) {
        return Err("Discard the unused review or wait for its native operation before removing this download.".into());
    }
    drop(preparing);
    if retained_journals(&state)?.iter().any(|(_, j)| {
        j["downloadId"] == download && j["submitted"] == true && !can_discard_saved(j)
    }) {
        return Err("This archive belongs to an unresolved native operation. Reconnect to its MO2 instance and refresh operations first.".into());
    }
    Ok(())
}

pub(super) fn ensure_resettable(app: &tauri::AppHandle) -> Result<()> {
    let Some(state) = app.try_state::<Reviews>() else {
        return Ok(());
    };
    if !state.preparing.lock().map_err(err)?.is_empty() {
        return Err("Cancel archive preparation before removing app data.".into());
    }
    if !state.lifecycle.lock().map_err(err)?.is_empty() {
        return Err("Wait for the current file-review operation before removing app data.".into());
    }
    if state
        .rows
        .lock()
        .map_err(err)?
        .values()
        .any(|row| row.submitted && !known_finished(&row.state) && !row.stage_release_allowed)
    {
        return Err(
            "Resolve every submitted native operation before removing its review evidence.".into(),
        );
    }
    if retained_journals(&state)?
        .iter()
        .any(|(_, row)| row["submitted"] == true && !can_discard_saved(row))
    {
        return Err("A retained native operation has an unresolved outcome. Reconnect to MO2 and refresh its recorded result before removing app data.".into());
    }
    Ok(())
}
fn field<'a>(value: &'a Value, name: &str) -> Result<&'a str> {
    value
        .get(name)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 1024)
        .ok_or_else(|| format!("Missing {name}."))
}
fn row(app: &tauri::AppHandle, value: &Value) -> Result<Review> {
    let state = app.state::<Reviews>();
    state
        .rows
        .lock()
        .map_err(err)?
        .get(field(value, "reviewId")?)
        .cloned()
        .ok_or("This review is no longer open. Prepare the archive again.".into())
}
struct ReviewLease<'a> {
    state: &'a Reviews,
    id: String,
}
impl Drop for ReviewLease<'_> {
    fn drop(&mut self) {
        self.state
            .lifecycle
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&self.id);
    }
}
fn review_lease<'a>(state: &'a Reviews, id: &str) -> Result<ReviewLease<'a>> {
    if !valid_review_id(id) {
        return Err("Invalid file-review identity.".into());
    }
    if !state.lifecycle.lock().map_err(err)?.insert(id.to_owned()) {
        return Err("This review is preparing, submitting or being discarded. Wait for that operation to finish.".into());
    }
    Ok(ReviewLease {
        state,
        id: id.to_owned(),
    })
}
fn new_id(state: &Reviews) -> String {
    format!(
        "review-{}-{}",
        now(),
        state.sequence.fetch_add(1, Ordering::AcqRel)
    )
}
fn query(receipt: &Receipt, target: &str) -> Result<Value> {
    if receipt.binding.content_intent == "update" {
        if !target.is_empty() {
            return Err("An update remains bound to its exact existing mod.".into());
        }
        let association = receipt
            .binding
            .association
            .as_ref()
            .ok_or("Choose the exact mod on this source before downloading.")?;
        Ok(json!({"itemId":association.item_id}))
    } else if receipt.binding.content_intent == "newContent" {
        if !modhunter_core::acquisition::valid_component(target) || target.len() > 120 {
            return Err("Enter a new MO2 mod name, at most 120 characters, without path or reserved filename characters.".into());
        }
        Ok(json!({"targetName":target}))
    } else {
        Err("Classify this exact file as an update or new content on its source before downloading.".into())
    }
}
fn validate_context(app: &tauri::AppHandle, row: &Review, context: &Value) -> Result<()> {
    if !crate::windows::mo2_connection_current(app, &row.endpoint, row.epoch) {
        return Err("The active MO2 connection changed. Prepare a fresh review.".into());
    }
    if context["gameRunning"] == true {
        return Err("Close the game before changing mod content.".into());
    }
    validate_native_lifetime(&row.context, context)?;
    for key in [
        "profileId",
        "profileName",
        "game",
        "gameRuntime",
        "executionEpoch",
        "target",
        "nativeContext",
    ] {
        if row.context[key] != context[key] {
            return Err(
                "The MO2 profile, runtime or target changed. Prepare a fresh review.".into(),
            );
        }
    }
    if let Some(association) = &row.receipt.binding.association
        && (context["profileId"] != association.profile_id
            || context["target"]["itemId"] != association.item_id)
    {
        return Err("This source belongs to a different MO2 profile or mod.".into());
    }
    acquisition::source_current(app, &row.receipt.binding)?;
    app.state::<WatchRuntime>()
        .store
        .validate_source_compatibility(
            &row.receipt.binding.watch_id,
            context["game"].as_str().unwrap_or(""),
            context["gameRuntime"].as_str().unwrap_or(""),
        )
}
fn validate_native_lifetime(reviewed: &Value, current: &Value) -> Result<()> {
    let valid = |context: &Value| {
        context["nativeLease"]
            .as_str()
            .is_some_and(|s| s.len() == 32 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            && context["nativeGeneration"].as_u64().is_some()
    };
    if !valid(reviewed)
        || !valid(current)
        || reviewed["nativeLease"] != current["nativeLease"]
        || reviewed["nativeGeneration"] != current["nativeGeneration"]
    {
        return Err(
            "Native MO2 state changed or its service restarted. Prepare a fresh review.".into(),
        );
    }
    Ok(())
}
fn compare(row: &Review, cancel: &impl Fn() -> bool) -> Result<Value> {
    if row.context["target"]["exists"] != true {
        let entries=row.inspected["entries"].as_array().ok_or("Archive manifest is missing.")?.iter().filter(|entry|!entry["path"].as_str().unwrap_or("").eq_ignore_ascii_case("meta.ini")).map(|entry| {
            let path=entry["path"].as_str().unwrap_or("");
            let lower=path.to_ascii_lowercase();
            let optional=lower.starts_with("docs/")||lower.starts_with("documentation/")||["readme","license","changelog"].iter().any(|p|lower.starts_with(p)&&!lower.contains('/'));
            json!({"path":path,"bytes":entry["bytes"],"state":"add","selectable":true,"selectedDefault":!optional,"optional":optional,"removalAuthorized":false,"renameHints":[]})
        }).collect::<Vec<_>>();
        return Ok(
            json!({"entries":entries,"identical":false,"digest":null,"scope":"New content in a new named MO2 mod. No existing mod is an overwrite target."}),
        );
    }
    let request:content_vault::Request=serde_json::from_value(json!({"action":"compare","root":row.context["nativeContext"]["modRoot"],
        "vault":row.context["nativeContext"]["vaultRoot"],"profileId":row.context["profileId"],"game":row.context["game"],
        "modName":row.context["target"]["modName"],"other":row.inspected["incomingRoot"]})).map_err(err)?;
    content_vault::execute(request, cancel)
}
fn exclusion_key(context: &Value) -> String {
    format!(
        "{:x}",
        Sha256::digest(
            serde_json::to_vec(&json!([
                context["profileId"],
                context["game"],
                context["target"]["modName"]
            ]))
            .unwrap_or_default()
        )
    )
}
fn archive_request(row: &Review, action: &str) -> archive_review::Request {
    archive_review::Request {
        action: action.into(),
        archive: row.archive.clone(),
        stage: row.stage.clone(),
        expected_sha256: row.receipt.sha256.clone().unwrap_or_default(),
        expected_bytes: row.receipt.bytes,
        selected: row.selected.iter().cloned().collect(),
    }
}

fn bounded_json(path: &Path, limit: u64) -> Result<(Vec<u8>, Value)> {
    reject_link(path)?;
    if std::fs::metadata(path).map_err(err)?.len() > limit {
        return Err("Owned review metadata exceeds its bound.".into());
    }
    let bytes = std::fs::read(path).map_err(err)?;
    let value = serde_json::from_slice(&bytes).map_err(err)?;
    Ok((bytes, value))
}
fn relative_review_path(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 32760
        || name.contains(['\\', ':', '\0'])
        || name.chars().any(char::is_control)
        || name
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err("Unsafe retained review path; its contents were preserved.".into());
    }
    Ok(())
}
fn hash_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn expected_file(
    expected: &mut BTreeMap<String, (u64, String)>,
    name: String,
    bytes: u64,
    sha: &str,
) -> Result<()> {
    relative_review_path(&name)?;
    if sha.len() != 64
        || !sha.bytes().all(|b| b.is_ascii_hexdigit())
        || expected.insert(name, (bytes, sha.to_owned())).is_some()
    {
        return Err("Ambiguous owned review file manifest.".into());
    }
    Ok(())
}
/// A completed archive marker authorizes only its exact extracted paths and
/// verified selected ZIP receipts. Unrecognized files and links stop cleanup.
/// Original downloads are never needed or removed by this stage-only cleanup.
fn cleanup_stage(root: &Path, stage: &Path, id: &str, expected_digest: Option<&str>) -> Result<()> {
    let root = content_vault::checked_root(root)?;
    let stage = content_vault::checked_root(stage)?;
    if !valid_review_id(id)
        || stage.parent() != Some(root.as_path())
        || stage.file_name().and_then(|n| n.to_str()) != Some(id)
    {
        return Err("Review staging path is not owned by this application.".into());
    }
    let journal = readable_journal(&stage.join("native-review.json"), id)?;
    if !can_discard_saved(&journal) {
        return Err("An unresolved native operation still owns this review.".into());
    }
    let (marker_bytes, marker) = bounded_json(
        &stage.join(".modhunter-review-owned.json"),
        128 * 1024 * 1024,
    )?;
    let marker_digest = hash_bytes(&marker_bytes);
    if marker["owner"] != "Modhunter-archive-review"
        || marker["schema"] != 1
        || expected_digest.is_some_and(|expected| expected != marker_digest)
        || journal["manifestDigest"]
            .as_str()
            .is_some_and(|expected| expected != marker_digest)
    {
        return Err("Archive review ownership changed; cleanup preserved its files.".into());
    }
    let files = marker["files"]
        .as_object()
        .filter(|files| files.len() <= 200_000)
        .ok_or("Missing complete owned archive manifest.")?;
    let directories = marker["directories"]
        .as_array()
        .filter(|dirs| dirs.len() <= 200_000)
        .ok_or("Missing complete archive directories.")?;
    let mut expected = BTreeMap::new();
    let mut expected_dirs = BTreeSet::from(["raw".to_owned()]);
    for (name, entry) in files {
        relative_review_path(name)?;
        expected_file(
            &mut expected,
            format!("raw/{name}"),
            entry["bytes"].as_u64().ok_or("Missing owned file size.")?,
            entry["sha256"].as_str().ok_or("Missing owned file hash.")?,
        )?;
    }
    for directory in directories {
        let name = directory.as_str().ok_or("Invalid owned directory.")?;
        relative_review_path(name)?;
        if !expected_dirs.insert(format!("raw/{name}")) {
            return Err("Duplicate owned archive directory.".into());
        }
    }
    expected_file(
        &mut expected,
        ".modhunter-review-owned.json".into(),
        marker_bytes.len() as u64,
        &marker_digest,
    )?;
    let journal_bytes = std::fs::read(stage.join("native-review.json")).map_err(err)?;
    expected_file(
        &mut expected,
        "native-review.json".into(),
        journal_bytes.len() as u64,
        &hash_bytes(&journal_bytes),
    )?;
    for child in std::fs::read_dir(&stage).map_err(err)? {
        let child = child.map_err(err)?;
        let name = child
            .file_name()
            .into_string()
            .map_err(|_| "Unknown review filename.")?;
        let Some(selection) = name
            .strip_prefix("selected-")
            .and_then(|name| name.strip_suffix(".json"))
        else {
            continue;
        };
        if selection.len() != 64 || !selection.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("Invalid selected archive identity.".into());
        }
        let (receipt_bytes, receipt) = bounded_json(&child.path(), 128 * 1024 * 1024)?;
        let zip_name = format!("selected-{selection}.zip");
        if receipt["selectionDigest"] != selection
            || receipt["originalSha256"] != marker["archiveSha256"]
            || receipt["path"].as_str().map(PathBuf::from) != Some(stage.join(&zip_name))
        {
            return Err("Selected archive receipt changed; its files were preserved.".into());
        }
        let selected = receipt["entries"]
            .as_array()
            .filter(|entries| !entries.is_empty() && entries.len() <= 200_000)
            .ok_or("Missing selected archive entries.")?;
        let mut selection_manifest = BTreeMap::new();
        for selected in selected {
            let path = selected["path"].as_str().ok_or("Missing selected path.")?;
            relative_review_path(path)?;
            let entry = json!({"bytes":selected["bytes"],"sha256":selected["sha256"]});
            let source = format!(
                "{}{path}",
                marker["prefix"].as_str().ok_or("Missing archive prefix.")?
            );
            if path.eq_ignore_ascii_case("meta.ini")
                || files.get(&source) != Some(&entry)
                || selection_manifest.insert(path.to_owned(), entry).is_some()
            {
                return Err("Selected paths no longer match the original owned manifest.".into());
            }
        }
        let selection_hash=hash_bytes(&serde_json::to_vec(&json!({"originalSha256":marker["archiveSha256"],"manifestDigest":marker_digest,"selected":selection_manifest})).map_err(err)?);
        if selection_hash != selection {
            return Err("Selected archive provenance changed.".into());
        }
        expected_file(
            &mut expected,
            zip_name,
            receipt["bytes"]
                .as_u64()
                .ok_or("Missing selected archive size.")?,
            receipt["sha256"]
                .as_str()
                .ok_or("Missing selected archive hash.")?,
        )?;
        expected_file(
            &mut expected,
            name,
            receipt_bytes.len() as u64,
            &hash_bytes(&receipt_bytes),
        )?;
    }
    let mut observed = BTreeMap::new();
    let mut observed_dirs = BTreeSet::new();
    let mut stack = vec![stage.clone()];
    while let Some(directory) = stack.pop() {
        reject_link(&directory)?;
        for child in std::fs::read_dir(directory).map_err(err)? {
            let child = child.map_err(err)?;
            let path = child.path();
            reject_link(&path)?;
            let relative = path
                .strip_prefix(&stage)
                .map_err(err)?
                .to_str()
                .ok_or("Unknown retained path.")?
                .replace('\\', "/");
            relative_review_path(&relative)?;
            let metadata = child.metadata().map_err(err)?;
            if metadata.is_dir() {
                observed_dirs.insert(relative);
                stack.push(path);
            } else if metadata.is_file() {
                if !expected.contains_key(&relative) {
                    return Err(
                        "Unexpected review files were preserved; inspect them before cleanup."
                            .into(),
                    );
                }
                observed.insert(
                    relative,
                    modhunter_core::acquisition::file_hash(&path, || false)?,
                );
            } else {
                return Err("A special review filesystem object was preserved.".into());
            }
            if observed.len() + observed_dirs.len() > 600_000 {
                return Err("Review cleanup inventory exceeds its complete limit.".into());
            }
        }
    }
    if observed
        .iter()
        .any(|(name, entry)| expected.get(name) != Some(entry))
        || !observed_dirs.is_subset(&expected_dirs)
        || !observed.contains_key("native-review.json")
        || !observed.contains_key(".modhunter-review-owned.json")
    {
        return Err("Owned review files changed; cleanup retained the stage.".into());
    }
    // Missing files are safe after an interrupted cleanup. Present files must
    // still match the complete original manifest; extras never acquire ownership.
    for (name, identity) in &observed {
        if matches!(
            name.as_str(),
            "native-review.json" | ".modhunter-review-owned.json"
        ) {
            continue;
        }
        let path = stage.join(name);
        check_stage_ancestors(&stage, &path)?;
        if modhunter_core::acquisition::file_hash(&path, || false)? != *identity {
            return Err("Review file changed immediately before cleanup.".into());
        }
        std::fs::remove_file(path).map_err(err)?;
    }
    let mut dirs = observed_dirs.into_iter().collect::<Vec<_>>();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.split('/').count()));
    for name in dirs {
        let path = stage.join(name);
        check_stage_ancestors(&stage, &path)?;
        std::fs::remove_dir(path).map_err(err)?;
    }
    // Keep the journal and ownership receipt until every data path was removed.
    let children = std::fs::read_dir(&stage)
        .map_err(err)?
        .collect::<std::io::Result<Vec<_>>>()
        .map_err(err)?;
    if children.len() != 2 {
        return Err("New review data appeared during cleanup; it was retained.".into());
    }
    for name in [".modhunter-review-owned.json", "native-review.json"] {
        let path = stage.join(name);
        check_stage_ancestors(&stage, &path)?;
        if expected.get(name) != Some(&modhunter_core::acquisition::file_hash(&path, || false)?) {
            return Err("Review ownership changed during cleanup.".into());
        }
        std::fs::remove_file(path).map_err(err)?;
    }
    std::fs::remove_dir(stage).map_err(err)
}
fn check_stage_ancestors(stage: &Path, path: &Path) -> Result<()> {
    let relative = path.strip_prefix(stage).map_err(err)?;
    let mut walked = stage.to_path_buf();
    reject_link(&walked)?;
    for component in relative.components() {
        walked.push(component);
        reject_link(&walked)?;
    }
    Ok(())
}
struct PreparedStage<'a> {
    state: &'a Reviews,
    row: Review,
    committed: bool,
}
impl Drop for PreparedStage<'_> {
    fn drop(&mut self) {
        if self.committed {
            return;
        }
        if let Err(error) = cleanup_stage(
            &self.state.root,
            &self.row.stage,
            &self.row.id,
            self.row.inspected["manifestDigest"].as_str(),
        ) {
            self.state.cleanup_errors.lock().unwrap_or_else(|e|e.into_inner()).insert(self.row.id.clone(),format!("Preparation did not finish. Its owned stage was retained because cleanup could not complete: {error}"));
        }
    }
}
fn prepare(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let download = field(value, "downloadId")?.to_string();
    let state = app.state::<Reviews>();
    if retained_journals(&state)?.iter().any(|(_, saved)| {
        saved.get("invalidReason").is_some()
            || (saved["downloadId"] == download && !can_discard_saved(saved))
    }) {
        return Err("A retained native review has unresolved evidence. Reconnect to its MO2 instance and refresh operations before preparing this download again.".into());
    }
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut preparing = state.preparing.lock().map_err(err)?;
        if preparing.len() >= 2 || preparing.contains_key(&download) {
            return Err(
                "Two archive reviews can run at once. Wait for or cancel the current review."
                    .into(),
            );
        }
        let rows = state.rows.lock().map_err(err)?;
        if rows.len() >= MAX_REVIEWS {
            return Err("Discard an unused file review before preparing another.".into());
        }
        if rows.values().any(|r| {
            r.receipt.id == download
                && (!r.submitted || (!known_finished(&r.state) && !r.stage_release_allowed))
        }) {
            return Err(
                "This download already has an active review. Open it from the review queue.".into(),
            );
        }
        preparing.insert(download.clone(), cancel.clone());
    }
    struct Guard<'a>(&'a Reviews, String, tauri::AppHandle);
    impl Drop for Guard<'_> {
        fn drop(&mut self) {
            self.0
                .preparing
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .remove(&self.1);
            notify(&self.2);
        }
    }
    let _guard = Guard(&state, download.clone(), app.clone());
    notify(app);
    let cancelled = || {
        cancel.load(Ordering::Acquire)
            || app.state::<WatchRuntime>().stopping.load(Ordering::Acquire)
    };
    let (receipt, archive, acquisitions) = acquisition::verified(app, &download, &cancelled)?;
    let target = value["targetName"].as_str().unwrap_or("");
    let (client, endpoint, epoch) = crate::windows::mo2_connection(app)?;
    let context = client
        .native_content(&endpoint, "content-context", &query(&receipt, target)?)
        .map_err(err)?;
    let id = new_id(&state);
    let _lease = review_lease(&state, &id)?;
    let mut row = Review {
        stage: state.root.join(&id),
        id,
        receipt,
        archive,
        acquisitions,
        context,
        endpoint,
        epoch,
        created: now(),
        inspected: Value::Null,
        compared: Value::Null,
        selected: BTreeSet::new(),
        submitted: false,
        state: "review".into(),
        job_id: None,
        stage_release_allowed: false,
        cancel: cancel.clone(),
    };
    validate_context(app, &row, &row.context)?;
    row.inspected = archive_review::inspect(&archive_request(&row, "inspect"), &cancelled)?;
    let mut prepared = PreparedStage {
        state: &state,
        row: row.clone(),
        committed: false,
    };
    // A restart can discover this stage even when comparison/cancellation fails.
    journal(&row)?;
    row.compared = compare(&row, &cancelled)?;
    let exclusions = state
        .exclusions
        .lock()
        .map_err(err)?
        .get(&exclusion_key(&row.context))
        .cloned()
        .unwrap_or_default();
    let entries = row.compared["entries"]
        .as_array()
        .ok_or("File comparison returned no complete manifest.")?;
    row.selected = entries
        .iter()
        .filter(|e| {
            e["selectable"] == true
                && e["selectedDefault"] == true
                && !exclusions.contains(e["path"].as_str().unwrap_or(""))
        })
        .filter_map(|e| e["path"].as_str().map(str::to_owned))
        .collect();
    if cancelled() {
        return Err("Archive review cancelled.".into());
    }
    validate_context(app, &row, &row.context)?;
    let mut rows = state.rows.lock().map_err(err)?;
    if rows.len() >= MAX_REVIEWS
        || rows
            .values()
            .map(|r| r.compared["entries"].as_array().map_or(0, Vec::len))
            .sum::<usize>()
            + entries.len()
            > MAX_ENTRIES
    {
        return Err("Open file reviews reached their complete-manifest limit. Discard unused reviews and try again.".into());
    }
    journal(&row)?;
    let result = detail(&row);
    rows.insert(row.id.clone(), row);
    prepared.committed = true;
    Ok(json!({"review":result}))
}
fn save_selection(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let state = app.state::<Reviews>();
    let _lease = review_lease(&state, field(value, "reviewId")?)?;
    let mut rows = state.rows.lock().map_err(err)?;
    let row = rows
        .get_mut(field(value, "reviewId")?)
        .ok_or("Review expired.")?;
    if row.submitted {
        return Err("A submitted native operation cannot be edited.".into());
    }
    let entries = row.compared["entries"]
        .as_array()
        .ok_or("Missing complete manifest.")?;
    let choices = value["selected"]
        .as_array()
        .filter(|v| v.len() <= 200_000)
        .ok_or("Choose exact incoming file paths.")?;
    let allowed = entries
        .iter()
        .filter(|e| e["selectable"] == true)
        .filter_map(|e| e["path"].as_str())
        .collect::<BTreeSet<_>>();
    let mut selected = BTreeSet::new();
    for choice in choices {
        let path = choice.as_str().ok_or("Invalid selected path.")?;
        if !allowed.contains(path) || !selected.insert(path.to_owned()) {
            return Err("Selection contains a duplicate or non-incoming path.".into());
        }
    }
    if selected.is_empty() {
        return Err("Select at least one incoming file.".into());
    }
    if value["rememberOptional"] == true {
        let key = exclusion_key(&row.context);
        let exclusions = entries
            .iter()
            .filter(|e| e["optional"] == true)
            .filter_map(|e| e["path"].as_str())
            .filter(|p| !selected.contains(*p))
            .map(str::to_owned)
            .collect();
        let mut stored = state.exclusions.lock().map_err(err)?;
        let mut draft = stored.clone();
        draft.insert(key, exclusions);
        if serde_json::to_vec(&draft).map_err(err)?.len() > 8 * 1024 * 1024 {
            return Err("Saved optional exclusions are full.".into());
        }
        atomic_json(&state.root.join("optional-exclusions.json"), &draft)?;
        *stored = draft;
    }
    row.selected = selected;
    Ok(json!({"review":detail(row)}))
}
fn submit(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    if value["confirm"] != "INSTALL" {
        return Err("Confirm INSTALL after reviewing the exact file and target.".into());
    }
    let state = app.state::<Reviews>();
    let _lease = review_lease(&state, field(value, "reviewId")?)?;
    let mut current = row(app, value)?;
    if current.submitted {
        return Err(
            "This review was already submitted. Refresh native operations; do not replay it."
                .into(),
        );
    }
    if now().saturating_sub(current.created) > REVIEW_AGE {
        return Err("This file review expired. Prepare the current archive again.".into());
    }
    if current.compared["identical"] == true {
        return Err(
            "The complete incoming content already matches this mod. No installation is needed."
                .into(),
        );
    }
    let cancel = || {
        current.cancel.load(Ordering::Acquire)
            || app.state::<WatchRuntime>().stopping.load(Ordering::Acquire)
    };
    acquisition::verified(app, &current.receipt.id, &cancel)?;
    let (client, endpoint, epoch) = crate::windows::mo2_connection(app)?;
    if epoch != current.epoch || endpoint != current.endpoint {
        return Err("The MO2 connection changed. Prepare a new review.".into());
    }
    let target = if current.receipt.binding.content_intent == "newContent" {
        current.context["target"]["modName"].as_str().unwrap_or("")
    } else {
        ""
    };
    let context = client
        .native_content(
            &endpoint,
            "content-context",
            &query(&current.receipt, target)?,
        )
        .map_err(err)?;
    validate_context(app, &current, &context)?;
    let fresh = compare(&current, &cancel)?;
    if fresh["digest"] != current.compared["digest"] {
        return Err("Installed or incoming files changed. Prepare a fresh file review.".into());
    }
    let prepared = if current.inspected["requiresInstaller"] == false
        && current.inspected["layout"] == "gameData"
    {
        if current.selected.is_empty() {
            return Err("Select at least one file before installing.".into());
        }
        archive_review::inspect(&archive_request(&current,"prepareSelected"),&cancel)?["preparedArchive"].clone()
    } else {
        Value::Null
    };
    if cancel() {
        return Err("Review cancelled before handoff.".into());
    }
    current.context = context;
    let mut compact_prepared = prepared;
    if let Some(object) = compact_prepared.as_object_mut() {
        object.remove("entries");
    }
    let request = json!({"reviewId":current.id,"downloadId":current.receipt.id,"contextId":current.context["contextId"],"confirm":"INSTALL",
        "targetName":if current.receipt.binding.content_intent=="newContent" {current.context["target"]["modName"].clone()} else {Value::Null},
        "resolvedArchive":{"archivePath":current.archive,"acquisitionRoot":current.acquisitions,"receipt":current.receipt,
            "review":{"stage":current.stage,"incomingRoot":current.inspected["incomingRoot"],"archiveIdentity":{"bytes":current.receipt.bytes,"sha256":current.receipt.sha256},
                "manifestDigest":current.inspected["manifestDigest"],"comparisonDigest":current.compared["digest"],"requiresInstaller":current.inspected["requiresInstaller"],
                "preparedArchive":compact_prepared}}});
    {
        let state = app.state::<Reviews>();
        let mut rows = state.rows.lock().map_err(err)?;
        let latest = rows.get_mut(&current.id).ok_or("Review was discarded.")?;
        if latest.submitted
            || latest.selected != current.selected
            || latest.cancel.load(Ordering::Acquire)
        {
            return Err("Review was changed, cancelled or submitted before handoff.".into());
        }
        current.submitted = true;
        current.state = "submitting".into();
        journal(&current)?;
        *latest = current.clone();
    }
    match client.native_content(&endpoint, "content-install", &request) {
        Ok(result) => {
            current.job_id = result["jobId"].as_str().map(str::to_owned);
            current.state = result["state"].as_str().unwrap_or("unknown").into();
            if current.job_id.is_none() {
                current.state = "unknown".into();
            }
            journal(&current)?;
            app.state::<Reviews>()
                .rows
                .lock()
                .map_err(err)?
                .insert(current.id.clone(), current.clone());
            if current.cancel.load(Ordering::Acquire) {
                let _ = forward_cancel(app, &current);
            }
            let changed = !crate::windows::mo2_connection_current(app, &endpoint, epoch);
            Ok(
                json!({"review":detail(&current),"operation":result,"contextChanged":changed,
                "message":if changed{"MO2 accepted the operation before the connection changed. Reconnect and inspect its recorded result."}else{"MO2 accepted the reviewed operation. Progress and recovery appear below."}}),
            )
        }
        Err(error) => {
            if matches!(error, CoreError::NativeRejected(_)) {
                current.submitted = false;
                current.state = "review".into();
            } else {
                current.state = "unknown".into();
            }
            journal(&current)?;
            app.state::<Reviews>()
                .rows
                .lock()
                .map_err(err)?
                .insert(current.id.clone(), current);
            Err(format!(
                "{error} Refresh native operations before another attempt."
            ))
        }
    }
}
fn forward_cancel(app: &tauri::AppHandle, row: &Review) -> Result<Value> {
    let (client, endpoint, _) = crate::windows::mo2_connection(app)?;
    if endpoint != row.endpoint {
        return Err("Reconnect to the MO2 instance that accepted this operation.".into());
    }
    client
        .native_content(
            &endpoint,
            "content-cancel",
            &json!({"jobId":row.job_id,"reviewId":row.id,"downloadId":row.receipt.id}),
        )
        .map_err(err)
}
fn operations(app: &tauri::AppHandle) -> Result<Value> {
    let (client, endpoint, _) = crate::windows::mo2_connection(app)?;
    let result = client
        .native_content(&endpoint, "content-jobs", &json!({}))
        .map_err(err)?;
    let jobs = result["jobs"]
        .as_array()
        .ok_or("Native operation list is missing.")?;
    let state = app.state::<Reviews>();
    let mut pending = Vec::new();
    {
        let mut rows = state.rows.lock().map_err(err)?;
        for job in jobs {
            if let Some(row) = job["reviewId"].as_str().and_then(|id| rows.get_mut(id)) {
                if state.lifecycle.lock().map_err(err)?.contains(&row.id) {
                    continue;
                }
                if row.endpoint != endpoint
                    || row.receipt.id != job["downloadId"]
                    || row.context["profileId"] != job["profileId"]
                    || row.job_id.as_deref().is_some_and(|id| job["jobId"] != id)
                {
                    continue;
                }
                row.state = job["state"].as_str().unwrap_or("unknown").into();
                row.job_id = job["jobId"].as_str().map(str::to_owned);
                row.submitted = true;
                row.stage_release_allowed = job["stageReleaseAllowed"] == true
                    && matches!(row.state.as_str(), "unknown" | "interrupted")
                    && row.job_id.is_some();
                journal(row)?;
                if row.cancel.load(Ordering::Acquire) && !known_finished(&row.state) {
                    pending.push(row.clone());
                }
            }
        }
    }
    for row in pending {
        let _ = forward_cancel(app, &row);
    }
    for (path, mut saved) in retained_journals(&state)? {
        if saved.get("invalidReason").is_some() {
            continue;
        }
        let id = saved["reviewId"]
            .as_str()
            .ok_or("Missing retained review identity.")?;
        let Ok(_lease) = review_lease(&state, id) else {
            continue;
        };
        if let Some(job) = jobs.iter().find(|job| {
            job["reviewId"] == saved["reviewId"]
                && job["downloadId"] == saved["downloadId"]
                && job["profileId"] == saved["profileId"]
                && saved["jobId"].as_str().is_none_or(|id| job["jobId"] == id)
        }) {
            saved["state"] = job["state"].clone();
            saved["jobId"] = job["jobId"].clone();
            saved["stageReleaseAllowed"] = json!(
                job["stageReleaseAllowed"] == true
                    && matches!(job["state"].as_str(), Some("unknown" | "interrupted"))
                    && job["jobId"].as_str().is_some_and(|id| !id.is_empty())
            );
            atomic_json(&path, &saved)?;
        }
    }
    Ok(json!({"operations":result}))
}
fn cancel_review(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    if let Some(download) = value["downloadId"].as_str()
        && let Some(cancel) = app
            .state::<Reviews>()
            .preparing
            .lock()
            .map_err(err)?
            .get(download)
    {
        cancel.store(true, Ordering::Release);
        return Ok(json!({"message":"Archive review cancellation requested."}));
    }
    let current = row(app, value)?;
    current.cancel.store(true, Ordering::Release);
    if current.submitted {
        let result = forward_cancel(app, &current);
        if let Err(error) = result {
            return Ok(
                json!({"message":format!("Cancellation recorded; reconnect and refresh operations to deliver it. {error}")}),
            );
        }
    }
    Ok(
        json!({"message":"Cancellation requested. An installer already running in MO2 must finish or be cancelled in its own dialog."}),
    )
}
fn discard(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let state = app.state::<Reviews>();
    let id = field(value, "reviewId")?;
    let _lease = review_lease(&state, id)?;
    let current = state.rows.lock().map_err(err)?.get(id).cloned();
    let digest = if let Some(row) = &current {
        if row.submitted && !known_finished(&row.state) && !row.stage_release_allowed {
            return Err("An unresolved native operation still owns this review.".into());
        }
        if row.stage_release_allowed && value["confirm"] != "DISCARD REVIEW" {
            return Err("Confirm DISCARD REVIEW to remove reconciled archive-stage evidence while keeping the MO2 recovery ledger and vault.".into());
        }
        row.cancel.store(true, Ordering::Release);
        row.inspected["manifestDigest"].as_str().map(str::to_owned)
    } else {
        let saved = retained_journals(&state)?
            .into_iter()
            .find(|(_, saved)| saved["reviewId"] == id)
            .ok_or("Retained review no longer exists.")?
            .1;
        if !can_discard_saved(&saved) {
            return Err("Unresolved or unreadable native evidence cannot be discarded.".into());
        }
        if reconciled_saved(&saved) && value["confirm"] != "DISCARD REVIEW" {
            return Err("Confirm DISCARD REVIEW to remove the reconciled stage. The unknown/interrupted operation outcome and its MO2 recovery vault remain recorded.".into());
        }
        saved["manifestDigest"].as_str().map(str::to_owned)
    };
    if let Err(error) = cleanup_stage(&state.root, &state.root.join(id), id, digest.as_deref()) {
        state
            .cleanup_errors
            .lock()
            .map_err(err)?
            .insert(id.to_owned(), error.clone());
        return Err(error);
    }
    state.rows.lock().map_err(err)?.remove(id);
    state.cleanup_errors.lock().map_err(err)?.remove(id);
    Ok(
        json!({"message":"File review and its prepared copies removed. The original download is retained."}),
    )
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    match kind {
        "prepareReview" => prepare(app, value),
        "openReview" => Ok(json!({"review":detail(&row(app,value)?)})),
        "saveReviewSelection" => save_selection(app, value),
        "installReview" => submit(app, value),
        "cancelReview" => cancel_review(app, value),
        "discardReview" => discard(app, value),
        "contentJobs" => operations(app),
        "cancelContentJob" => {
            let (client, endpoint, _) = crate::windows::mo2_connection(app)?;
            let current = client
                .native_content(&endpoint, "content-jobs", &json!({}))
                .map_err(err)?;
            let selected = current["jobs"]
                .as_array()
                .and_then(|jobs| {
                    jobs.iter().find(|job| {
                        job["jobId"] == value["jobId"]
                            && job["reviewId"] == value["reviewId"]
                            && job["downloadId"] == value["downloadId"]
                    })
                })
                .ok_or("This native operation changed. Refresh operations before cancelling.")?;
            Ok(
                json!({"result":client.native_content(&endpoint,"content-cancel",&json!({"jobId":selected["jobId"],"reviewId":selected["reviewId"],"downloadId":selected["downloadId"]})).map_err(err)?}),
            )
        }
        "contentBackups"
        | "contentRecovery"
        | "contentBatchRestore"
        | "duplicateScan"
        | "duplicateRecovery" => {
            let (client, endpoint, _) = crate::windows::mo2_connection(app)?;
            let route = match kind {
                "contentBackups" => "content-backups",
                "contentRecovery" => "content-recovery",
                "contentBatchRestore" => "content-batch-restore",
                "duplicateScan" => "duplicates",
                _ => "duplicate-recovery",
            };
            let allowed: &[&str] = match kind {
                "contentBackups" => &["itemId"],
                "duplicateScan" => &[],
                "contentRecovery" => &[
                    "itemId",
                    "backupId",
                    "action",
                    "confirm",
                    "expectedDigest",
                    "retentionDays",
                    "retentionCount",
                    "retentionBytes",
                    "selected",
                ],
                "contentBatchRestore" => &["backups", "confirm"],
                _ => &[
                    "action",
                    "modName",
                    "retainedModName",
                    "id",
                    "confirm",
                    "expectedDigest",
                ],
            };
            let object = value
                .as_object()
                .ok_or("Native operation parameters must be an object.")?;
            if object.keys().any(|key| !allowed.contains(&key.as_str())) {
                return Err("Native content operations accept only exact record identities and reviewed choices.".into());
            }
            let mut request = value.clone();
            if !matches!(kind, "contentBackups" | "duplicateScan") {
                let query = if let Some(item) = value["itemId"].as_str().filter(|s| !s.is_empty()) {
                    json!({"itemId":item})
                } else if let Some(backup) = value["backupId"].as_str() {
                    json!({"backupId":backup})
                } else {
                    json!({})
                };
                let context = client
                    .native_content(&endpoint, "content-context", &query)
                    .map_err(err)?;
                request["contextId"] = context["contextId"].clone();
                request["reviewId"] = json!(new_id(&app.state::<Reviews>()));
                if let Some(object) = request.as_object_mut() {
                    object.remove("itemId");
                }
            }
            Ok(json!({"result":client.native_content(&endpoint,route,&request).map_err(err)?}))
        }
        _ => Err("Unsupported file review operation.".into()),
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    fn state(root: PathBuf) -> Reviews {
        Reviews {
            root,
            rows: Mutex::new(BTreeMap::new()),
            preparing: Mutex::new(BTreeMap::new()),
            exclusions: Mutex::new(BTreeMap::new()),
            lifecycle: Mutex::new(BTreeSet::new()),
            cleanup_errors: Mutex::new(BTreeMap::new()),
            sequence: AtomicU64::new(1),
        }
    }
    fn fixture(root: &Path) -> (PathBuf, String) {
        let stage = root.join("review-123-1");
        std::fs::create_dir_all(stage.join("raw")).unwrap();
        std::fs::write(stage.join("raw/file.txt"), b"owned content").unwrap();
        let marker = json!({"schema":1,"owner":"Modhunter-archive-review","archive":root.join("original.zip"),
            "archiveBytes":100,"archiveSha256":"a".repeat(64),"prefix":"","layout":"gameData","requiresInstaller":false,
            "files":{"file.txt":{"bytes":13,"sha256":hash_bytes(b"owned content")}},"directories":[]});
        let bytes = serde_json::to_vec(&marker).unwrap();
        let digest = hash_bytes(&bytes);
        std::fs::write(stage.join(".modhunter-review-owned.json"), bytes).unwrap();
        atomic_json(&stage.join("native-review.json"),&json!({"schema":1,"owner":"Modhunter-content-review",
            "reviewId":"review-123-1","downloadId":"download-123","submitted":false,"state":"review","manifestDigest":digest})).unwrap();
        (stage, digest)
    }
    #[test]
    fn native_lifetime_rejects_round_trip_and_service_restart() {
        let original = json!({"nativeLease":"a".repeat(32),"nativeGeneration":7});
        assert!(validate_native_lifetime(&original, &original).is_ok());
        let mut changed = original.clone();
        changed["nativeGeneration"] = json!(9);
        assert!(validate_native_lifetime(&original, &changed).is_err());
        changed = original.clone();
        changed["nativeLease"] = json!("b".repeat(32));
        assert!(validate_native_lifetime(&original, &changed).is_err());
        changed = original.clone();
        changed["nativeGeneration"] = Value::Null;
        assert!(validate_native_lifetime(&original, &changed).is_err());
    }
    #[test]
    fn a_review_has_only_one_mutating_lifecycle_lease() {
        let state = state(PathBuf::new());
        let first = review_lease(&state, "review-1-1").unwrap();
        assert!(review_lease(&state, "review-1-1").is_err());
        assert!(review_lease(&state, "review-1-2").is_ok());
        drop(first);
        assert!(review_lease(&state, "review-1-1").is_ok());
        assert!(review_lease(&state, "../review-1").is_err());
    }
    #[test]
    fn completed_owned_stage_cleanup_keeps_original_download() {
        let temp = tempfile::tempdir().unwrap();
        let (stage, digest) = fixture(temp.path());
        std::fs::write(temp.path().join("original.zip"), b"original download").unwrap();
        cleanup_stage(temp.path(), &stage, "review-123-1", Some(&digest)).unwrap();
        assert!(!stage.exists());
        assert_eq!(
            std::fs::read(temp.path().join("original.zip")).unwrap(),
            b"original download"
        );
    }
    #[test]
    fn cleanup_preserves_unexpected_or_modified_files() {
        let temp = tempfile::tempdir().unwrap();
        let (stage, digest) = fixture(temp.path());
        std::fs::write(stage.join("unexpected.bin"), b"keep").unwrap();
        assert!(cleanup_stage(temp.path(), &stage, "review-123-1", Some(&digest)).is_err());
        assert!(stage.join("raw/file.txt").exists());
        std::fs::remove_file(stage.join("unexpected.bin")).unwrap();
        std::fs::write(stage.join("raw/file.txt"), b"changed bytes").unwrap();
        assert!(cleanup_stage(temp.path(), &stage, "review-123-1", Some(&digest)).is_err());
        assert_eq!(
            std::fs::read(stage.join("raw/file.txt")).unwrap(),
            b"changed bytes"
        );
    }
    #[test]
    fn interrupted_cleanup_can_finish_without_original_download() {
        let temp = tempfile::tempdir().unwrap();
        let (stage, digest) = fixture(temp.path());
        std::fs::remove_file(stage.join("raw/file.txt")).unwrap();
        cleanup_stage(temp.path(), &stage, "review-123-1", Some(&digest)).unwrap();
        assert!(!stage.exists());
    }
    #[test]
    fn retained_unknown_acceptance_is_not_deletion_authority() {
        let mut saved = json!({"submitted":true,"state":"unknown","jobId":null});
        assert!(!can_discard_saved(&saved));
        saved["stageReleaseAllowed"] = json!(true);
        assert!(!can_discard_saved(&saved));
        saved["jobId"] = json!("matched-native-job");
        assert!(reconciled_saved(&saved));
        assert!(can_discard_saved(&saved));
        saved["invalidReason"] = json!("corrupt");
        assert!(!can_discard_saved(&saved));
    }
    #[test]
    fn restart_lists_unreadable_and_valid_stages_without_replaying() {
        let temp = tempfile::tempdir().unwrap();
        let (stage, _) = fixture(temp.path());
        let state = state(temp.path().to_path_buf());
        let saved = retained_journals(&state).unwrap();
        assert_eq!(saved.len(), 1);
        let row = retained_summary(&state, &saved[0].1);
        assert_eq!(row["recoveryOnly"], true);
        assert_eq!(row["canDiscard"], true);
        std::fs::write(stage.join("native-review.json"), b"truncated").unwrap();
        let saved = retained_journals(&state).unwrap();
        assert_eq!(retained_summary(&state, &saved[0].1)["canDiscard"], false);
        assert!(saved[0].1["invalidReason"].is_string());
    }
}
