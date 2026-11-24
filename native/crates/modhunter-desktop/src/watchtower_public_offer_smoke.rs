//! Approved author-published archives in an isolated Watchtower manager store.
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
    if std::env::args().any(|arg| arg == "--verify-public-release-inventory") {
        return verify_inventory(&manager, output, checks);
    }
    let mut releases = vec![
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
    ];
    if std::env::args().any(|arg| arg == "--verify-public-release-history") {
        // Same filename across two genuine releases. Each approval and receipt
        // must stay bound to the selected tag's exact source and SHA-256.
        releases.extend([
            (
                "markekraus/ChairrowsSkyrimSEMod",
                "v2.0.0",
                "MarkekrausChairrows.7z",
                "318d6e4f2952ced680ac4f1a673eeae723172d34e8643c31a8b58fc5e94420b0",
                2_734_912,
            ),
            (
                "markekraus/ChairrowsSkyrimSEMod",
                "v2.0.1",
                "MarkekrausChairrows.7z",
                "bf7922674ef241ffa491bb79a7e85c80733ebc63e5f297e6663102688e4fe46e",
                2_734_932,
            ),
        ]);
    }
    for (project, tag, filename, expected_hash, expected_bytes) in releases {
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
        checks.push(json!({"ok":true,"source":source,"releaseTag":tag,"filename":filename,"offerKey":offer["sourceFileKey"],"receipt":receipt}));
    }
    Ok(())
}

fn verify_inventory(manager: &WebviewWindow, output: &Path, checks: &mut Vec<Value>) -> Result<()> {
    // Keep the fixture transport bounded: project the real manager response
    // inside its WebView rather than copying every unrelated Watchtower state
    // field and thousands of duplicated UI labels through the debug observer.
    let script = r#"async(url) => {
        const cmd=(kind,value)=>window.__TAURI__.core.invoke('watchtower_command',{kind,value});
        const saved=await cmd('saveWatch',{url,title:'Release inventory fixture',enabled:true});
        if(!saved.ok)throw Error(saved.error);
        const listed=await cmd('resolveFiles',{id:saved.watchId});
        if(!listed.ok)throw Error(listed.error);
        const offers=listed.state.acquisitions.offers.filter(x=>x.watchId===saved.watchId);
        const manual=offers.find(x=>x.kind==='manual');
        if(!manual||offers.some(x=>!x.current||!x.githubRelease||x.supported!==(x.kind!=='manual')))throw Error('Incomplete native release offers');
        const decision=await cmd('decideOffer',{id:manual.id,kind:'newContent'});
        const opened=await cmd('openOffer',{id:manual.id});
        if(decision.ok||opened.ok)throw Error('Manual asset acquired file authority');
        return {rows:offers.map(x=>[x.githubRelease.assetId,x.name,x.supported]).sort((a,b)=>a[0]-b[0]),manualControlsRejected:true};
    }"#;
    for (project, tag, total, archives, expected) in [
        (
            "be5invis/Iosevka",
            "v34.9.1",
            453,
            452,
            "cdba16da7c56a2371799935e6234eee031bc574cd53b23d655e013378f451ce7",
        ),
        (
            "astral-sh/python-build-standalone",
            "20261124",
            991,
            0,
            "276d6bb41ee6b1319d9216eb78584c121f8982e506ae21c1e4f01cdd8fcc9b58",
        ),
    ] {
        let source = format!("https://github.com/{project}/releases/tag/{tag}");
        event(output, "resolving-public-release-inventory");
        let start = Instant::now();
        manager.eval(format!("window.__releaseInventoryFixture=null;({script})({}).then(r=>window.__releaseInventoryFixture=r).catch(e=>window.__releaseInventoryFixture={{error:String(e)}})",json!(source))).map_err(err)?;
        let answer = until(
            manager,
            "window.__releaseInventoryFixture",
            Instant::now() + Duration::from_secs(100),
            Value::is_object,
        )?;
        let rows = answer["rows"].as_array().ok_or_else(|| {
            answer["error"]
                .as_str()
                .unwrap_or("Missing managed release inventory")
                .to_owned()
        })?;
        let fingerprint = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(rows).map_err(err)?)
        );
        if rows.len() != total
            || rows.iter().filter(|r| r[2] == true).count() != archives
            || fingerprint != expected
            || answer["manualControlsRejected"] != true
        {
            return Err("Manager release inventory differs from the pinned provider IDs, names or file eligibility.".into());
        }
        checks.push(json!({"ok":true,"phase":"completePublicReleaseInventory","source":source,"assets":total,"selectable":archives,
            "manual":total-archives,"assetIdentitySha256":fingerprint,"manualActionsRejected":true,"elapsedMs":start.elapsed().as_millis()}));
    }
    Ok(())
}
