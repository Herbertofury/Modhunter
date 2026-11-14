//! Trusted, on-demand readiness and unattributed-file inspection. No automatic
//! asset walk occurs on startup or during the normal Watchtower snapshot.
use super::*;
use modhunter_core::content_inventory::{self, Inventory, ModOwner};
use std::collections::BTreeSet;

const CONTEXT_KEYS: [&str; 7] = [
    "profileId",
    "game",
    "gameRuntime",
    "executionEpoch",
    "nativeLease",
    "nativeGeneration",
    "nativeContext",
];

struct Cached {
    id: String,
    context: Value,
    endpoint: String,
    epoch: u64,
    report: Inventory,
    captured_at: u64,
}
struct Inspection {
    cached: Mutex<Option<Cached>>,
    cancel: Arc<AtomicBool>,
    running: AtomicBool,
    sequence: AtomicU64,
}
pub(super) fn setup(app: &tauri::AppHandle) {
    app.manage(Inspection {
        cached: Mutex::new(None),
        cancel: Arc::new(AtomicBool::new(false)),
        running: AtomicBool::new(false),
        sequence: AtomicU64::new(1),
    });
}
fn same_context(before: &Value, after: &Value) -> bool {
    CONTEXT_KEYS.iter().all(|key| before[*key] == after[*key])
}
fn context_key(context: &Value) -> String {
    let fields = Value::Array(
        CONTEXT_KEYS
            .iter()
            .map(|key| context[*key].clone())
            .collect(),
    );
    format!("{:x}", Sha256::digest(fields.to_string().as_bytes()))
}
fn tracked_mods(watches: &Value, catalog: &Value) -> usize {
    let current: BTreeSet<&str> = catalog["items"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| item["id"].as_str())
        .collect();
    watches["watches"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|watch| {
            watch["removedAt"].is_null()
                && !catalog["profileId"].is_null()
                && watch["association"]["profileId"] == catalog["profileId"]
        })
        .filter_map(|watch| watch["association"]["itemId"].as_str())
        .filter(|id| current.contains(id))
        .collect::<BTreeSet<_>>()
        .len()
}
fn owners(catalog: &Value) -> Result<Vec<ModOwner>> {
    catalog["items"]
        .as_array()
        .ok_or("The complete native mod catalog is unavailable.")?
        .iter()
        .filter(|item| item["foreign"] != true && item["missingFromDisk"] != true)
        .map(|item| {
            Ok(ModOwner {
                id: item["id"]
                    .as_str()
                    .ok_or("A native mod has no exact identity.")?
                    .into(),
                name: item["mo2InternalName"]
                    .as_str()
                    .or_else(|| item["name"].as_str())
                    .ok_or("A native mod has no owned folder name.")?
                    .into(),
                source_verified: item["source"]["verified"] == true,
                enabled: item["enabled"] == true,
            })
        })
        .collect()
}
fn readiness(app: &tauri::AppHandle) -> Result<Value> {
    let snapshot = crate::windows::inspection_snapshot(app)?;
    let catalog = &snapshot["catalog"];
    let watches = app.state::<WatchRuntime>().store.snapshot();
    let items = catalog["items"].as_array().cloned().unwrap_or_default();
    let verified = items
        .iter()
        .filter(|item| item["source"]["verified"] == true)
        .count();
    let associated = tracked_mods(&watches, catalog);
    let sessions = watches["sessions"].as_array().cloned().unwrap_or_default();
    let accounts = watches["sourcePreferences"]["accounts"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let signed_in = sessions
        .iter()
        .filter(|session| {
            accounts.iter().any(|account| {
                account["sessionId"] == session["id"]
                    && account["storageGeneration"] == session["storageGeneration"]
                    && account["loginState"] == "signedIn"
                    && account["verificationHold"] != true
            })
        })
        .count();
    let held = sessions
        .iter()
        .filter(|session| {
            accounts.iter().any(|account| {
                account["sessionId"] == session["id"]
                    && account["storageGeneration"] == session["storageGeneration"]
                    && (account["loginState"] == "signedOut" || account["verificationHold"] == true)
            })
        })
        .count();
    let instances = crate::windows::inspect_available_instances()?;
    let (root, profiles) = if snapshot["mode"] == "live" {
        (
            snapshot["context"]["nativeContext"]["modsRoot"].clone(),
            snapshot["context"]["nativeContext"]["profilesRoot"].clone(),
        )
    } else {
        (Value::Null, Value::Null)
    };
    Ok(
        json!({"readiness":{"mode":snapshot["mode"],"capturedAt":now(),"profileId":catalog["profileId"],"profileName":catalog["profileName"],"game":catalog["game"],"gameRuntime":catalog["gameRuntime"],"endpoint":snapshot["endpoint"],"connectionEpoch":snapshot["connectionEpoch"],"contextKey":if snapshot["mode"]=="live"{Some(context_key(&snapshot["context"]))}else{None},"gameRunning":snapshot["context"]["gameRunning"],"modsRoot":root,"profilesRoot":profiles,"instances":instances,
        "counts":{"mods":items.len(),"verifiedSourceMods":verified,"unverifiedSourceMods":items.len()-verified,"trackedMods":associated,"siteSessions":sessions.len(),"observedSignedIn":signed_in,"signInOrVerification":held,"unobservedSessions":sessions.len().saturating_sub(signed_in+held)},
        "mods":items.iter().map(|item|json!({"id":item["id"],"name":item["name"],"sourceVerified":item["source"]["verified"],"enabled":item["enabled"]})).collect::<Vec<_>>(),
        "scope":if snapshot["mode"]=="live"{"Current live MO2 profile and observed source/account readiness. Account sign-in is not proof of access to a particular release. No source check, download or install was started."}else{"Read-only imported metadata or disconnected state. Demo content is not counted as a live profile. Connect the intended MO2 instance before changing files."}}}),
    )
}
fn public_cached(cached: &Cached) -> Value {
    json!({"id":cached.id,"capturedAt":cached.captured_at,"profileId":cached.context["profileId"],"profileName":cached.context["profileName"],"game":cached.context["game"],"gameRuntime":cached.context["gameRuntime"],"contextKey":context_key(&cached.context),"complete":cached.report.complete,"entries":cached.report.entries,"files":cached.report.files,"bytes":cached.report.bytes,"verifiedModsExcluded":cached.report.verified_mods_excluded,"scope":cached.report.scope})
}
fn scan(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let state = app.state::<Inspection>();
    if state.running.swap(true, Ordering::AcqRel) {
        return Err("An unattributed-file inspection is already running. Cancel it or wait for its complete result.".into());
    }
    struct Guard<'a>(&'a AtomicBool);
    impl Drop for Guard<'_> {
        fn drop(&mut self) {
            self.0.store(false, Ordering::Release);
        }
    }
    let _guard = Guard(&state.running);
    state.cancel.store(false, Ordering::Release);
    let snapshot = crate::windows::inspection_snapshot(app)?;
    if snapshot["mode"] != "live" {
        return Err("Connect to the intended live MO2 instance before inspecting its actual files. The imported catalog remains read-only metadata.".into());
    }
    let context = snapshot["context"].clone();
    let root = context["nativeContext"]["modsRoot"]
        .as_str()
        .ok_or("Native mods directory is unavailable.")?;
    let endpoint = snapshot["endpoint"]
        .as_str()
        .ok_or("Native connection is unavailable.")?
        .to_string();
    let epoch = snapshot["connectionEpoch"]
        .as_u64()
        .ok_or("Native connection identity is unavailable.")?;
    let cancelled = || {
        state.cancel.load(Ordering::Acquire)
            || app.state::<WatchRuntime>().stopping.load(Ordering::Acquire)
            || !crate::windows::mo2_connection_current(app, &endpoint, epoch)
    };
    let selected = value["itemId"].as_str().filter(|id| !id.is_empty());
    let report = content_inventory::inspect(
        Path::new(root),
        &owners(&snapshot["catalog"])?,
        selected,
        &cancelled,
    )?;
    let (client, current_endpoint, current_epoch) = crate::windows::mo2_connection(app)?;
    let current = client
        .native_content(&current_endpoint, "content-context", &json!({}))
        .map_err(err)?;
    if cancelled()
        || epoch != current_epoch
        || endpoint != current_endpoint
        || !same_context(&context, &current)
    {
        return Err("MO2 changed during file inspection. The previous complete result is retained; refresh the current profile.".into());
    }
    let cached = Cached {
        id: format!(
            "inspection-{}-{}",
            now(),
            state.sequence.fetch_add(1, Ordering::AcqRel)
        ),
        context,
        endpoint,
        epoch,
        report,
        captured_at: now(),
    };
    let result = public_cached(&cached);
    *state.cached.lock().map_err(err)? = Some(cached);
    Ok(json!({"inventory":result}))
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    match kind {
        "readiness" => readiness(app),
        "scanUnattributed" => scan(app, value),
        "cancelUnattributed" => {
            app.state::<Inspection>()
                .cancel
                .store(true, Ordering::Release);
            Ok(
                json!({"message":"File inspection cancellation requested. The last complete inventory remains available."}),
            )
        }
        "unattributedInventory" => {
            let state = app.state::<Inspection>();
            let cached = state.cached.lock().map_err(err)?;
            Ok(
                json!({"inventory":cached.as_ref().map(public_cached),"running":state.running.load(Ordering::Acquire)}),
            )
        }
        "revealUnattributed" => {
            let state = app.state::<Inspection>();
            let cached = state.cached.lock().map_err(err)?;
            let cached = cached
                .as_ref()
                .filter(|cached| value["inventoryId"] == cached.id)
                .ok_or("This inventory is no longer current. Refresh the file list.")?;
            let (client, endpoint, epoch) = crate::windows::mo2_connection(app)?;
            let current = client
                .native_content(&endpoint, "content-context", &json!({}))
                .map_err(err)?;
            if epoch != cached.epoch
                || endpoint != cached.endpoint
                || !crate::windows::mo2_connection_current(app, &endpoint, epoch)
                || !same_context(&cached.context, &current)
            {
                return Err("The native profile or context changed. Refresh the inventory before revealing its files.".into());
            }
            let entry = value["entryId"]
                .as_str()
                .ok_or("Choose an exact inspected entry.")?;
            let path = content_inventory::reveal_path(&cached.report, entry)?;
            if !crate::windows::mo2_connection_current(app, &endpoint, epoch) {
                return Err(
                    "The native connection changed before revealing this inspected entry.".into(),
                );
            }
            app.opener().reveal_item_in_dir(path).map_err(err)?;
            Ok(json!({"message":"Exact inspected entry revealed in its folder."}))
        }
        _ => Err("Unknown inspection action.".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cache_hint_binds_all_authority_fields_but_not_ephemeral_capture_ids() {
        let original = json!({"profileId":"p","game":"g","gameRuntime":"r","executionEpoch":2,"nativeLease":"a".repeat(32),"nativeGeneration":7,"nativeContext":{"modsRoot":"C:/Mods"},"contextId":"first"});
        let mut changed = original.clone();
        changed["contextId"] = json!("second");
        assert_eq!(context_key(&original), context_key(&changed));
        assert!(same_context(&original, &changed));
        for key in CONTEXT_KEYS {
            let mut changed = original.clone();
            changed[key] = json!("different");
            assert!(!same_context(&original, &changed));
            assert_ne!(context_key(&original), context_key(&changed));
        }
    }
    #[test]
    fn tracked_counts_ignore_removed_mods_other_profiles_and_duplicate_links() {
        let catalog =
            json!({"profileId":"current","items":[{"id":"live-existing"},{"id":"live-untracked"}]});
        let watches = json!({"watches":[
            {"association":{"profileId":"current","itemId":"live-existing"}},
            {"association":{"profileId":"current","itemId":"live-existing"}},
            {"association":{"profileId":"current","itemId":"live-removed"}},
            {"association":{"profileId":"other","itemId":"live-untracked"}},
            {"removedAt":1,"association":{"profileId":"current","itemId":"live-untracked"}}
        ]});
        assert_eq!(tracked_mods(&watches, &catalog), 1);
    }
}
