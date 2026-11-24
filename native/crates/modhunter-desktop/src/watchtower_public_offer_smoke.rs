//! Explicit opt-in proof using two author-published Skyrim configuration-only
//! FOMODs. The caller initializes a new isolated manager store; no MO2 bridge
//! or installation is involved.
use super::*;
use tauri::Listener;

pub(super) fn run(app: &tauri::AppHandle, output: &Path, checks: &mut Vec<Value>) -> Result<()> {
    open(app, None)?;
    let manager = app
        .get_webview_window("watchtower")
        .ok_or("Manager unavailable")?;
    until(
        &manager,
        "!!window.__TAURI__?.core?.invoke",
        Instant::now() + Duration::from_secs(15),
        |v| v == true,
    )?;
    for (project, tag, filename, expected_hash, expected_bytes) in [
        (
            "3pvxiozkua/skyrim-tiered-casters",
            "v1.0.0",
            "skyrim-tiered-casters-1.0.0.zip",
            "a9e7bfdc1c849d7a25d20d51b476046b41667b27bac5bfc2939c8de8f09632c7",
            16202u64,
        ),
        (
            "kxgcayh/waccf-simonrim-skypatcher-patch",
            "v0.1.2",
            "waccf-simonrim-skypatcher-patch-0.1.2.zip",
            "80bb9e57790973f0c14060986d597fbc29cbcc6cf97923104000fbeb604b1892",
            10956u64,
        ),
    ] {
        let source = format!("https://github.com/{project}/releases/expanded_assets/{tag}");
        let execute = |kind: &str, value: Value| -> Result<Value> {
            event(output, kind);
            let answer = download_fixture::command(&manager, kind, value)?;
            if answer["ok"] != true {
                return Err(format!(
                    "{kind}: {}",
                    answer["error"]
                        .as_str()
                        .unwrap_or("Manager rejected command")
                ));
            }
            Ok(answer)
        };
        let saved = execute(
            "saveWatch",
            json!({"url":source,"title":filename,"enabled":true}),
        )?;
        let watch = saved["watchId"].as_str().ok_or("No saved watch ID")?;
        let listed = execute("resolveFiles", json!({"id":watch}))?;
        let choices = listed["state"]["acquisitions"]["offers"]
            .as_array()
            .ok_or("Missing file choices")?;
        let offer = choices
            .iter()
            .find(|o| {
                o["watchId"] == watch
                    && o["name"] == filename
                    && o["supported"] == true
                    && o["current"] == true
            })
            .ok_or_else(|| {
                format!(
                    "Exact public archive was not offered ({filename}, {} discovered choices)",
                    choices.len()
                )
            })?;
        let offer_id = offer["id"].as_str().ok_or("No offer ID")?;
        checks.push(json!({"ok":true,"phase":"exactOffer","source":source,"filename":filename,"sourceFileKey":offer["sourceFileKey"]}));
        execute("decideOffer", json!({"id":offer_id,"kind":"newContent"}))?;
        let errors = Arc::new(Mutex::new(Vec::new()));
        let seen_errors = errors.clone();
        let listener = app.listen_any("watchtower-download-error", move |event| {
            if let Ok(value) = serde_json::from_str::<Value>(event.payload())
                && let Some(message) = value["message"].as_str()
                && let Ok(mut errors) = seen_errors.lock()
                && errors.len() < 4
            {
                errors.push(message.chars().take(350).collect::<String>());
            }
        });
        execute("openOffer", json!({"id":offer_id}))?;
        let deadline = Instant::now() + Duration::from_secs(35);
        let mut pending = None;
        while Instant::now() < deadline {
            let state = acquisition::snapshot(app);
            pending = state["jobs"]
                .as_array()
                .and_then(|jobs| {
                    jobs.iter().find(|j| {
                        j["filename"] == filename
                            && j["binding"]["watchId"] == watch
                            && j["state"] == "pending"
                    })
                })
                .cloned();
            if pending.is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        app.unlisten(listener);
        let pending = pending.ok_or_else(|| {
            format!(
                "No pending receipt for {filename}. Native errors: {}",
                json!(errors.lock().unwrap_or_else(|e| e.into_inner()).as_slice())
            )
        })?;
        let id = pending["id"]
            .as_str()
            .ok_or("Missing pending download ID")?;
        execute("approveDownload", json!({"id":id,"confirm":"DOWNLOAD"}))?;
        let receipt = download_fixture::wait_job(app, id, |r| {
            matches!(r["state"].as_str(), Some("ready" | "failed" | "cancelled"))
        })?;
        if receipt["state"] != "ready"
            || receipt["sha256"] != expected_hash
            || receipt["bytes"] != expected_bytes
            || receipt["binding"]["contentIntent"] != "newContent"
        {
            return Err(format!(
                "Managed download did not match the publisher's file identity: {} ({} bytes)",
                receipt["state"], receipt["bytes"]
            ));
        }
        checks.push(json!({"ok":true,"source":source,"filename":filename,"offerKey":offer["sourceFileKey"],"receipt":receipt}));
    }
    Ok(())
}
