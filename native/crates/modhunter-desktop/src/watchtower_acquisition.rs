//! WebView2 owns credentials and transfer streams. Only the bundled manager can
//! approve a deferred download. Remote HTML never gains native IPC authority.
use super::*;
use modhunter_core::acquisition::{Binding, MAX_BYTES, Store, archive_identity};
use std::{
    cell::RefCell,
    collections::{BTreeSet, HashMap},
};
use webview2_com::Microsoft::Web::WebView2::Win32::{
    COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED, COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS, ICoreWebView2,
    ICoreWebView2_4, ICoreWebView2Deferral, ICoreWebView2DownloadOperation,
    ICoreWebView2DownloadStartingEventArgs,
};

struct Downloads {
    store: Mutex<Store>,
    root: PathBuf,
    lifecycle: Arc<Mutex<BTreeSet<String>>>,
}
/// This capability spans verification and the complete operation using the
/// bytes. Receipt mutexes stay short-lived; deletion and review share one owner.
pub(super) struct DownloadClaim {
    registry: Arc<Mutex<BTreeSet<String>>>,
    id: String,
}
impl Drop for DownloadClaim {
    fn drop(&mut self) {
        self.registry
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&self.id);
    }
}
fn claim_ids(
    registry: &Arc<Mutex<BTreeSet<String>>>,
    ids: &[String],
) -> Result<Vec<DownloadClaim>> {
    let unique = ids.iter().collect::<BTreeSet<_>>();
    if ids.len() > 2000
        || unique.len() != ids.len()
        || ids.iter().any(|id| {
            !id.starts_with("download-")
                || id.len() > 90
                || !id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        })
    {
        return Err("Invalid or repeated download lifecycle identity.".into());
    }
    let mut owned = registry.lock().map_err(err)?;
    if ids.iter().any(|id| owned.contains(id)) {
        return Err("A selected download is being reviewed, verified or removed. Wait for that operation to finish.".into());
    }
    owned.extend(ids.iter().cloned());
    Ok(ids
        .iter()
        .map(|id| DownloadClaim {
            registry: registry.clone(),
            id: id.clone(),
        })
        .collect())
}
pub(super) fn claim_downloads(
    app: &tauri::AppHandle,
    ids: &[String],
) -> Result<Vec<DownloadClaim>> {
    claim_ids(&app.state::<Downloads>().lifecycle, ids)
}
pub(super) fn claim_download(app: &tauri::AppHandle, id: &str) -> Result<DownloadClaim> {
    claim_downloads(app, &[id.to_owned()])?
        .pop()
        .ok_or("Missing download lifecycle claim.".into())
}
fn verify_claim(downloads: &Downloads, claim: &DownloadClaim) -> Result<()> {
    if !Arc::ptr_eq(&downloads.lifecycle, &claim.registry)
        || !claim.registry.lock().map_err(err)?.contains(&claim.id)
    {
        return Err("Download lifecycle ownership changed.".into());
    }
    Ok(())
}
pub(super) fn remove_claimed(
    app: &tauri::AppHandle,
    claim: &DownloadClaim,
    confirm: &str,
    expected: Option<&Value>,
) -> Result<()> {
    let downloads = app.state::<Downloads>();
    verify_claim(&downloads, claim)?;
    // Preparation cannot register itself between this check and Store::remove:
    // both paths require the same exclusive per-download capability.
    super::review::ensure_removable(app, &claim.id)?;
    let mut store = downloads.store.lock().map_err(err)?;
    if let Some(expected) = expected
        && serde_json::to_value(store.row(&claim.id)?).map_err(err)? != *expected
    {
        return Err("A previewed download changed. Review cleanup again.".into());
    }
    store.remove(&claim.id, confirm)
}
struct Held {
    operation: ICoreWebView2DownloadOperation,
    args: ICoreWebView2DownloadStartingEventArgs,
    deferral: Option<ICoreWebView2Deferral>,
    context: SiteContext,
    label: String,
    created: Instant,
    active: bool,
}
thread_local! { static HANDLES:RefCell<HashMap<String,Held>>=RefCell::new(HashMap::new()); }
struct FolderValidation {
    view: ICoreWebView2,
    args: ICoreWebView2DownloadStartingEventArgs,
    deferral: ICoreWebView2Deferral,
    context: SiteContext,
    label: String,
    created: Instant,
}
thread_local! { static FOLDER_VALIDATIONS:RefCell<HashMap<String,FolderValidation>>=RefCell::new(HashMap::new()); }

pub(super) fn setup(app: &tauri::AppHandle, path: PathBuf) {
    app.manage(Downloads {
        store: Mutex::new(Store::open(path.clone())),
        root: path,
        lifecycle: Arc::new(Mutex::new(BTreeSet::new())),
    });
    offers::setup(app);
    let a = app.clone();
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(Duration::from_millis(500));
            if a.state::<WatchRuntime>().stopping.load(Ordering::Acquire) {
                break;
            }
            let tick_app = a.clone();
            if a.run_on_main_thread(move || tick(&tick_app)).is_err() {
                break;
            }
        }
    });
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let mut data = app
        .try_state::<Downloads>()
        .map(|s| s.store.lock().unwrap_or_else(|e| e.into_inner()).snapshot())
        .unwrap_or_else(|| json!({"revision":0,"jobs":[],"warning":"Downloads unavailable"}));
    let offers = offers::snapshot(app);
    data["offers"] = offers["offers"].clone();
    data["discoveries"] = offers["discoveries"].clone();
    data["revision"] = json!(
        data["revision"]
            .as_u64()
            .unwrap_or(0)
            .saturating_add(offers["revision"].as_u64().unwrap_or(0))
    );
    data
}
fn binding(app: &tauri::AppHandle, context: &SiteContext) -> Result<Binding> {
    let state = app.state::<WatchRuntime>();
    state.writable()?;
    if context
        .download_scope
        .as_ref()
        .is_some_and(|scope| scope.lock().map_or(true, |scope| !scope.live()))
    {
        return Err(
            "The selected file's browser authority expired or changed. Find files again.".into(),
        );
    }
    if !context.current(&state) {
        return Err("Account session changed; reopen the tracked page.".into());
    }
    let (id, generation) = context
        .download_watch
        .as_ref()
        .ok_or("Downloads must start from a saved tracked page.")?;
    let watch = state
        .store
        .watch(id)
        .ok_or("The tracked page was removed.")?;
    if watch.generation != *generation || watch.removed_at.is_some() {
        return Err("Tracked source changed; reopen it before downloading.".into());
    }
    let page = state.store.source_binding(id);
    let decision = context
        .download_file_key
        .as_ref()
        .and_then(|key| state.store.source_file_decision(id, key));
    if decision.as_ref().is_some_and(|file| file.ignored) {
        return Err(
            "This exact file is marked not interested. Change its decision before downloading."
                .into(),
        );
    }
    let content_intent = if context.download_file_key.is_some() {
        decision
            .as_ref()
            .map(|file| file.kind.clone())
            .unwrap_or_else(|| "unclassified".into())
    } else {
        // A release page can offer several unrelated archives. Only an exact
        // selected file decision grants an installation relationship.
        "unclassified".into()
    };
    let association = if content_intent == "update" {
        if !page.as_ref().is_some_and(|page| {
            page.compatibility_confirmed && !page.game.is_empty() && page.content_intent == "update"
        }) {
            return Err(
                "Confirm this source's game, runtime and exact update target before downloading."
                    .into(),
            );
        }
        let selected = decision
            .as_ref()
            .and_then(|file| file.association.clone())
            .or_else(|| page.as_ref().and_then(|page| page.association.clone()))
            .or_else(|| watch.association.clone());
        if !selected
            .as_ref()
            .zip(watch.association.as_ref())
            .is_some_and(|(selected, current)| {
                selected.profile_id == current.profile_id && selected.item_id == current.item_id
            })
        {
            return Err(
                "The exact update target changed; review the source association again.".into(),
            );
        }
        watch.association.clone()
    } else {
        None
    };
    Ok(Binding {
        watch_id: id.clone(),
        watch_generation: *generation,
        session_id: context.session.as_ref().map(|s| s.id.clone()),
        session_generation: context.session.as_ref().map_or(0, |s| s.storage_generation),
        source_origin: context.origin.clone(),
        association,
        source_file_key: context.download_file_key.clone(),
        content_intent,
    })
}
fn allowed_download(raw: &str, source: &str, fixture_origin: Option<&str>) -> bool {
    let Ok(url) = Url::parse(raw) else {
        return false;
    };
    if url.scheme() == "blob" {
        return url
            .as_str()
            .strip_prefix("blob:")
            .and_then(|u| Url::parse(u).ok())
            .is_some_and(|u| u.origin().ascii_serialization() == source);
    }
    // This capability is constructed only by the private acceptance server.
    // No manager command or website payload can request a loopback exemption.
    if fixture_origin == Some(source)
        && url.scheme() == "http"
        && url.host_str().is_some_and(|host| {
            host.parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
        })
        && url.origin().ascii_serialization() == source
    {
        return true;
    }
    if !policy::website_navigation(&url) {
        return false;
    }
    let host = url.host_str().unwrap_or("");
    url.origin().ascii_serialization() == source
        || [
            "patreonusercontent.com",
            "loverslab.com",
            "drive.usercontent.google.com",
            "drive.google.com",
            "mega.nz",
            "mega.io",
        ]
        .iter()
        .any(|h| host == *h || host.ends_with(&format!(".{h}")))
}
/// Runs on the WebView2 UI thread. No COM pointer is sent to worker threads.
pub(super) fn install(
    app: tauri::AppHandle,
    view: &ICoreWebView2,
    context: SiteContext,
    label: String,
    interactive: bool,
) -> windows_core::Result<()> {
    let view = view.cast::<ICoreWebView2_4>()?;
    let callback =
        webview2_com::DownloadStartingEventHandler::create(Box::new(move |sender, args| {
            let Some(args) = args else { return Ok(()) };
            unsafe {
                args.SetCancel(true)?;
                args.SetHandled(true)?;
            }
            if !interactive {
                return Ok(());
            }
            let requested = (|| -> Result<()> {
                let sender = sender.as_ref().ok_or("The download owner disappeared.")?;
                binding(&app, &context)?;
                let folder = context
                    .download_scope
                    .as_ref()
                    .map(|scope| scope.lock().map(|s| s.mega_folder()).map_err(err))
                    .transpose()?
                    .flatten();
                let deferral = unsafe { args.GetDeferral() }.map_err(err)?;
                if let Some((_, key, _)) = folder {
                    return validate_folder_download(
                        &app, sender, &args, &deferral, &context, &label, &key,
                    );
                }
                let result =
                    request_download(&app, sender, &args, &deferral, &context, &label, None);
                if result.is_err() {
                    let _ = unsafe { deferral.Complete() };
                }
                result
            })();
            if let Err(message) = requested {
                let _ = app.emit_to(
                    "watchtower",
                    "watchtower-download-error",
                    json!({"message":message}),
                );
            }
            Ok(())
        }));
    let mut token = 0;
    unsafe { view.add_DownloadStarting(&callback, &mut token) }?;
    Ok(())
}

fn report_download_error(app: &tauri::AppHandle, message: String) {
    let _ = app.emit_to(
        "watchtower",
        "watchtower-download-error",
        json!({"message":message}),
    );
}
/// A folder URL alone never authorizes a transfer. Read the provider's observed
/// exact node/blob handoff while DownloadStarting remains cancelled and deferred.
fn validate_folder_download(
    app: &tauri::AppHandle,
    view: &ICoreWebView2,
    args: &ICoreWebView2DownloadStartingEventArgs,
    deferral: &ICoreWebView2Deferral,
    context: &SiteContext,
    label: &str,
    key: &str,
) -> Result<()> {
    let Some(window) = app.get_webview_window(label) else {
        let _ = unsafe { deferral.Complete() };
        return Err("The folder download window closed.".into());
    };
    if FOLDER_VALIDATIONS.with(|pending| pending.borrow().len() >= 16) {
        let _ = unsafe { deferral.Complete() };
        return Err(
            "Too many pending browser download validations. Retry the intended archive.".into(),
        );
    }
    let id = format!(
        "folder-validation-{}",
        app.state::<WatchRuntime>()
            .sequence
            .fetch_add(1, Ordering::AcqRel)
    );
    FOLDER_VALIDATIONS.with(|pending| {
        pending.borrow_mut().insert(
            id.clone(),
            FolderValidation {
                view: view.clone(),
                args: args.clone(),
                deferral: deferral.clone(),
                context: context.clone(),
                label: label.into(),
                created: Instant::now(),
            },
        )
    });
    let a = app.clone();
    let token = id.clone();
    let script = format!(
        "window[{}]?.proof||null",
        serde_json::to_string(key).map_err(err)?
    );
    let result=window.eval_with_callback(script,move|data|{
        let gui=a.clone();let token=token.clone();let _=a.run_on_main_thread(move||{
            let Some(held)=FOLDER_VALIDATIONS.with(|pending|pending.borrow_mut().remove(&token)) else{return};
            let outcome=(||{
                if held.created.elapsed()>Duration::from_secs(15)||gui.get_webview_window(&held.label).is_none(){return Err("Folder download verification expired or its browser closed.".into())}
                if data.len()>8192{return Err("The folder download returned oversized verification metadata.".into())}
                let proof:policy::download_scope::MegaBlobProof=serde_json::from_str(&data).map_err(|_|"The provider did not confirm this exact file-to-blob download. Use the selected archive's normal browser download.".to_owned())?;
                request_download(&gui,&held.view,&held.args,&held.deferral,&held.context,&held.label,Some(&proof))
            })();
            if let Err(error)=outcome{
                if let Some(scope)=&held.context.download_scope&&let Ok(mut scope)=scope.lock(){scope.invalidate();}
                let _=unsafe{held.args.SetCancel(true)};let _=unsafe{held.deferral.Complete()};report_download_error(&gui,error);
            }
        });
    });
    if let Err(error) = result {
        FOLDER_VALIDATIONS.with(|pending| pending.borrow_mut().remove(&id));
        let _ = unsafe { deferral.Complete() };
        return Err(err(error));
    }
    Ok(())
}
fn request_download(
    app: &tauri::AppHandle,
    sender: &ICoreWebView2,
    args: &ICoreWebView2DownloadStartingEventArgs,
    deferral: &ICoreWebView2Deferral,
    context: &SiteContext,
    label: &str,
    folder_proof: Option<&policy::download_scope::MegaBlobProof>,
) -> Result<()> {
    let b = binding(app, context)?;
    let operation = unsafe { args.DownloadOperation() }.map_err(err)?;
    let mut uri = windows_core::PWSTR::null();
    unsafe { operation.Uri(&mut uri) }.map_err(err)?;
    let uri = webview2_com::take_pwstr(uri);
    let mut name = windows_core::PWSTR::null();
    unsafe { args.ResultFilePath(&mut name) }.map_err(err)?;
    let path = webview2_com::take_pwstr(name);
    let name = Path::new(&path)
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or("Missing archive filename.")?;
    let mut total = 0;
    unsafe { operation.TotalBytesToReceive(&mut total) }.map_err(err)?;
    let total = (total > 0).then_some(total as u64);
    let mut allowed_origin = b.source_origin.clone();
    if context.download_file_key.is_some() {
        let scope = context
            .download_scope
            .as_ref()
            .ok_or("This exact file has no live browser authority. Find files again.")?;
        let mut current = windows_core::PWSTR::null();
        unsafe { sender.Source(&mut current) }.map_err(err)?;
        let current = Url::parse(&webview2_com::take_pwstr(current))
            .map_err(|_| "The browser did not expose an exact current page.")?;
        let mut scope = scope.lock().map_err(err)?;
        if let Some(proof) = folder_proof {
            if proof.file.name != name || total != Some(proof.file.bytes) {
                scope.invalidate();
                return Err("The browser download filename or byte count changed from the reviewed folder file.".into());
            }
            scope.claim_mega_blob(&Url::parse(&uri).map_err(err)?, &current, proof, now())?;
        } else {
            scope.claim(&Url::parse(&uri).map_err(err)?, &current)?;
        }
        if matches!(current.scheme(), "https" | "http") {
            allowed_origin = current.origin().ascii_serialization();
        }
    }
    if !allowed_download(&uri, &allowed_origin, context.fixture_origin.as_deref()) {
        return Err("This download left the tracked site for an unsupported host.".into());
    }
    let row = app
        .state::<Downloads>()
        .store
        .lock()
        .map_err(err)?
        .request(name, b, total, now())?;
    HANDLES.with(|handles| {
        handles.borrow_mut().insert(
            row.id,
            Held {
                operation,
                args: args.clone(),
                deferral: Some(deferral.clone()),
                context: context.clone(),
                label: label.into(),
                created: Instant::now(),
                active: false,
            },
        )
    });
    notify(app);
    if let Some(manager) = app.get_webview_window("watchtower")
        && let Some(window) = app.get_window(manager.label())
    {
        let _ = super::privacy::guarded_show(app, &window, true);
    }
    Ok(())
}
fn cancel_handle(h: &mut Held) {
    unsafe {
        let _ = h.args.SetCancel(true);
        if let Some(d) = h.deferral.take() {
            let _ = d.Complete();
        }
        let _ = h.operation.Cancel();
    }
}
fn tick(app: &tauri::AppHandle) {
    FOLDER_VALIDATIONS.with(|pending| {
        pending.borrow_mut().retain(|_, held| {
            let current = held.created.elapsed() <= Duration::from_secs(15)
                && app.get_webview_window(&held.label).is_some()
                && binding(app, &held.context).is_ok();
            if !current {
                let _ = unsafe { held.args.SetCancel(true) };
                let _ = unsafe { held.deferral.Complete() };
            }
            current
        })
    });
    let mut validations = Vec::new();
    let mut changed = false;
    HANDLES.with(|handles| {
        let mut handles = handles.borrow_mut();
        let mut remove = Vec::new();
        for (id, h) in handles.iter_mut() {
            let b = binding(app, &h.context);
            if b.is_err()
                || app.get_webview_window(&h.label).is_none()
                || (!h.active && h.created.elapsed() > Duration::from_secs(120))
            {
                cancel_handle(h);
                let _ = app
                    .state::<Downloads>()
                    .store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .finish_error(
                        id,
                        "cancelled",
                        "The page closed, approval expired, or its account/source changed.",
                    );
                remove.push(id.clone());
                changed = true;
                continue;
            }
            if !h.active {
                continue;
            }
            let mut state = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
            let mut received = 0;
            let mut total = 0;
            let result = unsafe {
                h.operation
                    .State(&mut state)
                    .and_then(|_| h.operation.BytesReceived(&mut received))
                    .and_then(|_| h.operation.TotalBytesToReceive(&mut total))
            };
            if result.is_err()
                || received < 0
                || received as u64 > MAX_BYTES
                || total > MAX_BYTES as i64
            {
                cancel_handle(h);
                let _ = app
                    .state::<Downloads>()
                    .store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .finish_error(
                        id,
                        "failed",
                        "The browser transfer failed or exceeded its size limit.",
                    );
                remove.push(id.clone());
                changed = true;
                continue;
            }
            if state == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
                let b = b.unwrap();
                let downloads = app.state::<Downloads>();
                let mut store = downloads.store.lock().unwrap_or_else(|e| e.into_inner());
                if store.begin_validation(id, &b).is_ok()
                    && let Ok(path) = store.staging(id)
                {
                    validations.push((id.clone(), b, h.context.clone(), path));
                }
                remove.push(id.clone());
                changed = true;
            } else if state != COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS {
                let _ = app
                    .state::<Downloads>()
                    .store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .finish_error(
                        id,
                        "interrupted",
                        "Browser interrupted this transfer. Reopen the source and retry.",
                    );
                remove.push(id.clone());
                changed = true;
            } else {
                let downloads = app.state::<Downloads>();
                let mut store = downloads.store.lock().unwrap_or_else(|e| e.into_inner());
                if let Err(e) = store.progress(
                    id,
                    received as u64,
                    if total > 0 { Some(total as u64) } else { None },
                ) {
                    cancel_handle(h);
                    let _ = store.finish_error(id, "failed", &e);
                    remove.push(id.clone());
                }
                changed = true;
            }
        }
        for id in remove {
            handles.remove(&id);
        }
    });
    for (id, b, context, path) in validations {
        let a = app.clone();
        std::thread::spawn(move || {
            let result = archive_identity(&path, || {
                !context.current(&a.state::<WatchRuntime>())
                    || a.state::<Downloads>()
                        .store
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .row(&id)
                        .map_or(true, |row| row.state != "validating")
            });
            let downloads = a.state::<Downloads>();
            let mut store = downloads.store.lock().unwrap_or_else(|e| e.into_inner());
            let outcome = result.and_then(|identity| {
                if binding(&a, &context)? != b {
                    return Err("Source changed before download verification completed.".into());
                }
                store.complete_validation(&id, &b, identity).map(|_| ())
            });
            if let Err(e) = outcome {
                let _ = store.finish_error(&id, "failed", &e);
            }
            drop(store);
            notify(&a);
        });
    }
    // WebView2 can release a cancelled file after its callback returns. Retry
    // only the exact payload.part of durable terminal rows; ready archives are
    // never eligible for this cleanup path.
    {
        let downloads = app.state::<Downloads>();
        let store = downloads.store.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(rows) = store.snapshot()["jobs"].as_array() {
            for row in rows {
                if row["state"]
                    .as_str()
                    .is_some_and(|s| matches!(s, "cancelled" | "failed" | "interrupted"))
                    && let Some(id) = row["id"].as_str()
                {
                    let _ = store.cleanup_part(id);
                }
            }
        }
    }
    if changed {
        notify(app)
    }
}
fn on_gui<T: Send + 'static>(
    app: &tauri::AppHandle,
    f: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    let (send, receive) = mpsc::sync_channel(1);
    let alive = Arc::new(AtomicBool::new(true));
    let pending = alive.clone();
    let deadline = Instant::now() + Duration::from_secs(15);
    app.run_on_main_thread(move || {
        if !pending.swap(false, Ordering::AcqRel) || Instant::now() >= deadline {
            let _ = send.send(Err("Website command expired before dispatch.".into()));
            return;
        }
        let _ = send.send(f());
    })
    .map_err(err)?;
    match receive.recv_timeout(Duration::from_secs(15)) {
        Ok(result) => result,
        Err(_) => {
            alive.store(false, Ordering::Release);
            Err("Website command timed out. Review its current state before retrying.".into())
        }
    }
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    if kind == "resolveFiles" {
        return resolve_files(app, value);
    }
    if kind == "resolveFolder" {
        return offers::resolve_folder(app, value);
    }
    if kind == "openOffer" {
        return open_offer(app, value);
    }
    if matches!(kind, "decideOffer" | "clearOfferDecision") {
        return offers::decide_offer(app, value, kind == "clearOfferDecision");
    }
    let id = id(value)?.to_string();
    match kind {
        "approveDownload" => {
            if value.get("confirm").and_then(Value::as_str) != Some("DOWNLOAD") {
                return Err("Confirm DOWNLOAD in the trusted manager.".into());
            }
            let a = app.clone();
            on_gui(app, move || {
                HANDLES.with(|handles|{
                let mut handles=handles.borrow_mut();let h=handles.get_mut(&id).ok_or("This download approval expired; choose the file again.")?;
                let b=binding(&a,&h.context)?;
                let path=a.state::<Downloads>().store.lock().map_err(err)?.approve(&id,&b,"DOWNLOAD",now())?;
                let wide=windows_core::HSTRING::from(path.as_os_str());
                unsafe{h.args.SetResultFilePath(&wide).map_err(err)?;h.args.SetCancel(false).map_err(err)?;if let Some(d)=h.deferral.take(){d.Complete().map_err(err)?;}}
                h.active=true;Ok(json!({"message":"Download approved. Installation remains a separate action."}))
            })
            })
        }
        "rejectDownload" | "cancelDownload" => {
            let a = app.clone();
            on_gui(app, move || {
                HANDLES.with(|handles| {
                    if let Some(mut h) = handles.borrow_mut().remove(&id) {
                        cancel_handle(&mut h)
                    }
                });
                a.state::<Downloads>()
                    .store
                    .lock()
                    .map_err(err)?
                    .finish_error(&id, "cancelled", "Cancelled by you.")?;
                Ok(json!({}))
            })
        }
        "removeDownload" => {
            let claim = claim_download(app, &id)?;
            remove_claimed(
                app,
                &claim,
                value.get("confirm").and_then(Value::as_str).unwrap_or(""),
                None,
            )?;
            Ok(json!({}))
        }
        "verifyDownload" | "showDownload" => {
            let _claim = claim_download(app, &id)?;
            let (row, path) = {
                let d = app.state::<Downloads>();
                let s = d.store.lock().map_err(err)?;
                let row = s.row(&id)?;
                let path = s.staging(&id)?.with_file_name(format!(
                    "archive.{}",
                    row.extension.as_deref().ok_or("Archive is not verified.")?
                ));
                (row, path)
            };
            let (bytes, hash, _) = archive_identity(&path, || {
                app.state::<WatchRuntime>().stopping.load(Ordering::Acquire)
            })?;
            if row.state != "ready" || row.bytes != bytes || row.sha256.as_deref() != Some(&hash) {
                return Err("The archive changed; download it again.".into());
            }
            if kind == "showDownload" {
                app.opener().reveal_item_in_dir(&path).map_err(err)?;
            }
            Ok(
                json!({"message":"Archive identity verified. This is not a game compatibility certification."}),
            )
        }
        _ => Err("Unsupported acquisition command.".into()),
    }
}

/// Resolve host-owned bytes without holding the receipt mutex during hashing.
pub(super) fn verified(
    app: &tauri::AppHandle,
    claim: &DownloadClaim,
    cancel: &impl Fn() -> bool,
) -> Result<(modhunter_core::acquisition::Receipt, PathBuf, PathBuf)> {
    let (row, path, root) = {
        let downloads = app.state::<Downloads>();
        verify_claim(&downloads, claim)?;
        let store = downloads.store.lock().map_err(err)?;
        let row = store.row(&claim.id)?;
        let extension = row
            .extension
            .as_deref()
            .filter(|ext| matches!(*ext, "zip" | "7z" | "rar"))
            .ok_or("Download is not a verified archive.")?;
        let path = store
            .staging(&claim.id)?
            .with_file_name(format!("archive.{extension}"));
        (row, path, downloads.root.clone())
    };
    source_current(app, &row.binding)?;
    let (bytes, hash, _) = archive_identity(&path, cancel)?;
    if row.state != "ready" || row.bytes != bytes || row.sha256.as_deref() != Some(&hash) {
        return Err("Archive bytes changed. Download and review the file again.".into());
    }
    let current = app
        .state::<Downloads>()
        .store
        .lock()
        .map_err(err)?
        .row(&claim.id)?;
    if current.state != row.state
        || current.binding != row.binding
        || current.sha256 != row.sha256
        || current.bytes != row.bytes
    {
        return Err("The download changed during verification.".into());
    }
    Ok((row, path, root))
}

pub(super) fn source_current(app: &tauri::AppHandle, saved: &Binding) -> Result<()> {
    let state = app.state::<WatchRuntime>();
    let watch = state
        .store
        .watch(&saved.watch_id)
        .ok_or("Tracked source was removed.")?;
    let context = SiteContext {
        session: saved
            .session_id
            .as_ref()
            .map(|id| state.store.session(id).ok_or("Source account was removed."))
            .transpose()?,
        origin: saved.source_origin.clone(),
        fixture_origin: None,
        lease: None,
        download_watch: Some((watch.id, saved.watch_generation)),
        download_file_key: saved.source_file_key.clone(),
        download_scope: None, // durable review never recreates network authority
        cancellation: None,
    };
    if binding(app, &context)? != *saved {
        return Err(
            "Source, file decision or account changed. Download and review the current file again."
                .into(),
        );
    }
    Ok(())
}
fn cancel_folder_validations() -> usize {
    FOLDER_VALIDATIONS.with(|pending| {
        let mut pending = pending.borrow_mut();
        let count = pending.len();
        for (_, held) in pending.drain() {
            let _ = unsafe { held.args.SetCancel(true) };
            let _ = unsafe { held.deferral.Complete() };
        }
        count
    })
}
fn cancel_transfers(app: &tauri::AppHandle, outcome: &str, message: &str) -> Value {
    let preparation = offers::cancel_preparations(app);
    let deferred = cancel_folder_validations();
    let mut failures = Vec::new();
    let requested = {
        let downloads = app.state::<Downloads>();
        let mut store = downloads.store.lock().unwrap_or_else(|e| e.into_inner());
        let ids = store.snapshot()["jobs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|row| {
                matches!(
                    row["state"].as_str(),
                    Some("pending" | "downloading" | "validating")
                )
            })
            .filter_map(|row| row["id"].as_str().map(str::to_owned))
            .collect::<Vec<_>>();
        for id in &ids {
            if let Err(error) = store.finish_error(id, outcome, message) {
                // A browser file may still be locked, but its durable cancelled
                // state stops validation and the terminal cleanup retries later.
                if store.row(id).map_or(true, |row| row.state != outcome) {
                    failures.push(error);
                }
            }
        }
        ids.len()
    };
    HANDLES.with(|handles| {
        for (_, mut held) in handles.borrow_mut().drain() {
            cancel_handle(&mut held);
        }
    });
    // Preserve the explicit durable cancellation outcome before closing a
    // browser can produce its own interruption callback.
    for label in &preparation.windows {
        if let Some(window) = app.get_webview_window(label)
            && let Err(error) = window.close()
        {
            failures.push(format!(
                "A cancelled transfer window could not close: {error}"
            ));
        }
    }
    notify(app);
    json!({"requested":requested,"deferredOffers":deferred,"preparation":{"operations":preparation.operations,"windows":preparation.windows.len()},"errors":failures,
        "message":"Cancellation requested for active browser transfers and archive validation."})
}
pub(super) fn cancel_preparation(app: &tauri::AppHandle) -> Result<Value> {
    let a = app.clone();
    on_gui(app, move || {
        Ok(cancel_transfers(
            &a,
            "cancelled",
            "Cancelled by the user from the preparation queue.",
        ))
    })
}
pub(super) fn cancel_all(app: &tauri::AppHandle) {
    let a = app.clone();
    let _ = app.run_on_main_thread(move || {
        cancel_transfers(&a, "interrupted", "Application closed.");
    });
}

#[path = "watchtower_mega.rs"]
mod mega;
#[path = "watchtower_offers.rs"]
mod offers;
pub(super) use offers::begin_preparation;
use offers::{open_offer, resolve_files};

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    #[test]
    fn preparation_and_removal_cannot_both_own_the_same_download() {
        let registry = Arc::new(Mutex::new(BTreeSet::new()));
        let start = Arc::new(std::sync::Barrier::new(2));
        let held = Arc::new(std::sync::Barrier::new(2));
        let contenders = (0..2)
            .map(|_| {
                let registry = registry.clone();
                let start = start.clone();
                let held = held.clone();
                std::thread::spawn(move || {
                    start.wait();
                    let claim = claim_ids(&registry, &["download-exact".into()]);
                    held.wait();
                    claim.is_ok()
                })
            })
            .collect::<Vec<_>>();
        assert_eq!(
            contenders
                .into_iter()
                .map(|thread| usize::from(thread.join().unwrap()))
                .sum::<usize>(),
            1
        );
        assert!(claim_ids(&registry, &["download-exact".into()]).is_ok());
    }
    #[test]
    fn contested_cleanup_batch_claims_nothing_and_releases_every_claim_on_drop() {
        let registry = Arc::new(Mutex::new(BTreeSet::new()));
        let review = claim_ids(&registry, &["download-reviewed".into()]).unwrap();
        assert!(
            claim_ids(
                &registry,
                &["download-free".into(), "download-reviewed".into()]
            )
            .is_err()
        );
        assert!(!registry.lock().unwrap().contains("download-free"));
        drop(review);
        let batch = claim_ids(
            &registry,
            &["download-free".into(), "download-reviewed".into()],
        )
        .unwrap();
        assert!(claim_ids(&registry, &["download-free".into()]).is_err());
        drop(batch);
        assert!(registry.lock().unwrap().is_empty());
        assert!(
            claim_ids(
                &registry,
                &["download-duplicate".into(), "download-duplicate".into()]
            )
            .is_err()
        );
    }
}
