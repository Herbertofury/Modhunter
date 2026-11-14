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
    publication_evidence: String,
    access_evidence: String,
    discovery_id: u64,
    mega_file: Option<policy::download_scope::MegaFolderFile>,
    supported: bool,
    context: SiteContext,
    created: Instant,
}
struct Offers {
    rows: Mutex<BTreeMap<String, Offer>>,
    discoveries: Mutex<BTreeMap<String, Discovery>>,
    preparations: Mutex<policy::preparation::Registry>,
    revision: AtomicU64,
}
pub(in crate::watchtower_windows) struct Preparation {
    app: tauri::AppHandle,
    ticket: policy::preparation::Ticket,
    retained: bool,
}
impl Preparation {
    pub(in crate::watchtower_windows) fn cancel(&self) -> Arc<AtomicBool> {
        self.ticket.cancel.clone()
    }
    pub(in crate::watchtower_windows) fn window(&self, window: &WebviewWindow) -> Result<()> {
        let result = self
            .app
            .state::<Offers>()
            .preparations
            .lock()
            .map_err(err)?
            .window(&self.ticket, window.label().into());
        if result.is_err() {
            let _ = window.close();
        }
        result
    }
    pub(in crate::watchtower_windows) fn scope(
        &self,
        scope: Arc<Mutex<policy::download_scope::SelectedDownloadScope>>,
    ) -> Result<()> {
        self.app
            .state::<Offers>()
            .preparations
            .lock()
            .map_err(err)?
            .scope(&self.ticket, scope)
    }
    pub(in crate::watchtower_windows) fn retain(mut self, window: &WebviewWindow) -> Result<()> {
        let registry = self.app.state::<Offers>();
        let guard = registry.preparations.lock().map_err(err)?;
        if !guard.current(&self.ticket) {
            return Err("The selected file preparation was cancelled.".into());
        }
        let app = self.app.clone();
        let ticket = self.ticket.clone();
        window.on_window_event(move |event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                app.state::<Offers>()
                    .preparations
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .abandon(&ticket);
            }
        });
        self.retained = true;
        Ok(())
    }
}
impl Drop for Preparation {
    fn drop(&mut self) {
        if self.retained {
            return;
        }
        let windows = self
            .app
            .state::<Offers>()
            .preparations
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .abandon(&self.ticket);
        for label in windows {
            if let Some(window) = self.app.get_webview_window(&label) {
                let _ = window.close();
            }
        }
    }
}
pub(in crate::watchtower_windows) fn begin_preparation(
    app: &tauri::AppHandle,
) -> Result<Preparation> {
    app.state::<WatchRuntime>().writable()?;
    let ticket = app
        .state::<Offers>()
        .preparations
        .lock()
        .map_err(err)?
        .begin()?;
    Ok(Preparation {
        app: app.clone(),
        ticket,
        retained: false,
    })
}
/// Called on the GUI thread. Only registered resolution/selected-file windows
/// belong to this operation; ordinary sign-in and completed archives do not.
pub(super) fn cancel_preparations(app: &tauri::AppHandle) -> policy::preparation::Cancelled {
    let registry = app.state::<Offers>();
    let cancelled = {
        let mut gate = registry
            .preparations
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let cancelled = gate.cancel_all();
        for discovery in registry
            .discoveries
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values_mut()
        {
            if discovery.state == "resolving" {
                discovery.state = "cancelled";
            }
        }
        cancelled
    };
    registry.revision.fetch_add(1, Ordering::AcqRel);
    cancelled
}
#[derive(Clone)]
struct Discovery {
    id: u64,
    generation: u64,
    state: &'static str,
}
pub(super) fn setup(app: &tauri::AppHandle) {
    app.manage(Offers {
        rows: Mutex::new(BTreeMap::new()),
        discoveries: Mutex::new(BTreeMap::new()),
        preparations: Mutex::new(policy::preparation::Registry::default()),
        revision: AtomicU64::new(1),
    });
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let state = app.state::<Offers>();
    let rows = state.rows.lock().unwrap_or_else(|e| e.into_inner());
    let discoveries = state.discoveries.lock().unwrap_or_else(|e| e.into_inner());
    let runtime = app.state::<WatchRuntime>();
    let current = |offer: &Offer| {
        let Some((id, generation)) = &offer.context.download_watch else {
            return false;
        };
        offer.created.elapsed() < Duration::from_secs(300)
            && offer.context.current(&runtime)
            && runtime
                .store
                .watch(id)
                .is_some_and(|w| w.generation == *generation && w.removed_at.is_none())
            && discoveries.get(id).is_some_and(|d| {
                d.id == offer.discovery_id && d.generation == *generation && d.state == "ready"
            })
    };
    let evidence: Vec<_> = rows
        .values()
        .map(|offer| modhunter_core::watchtower::SourceOfferEvidence {
            id: offer.id.clone(),
            watch_id: offer
                .context
                .download_watch
                .as_ref()
                .map_or(String::new(), |w| w.0.clone()),
            watch_generation: offer.context.download_watch.as_ref().map_or(0, |w| w.1),
            source_file_key: offer.source_file_key.clone(),
            current: current(offer) && offer.supported,
            access_evidence: offer.access_evidence.clone(),
            published_at: offer.published_at.clone(),
            publication_evidence: offer.publication_evidence.clone(),
        })
        .collect();
    let choices = runtime.store.source_offer_choices(&evidence, now());
    let offers: Vec<_> = rows.values().filter(|o| o.created.elapsed() < Duration::from_secs(300)).map(|o| {
        let watch_id = o.context.download_watch.as_ref().map(|w| w.0.as_str()).unwrap_or("");
        let watch = runtime.store.watch(watch_id);
        let decision = runtime.store.source_file_decision(watch_id, &o.source_file_key);
        let current = current(o);
        let selection=choices.iter().find(|choice|choice.id==o.id);
        json!({"id":o.id,"name":o.name,"kind":o.kind,"origin":o.context.origin,"watchId":watch_id,
            "sourceFileKey":o.source_file_key,"related":o.related,"publishedAt":o.published_at,
            "publicationEvidence":o.publication_evidence,"accessEvidence":o.access_evidence,"selection":selection,
            "resolvedFolderFile":o.mega_file.is_some(),"canResolveFolder":o.kind=="folder"&&Url::parse(&o.url).is_ok_and(|u|matches!(u.host_str(),Some("mega.nz"|"mega.io"))),
            "browserTransferLimitMinutes":Url::parse(&o.url).ok().filter(|u|matches!(u.host_str(),Some("mega.nz"|"mega.io"))).map(|_|30),
            "supported":o.supported,"current":current,"ignored":decision.as_ref().is_some_and(|d|d.ignored),
            "classification":decision.as_ref().map_or("unclassified",|d|d.kind.as_str()),"explicitDecision":decision.is_some(),
            "association":decision.as_ref().and_then(|d|d.association.as_ref()),"canUpdate":!matches!(o.kind.as_str(),"folder"|"chooser")&&watch.as_ref().is_some_and(|w|w.association.is_some()),
            "newContentOptIn":runtime.store.creator_offers_new_content(watch_id)})
    }).collect();
    json!({"revision":state.revision.load(Ordering::Acquire),"offers":offers,
        "discoveries":discoveries.iter().map(|(id,d)|json!({"watchId":id,"state":d.state})).collect::<Vec<_>>()})
}
pub(super) fn resolve_files(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let watch_id = id(value)?.to_owned();
    let runtime = app.state::<WatchRuntime>();
    let watch = runtime
        .store
        .watch(&watch_id)
        .ok_or("Watched source no longer exists.")?;
    let attempt = runtime.sequence.fetch_add(1, Ordering::AcqRel);
    let registry = app.state::<Offers>();
    let preparation = begin_preparation(app)?;
    let gate = registry.preparations.lock().map_err(err)?;
    if !gate.current(&preparation.ticket) {
        return Err("File discovery was cancelled before it began.".into());
    }
    registry.discoveries.lock().map_err(err)?.insert(
        watch_id.clone(),
        Discovery {
            id: attempt,
            generation: watch.generation,
            state: "resolving",
        },
    );
    drop(gate);
    registry.revision.fetch_add(1, Ordering::AcqRel);
    notify(app);
    let result = resolve_files_inner(app, value, attempt, &preparation);
    if result.is_err() {
        let gate = registry.preparations.lock().map_err(err)?;
        if gate.current(&preparation.ticket)
            && let Some(discovery) = registry.discoveries.lock().map_err(err)?.get_mut(&watch_id)
            && discovery.id == attempt
        {
            discovery.state = "failed";
        }
    }
    registry.revision.fetch_add(1, Ordering::AcqRel);
    notify(app);
    result
}
fn resolve_files_inner(
    app: &tauri::AppHandle,
    value: &Value,
    attempt: u64,
    preparation: &Preparation,
) -> Result<Value> {
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
    context.cancellation = Some(preparation.cancel());
    let load = Arc::new(Load::default());
    let window = site_window(app, context.clone(), url.clone(), false, None, load.clone())?;
    let view = WindowGuard(window);
    preparation.window(&view.0)?;
    let cancel = preparation.cancel();
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
        let publication_evidence = row["publicationEvidence"]
            .as_str()
            .filter(|s| {
                matches!(
                    *s,
                    "patreonPost" | "loversLabAttachment" | "loversLabRelease"
                )
            })
            .unwrap_or("")
            .to_owned();
        let access_evidence = row["accessEvidence"]
            .as_str()
            .filter(|s| {
                matches!(
                    *s,
                    "patreonAttachment" | "loversLabChooser" | "loversLabDirect"
                )
            })
            .unwrap_or("visibleLink")
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
            publication_evidence,
            access_evidence,
            discovery_id: attempt,
            mega_file: None,
            supported,
            context: context.clone(),
            created: Instant::now(),
        });
    }
    // Commit only after every returned row is validated. A failed resolver never
    // replaces a complete result with a shortened or partially accepted list.
    let accepted = collected.len();
    let registry = app.state::<Offers>();
    let result=registry.preparations.lock().map_err(err)?.publish(&preparation.ticket,||{
        binding(app,&context)?;
        let mut offers=registry.rows.lock().map_err(err)?;
        let mut discoveries=registry.discoveries.lock().map_err(err)?;
        let discovery=discoveries.get_mut(&watch.id).filter(|d|d.id==attempt&&d.generation==watch.generation&&d.state=="resolving")
            .ok_or("A newer or cancelled file discovery replaced this attempt. Its late result was not accepted.")?;
        offers.retain(|_,o|o.created.elapsed()<Duration::from_secs(300)&&o.context.download_watch.as_ref().is_none_or(|w|w.0!=watch.id));
        for offer in collected{offers.insert(offer.id.clone(),offer);}
        discovery.state="ready";Ok(())
    });
    result?;
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
    let watch_id = offer
        .context
        .download_watch
        .as_ref()
        .map(|w| w.0.as_str())
        .unwrap_or("");
    if !registry
        .discoveries
        .lock()
        .map_err(err)?
        .get(watch_id)
        .is_some_and(|d| d.id == offer.discovery_id && d.state == "ready")
    {
        return Err(
            "The latest discovery did not confirm this exact file's access. Find files again."
                .into(),
        );
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
    if let Some(discovery) = registry.discoveries.lock().map_err(err)?.get_mut(watch_id)
        && discovery.generation == *generation
    {
        discovery.generation = next;
    }
    registry.revision.fetch_add(1, Ordering::AcqRel);
    Ok(result)
}
pub(super) fn open_offer(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let preparation = begin_preparation(app)?;
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
    let mega = matches!(url.host_str(), Some("mega.nz" | "mega.io"));
    let mut context = offer.context;
    context.cancellation = Some(preparation.cancel());
    context.download_file_key = Some(offer.source_file_key);
    let folder = offer.mega_file.is_some();
    let scope = if let Some(file) = offer.mega_file {
        let key = format!(
            "__modhunterMegaFile{}",
            runtime.sequence.fetch_add(1, Ordering::AcqRel)
        );
        policy::download_scope::SelectedDownloadScope::new_mega_folder_file(
            url.clone(),
            file,
            key,
            context.fixture_origin.as_deref(),
        )?
    } else {
        policy::download_scope::SelectedDownloadScope::new(url.clone())?
    };
    context.download_scope = Some(Arc::new(Mutex::new(scope)));
    preparation.scope(
        context
            .download_scope
            .as_ref()
            .expect("created scope")
            .clone(),
    )?;
    if url.origin().ascii_serialization() != context.origin {
        // Never lend a creator account's browser profile to an external host.
        context.origin = url.origin().ascii_serialization();
        context.session = None;
    }
    let load = Arc::new(Load::default());
    let window = site_window(app, context.clone(), url, true, None, load.clone())?;
    preparation.window(&window)?;
    if folder && let Err(error) = super::mega::arm(app, &window, &load, &context) {
        if let Some(scope) = &context.download_scope {
            scope.lock().map_err(err)?.invalidate()
        }
        let _ = window.close();
        return Err(error);
    }
    binding(app, &context)?;
    preparation.retain(&window)?;
    Ok(
        json!({"message":if mega{"Opened the selected MEGA file. Its browser preparation must finish within 30 minutes; after expiry, use Find files and open the exact archive again. Approve the resulting archive in Page downloads before separate installation review."}else{"Opened the selected file. Approve any resulting archive in Page downloads; installation requires a separate exact target review."}}),
    )
}

pub(super) fn resolve_folder(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let preparation = begin_preparation(app)?;
    let parent = current_offer(app, value)?;
    let url = Url::parse(&parent.url).map_err(err)?;
    if parent.kind != "folder" || !matches!(url.host_str(), Some("mega.nz" | "mega.io")) {
        return Err("Choose a current MEGA folder offer to inspect its complete files.".into());
    }
    let watch_id = parent
        .context
        .download_watch
        .as_ref()
        .ok_or("Missing exact folder source.")?
        .0
        .as_str();
    if app
        .state::<WatchRuntime>()
        .store
        .source_file_decision(watch_id, &parent.source_file_key)
        .is_some_and(|d| d.ignored)
    {
        return Err("This folder is ignored. Restore its saved choice before inspection.".into());
    }
    let mut context = parent.context.clone();
    context.cancellation = Some(preparation.cancel());
    if url.origin().ascii_serialization() != context.origin {
        context.origin = url.origin().ascii_serialization();
        context.session = None;
    }
    let (file, selected) = super::mega::resolve(app, &context, &url, &preparation)?;
    // Recheck the original opaque offer after browser work. A fresh discovery,
    // source edit or account reset cannot borrow this result's authority.
    current_offer(app, value)?;
    let runtime = app.state::<WatchRuntime>();
    let watch_id = context
        .download_watch
        .as_ref()
        .ok_or("Missing folder source ownership.")?
        .0
        .as_str();
    let watch = runtime
        .store
        .watch(watch_id)
        .ok_or("Tracked source disappeared.")?;
    let identity = policy::download_scope::mega_folder_file_identity(&selected)
        .ok_or("The folder did not supply one exact child identity.")?;
    let source_file_key = format!(
        "{:x}",
        Sha256::digest(format!("{}\0{identity}", watch.url).as_bytes())
    );
    let id = format!("offer-{}", runtime.sequence.fetch_add(1, Ordering::AcqRel));
    let child = Offer {
        id: id.clone(),
        name: file.name.clone(),
        url: selected.to_string(),
        kind: "external".into(),
        source_file_key,
        related: parent.related,
        published_at: parent.published_at,
        publication_evidence: parent.publication_evidence,
        access_evidence: "megaFolderFile".into(),
        discovery_id: parent.discovery_id,
        mega_file: Some(file),
        supported: true,
        context,
        created: Instant::now(),
    };
    let registry = app.state::<Offers>();
    let result = registry
        .preparations
        .lock()
        .map_err(err)?
        .publish(&preparation.ticket, || {
            current_offer(app, value)?;
            binding(app, &child.context)?;
            let mut rows = registry.rows.lock().map_err(err)?;
            let original = rows
                .get(&parent.id)
                .ok_or("The folder offer changed during inspection.")?;
            if original.discovery_id != parent.discovery_id
                || original.source_file_key != parent.source_file_key
            {
                return Err("A newer folder discovery replaced this result.".into());
            }
            rows.remove(&parent.id);
            rows.insert(id, child);
            Ok(())
        });
    result?;
    registry.revision.fetch_add(1, Ordering::AcqRel);
    Ok(
        json!({"message":"The complete folder contains one eligible archive. Classify this exact file, then open it for a separate download approval."}),
    )
}
