//! WebView2 owns credentials and transfer streams. Only the bundled manager can
//! approve a deferred download. Remote HTML never gains native IPC authority.
use super::*;
use modhunter_core::acquisition::{Binding, MAX_BYTES, Store, archive_identity};
use std::{cell::RefCell, collections::HashMap};
use webview2_com::Microsoft::Web::WebView2::Win32::{
    COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED, COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS, ICoreWebView2,
    ICoreWebView2_4, ICoreWebView2Deferral, ICoreWebView2DownloadOperation,
    ICoreWebView2DownloadStartingEventArgs,
};

struct Downloads {
    store: Mutex<Store>,
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

pub(super) fn setup(app: &tauri::AppHandle, path: PathBuf) {
    app.manage(Downloads {
        store: Mutex::new(Store::open(path)),
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
    Ok(Binding {
        watch_id: id.clone(),
        watch_generation: *generation,
        session_id: context.session.as_ref().map(|s| s.id.clone()),
        session_generation: context.session.as_ref().map_or(0, |s| s.storage_generation),
        source_origin: context.origin.clone(),
        association: watch.association,
    })
}
fn allowed_download(raw: &str, source: &str) -> bool {
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
    let callback = webview2_com::DownloadStartingEventHandler::create(Box::new(move |_, args| {
        let Some(args) = args else { return Ok(()) };
        unsafe {
            args.SetCancel(true)?;
            args.SetHandled(true)?;
        }
        if !interactive {
            return Ok(());
        }
        let requested = (|| -> Result<()> {
            let b = binding(&app, &context)?;
            let operation = unsafe { args.DownloadOperation() }.map_err(err)?;
            let mut uri = windows_core::PWSTR::null();
            unsafe { operation.Uri(&mut uri) }.map_err(err)?;
            let uri = webview2_com::take_pwstr(uri);
            if !allowed_download(&uri, &b.source_origin) {
                return Err("This download left the tracked site for an unsupported host.".into());
            }
            let mut name = windows_core::PWSTR::null();
            unsafe { args.ResultFilePath(&mut name) }.map_err(err)?;
            let name = webview2_com::take_pwstr(name);
            let name = Path::new(&name)
                .file_name()
                .and_then(|n| n.to_str())
                .ok_or("Missing archive filename.")?;
            let mut total = 0;
            unsafe { operation.TotalBytesToReceive(&mut total) }.map_err(err)?;
            let total = if total > 0 { Some(total as u64) } else { None };
            let deferral = unsafe { args.GetDeferral() }.map_err(err)?;
            let result =
                app.state::<Downloads>()
                    .store
                    .lock()
                    .map_err(err)?
                    .request(name, b, total, now());
            let row = match result {
                Ok(r) => r,
                Err(e) => {
                    let _ = unsafe { deferral.Complete() };
                    return Err(e);
                }
            };
            HANDLES.with(|h| {
                h.borrow_mut().insert(
                    row.id,
                    Held {
                        operation,
                        args: args.clone(),
                        deferral: Some(deferral),
                        context: context.clone(),
                        label: label.clone(),
                        created: Instant::now(),
                        active: false,
                    },
                )
            });
            notify(&app);
            if let Some(manager) = app.get_webview_window("watchtower") {
                let _ = manager.show();
                let _ = manager.set_focus();
            }
            Ok(())
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
                if store.begin_validation(id, &b).is_ok() {
                    if let Ok(path) = store.staging(id) {
                        validations.push((id.clone(), b, h.context.clone(), path));
                    }
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
            let result = archive_identity(&path, || !context.current(&a.state::<WatchRuntime>()));
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
    if kind == "openOffer" {
        return open_offer(app, value);
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
            app.state::<Downloads>().store.lock().map_err(err)?.remove(
                &id,
                value.get("confirm").and_then(Value::as_str).unwrap_or(""),
            )?;
            Ok(json!({}))
        }
        "verifyDownload" | "showDownload" => {
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
pub(super) fn cancel_all(app: &tauri::AppHandle) {
    let a = app.clone();
    let _ = app.run_on_main_thread(move || {
        HANDLES.with(|handles| {
            for (id, mut h) in handles.borrow_mut().drain() {
                cancel_handle(&mut h);
                let _ = a
                    .state::<Downloads>()
                    .store
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .finish_error(&id, "interrupted", "Application closed.");
            }
        })
    });
}

#[path = "watchtower_offers.rs"]
mod offers;
use offers::{open_offer, resolve_files};
