//! Actual WebView2 DownloadStarting + deferral + manager IPC acceptance cases.
//! The owned HTTP capability never leaves the private fixture SiteContext.
use super::*;
use modhunter_core::acquisition::Store;
use std::collections::BTreeSet;

pub(super) fn archive_bytes() -> Vec<u8> {
    let name = b"meshes/fixture.nif";
    let data = b"owned authenticated archive bytes";
    let mut crc = 0xffff_ffffu32;
    for byte in data {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ if crc & 1 == 1 { 0xedb8_8320 } else { 0 };
        }
    }
    crc = !crc;
    fn word(out: &mut Vec<u8>, value: u16) {
        out.extend_from_slice(&value.to_le_bytes());
    }
    fn dword(out: &mut Vec<u8>, value: u32) {
        out.extend_from_slice(&value.to_le_bytes());
    }
    let mut out = Vec::new();
    dword(&mut out, 0x04034b50);
    for value in [20, 0, 0, 0, 0] {
        word(&mut out, value);
    }
    dword(&mut out, crc);
    dword(&mut out, data.len() as u32);
    dword(&mut out, data.len() as u32);
    word(&mut out, name.len() as u16);
    word(&mut out, 0);
    out.extend_from_slice(name);
    out.extend_from_slice(data);
    let offset = out.len() as u32;
    dword(&mut out, 0x02014b50);
    for value in [20, 20, 0, 0, 0, 0] {
        word(&mut out, value);
    }
    dword(&mut out, crc);
    dword(&mut out, data.len() as u32);
    dword(&mut out, data.len() as u32);
    for value in [name.len() as u16, 0, 0, 0, 0] {
        word(&mut out, value);
    }
    dword(&mut out, 0);
    dword(&mut out, 0);
    out.extend_from_slice(name);
    let size = out.len() as u32 - offset;
    dword(&mut out, 0x06054b50);
    for value in [0, 0, 1, 1] {
        word(&mut out, value);
    }
    dword(&mut out, size);
    dword(&mut out, offset);
    word(&mut out, 0);
    out
}
fn jobs(app: &tauri::AppHandle) -> Vec<Value> {
    acquisition::snapshot(app)["jobs"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}
fn wait_job(app: &tauri::AppHandle, id: &str, test: impl Fn(&Value) -> bool) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        if let Some(row) = jobs(app).into_iter().find(|r| r["id"] == id)
            && test(&row)
        {
            return Ok(row);
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    Err(format!(
        "Actual browser download did not reach its expected state: {id}"
    ))
}
fn command(manager: &WebviewWindow, kind: &str, value: Value) -> Result<Value> {
    manager.eval(format!("window.__fixtureDownloadCommand=null;window.__TAURI__.core.invoke('watchtower_command',{{kind:{},value:{}}}).then(v=>window.__fixtureDownloadCommand=v).catch(e=>window.__fixtureDownloadCommand={{ok:false,error:String(e)}});",serde_json::to_string(kind).map_err(err)?,value)).map_err(err)?;
    until(
        manager,
        "window.__fixtureDownloadCommand??null",
        Instant::now() + Duration::from_secs(15),
        Value::is_object,
    )
}
fn new_pending(app: &tauri::AppHandle, window: &WebviewWindow, script: &str) -> Result<String> {
    let old: BTreeSet<_> = jobs(app)
        .into_iter()
        .filter_map(|v| v["id"].as_str().map(str::to_owned))
        .collect();
    window.eval(script).map_err(err)?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        for row in jobs(app) {
            if let Some(id) = row["id"].as_str()
                && !old.contains(id)
            {
                if row["state"] != "pending" {
                    return Err(format!(
                        "Download did not defer for consent: {}",
                        row["state"]
                    ));
                }
                return Ok(id.into());
            }
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    Err("The real WebView2 DownloadStarting event did not create a deferred receipt.".into())
}
fn click(path: &str) -> String {
    format!(
        "(()=>{{const a=document.createElement('a');a.href={};a.download='fixture.zip';document.body.append(a);a.click();a.remove();}})()",
        serde_json::to_string(path).unwrap()
    )
}
fn no_part(root: &Path, id: &str) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(6);
    let part = root.join(id).join("payload.part");
    while part.exists() {
        if Instant::now() >= deadline {
            return Err(
                "Cancelled browser partial bytes were not cleaned after its handle closed.".into(),
            );
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Ok(())
}
fn context(app: &tauri::AppHandle, server: &FixtureServer, suffix: &str) -> Result<SiteContext> {
    let saved=app.state::<WatchRuntime>().store.command("saveWatch",&json!({"url":format!("https://mods.example.com/native-download-{suffix}"),"title":"Owned download fixture","enabled":true}),now())?;
    let id = saved["watchId"]
        .as_str()
        .ok_or("Missing source identity.")?;
    let watch = app
        .state::<WatchRuntime>()
        .store
        .watch(id)
        .ok_or("Missing fixture source.")?;
    let mut context = server.context("fixture-a", 1);
    context.download_watch = Some((watch.id, watch.generation));
    Ok(context)
}
fn exact_context(
    app: &tauri::AppHandle,
    server: &FixtureServer,
    suffix: &str,
    path: &str,
) -> Result<SiteContext> {
    let mut context = context(app, server, suffix)?;
    let store = &app.state::<WatchRuntime>().store;
    let id = context
        .download_watch
        .as_ref()
        .ok_or("Missing exact fixture watch.")?
        .0
        .clone();
    let source = store.watch(&id).ok_or("Missing fixture source.")?.url;
    store.command("saveWatch", &json!({"id":id,"association":{"profileId":"native-fixture-profile","itemId":"exact-update-fixture","name":"Exact fixture update","sourceUrl":source}}), now())?;
    let generation = store
        .watch(&id)
        .ok_or("Missing fixture source.")?
        .generation;
    store.command("saveSourceBinding", &json!({"watchId":id,"generation":generation,"game":"SkyrimSE","runtime":"Not applicable","variant":"fixture","compatibilityConfirmed":true,"contentIntent":"update","preferred":false}), now())?;
    let generation = store
        .watch(&id)
        .ok_or("Missing fixture source.")?
        .generation;
    let key = format!(
        "{:x}",
        Sha256::digest(format!("{source}\0owned-fixture:{path}").as_bytes())
    );
    store.command("saveFileDecision", &json!({"watchId":id,"generation":generation,"sourceFileKey":key,"name":"fixture.zip","kind":"update","ignored":false}), now())?;
    context.download_watch = Some((
        id.clone(),
        store
            .watch(&id)
            .ok_or("Missing fixture source.")?
            .generation,
    ));
    context.download_file_key = Some(key);
    context.download_scope = Some(Arc::new(Mutex::new(
        policy::download_scope::SelectedDownloadScope::new(server.url(path))?,
    )));
    Ok(context)
}

fn exact_authority_check(
    app: &tauri::AppHandle,
    manager: &WebviewWindow,
    server: &FixtureServer,
    checks: &mut Vec<Value>,
    output: &Path,
) -> Result<()> {
    let exact = exact_context(app, server, "scoped-redirect", "/redirect-download.zip")?;
    let watch_id = exact
        .download_watch
        .as_ref()
        .ok_or("Missing scoped watch.")?
        .0
        .clone();
    let window = WindowGuard(site_window(
        app,
        exact,
        server.url("/redirect-download.zip"),
        true,
        None,
        Arc::new(Load::default()),
    )?);
    let deadline = Instant::now() + Duration::from_secs(15);
    let pending = loop {
        if let Some(row) = jobs(app)
            .into_iter()
            .find(|row| row["binding"]["watchId"] == watch_id)
        {
            break row;
        }
        if Instant::now() >= deadline {
            return Err("Exact native redirect did not preserve selected file authority.".into());
        }
        std::thread::sleep(Duration::from_millis(40));
    };
    let id = pending["id"]
        .as_str()
        .ok_or("Missing exact download receipt.")?
        .to_owned();
    if pending["state"] != "pending"
        || pending["binding"]["contentIntent"] != "update"
        || pending["binding"]["association"]["itemId"] != "exact-update-fixture"
        || pending["binding"]["sourceFileKey"]
            .as_str()
            .is_none_or(|key| key.len() != 64)
    {
        return Err(
            "The selected redirect did not keep its exact file decision and update target.".into(),
        );
    }
    if command(
        manager,
        "approveDownload",
        json!({"id":id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("Could not approve the scoped archive.".into());
    }
    let ready = wait_job(app, &id, |row| {
        matches!(
            row["state"].as_str(),
            Some("ready" | "failed" | "interrupted")
        )
    })?;
    if ready["state"] != "ready" {
        return Err("The native-scoped download did not finish.".into());
    }
    let saved: modhunter_core::acquisition::Binding =
        serde_json::from_value(ready["binding"].clone()).map_err(err)?;
    acquisition::source_current(app, &saved)?;
    drop(window);

    let unrelated = exact_context(app, server, "scoped-unrelated", "/tracked")?;
    let scope = unrelated
        .download_scope
        .clone()
        .ok_or("Missing exact scope.")?;
    let (other, _) = fixture_view(app, unrelated, server.url("/tracked"), true)?;
    let before = jobs(app).len();
    other.0.eval(click("/download.zip")).map_err(err)?;
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if !scope.lock().map_err(err)?.live() {
            break;
        }
        if Instant::now() >= deadline {
            return Err(
                "A same-host unrelated download did not revoke the selected-file scope.".into(),
            );
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    let no_receipt = jobs(app).len() == before;
    drop(other);
    record(
        checks,
        output,
        "download_exact_file_authority",
        no_receipt,
        "An actual WebView2 redirect from the exact selected file retains its file decision and durable update target; a same-host unrelated download invalidates the native scope and creates no receipt. Durable review validates the saved decision without recreating network authority.",
    )
}

pub(super) fn check(
    app: &tauri::AppHandle,
    manager: &WebviewWindow,
    server: &FixtureServer,
    fixture_root: &Path,
    output: &Path,
    checks: &mut Vec<Value>,
) -> Result<()> {
    let root = fixture_root.join("data/watchtower-acquisitions");
    event(output, "checking-actual-authenticated-downloads");
    let direct_context = context(app, server, "direct")?;
    let (direct, _) = fixture_view(app, direct_context.clone(), server.url("/tracked"), true)?;
    let id = new_pending(app, &direct.0, &click("/download.zip"))?;
    let denied = command(manager, "approveDownload", json!({"id":id}))?;
    record(
        checks,
        output,
        "download_requires_explicit_consent",
        denied["ok"] == false
            && jobs(app)
                .iter()
                .any(|r| r["id"] == id && r["state"] == "pending")
            && std::fs::read_dir(root.join(&id))
                .map_err(err)?
                .next()
                .is_none(),
        "A real DownloadStarting deferral creates an empty owned receipt directory; missing DOWNLOAD consent cannot release the stream to disk.",
    )?;
    if command(
        manager,
        "approveDownload",
        json!({"id":id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("Trusted manager could not approve the real download.".into());
    }
    let ready = wait_job(app, &id, |r| {
        matches!(
            r["state"].as_str(),
            Some("ready" | "failed" | "interrupted")
        )
    })?;
    let saved = Store::open(&root);
    let path = saved.verify(&id, || false)?;
    record(
        checks,
        output,
        "authenticated_direct_archive_download",
        ready["state"] == "ready"
            && std::fs::read(path).map_err(err)? == archive_bytes()
            && ready["binding"]["sessionId"] == "fixture-a"
            && ready["binding"]["sessionGeneration"] == 1
            && ready["binding"]["contentIntent"] == "unclassified"
            && ready["binding"]["association"].is_null(),
        "The HttpOnly authenticated endpoint delivered exact ZIP bytes through WebView2, explicit manager approval and durable original-byte hashing.",
    )?;
    drop(direct);

    let blob_context = context(app, server, "blob")?;
    let (blob, _) = fixture_view(app, blob_context, server.url("/tracked"), true)?;
    let blob_id = new_pending(
        app,
        &blob.0,
        "(async()=>{const response=await fetch('/download.zip',{credentials:'same-origin'});const bytes=await response.arrayBuffer();const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));const a=document.createElement('a');a.href=url;a.download='blob-fixture.zip';document.body.append(a);a.click();a.remove();window.__fixtureBlob=url;})().catch(e=>window.__fixtureBlobError=String(e));",
    )?;
    if command(
        manager,
        "approveDownload",
        json!({"id":blob_id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("Manager rejected the owned same-origin blob.".into());
    }
    let blob_ready = wait_job(app, &blob_id, |r| {
        matches!(
            r["state"].as_str(),
            Some("ready" | "failed" | "interrupted")
        )
    })?;
    let blob_path = Store::open(&root).verify(&blob_id, || false)?;
    record(
        checks,
        output,
        "authenticated_blob_archive_download",
        blob_ready["state"] == "ready" && std::fs::read(blob_path).map_err(err)? == archive_bytes(),
        "An actual authenticated fetch became a same-origin Blob download and passed the same DownloadStarting consent, owned destination and complete original-byte verification.",
    )?;
    drop(blob);

    let (rejected, _) = fixture_view(
        app,
        context(app, server, "reject")?,
        server.url("/tracked"),
        true,
    )?;
    let rejected_id = new_pending(app, &rejected.0, &click("/download.zip"))?;
    let rejection = command(manager, "rejectDownload", json!({"id":rejected_id}))?;
    let rejected_row = wait_job(app, &rejected_id, |r| r["state"] == "cancelled")?;
    no_part(&root, &rejected_id)?;
    record(
        checks,
        output,
        "download_rejection_prevents_bytes",
        rejection["ok"] == true
            && rejected_row["sha256"].is_null()
            && std::fs::read_dir(root.join(&rejected_id))
                .map_err(err)?
                .next()
                .is_none(),
        "Rejecting a real deferred browser operation leaves its receipt cancelled and stores no partial or ready archive bytes.",
    )?;
    drop(rejected);

    let (cancelled, _) = fixture_view(
        app,
        context(app, server, "cancel")?,
        server.url("/tracked"),
        true,
    )?;
    let cancelled_id = new_pending(app, &cancelled.0, &click("/slow.zip"))?;
    if command(
        manager,
        "approveDownload",
        json!({"id":cancelled_id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("Slow transfer could not start.".into());
    }
    wait_job(app, &cancelled_id, |r| {
        r["state"] == "downloading" && r["received"].as_u64().is_some_and(|n| n > 0)
    })?;
    let _ = command(manager, "cancelDownload", json!({"id":cancelled_id}))?;
    let cancelled_row = wait_job(app, &cancelled_id, |r| r["state"] == "cancelled")?;
    no_part(&root, &cancelled_id)?;
    record(
        checks,
        output,
        "download_cancel_cleans_partial",
        cancelled_row["sha256"].is_null() && !root.join(&cancelled_id).join("archive.zip").exists(),
        "A live slow browser transfer receives bytes, then cancellation closes it and removes only its owned payload.part; no late ready receipt appears.",
    )?;
    drop(cancelled);

    let (html, _) = fixture_view(
        app,
        context(app, server, "html")?,
        server.url("/tracked"),
        true,
    )?;
    let html_id = new_pending(app, &html.0, &click("/disguised.zip"))?;
    if command(
        manager,
        "approveDownload",
        json!({"id":html_id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("Disguised archive fixture could not be approved.".into());
    }
    let html_row = wait_job(app, &html_id, |r| {
        matches!(r["state"].as_str(), Some("failed" | "ready"))
    })?;
    no_part(&root, &html_id)?;
    record(
        checks,
        output,
        "download_html_rejected",
        html_row["state"] == "failed"
            && html_row["sha256"].is_null()
            && !root.join(&html_id).join("archive.zip").exists(),
        "HTML served with a ZIP filename is rejected after the actual browser transfer; no verified archive or leftover partial is accepted.",
    )?;
    drop(html);

    let (closed, _) = fixture_view(
        app,
        context(app, server, "close")?,
        server.url("/tracked"),
        true,
    )?;
    let closed_id = new_pending(app, &closed.0, &click("/download.zip"))?;
    let label = closed.0.label().to_owned();
    closed.0.close().map_err(err)?;
    wait_closed(app, &label)?;
    let closed_row = wait_job(app, &closed_id, |r| r["state"] == "cancelled")?;
    no_part(&root, &closed_id)?;
    record(
        checks,
        output,
        "download_owner_close_cancels",
        closed_row["sha256"].is_null(),
        "Closing the real owning website window cancels its pending DownloadStarting deferral instead of leaving approval authority behind.",
    )?;

    let stale_context = context(app, server, "stale")?;
    let watch_id = stale_context.download_watch.as_ref().unwrap().0.clone();
    let (stale, _) = fixture_view(app, stale_context, server.url("/tracked"), true)?;
    let stale_id = new_pending(app, &stale.0, &click("/download.zip"))?;
    let edit = command(
        manager,
        "saveWatch",
        json!({"id":watch_id,"title":"Changed source identity"}),
    )?;
    let stale_row = wait_job(app, &stale_id, |r| r["state"] == "cancelled")?;
    let late = command(
        manager,
        "approveDownload",
        json!({"id":stale_id,"confirm":"DOWNLOAD"}),
    )?;
    no_part(&root, &stale_id)?;
    record(
        checks,
        output,
        "download_stale_source_cancels",
        edit["ok"] == true && late["ok"] == false && stale_row["sha256"].is_null(),
        "Editing the saved source while a real download waits for approval invalidates the old lease, cancels its browser operation and rejects late approval.",
    )?;
    drop(stale);
    exact_authority_check(app, manager, server, checks, output)?;
    let reopened = Store::open(&root);
    let first = reopened.row(&id)?;
    let second = reopened.row(&blob_id)?;
    record(
        checks,
        output,
        "download_receipt_survives_reopen",
        first.state == "ready"
            && second.state == "ready"
            && reopened.verify(&id, || false).is_ok()
            && reopened.verify(&blob_id, || false).is_ok(),
        "Reopening the actual durable receipt store preserves both original byte identities; cancellation and error cleanup did not remove either verified archive.",
    )?;
    Ok(())
}
