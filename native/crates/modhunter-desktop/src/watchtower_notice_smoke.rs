//! Actual bundled-manager buttons exercise production notification IPC. The
//! fixture does not claim an OS toast was displayed or physically clicked.
use super::*;

fn wait(
    window: &WebviewWindow,
    script: String,
    predicate: impl Fn(&Value) -> bool,
) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        let value = eval_json(window, script.clone(), &AtomicBool::new(false), deadline)?;
        if predicate(&value) {
            return Ok(value);
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    Err("The actual notification button did not produce its expected native outcome.".into())
}
fn completed(app: &tauri::AppHandle, id: &str, text: &str) -> Result<()> {
    let job = app.state::<WatchRuntime>().store.begin_check(id, now())?;
    finish_check(
        app,
        &job,
        Ok(PageObservation {
            final_url: job.url.clone(),
            title: "Private fixture page name".into(),
            text: text.into(),
            status: "ok".into(),
            http_status: Some(200),
            ..Default::default()
        }),
        &AtomicBool::new(false),
    );
    Ok(())
}
pub(super) fn check(app: &tauri::AppHandle, manager: &WebviewWindow) -> Result<Value> {
    let state = app.state::<WatchRuntime>();
    let before = app
        .state::<Notices>()
        .ledger
        .lock()
        .map_err(err)?
        .document
        .notices
        .len();
    let response = state.store.command(
        "saveWatch",
        &json!({"url":"https://example.com/private-notice-fixture",
        "title":"Private fixture page name","enabled":true}),
        now(),
    )?;
    let watch_id = response["watchId"]
        .as_str()
        .ok_or("Fixture watch identity is missing.")?;
    completed(app, watch_id, "Complete baseline content")?;
    if app
        .state::<Notices>()
        .ledger
        .lock()
        .map_err(err)?
        .document
        .notices
        .len()
        != before
    {
        return Err("A first baseline incorrectly generated a change notice.".into());
    }
    completed(app, watch_id, "Complete changed content")?;
    let notice = {
        let notices = app.state::<Notices>();
        let ledger = notices.ledger.lock().map_err(err)?;
        let notice = ledger
            .document
            .notices
            .iter()
            .find(|row| matches!(&row.target,Target::Page{watch_id:id,..} if id==watch_id))
            .cloned()
            .ok_or("An accepted change did not produce its private notice.")?;
        let persisted = load(&ledger.path)?;
        if !persisted.notices.iter().any(|row| row.id == notice.id)
            || !persisted.deduplicated.contains_key(&notice.content_key)
        {
            return Err("The notification and deduplication receipt were not durable.".into());
        }
        notice
    };
    let public = snapshot(app).to_string();
    if public.contains("Private fixture page name") || public.contains("private-notice-fixture") {
        return Err("A private notification exposed its target title or address.".into());
    }
    completed(app, watch_id, "Complete changed content")?;
    if app
        .state::<Notices>()
        .ledger
        .lock()
        .map_err(err)?
        .document
        .notices
        .iter()
        .filter(|row| matches!(&row.target,Target::Page{watch_id:id,..} if id==watch_id))
        .count()
        != 1
    {
        return Err("An unchanged complete capture duplicated the notification.".into());
    }
    notify(app);
    let selector = format!("[data-notice-action=\"{}\"]", notice.id);
    wait(
        manager,
        format!(
            "!!document.querySelector({})",
            serde_json::to_string(&selector).map_err(err)?
        ),
        |value| value == true,
    )?;
    manager
        .eval(format!(
            "document.getElementById('noticeOpen').click();document.querySelector({}).click();",
            serde_json::to_string(&selector).map_err(err)?
        ))
        .map_err(err)?;
    let opened = wait(
        manager,
        "(()=>{const drawer=document.getElementById('detailDrawer');return {open:!drawer.hidden,watchId:drawer.dataset.watchId,noticeId:drawer.dataset.noticeId,loaded:!document.getElementById('detailBody').textContent.includes('Loading page history')};})()".into(),
        |value| {
            value["open"] == true
                && value["watchId"] == watch_id
                && value["noticeId"] == notice.id
                && value["loaded"] == true
        },
    )?;
    manager
        .eval("document.getElementById('closeDetail').click();")
        .map_err(err)?;
    state
        .store
        .command("pauseWatch", &json!({"id":watch_id,"enabled":false}), now())?;
    notify(app);
    wait(
        manager,
        "document.getElementById('detailDrawer').hidden".into(),
        |value| value == true,
    )?;
    manager
        .eval(format!(
            "document.getElementById('noticeOpen').click();document.querySelector({}).click();",
            serde_json::to_string(&selector).map_err(err)?
        ))
        .map_err(err)?;
    let rejected=wait(manager,"(()=>({rejected:document.getElementById('noticeStatus').dataset.outcome==='rejected',detailClosed:document.getElementById('detailDrawer').hidden}))()".into(),|value|value["rejected"]==true&&value["detailClosed"]==true)?;
    if request_focus(app, &notice).is_ok() {
        return Err("The OS action route accepted a stale notification identity.".into());
    }
    manager
        .eval("document.getElementById('noticeClose').click();")
        .map_err(err)?;
    state
        .store
        .command("removeWatch", &json!({"id":watch_id}), now())?;
    execute(app, "dismissNotice", &json!({"id":notice.id}))?;
    notify(app);
    Ok(
        json!({"actualBundledButton":true,"currentExactPageOpened":opened,"staleExactPageRejected":rejected,
        "baselineSuppressed":true,"identicalCaptureDeduplicated":true,"durableDeduplication":true,
        "privateGenericPayload":true,"nativeOsActionRouteRejectsStale":true,
        "osToastDisplayedOrClicked":false,
        "scope":"Real bundled WebView button → native notification IPC → current exact page detail; stale page rejected. Accepted observations are owned fixture data. Native updater freshness/lease tests and Python fact-producer tests are separate; no physical OS toast click is claimed."}),
    )
}
