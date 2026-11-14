//! Browser-owned file URLs stay transient. Durable choices contain only exact
//! source identity hashes and user-confirmed associations.
use super::*;
#[derive(Clone)]
struct Offer {
    id: String,
    name: String,
    url: String,
    kind: String,
    source_file_key: String,
    related: bool,
    published_at: String,
    supported: bool,
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
    let runtime = app.state::<WatchRuntime>();
    let offers: Vec<_> = rows.values().filter(|o| o.created.elapsed() < Duration::from_secs(300)).map(|o| {
        let watch_id = o.context.download_watch.as_ref().map(|w| w.0.as_str()).unwrap_or("");
        let watch = runtime.store.watch(watch_id);
        let decision = runtime.store.source_file_decision(watch_id, &o.source_file_key);
        let current = watch.as_ref().is_some_and(|w| w.removed_at.is_none() && o.context.download_watch.as_ref().is_some_and(|(_,generation)| w.generation == *generation)) && o.context.current(&runtime);
        json!({"id":o.id,"name":o.name,"kind":o.kind,"origin":o.context.origin,"watchId":watch_id,
            "sourceFileKey":o.source_file_key,"related":o.related,"publishedAt":o.published_at,
            "supported":o.supported,"current":current,"ignored":decision.as_ref().is_some_and(|d|d.ignored),
            "classification":decision.as_ref().map_or("unclassified",|d|d.kind.as_str()),"explicitDecision":decision.is_some(),
            "association":decision.as_ref().and_then(|d|d.association.as_ref()),"canUpdate":!matches!(o.kind.as_str(),"folder"|"chooser")&&watch.as_ref().is_some_and(|w|w.association.is_some()),
            "newContentOptIn":runtime.store.creator_offers_new_content(watch_id)})
    }).collect();
    json!({"revision":state.revision.load(Ordering::Acquire),"offers":offers})
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
    if !runtime.store.watch_eligible(&watch.id) {
        return Err("This source is paused, muted, or waiting for account verification. Resume it before finding files.".into());
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
    let cfg = json!({"key":key,"source":url.as_str(),"includeNewContent":runtime.store.creator_offers_new_content(&watch.id)});
    view.0
        .eval(format!(
            "(()=>{{const cfg={cfg};{}}})()",
            include_str!("../../../ui/resolve-offers.js")
        ))
        .map_err(err)?;
    let reply = loop {
        binding(app, &context)?;
        if !runtime.store.watch_eligible(&watch.id) {
            return Err("Source checks were paused or account verification is required. The previous complete files remain available.".into());
        }
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
        .ok_or("Source did not return a complete file list.")?;
    if rows.len() > 200 {
        return Err("Source returned too many files. Select a narrower source page; the previous complete result is retained.".into());
    }
    let mut collected = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for row in rows {
        let raw = row["url"]
            .as_str()
            .ok_or("Source returned an invalid file address; no partial result was accepted.")?;
        let target = Url::parse(raw).map_err(
            |_| "Source returned an invalid file address; no partial result was accepted.",
        )?;
        if !policy::website_navigation(&target) {
            return Err(
                "Source returned an unsupported file address; no partial result was accepted."
                    .into(),
            );
        }
        let name = row["name"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 960 && !s.chars().any(char::is_control))
            .ok_or("Source returned an invalid file name.")?
            .to_owned();
        let identity = row["identity"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 8192 && !s.chars().any(char::is_control))
            .ok_or("Source returned no stable file identity.")?;
        let identity = policy::download_scope::stable_identity(&url, &target, identity)?;
        let source_file_key = format!(
            "{:x}",
            Sha256::digest(format!("{}\0{}", watch.url, identity).as_bytes())
        );
        if !seen.insert(source_file_key.clone()) {
            return Err(
                "Source returned duplicate file identities; refresh it before choosing a file."
                    .into(),
            );
        }
        let mut kind = row["kind"]
            .as_str()
            .filter(|s| matches!(*s, "attachment" | "external" | "chooser" | "folder"))
            .ok_or("Source returned an unknown file kind.")?
            .to_owned();
        let published_at = row["publishedAt"]
            .as_str()
            .filter(|s| s.len() <= 100 && !s.chars().any(char::is_control))
            .unwrap_or("")
            .to_owned();
        if policy::download_scope::is_folder(&target) {
            kind = "folder".into();
        }
        let supported = !matches!(kind.as_str(), "folder" | "chooser")
            && allowed_download(raw, &context.origin, context.fixture_origin.as_deref());
        let id = format!("offer-{}", runtime.sequence.fetch_add(1, Ordering::AcqRel));
        collected.push(Offer {
            id,
            name,
            url: raw.into(),
            kind,
            source_file_key,
            related: row["related"].as_bool().unwrap_or(false),
            published_at,
            supported,
            context: context.clone(),
            created: Instant::now(),
        });
    }
    // Commit only after every returned row is validated. A failed resolver never
    // replaces a complete result with a shortened or partially accepted list.
    let accepted = collected.len();
    let registry = app.state::<Offers>();
    let mut offers = registry.rows.lock().map_err(err)?;
    offers.retain(|_, o| {
        o.created.elapsed() < Duration::from_secs(300)
            && o.context
                .download_watch
                .as_ref()
                .is_none_or(|w| w.0 != watch.id)
    });
    for offer in collected {
        offers.insert(offer.id.clone(), offer);
    }
    registry.revision.fetch_add(1, Ordering::AcqRel);
    Ok(
        json!({"message":if accepted>0{format!("Found {accepted} file choices. Classify exact files before opening them; ignored and separate-content choices are retained.")}else{"No accessible archive links found. Open the source page to choose a file or finish sign-in.".into()}}),
    )
}
fn current_offer(app: &tauri::AppHandle, value: &Value) -> Result<Offer> {
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
    Ok(offer)
}
/// The renderer supplies an opaque offer ID, never a source URL or file key.
pub(super) fn decide_offer(app: &tauri::AppHandle, value: &Value, clear: bool) -> Result<Value> {
    let offer = current_offer(app, value)?;
    if !clear
        && matches!(offer.kind.as_str(), "chooser" | "folder")
        && value
            .get("kind")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind != "unclassified")
    {
        return Err("Choose an exact attachment or a direct external file link before assigning an installation relationship.".into());
    }
    let (watch_id, generation) = offer
        .context
        .download_watch
        .as_ref()
        .ok_or("Missing exact source identity.")?;
    let runtime = app.state::<WatchRuntime>();
    let result = runtime.store.command(if clear {"clearFileDecision"} else {"saveFileDecision"}, &json!({
        "watchId":watch_id,"generation":generation,"sourceFileKey":offer.source_file_key,"name":offer.name,
        "kind":value.get("kind").and_then(Value::as_str).unwrap_or("unclassified"),"ignored":value.get("ignored").and_then(Value::as_bool).unwrap_or(false)
    }),now())?;
    // Existing offers can remain visible, but only refreshed source decisions
    // can acquire an archive. Keep the clicked row usable for a follow-up choice.
    let next = runtime
        .store
        .watch(watch_id)
        .ok_or("Source disappeared after saving its choice.")?
        .generation;
    let registry = app.state::<Offers>();
    let mut rows = registry.rows.lock().map_err(err)?;
    for row in rows.values_mut() {
        if row
            .context
            .download_watch
            .as_ref()
            .is_some_and(|(id, g)| id == watch_id && *g == *generation)
        {
            row.context.download_watch = Some((watch_id.clone(), next));
        }
    }
    registry.revision.fetch_add(1, Ordering::AcqRel);
    Ok(result)
}
pub(super) fn open_offer(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let offer = current_offer(app, value)?;
    if !offer.supported {
        return Err("This host is not supported for managed downloads. Open the tracked source to review its download instructions.".into());
    }
    let runtime = app.state::<WatchRuntime>();
    let watch_id = offer
        .context
        .download_watch
        .as_ref()
        .ok_or("Missing source identity.")?
        .0
        .as_str();
    let decision = runtime
        .store
        .source_file_decision(watch_id, &offer.source_file_key);
    if decision.as_ref().is_some_and(|d| d.ignored) {
        return Err("This exact file is ignored. Restore its choice before opening it.".into());
    }
    if decision
        .as_ref()
        .is_none_or(|decision| !matches!(decision.kind.as_str(), "update" | "newContent"))
    {
        return Err("Classify this exact file as an installed update or separate content before opening it.".into());
    }
    let url = Url::parse(&offer.url).map_err(err)?;
    let mut context = offer.context;
    context.download_file_key = Some(offer.source_file_key);
    context.download_scope = Some(Arc::new(Mutex::new(
        policy::download_scope::SelectedDownloadScope::new(url.clone())?,
    )));
    if url.origin().ascii_serialization() != context.origin {
        // Never lend a creator account's browser profile to an external host.
        context.origin = url.origin().ascii_serialization();
        context.session = None;
    }
    site_window(app, context, url, true, None, Arc::new(Load::default()))?;
    Ok(
        json!({"message":"Opened the selected file. Approve any resulting archive in Page downloads; installation requires a separate exact target review."}),
    )
}
