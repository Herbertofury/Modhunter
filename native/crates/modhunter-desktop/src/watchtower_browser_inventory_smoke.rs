//! Owned native browser fixture. Login uses an HTTP-only cookie and every
//! offered archive points to a reserved example domain. No asset is opened.
use super::*;
use crate::watchtower_windows::acquisition::offers;
use std::collections::BTreeMap;

const COUNT: usize = 641;
const SOURCE: &str = "https://mods.example.com/native-batched-release";
fn link(i: usize, replacement: bool) -> (String, String) {
    let prefix = if replacement { "different" } else { "archive" };
    let name = format!("{prefix}-{i:04}.zip");
    let url = format!(
        "https://mods.example.com/files/{name}?part={}",
        "x".repeat(1600)
    );
    (name, url)
}
pub(super) fn release_html(replacement: bool) -> String {
    let mut body = String::from("<main><h1>Owned signed-in file list</h1>");
    for i in 0..COUNT {
        let (name, url) = link(i, replacement);
        body.push_str(&format!("<a href=\"{url}\">{name}</a>"));
    }
    body.push_str("</main>");
    body
}
fn inventory(
    app: &tauri::AppHandle,
    watch: &str,
) -> Result<BTreeMap<String, (String, bool, bool, String)>> {
    let snapshot = acquisition::snapshot(app);
    let rows = snapshot["offers"]
        .as_array()
        .ok_or("No native offer snapshot.")?;
    let mut result = BTreeMap::new();
    for row in rows.iter().filter(|r| r["watchId"] == watch) {
        let key = row["sourceFileKey"]
            .as_str()
            .ok_or("Missing file identity.")?;
        let entry = (
            row["name"].as_str().ok_or("Missing filename.")?.into(),
            row["current"] == true,
            row["supported"] == true,
            row["id"].as_str().ok_or("Missing offer ID.")?.into(),
        );
        if result.insert(key.into(), entry).is_some() {
            return Err("Duplicate published file key.".into());
        }
    }
    Ok(result)
}
pub(super) fn run(app: &tauri::AppHandle, output: &Path, checks: &mut Vec<Value>) -> Result<()> {
    let server = FixtureServer::start()?;
    open(app, None)?;
    let manager = app
        .get_webview_window("watchtower")
        .ok_or("Owned manager unavailable.")?;
    until(
        &manager,
        "!!window.__TAURI__?.core?.invoke",
        Instant::now() + Duration::from_secs(15),
        |v| v == true,
    )?;
    let store = &app.state::<WatchRuntime>().store;
    let saved=store.command("saveSession",&json!({"origin":"https://mods.example.com","label":"Owned batching account","remember":true}),now())?;
    let session_id = saved["sessionId"]
        .as_str()
        .ok_or("Missing fixture account ID.")?;
    let mut fixture = server.context(session_id, 1);
    fixture.session = store.session(session_id);
    {
        let (guest, _) = fixture_view(app, fixture.clone(), server.url("/batched-release"), false)?;
        until(
            &guest.0,
            "!!document.querySelector('input[type=password]')",
            Instant::now() + Duration::from_secs(5),
            |v| v == true,
        )?;
    }
    let (login, _) = fixture_view(app, fixture.clone(), server.url("/login"), false)?;
    until(
        &login.0,
        "document.body.innerText.includes('Fixture sign-in completed.')",
        Instant::now() + Duration::from_secs(5),
        |v| v == true,
    )?;
    store.command("confirmSession", &json!({"id":session_id}), now())?;
    let saved=store.command("saveWatch",&json!({"url":SOURCE,"title":"Owned batched release","sessionId":session_id,"requiresSession":true,"enabled":true}),now())?;
    let watch = saved["watchId"].as_str().ok_or("Missing fixture watch.")?;
    let mut first_keys = BTreeMap::new();
    for phase in ["complete", "cancel", "reset"] {
        let mut batches = Vec::new();
        let start = Instant::now();
        let result = offers::resolve_files_with(
            app,
            &json!({"id":watch}),
            |_, id, production, preparation| {
                let mut bound = fixture.clone();
                bound.download_watch = production.download_watch.clone();
                bound.cancellation = production.cancellation.clone();
                bound.session = production.session.clone();
                let address = server.url(if phase == "complete" {
                    "/batched-release"
                } else {
                    "/batched-replacement"
                });
                offers::browser_release_rows(app, &address, id, &bound, preparation, |n| {
                    batches.push(n);
                    if batches.len() == 1 {
                        match phase {
                            "cancel" => {
                                acquisition::cancel_preparation(app)?;
                            }
                            "reset" => {
                                store.command("resetSession", &json!({"id":session_id}), now())?;
                            }
                            _ => {}
                        }
                    }
                    Ok(())
                })
            },
        );
        let rows = inventory(app, watch)?;
        if phase == "complete" {
            result?;
            if batches.len() < 10 || batches.last() != Some(&COUNT) || rows.len() != COUNT {
                return Err("Large native browser inventory was incomplete.".into());
            }
            for i in 0..COUNT {
                let (name, url) = link(i, false);
                let key = format!(
                    "{:x}",
                    Sha256::digest(format!("{SOURCE}\0url:{url}").as_bytes())
                );
                let row = rows
                    .get(&key)
                    .ok_or("Browser lost or rebound a native file identity.")?;
                if row.0 != name || !row.1 || !row.2 {
                    return Err("A signed-in file was renamed, stale or ineligible.".into());
                }
                first_keys.insert(key, row.3.clone());
            }
            until(
                &manager,
                &format!(
                    "document.querySelectorAll('#pageDownloadList article[data-download-id]').length==={COUNT}"
                ),
                Instant::now() + Duration::from_secs(15),
                |v| v == true,
            )?;
        } else {
            if result.is_ok()
                || batches.len() != 1
                || rows.len() != COUNT
                || rows
                    .iter()
                    .any(|(key, row)| first_keys.get(key) != Some(&row.3) || row.1)
            {
                return Err(format!(
                    "The {phase} fixture did not retain an ineligible complete inventory: outcome={:?}, batches={}, retained={}, stillCurrent={}, changedIds={}.",
                    result.as_ref().err(),
                    batches.len(),
                    rows.len(),
                    rows.values().filter(|row| row.1).count(),
                    rows.iter()
                        .filter(|(k, row)| first_keys.get(*k) != Some(&row.3))
                        .count()
                ));
            }
            let stale = download_fixture::command(
                &manager,
                "openOffer",
                json!({"id":first_keys.values().next()}),
            )?;
            if stale["ok"] != false {
                return Err("A stale release offer received download authority.".into());
            }
        }
        event(output, phase);
        checks.push(json!({"ok":true,"phase":phase,"batchesRead":batches.len(),"lastCursor":batches.last(),"completeIdentitiesRetained":rows.len(),
            "renderedCompleteList":phase=="complete","previousOffersIneligible":phase!="complete","nativeMillis":start.elapsed().as_millis()}));
    }
    Ok(())
}
