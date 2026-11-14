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
    cancel: Arc<AtomicBool>,
}
struct Reviews {
    root: PathBuf,
    rows: Mutex<BTreeMap<String, Review>>,
    preparing: Mutex<BTreeMap<String, Arc<AtomicBool>>>,
    exclusions: Mutex<BTreeMap<String, BTreeSet<String>>>,
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
        "requiresInstaller":row.inspected["requiresInstaller"],"identical":row.compared["identical"]})
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
    let rows = state
        .rows
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .values()
        .map(summary)
        .collect::<Vec<_>>();
    json!({"rows":rows,"preparing":preparing})
}
fn known_finished(state: &str) -> bool {
    matches!(state, "complete" | "failed" | "cancelled")
}
fn journal(row: &Review) -> Result<()> {
    atomic_json(
        &row.stage.join("native-review.json"),
        &json!({"schema":1,"owner":"Modhunter-content-review","reviewId":row.id,
        "downloadId":row.receipt.id,"submitted":row.submitted,"state":row.state,"jobId":row.job_id,
        "profileId":row.context["profileId"],"itemId":row.context["target"]["itemId"],"contextId":row.context["contextId"]}),
    )
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
        if !path.exists() {
            continue;
        }
        reject_link(&path)?;
        if std::fs::metadata(&path).map_err(err)?.len() > 16 * 1024 {
            return Err("A retained review journal needs manual inspection.".into());
        }
        let value: Value =
            serde_json::from_slice(&std::fs::read(&path).map_err(err)?).map_err(err)?;
        if value["owner"] != "Modhunter-content-review" || value["schema"] != 1 {
            return Err("Unrecognized retained review journal.".into());
        }
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
    if state
        .rows
        .lock()
        .map_err(err)?
        .values()
        .any(|row| row.receipt.id == download && (!row.submitted || !known_finished(&row.state)))
    {
        return Err("Discard the unused review or wait for its native operation before removing this download.".into());
    }
    drop(preparing);
    if retained_journals(&state)?.iter().any(|(_, j)| {
        j["downloadId"] == download
            && j["submitted"] == true
            && !known_finished(j["state"].as_str().unwrap_or("unknown"))
    }) {
        return Err("This archive belongs to an unresolved native operation. Reconnect to its MO2 instance and refresh operations first.".into());
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
    if let Some(association) = &row.receipt.binding.association {
        if context["profileId"] != association.profile_id
            || context["target"]["itemId"] != association.item_id
        {
            return Err("This source belongs to a different MO2 profile or mod.".into());
        }
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
fn compare(row: &Review, cancel: &impl Fn() -> bool) -> Result<Value> {
    if row.context["target"]["exists"] != true {
        let entries=row.inspected["entries"].as_array().ok_or("Archive manifest is missing.")?.iter().map(|entry| {
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
fn prepare(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let download = field(value, "downloadId")?.to_string();
    let state = app.state::<Reviews>();
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
        if rows
            .values()
            .any(|r| r.receipt.id == download && (!r.submitted || !known_finished(&r.state)))
        {
            return Err(
                "This download already has an active review. Open it from the review queue.".into(),
            );
        }
        preparing.insert(download.clone(), cancel.clone());
    }
    struct Guard<'a>(&'a Reviews, String);
    impl Drop for Guard<'_> {
        fn drop(&mut self) {
            self.0
                .preparing
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .remove(&self.1);
        }
    }
    let _guard = Guard(&state, download.clone());
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
        cancel: cancel.clone(),
    };
    validate_context(app, &row, &row.context)?;
    row.inspected = archive_review::inspect(&archive_request(&row, "inspect"), &cancelled)?;
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
    Ok(json!({"review":result}))
}
fn save_selection(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let state = app.state::<Reviews>();
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
                if row.endpoint != endpoint
                    || row.receipt.id != job["downloadId"]
                    || row.context["profileId"] != job["profileId"]
                {
                    continue;
                }
                row.state = job["state"].as_str().unwrap_or("unknown").into();
                row.job_id = job["jobId"].as_str().map(str::to_owned);
                row.submitted = true;
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
        if let Some(job) = jobs.iter().find(|job| {
            job["reviewId"] == saved["reviewId"]
                && job["downloadId"] == saved["downloadId"]
                && job["profileId"] == saved["profileId"]
        }) {
            saved["state"] = job["state"].clone();
            saved["jobId"] = job["jobId"].clone();
            atomic_json(&path, &saved)?;
        }
    }
    Ok(json!({"operations":result}))
}
fn cancel_review(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    if let Some(download) = value["downloadId"].as_str() {
        if let Some(cancel) = app
            .state::<Reviews>()
            .preparing
            .lock()
            .map_err(err)?
            .get(download)
        {
            cancel.store(true, Ordering::Release);
            return Ok(json!({"message":"Archive review cancellation requested."}));
        }
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
    let mut rows = state.rows.lock().map_err(err)?;
    let id = field(value, "reviewId")?;
    let row = rows.get(id).ok_or("Review is no longer open.")?;
    if row.submitted && !known_finished(&row.state) {
        return Err("An unresolved native operation still owns this review.".into());
    }
    row.cancel.store(true, Ordering::Release);
    let root = content_vault::checked_root(&state.root)?;
    let stage = content_vault::checked_root(&row.stage)?;
    if stage.parent() != Some(root.as_path()) {
        return Err("Review staging path is not owned by this application.".into());
    }
    // Verify every path before recursive cleanup; reparse points are never followed.
    let mut stack = vec![stage.clone()];
    let mut count = 0usize;
    while let Some(path) = stack.pop() {
        reject_link(&path)?;
        count += 1;
        if count > 600_000 {
            return Err("Review staging exceeds its cleanup limit.".into());
        }
        if path.is_dir() {
            for child in std::fs::read_dir(path).map_err(err)? {
                stack.push(child.map_err(err)?.path());
            }
        }
    }
    std::fs::remove_dir_all(&stage).map_err(err)?;
    rows.remove(id);
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
