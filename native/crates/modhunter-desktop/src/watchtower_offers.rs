use super::*;
#[derive(Clone)]
struct Offer {
    id: String,
    name: String,
    url: String,
    kind: String,
    context: SiteContext,
    created: Instant,
}
struct Offers {
    rows: Mutex<BTreeMap<String, Offer>>,
    revision: AtomicU64,
}
pub(super) fn setup(app: &tauri::AppHandle) {
    app.manage(Offers {
        rows: Mutex::new(BTreeMap::new()),
        revision: AtomicU64::new(1),
    });
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let state = app.state::<Offers>();
    let rows = state.rows.lock().unwrap_or_else(|e| e.into_inner());
    json!({"revision":state.revision.load(Ordering::Acquire),"offers":rows.values().filter(|o|o.created.elapsed()<Duration::from_secs(300)).map(|o|json!({"id":o.id,"name":o.name,"kind":o.kind,"origin":o.context.origin,"watchId":o.context.download_watch.as_ref().map(|w|&w.0)})).collect::<Vec<_>>()})
}
pub(super) fn resolve_files(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let runtime = app.state::<WatchRuntime>();
    let watch = runtime
        .store
        .watch(id(value)?)
        .ok_or("Watched source no longer exists.")?;
    if watch.removed_at.is_some() {
        return Err("Restore the tracked source first.".into());
    }
    let session = watch
        .session_id
        .as_ref()
        .map(|id| {
            runtime
                .store
                .session(id)
                .ok_or("Choose an available site session.")
        })
        .transpose()?;
    if watch.requires_session && session.is_none() {
        return Err("Choose the source account before resolving its files.".into());
    }
    let url = Url::parse(&watch.url).map_err(err)?;
    let mut context = SiteContext::production(session, &url)?;
    context.download_watch = Some((watch.id.clone(), watch.generation));
    let load = Arc::new(Load::default());
    let window = site_window(app, context.clone(), url.clone(), false, None, load.clone())?;
    let view = WindowGuard(window);
    let cancel = AtomicBool::new(false);
    let deadline = Instant::now() + CHECK_DEADLINE;
    wait_loaded(&view.0, &load, &cancel, deadline)?;
    let key = format!(
        "__modhunterOffers{}",
        runtime.sequence.fetch_add(1, Ordering::AcqRel)
    );
    let cfg = json!({"key":key,"source":url.as_str()});
    view.0
        .eval(format!(
            "(()=>{{const cfg={cfg};{}}})()",
            include_str!("../../../ui/resolve-offers.js")
        ))
        .map_err(err)?;
    let reply = loop {
        binding(app, &context)?;
        let reply = eval_json(
            &view.0,
            format!(
                "window[{}]||null",
                serde_json::to_string(&key).map_err(err)?
            ),
            &cancel,
            deadline,
        )?;
        if reply.is_object() && reply["state"] != "pending" {
            break reply;
        }
        if Instant::now() >= deadline {
            return Err(
                "Source file discovery timed out; open the page to finish sign-in or verification."
                    .into(),
            );
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    binding(app, &context)?;
    if reply["state"] != "ready" {
        return Err(reply["message"]
            .as_str()
            .unwrap_or("Open the page to review its accessible files.")
            .chars()
            .take(500)
            .collect());
    }
    let rows = reply["files"]
        .as_array()
        .ok_or("Source did not return a file list.")?;
    if rows.len() > 200 {
        return Err("Source returned too many files. Select a narrower source page.".into());
    }
    let registry = app.state::<Offers>();
    let mut offers = registry.rows.lock().map_err(err)?;
    offers.retain(|_, o| {
        o.created.elapsed() < Duration::from_secs(300)
            && o.context
                .download_watch
                .as_ref()
                .is_none_or(|w| w.0 != watch.id)
    });
    let mut accepted = 0;
    for row in rows {
        let Some(raw) = row["url"].as_str() else {
            continue;
        };
        let Ok(target) = Url::parse(raw) else {
            continue;
        };
        if !policy::website_navigation(&target) {
            continue;
        }
        let name = row["name"]
            .as_str()
            .unwrap_or("Open source file chooser")
            .chars()
            .filter(|c| !c.is_control())
            .take(240)
            .collect::<String>();
        if !allowed_download(raw, &context.origin)
            && !matches!(
                target.host_str(),
                Some("drive.google.com" | "mega.nz" | "mega.io")
            )
        {
            continue;
        }
        let id = format!("offer-{}", runtime.sequence.fetch_add(1, Ordering::AcqRel));
        let kind = if target.origin() != url.origin() {
            "external"
        } else {
            "attachment"
        };
        offers.insert(
            id.clone(),
            Offer {
                id,
                name,
                url: raw.into(),
                kind: kind.into(),
                context: context.clone(),
                created: Instant::now(),
            },
        );
        accepted += 1;
    }
    registry.revision.fetch_add(1, Ordering::AcqRel);
    drop(offers);
    Ok(
        json!({"message":if accepted>0{format!("Found {accepted} accessible file links. Choose one to review; nothing downloaded yet.")}else{"No accessible archive links found. Open the source page to choose a file or finish sign-in.".into()}}),
    )
}
pub(super) fn open_offer(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let registry = app.state::<Offers>();
    let offer = registry
        .rows
        .lock()
        .map_err(err)?
        .get(id(value)?)
        .cloned()
        .ok_or("File offer expired; find files again.")?;
    if offer.created.elapsed() > Duration::from_secs(300) {
        return Err("File offer expired; find files again.".into());
    }
    binding(app, &offer.context)?;
    let url = Url::parse(&offer.url).map_err(err)?;
    let mut context = offer.context;
    if url.origin().ascii_serialization() != context.origin {
        // Never lend a creator account's browser profile to an external host.
        context.origin = url.origin().ascii_serialization();
        context.session = None;
    }
    site_window(app, context, url, true, None, Arc::new(Load::default()))?;
    Ok(
        json!({"message":"Opened the selected offer. Approve any resulting archive in Page downloads; installation is separate."}),
    )
}
