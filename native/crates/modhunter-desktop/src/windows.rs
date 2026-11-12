use crate::policy::{self, BAR_HEIGHT, MAX_TABS, Mode};
use modhunter_core::{BridgeClient, ImportRequest, ProfileImporter};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
use tauri::{
    Emitter, Manager, Webview, WebviewUrl,
    webview::{DownloadEvent, NewWindowResponse, PageLoadEvent, WebviewBuilder},
    window::WindowBuilder,
};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use url::Url;

type Result<T> = std::result::Result<T, String>;

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Preferences {
    #[serde(default)]
    profile: Option<ImportRequest>,
    #[serde(default)]
    ini_path: Option<PathBuf>,
    #[serde(default)]
    mods_path: Option<PathBuf>,
    #[serde(default)]
    endpoint: Option<String>,
    #[serde(default)]
    eslifier_data_path: Option<PathBuf>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct Tab {
    id: u64,
    label: String,
    title: String,
    mode: Mode,
    url: String,
    loading: bool,
}

struct Model {
    prefs: Preferences,
    tabs: Vec<Tab>,
    next_id: u64,
    active_id: Option<u64>,
    endpoint: Option<String>,
    connected: bool,
    connection_error: String,
    latency_ms: Option<f64>,
    catalog: Option<Arc<Value>>,
    stats: Option<Value>,
    warnings: Vec<String>,
    shell_ready: bool,
}

struct Runtime {
    model: Mutex<Model>,
    save_guard: Mutex<()>,
    importer: ProfileImporter,
    client: BridgeClient,
    prefs_path: PathBuf,
    epoch: AtomicU64,
    importing: AtomicBool,
    auditing: AtomicBool,
    probing: AtomicBool,
    smoke_output: Option<PathBuf>,
    smoke_sent: AtomicBool,
    smoke_ui: Mutex<Option<Value>>,
    smoke_export: Mutex<Option<PathBuf>>,
}

impl Runtime {
    fn snapshot(&self) -> Value {
        let m = self.model.lock().unwrap_or_else(|e| e.into_inner());
        json!({"tabs": m.tabs, "activeId": m.active_id, "connected": m.connected,
            "epoch": self.epoch.load(Ordering::Acquire),
            "endpoint": m.endpoint, "connectionError": m.connection_error, "latencyMs": m.latency_ms,
            "offlineProfile": m.catalog.as_ref().and_then(|c| c.get("profileName")),
            "offlineModCount": m.catalog.as_ref().and_then(|c| c.get("items")).and_then(Value::as_array).map_or(0, Vec::len),
            "stats": m.stats, "warnings": m.warnings, "importing": self.importing.load(Ordering::Acquire),
            "auditing": self.auditing.load(Ordering::Acquire),
            "configPath": m.prefs.ini_path, "modsPath": m.prefs.mods_path,
            "eslifierDataPath": m.prefs.eslifier_data_path,
            "pluginAnalysis": m.catalog.as_ref().and_then(|c| c.get("pluginAnalysis")).map(|a| json!({
                "depth": a.get("depth"), "checkedAt": a.get("checkedAt"), "stats": a.get("stats")
            })),
            "version": env!("CARGO_PKG_VERSION"), "runtime": "Rust · WebView2"})
    }
    fn save(&self) -> Result<()> {
        let _save = self.save_guard.lock().map_err(err)?;
        let prefs = self.model.lock().map_err(err)?.prefs.clone();
        self.persist_preferences(&prefs)
    }
    fn persist_preferences(&self, prefs: &Preferences) -> Result<()> {
        // Callers serialize disk writes with save_guard. Keep file IO outside
        // the model lock so toolbar snapshots can still be read while saving.
        let parent = self
            .prefs_path
            .parent()
            .ok_or("Invalid preferences directory")?;
        std::fs::create_dir_all(parent).map_err(err)?;
        let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(err)?;
        serde_json::to_writer_pretty(&mut tmp, prefs).map_err(err)?;
        tmp.flush().map_err(err)?;
        tmp.as_file().sync_all().map_err(err)?;
        tmp.persist(&self.prefs_path).map_err(err)?;
        Ok(())
    }
    fn set_eslifier_data_path(&self, path: Option<PathBuf>) -> Result<bool> {
        let _save = self.save_guard.lock().map_err(err)?;
        let mut prospective = self.model.lock().map_err(err)?.prefs.clone();
        prospective.eslifier_data_path = path;
        self.persist_preferences(&prospective).map_err(|e| {
            format!("Could not save the ESLifier setting; the previous setting remains in use: {e}")
        })?;

        let mut m = self.model.lock().map_err(err)?;
        // Another command may update an unrelated preference while disk IO is
        // in progress. Publish only this field; its waiting save() will persist
        // the combined current preferences after this save guard is released.
        m.prefs.eslifier_data_path = prospective.eslifier_data_path;
        self.epoch.fetch_add(1, Ordering::AcqRel);
        invalidate_analysis(&mut m);
        Ok(m.endpoint.is_none() && m.catalog.is_some() && m.prefs.profile.is_some())
    }
}

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn notify(app: &tauri::AppHandle) {
    let state = app.state::<Runtime>().snapshot();
    let _ = app.emit_to("chrome", "modhunter-state", state);
}
fn alert(app: &tauri::AppHandle, message: impl Into<String>) {
    let message = message.into();
    smoke_event(app, "message", json!({"message": message}));
    let _ = app.emit_to("chrome", "modhunter-message", message);
}

fn smoke_event(app: &tauri::AppHandle, stage: &str, details: Value) {
    let state = app.state::<Runtime>();
    let Some(path) = &state.smoke_output else {
        return;
    };
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path.with_extension("events.jsonl"))
    {
        let observed_at_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis())
            .unwrap_or_default();
        let _ = writeln!(
            file,
            "{}",
            json!({"observedAtUnixMs": observed_at_ms, "stage": stage, "details": details})
        );
    }
}

fn smoke_download_details(
    app: &tauri::AppHandle,
    operation: &webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2DownloadOperation,
    stage: &str,
    elapsed_ms: u128,
) {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON, COREWEBVIEW2_DOWNLOAD_STATE,
    };
    use windows_core::PWSTR;

    let mut state = COREWEBVIEW2_DOWNLOAD_STATE::default();
    let state_result = unsafe { operation.State(&mut state) }.map(|()| state.0);
    let mut reason = COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON::default();
    let reason_result = unsafe { operation.InterruptReason(&mut reason) }.map(|()| reason.0);
    let mut received = 0_i64;
    let received_result = unsafe { operation.BytesReceived(&mut received) }.map(|()| received);
    let mut total = 0_i64;
    let total_result = unsafe { operation.TotalBytesToReceive(&mut total) }.map(|()| total);
    let mut path = PWSTR::null();
    let path_read = unsafe { operation.ResultFilePath(&mut path) };
    // Frees the COM-allocated buffer, including when the getter reported an error.
    let path_text = webview2_com::take_pwstr(path);
    let path_result = path_read.map(|()| path_text);
    smoke_event(
        app,
        stage,
        json!({
            "elapsedMs": elapsed_ms,
            "state": state_result.as_ref().ok().copied(),
            "stateError": state_result.err().map(|error| error.to_string()),
            "interruptReason": reason_result.as_ref().ok().copied(),
            "interruptReasonError": reason_result.err().map(|error| error.to_string()),
            "receivedBytes": received_result.as_ref().ok().copied(),
            "receivedBytesError": received_result.err().map(|error| error.to_string()),
            "totalBytes": total_result.as_ref().ok().copied(),
            "totalBytesError": total_result.err().map(|error| error.to_string()),
            "resultFilePath": path_result.as_ref().ok(),
            "resultFilePathError": path_result.as_ref().err().map(|error| error.to_string())
        }),
    );
}

fn install_smoke_download_observer(app: &tauri::AppHandle, view: &Webview) -> Result<()> {
    use webview2_com::{
        DownloadStartingEventHandler, Microsoft::Web::WebView2::Win32::ICoreWebView2_4,
        StateChangedEventHandler,
    };
    use windows_core::Interface;

    if app.state::<Runtime>().smoke_output.is_none() {
        return Ok(());
    }
    let app = app.clone();
    view.with_webview(move |platform| {
        let setup = (|| -> windows_core::Result<()> {
            let core = unsafe { platform.controller().CoreWebView2()? };
            let core4: ICoreWebView2_4 = core.cast()?;
            let start_app = app.clone();
            let starting = DownloadStartingEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else {
                    return Ok(());
                };
                let operation = match unsafe { args.DownloadOperation() } {
                    Ok(operation) => operation,
                    Err(error) => {
                        smoke_event(
                            &start_app,
                            "export-native-observer-error",
                            json!({"operation": "DownloadOperation", "error": error.to_string()}),
                        );
                        return Ok(());
                    }
                };
                let started = std::time::Instant::now();
                smoke_download_details(&start_app, &operation, "export-native-start", 0);
                let state_app = start_app.clone();
                let changed = StateChangedEventHandler::create(Box::new(move |operation, _| {
                    if let Some(operation) = operation {
                        smoke_download_details(
                            &state_app,
                            &operation,
                            "export-native-state",
                            started.elapsed().as_millis(),
                        );
                    }
                    Ok(())
                }));
                let mut state_token = 0_i64;
                if let Err(error) =
                    unsafe { operation.add_StateChanged(&changed, &mut state_token) }
                {
                    smoke_event(
                        &start_app,
                        "export-native-observer-error",
                        json!({"operation": "add_StateChanged", "error": error.to_string()}),
                    );
                }
                // Registration owns the COM callback until the operation ends.
                // Do not cancel, resume, redirect, mark handled or change the path.
                Ok(())
            }));
            let mut start_token = 0_i64;
            unsafe {
                core4.add_DownloadStarting(&starting, &mut start_token)?;
            }
            Ok(())
        })();
        match setup {
            Ok(()) => smoke_event(&app, "export-native-observer-installed", json!({})),
            Err(error) => smoke_event(
                &app,
                "export-native-observer-error",
                json!({"operation": "setup", "error": error.to_string()}),
            ),
        }
    })
    .map_err(err)
}

fn page_url(mode: Mode, endpoint: Option<&str>) -> String {
    match endpoint {
        Some(base) => format!("{base}{}", mode.query()),
        None => format!("http://tauri.localhost/Modhunter.html{}", mode.query()),
    }
}

fn resize(app: &tauri::AppHandle) {
    let Some(window) = app.get_window("main") else {
        return;
    };
    let Ok(scale) = window.scale_factor() else {
        return;
    };
    let Ok(size) = window.inner_size() else {
        return;
    };
    let logical = size.to_logical::<f64>(scale);
    for (label, view) in app.webviews() {
        let (y, height) = if label == "chrome" {
            (0.0, BAR_HEIGHT)
        } else {
            (BAR_HEIGHT, (logical.height - BAR_HEIGHT).max(1.0))
        };
        let _ = view.set_position(tauri::LogicalPosition::new(0.0, y));
        let _ = view.set_size(tauri::LogicalSize::new(logical.width.max(1.0), height));
    }
}

fn activate(app: &tauri::AppHandle, id: u64) -> Result<()> {
    let (active, labels) = {
        let state = app.state::<Runtime>();
        let mut m = state.model.lock().map_err(err)?;
        let active = m
            .tabs
            .iter()
            .find(|t| t.id == id)
            .ok_or("Tab no longer exists")?
            .label
            .clone();
        m.active_id = Some(id);
        (
            active,
            m.tabs.iter().map(|t| t.label.clone()).collect::<Vec<_>>(),
        )
    };
    for label in labels {
        if let Some(view) = app.get_webview(&label) {
            if label == active {
                view.show().map_err(err)?;
                view.set_focus().map_err(err)?;
            } else {
                view.hide().map_err(err)?;
            }
        }
    }
    resize(app);
    notify(app);
    Ok(())
}

fn handle_external(app: &tauri::AppHandle, url: &Url) {
    if policy::external_url(url)
        && let Err(e) = app.opener().open_url(url.as_str(), None::<&str>)
    {
        alert(app, format!("Could not open source: {e}"));
    }
}

fn smoke_result(app: &tauri::AppHandle, url: &Url, view_label: &str) {
    smoke_event(app, "dashboard-proof", json!({"url": url.as_str()}));
    let state = app.state::<Runtime>();
    let Some(path) = &state.smoke_output else {
        return;
    };
    let values: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
    let source_is_local = app
        .get_webview(view_label)
        .and_then(|v| v.url().ok())
        .is_some_and(|u| policy::local_asset(&u, "/Modhunter.html"));
    let snapshot = state.snapshot();
    let pass = source_is_local
        && values.get("ok").is_some_and(|v| v == "1")
        && values.get("denied").is_some_and(|v| v == "1")
        && snapshot["offlineModCount"].as_u64().unwrap_or(0) >= 3
        && state.model.lock().map(|m| m.shell_ready).unwrap_or(false);
    let report = json!({"ok": pass, "nativeWebView2": true, "shellIpc": true,
        "gameViewNativeIpcDenied": values.get("denied"), "proof": values,
        "state": snapshot, "fixtureOnly": true});
    let _ = path;
    *state.smoke_ui.lock().unwrap_or_else(|e| e.into_inner()) = Some(report);
    smoke_event(app, "dashboard-proof-accepted", json!({"passed": pass}));
    finish_smoke(app);
}

fn finish_smoke(app: &tauri::AppHandle) {
    let state = app.state::<Runtime>();
    let Some(path) = &state.smoke_output else {
        return;
    };
    let report = state
        .smoke_ui
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let export = state
        .smoke_export
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let (Some(mut report), Some(export)) = (report, export) else {
        smoke_event(app, "waiting-for-dashboard-or-export", json!({}));
        return;
    };
    let exported = std::fs::read(&export)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok());
    let export_ok = exported.as_ref().is_some_and(|v| {
        v["format"] == "Mod Update Arsenal Backup" && v["items"].as_u64().unwrap_or(0) >= 3
    });
    let pass = report["ok"] == true && export_ok;
    report["ok"] = json!(pass);
    report["exportDownloadVerified"] = json!(export_ok);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let written =
        std::fs::write(path, serde_json::to_vec_pretty(&report).unwrap_or_default()).is_ok();
    smoke_event(
        app,
        "report-written",
        json!({"passed": pass, "written": written}),
    );
    app.exit(if pass && written { 0 } else { 1 });
}

fn inject_catalog(app: &tauri::AppHandle, label: &str) -> Result<()> {
    smoke_event(app, "catalog-injection-requested", json!({"label": label}));
    let state = app.state::<Runtime>();
    let catalog = {
        let m = state.model.lock().map_err(err)?;
        if m.endpoint.is_some() {
            return Ok(());
        }
        if !m
            .tabs
            .iter()
            .any(|t| t.label == label && t.mode == Mode::Dashboard && !t.loading)
        {
            return Ok(());
        }
        m.catalog.clone()
    };
    let Some(catalog) = catalog else {
        return Ok(());
    };
    let Some(view) = app.get_webview(label) else {
        return Ok(());
    };
    if !view
        .url()
        .map_err(err)
        .is_ok_and(|u| policy::local_asset(&u, "/Modhunter.html"))
    {
        return Ok(());
    }
    let data = serde_json::to_string(catalog.as_ref())
        .map_err(err)?
        .replace('<', "\\u003c")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029");
    view.eval(format!("window.modhunterImportCatalog?.({data});"))
        .map_err(err)?;
    smoke_event(app, "catalog-injection-scheduled", json!({"label": label}));
    if state.smoke_output.is_some() && !state.smoke_sent.swap(true, Ordering::AcqRel) {
        view.eval(r#"(async()=>{
            let denied=false;
            try { if (!window.__TAURI__?.core) denied=true;
                  else await window.__TAURI__.core.invoke('shell_command',{kind:'state'}); }
            catch { denied=true; }
            const qa=window.__ARSENAL_QA__;
            const c=qa?.getCatalog();
            const ok=!!(c?.offline && c.items?.length>=3 && document.getElementById('unifiedCommand')
                && typeof window.modhunterImportCatalog==='function');
            window.__modhunterNativeSmokeProof=()=>{
                delete window.__modhunterNativeSmokeProof;
                window.location.href='modhunter-proof://result?ok='+(ok?'1':'0')+'&denied='+(denied?'1':'0')
                    +'&items='+(c?.items?.length||0)+'&profile='+encodeURIComponent(c?.profileName||'');
            };
            window.__modhunterNativeSmokeDiagnostics={
                userActivationActive:!!navigator.userActivation?.isActive,
                userActivationEver:!!navigator.userActivation?.hasBeenActive,
                documentReadyState:document.readyState,
                requestedAtPerformanceMs:performance.now()
            };
            document.getElementById('backupBtn').click();
        })();"#).map_err(err)?;
    }
    Ok(())
}

fn invalidate_analysis(model: &mut Model) {
    if let Some(catalog) = model.catalog.as_mut() {
        let catalog = Arc::make_mut(catalog);
        if let Some(object) = catalog.as_object_mut() {
            object.remove("pluginAnalysis");
        }
        if let Some(items) = catalog.get_mut("items").and_then(Value::as_array_mut) {
            for item in items {
                if let Some(object) = item.as_object_mut() {
                    object.remove("localAnalysis");
                }
            }
        }
    }
}

fn refresh_catalog_views(app: &tauri::AppHandle) -> Result<()> {
    let labels = app
        .state::<Runtime>()
        .model
        .lock()
        .map_err(err)?
        .tabs
        .iter()
        .filter(|t| t.mode == Mode::Dashboard)
        .map(|t| t.label.clone())
        .collect::<Vec<_>>();
    for label in labels {
        inject_catalog(app, &label)?;
    }
    Ok(())
}

fn open_tab(app: &tauri::AppHandle, mode: Mode) -> Result<u64> {
    let (id, label, url) = {
        let state = app.state::<Runtime>();
        let mut m = state.model.lock().map_err(err)?;
        if m.tabs.len() >= MAX_TABS {
            return Err(format!(
                "Close a tab before opening another (maximum {MAX_TABS})."
            ));
        }
        let id = m.next_id;
        m.next_id += 1;
        let label = format!("tab-{id}");
        let url = page_url(mode, m.endpoint.as_deref());
        m.tabs.push(Tab {
            id,
            label: label.clone(),
            title: mode.title().into(),
            mode,
            url: url.clone(),
            loading: true,
        });
        (id, label, url)
    };
    let window = app.get_window("main").ok_or("Main window is unavailable")?;
    let nav_app = app.clone();
    let nav_label = label.clone();
    let new_app = app.clone();
    let load_app = app.clone();
    let download_app = app.clone();
    let builder = WebviewBuilder::new(&label, WebviewUrl::External(Url::parse(&url).map_err(err)?))
        .on_navigation(move |url| {
            smoke_event(
                &nav_app,
                "dashboard-navigation",
                json!({"url": url.as_str()}),
            );
            if url.scheme() == "modhunter-proof" {
                smoke_result(&nav_app, url, &nav_label);
                return false;
            }
            let allowed = {
                let state = nav_app.state::<Runtime>();
                let Ok(m) = state.model.lock() else {
                    return false;
                };
                policy::owned_page(url, m.endpoint.as_deref())
                    || policy::export_blob(url, m.endpoint.as_deref())
            };
            if !allowed {
                handle_external(&nav_app, url);
            }
            allowed
        })
        .on_new_window(move |url, _| {
            let owned = {
                let state = new_app.state::<Runtime>();
                let Ok(m) = state.model.lock() else {
                    return NewWindowResponse::Deny;
                };
                policy::owned_page(&url, m.endpoint.as_deref())
            };
            if owned {
                let app = new_app.clone();
                let mode = Mode::from_url(&url);
                tauri::async_runtime::spawn_blocking(move || {
                    if let Err(e) = open_tab(&app, mode) {
                        alert(&app, e);
                    }
                });
            } else {
                handle_external(&new_app, &url);
            }
            NewWindowResponse::Deny
        })
        .on_download(move |view, event| match event {
            DownloadEvent::Requested { url, destination } => {
                smoke_event(
                    &download_app,
                    "export-requested",
                    json!({"url": url.as_str(), "destination": destination}),
                );
                let state = download_app.state::<Runtime>();
                if state.smoke_output.is_some() {
                    let diagnostic_app = download_app.clone();
                    if let Err(error) = view.eval_with_callback(
                        "window.__modhunterNativeSmokeDiagnostics ?? null",
                        move |value| {
                            let details = serde_json::from_str(&value)
                                .unwrap_or_else(|_| json!({"raw": value}));
                            smoke_event(&diagnostic_app, "export-browser-state", details);
                        },
                    ) {
                        smoke_event(
                            &download_app,
                            "export-browser-state-error",
                            json!({"error": error.to_string()}),
                        );
                    }
                }
                let endpoint = state
                    .model
                    .lock()
                    .map(|m| m.endpoint.clone())
                    .unwrap_or(None);
                if !policy::export_blob(&url, endpoint.as_deref()) {
                    smoke_event(&download_app, "export-origin-rejected", json!({}));
                    return false;
                }
                let Some(name) = destination
                    .file_name()
                    .and_then(|s| s.to_str())
                    .and_then(policy::export_name)
                else {
                    smoke_event(&download_app, "export-filename-rejected", json!({}));
                    return false;
                };
                let directory = if let Some(report) = &state.smoke_output {
                    report
                        .parent()
                        .unwrap_or_else(|| std::path::Path::new("."))
                        .to_path_buf()
                } else {
                    match download_app.path().download_dir() {
                        Ok(p) => p,
                        Err(e) => {
                            alert(&download_app, e.to_string());
                            return false;
                        }
                    }
                };
                if let Err(e) = std::fs::create_dir_all(&directory) {
                    alert(&download_app, e.to_string());
                    return false;
                }
                let stamp = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_nanos())
                    .unwrap_or_default();
                *destination = directory.join(format!("{stamp}-{name}"));
                smoke_event(
                    &download_app,
                    "export-destination-selected",
                    json!({"destination": destination,
                    "absolute": destination.is_absolute(), "directoryExists": directory.is_dir()}),
                );
                true
            }
            DownloadEvent::Finished { path, success, .. } => {
                smoke_event(
                    &download_app,
                    "export-finished",
                    json!({"success": success, "path": path}),
                );
                if success {
                    if let Some(path) = path {
                        if download_app.state::<Runtime>().smoke_output.is_some() {
                            *download_app
                                .state::<Runtime>()
                                .smoke_export
                                .lock()
                                .unwrap_or_else(|e| e.into_inner()) = Some(path.clone());
                            // Even a cancelled navigation can interrupt an
                            // in-flight WebView2 blob transfer. Submit the
                            // fixture's UI proof only after the real download
                            // completes; finish_smoke still reads its bytes.
                            if let Err(error) = view.eval("window.__modhunterNativeSmokeProof?.();")
                            {
                                smoke_event(
                                    &download_app,
                                    "dashboard-proof-dispatch-error",
                                    json!({"error": error.to_string()}),
                                );
                            }
                            finish_smoke(&download_app);
                        }
                        alert(&download_app, format!("Export saved to {}", path.display()));
                    }
                } else {
                    alert(
                        &download_app,
                        "Export could not be saved. Try again from the dashboard.",
                    );
                }
                true
            }
            _ => false,
        })
        .on_page_load(move |view, payload| {
            let done = payload.event() == PageLoadEvent::Finished;
            smoke_event(
                &load_app,
                "dashboard-load",
                json!({"finished": done, "url": payload.url().as_str()}),
            );
            {
                let state = load_app.state::<Runtime>();
                if let Ok(mut m) = state.model.lock()
                    && let Some(tab) = m.tabs.iter_mut().find(|t| t.label == view.label())
                {
                    tab.loading = !done;
                }
            }
            if done {
                if let Err(error) = inject_catalog(&load_app, view.label()) {
                    smoke_event(
                        &load_app,
                        "catalog-injection-error",
                        json!({"error": error}),
                    );
                }
                let active = load_app
                    .state::<Runtime>()
                    .model
                    .lock()
                    .map(|m| m.active_id == Some(id))
                    .unwrap_or(false);
                if !active {
                    let _ = view.hide();
                }
            }
            notify(&load_app);
        });
    if let Err(e) = window.add_child(
        builder,
        tauri::LogicalPosition::new(0.0, BAR_HEIGHT),
        tauri::LogicalSize::new(1480.0, 800.0),
    ) {
        app.state::<Runtime>()
            .model
            .lock()
            .map_err(err)?
            .tabs
            .retain(|t| t.id != id);
        return Err(format!("Could not create a WebView2 tab: {e}"));
    }
    if let Some(view) = app.get_webview(&label) {
        install_smoke_download_observer(app, &view)?;
    }
    activate(app, id)?;
    Ok(id)
}

fn navigate_all(app: &tauri::AppHandle) -> Result<()> {
    let tabs = {
        let state = app.state::<Runtime>();
        let mut m = state.model.lock().map_err(err)?;
        let endpoint = m.endpoint.clone();
        for tab in &mut m.tabs {
            tab.url = page_url(tab.mode, endpoint.as_deref());
            tab.loading = true;
        }
        m.tabs.clone()
    };
    for tab in tabs {
        if let Some(view) = app.get_webview(&tab.label) {
            view.navigate(Url::parse(&tab.url).map_err(err)?)
                .map_err(err)?;
        }
    }
    notify(app);
    Ok(())
}

struct FlagGuard<'a>(&'a AtomicBool);
impl Drop for FlagGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

struct ImportGuard<'a> {
    state: &'a Runtime,
    app: &'a tauri::AppHandle,
}

impl Drop for ImportGuard<'_> {
    fn drop(&mut self) {
        self.state.auditing.store(false, Ordering::Release);
        self.state.importing.store(false, Ordering::Release);
        notify(self.app);
    }
}

fn import_profile(app: &tauri::AppHandle, refresh: bool, deep: bool) -> Result<Value> {
    let state = app.state::<Runtime>();
    if state.importing.swap(true, Ordering::AcqRel) {
        return Err("A profile import or plugin audit is already running.".into());
    }
    state.auditing.store(deep, Ordering::Release);
    let guard = ImportGuard { state: &state, app };
    let (old, ini_path, mods_path, eslifier_data_paths, epoch) = {
        let m = state.model.lock().map_err(err)?;
        if m.endpoint.is_some() {
            return Err(if deep {
                "For live MO2, use Plugin audit in the dashboard to inspect the current profile."
                    .into()
            } else {
                "Unlink live MO2 before importing a local profile.".into()
            });
        }
        (
            m.prefs.profile.clone(),
            m.prefs.ini_path.clone(),
            m.prefs.mods_path.clone(),
            m.prefs
                .eslifier_data_path
                .iter()
                .cloned()
                .collect::<Vec<_>>(),
            state.epoch.fetch_add(1, Ordering::AcqRel) + 1,
        )
    };
    notify(app);
    let request = if refresh {
        let mut request = old.ok_or("Import a profile first.")?;
        request.ini_path = ini_path;
        request.mods_path = mods_path;
        request
    } else {
        let file = app
            .dialog()
            .file()
            .set_title("Choose an MO2 profile's modlist.txt")
            .add_filter("MO2 modlist", &["txt"])
            .blocking_pick_file();
        let Some(file) = file else {
            return Ok(json!({"ok": true, "canceled": true}));
        };
        ImportRequest {
            modlist_path: file.into_path().map_err(err)?,
            ini_path,
            mods_path,
            workers: None,
        }
    };
    if state.epoch.load(Ordering::Acquire) != epoch {
        return Err(
            "Profile settings or connection changed; start the import or audit again.".into(),
        );
    }
    let result = state
        .importer
        .import_with_analysis(&request, deep, &eslifier_data_paths)
        .map_err(err)?;
    let count = result.catalog["items"].as_array().map_or(0, Vec::len);
    let profile = result.catalog["profileName"]
        .as_str()
        .unwrap_or("Profile")
        .to_owned();
    let dashboard = {
        let mut m = state.model.lock().map_err(err)?;
        if state.epoch.load(Ordering::Acquire) != epoch || m.endpoint.is_some() {
            return Err(
                "Profile settings or connection changed; the old result was discarded.".into(),
            );
        }
        m.catalog = Some(Arc::new(result.catalog));
        m.stats = Some(serde_json::to_value(result.stats).map_err(err)?);
        m.warnings = result.warnings;
        m.prefs.profile = Some(request);
        m.tabs
            .iter()
            .find(|t| t.mode == Mode::Dashboard)
            .map(|t| t.id)
    };
    if let Err(e) = state.save() {
        alert(
            app,
            format!("Profile loaded. Could not remember its path: {e}"),
        );
    }
    let id = match dashboard {
        Some(id) => id,
        None => open_tab(app, Mode::Dashboard)?,
    };
    activate(app, id)?;
    refresh_catalog_views(app)?;
    // Return the settled state to the caller as well as notifying the toolbar.
    drop(guard);
    let message = if deep {
        format!(
            "Plugin audit finished for {profile}. Review each mod's plugin evidence in the dashboard."
        )
    } else {
        format!("Loaded {count} mods from {profile}.")
    };
    Ok(json!({"ok": true, "count": count, "message": message, "state": state.snapshot()}))
}

fn connect(app: &tauri::AppHandle, endpoint: &str) -> Result<Value> {
    let state = app.state::<Runtime>();
    let epoch = state.epoch.fetch_add(1, Ordering::AcqRel) + 1;
    connect_at_epoch(app, endpoint, epoch)
}

fn connect_at_epoch(app: &tauri::AppHandle, endpoint: &str, epoch: u64) -> Result<Value> {
    let state = app.state::<Runtime>();
    if state.epoch.load(Ordering::Acquire) != epoch {
        return Err("A newer connection request replaced this one.".into());
    }
    let health = state.client.probe(endpoint).map_err(err)?;
    {
        let mut m = state.model.lock().map_err(err)?;
        if state.epoch.load(Ordering::Acquire) != epoch {
            return Err("A newer connection request replaced this one.".into());
        }
        m.endpoint = Some(health.endpoint.clone());
        m.prefs.endpoint = Some(health.endpoint.clone());
        m.connected = true;
        m.connection_error.clear();
        m.latency_ms = Some(health.latency_ms);
    }
    if let Err(e) = state.save() {
        alert(app, format!("Connected. Could not remember address: {e}"));
    }
    navigate_all(app)?;
    Ok(json!({"ok": true, "health": health, "state": state.snapshot()}))
}

fn probe(app: &tauri::AppHandle) -> Result<Value> {
    let state = app.state::<Runtime>();
    if state.probing.swap(true, Ordering::AcqRel) {
        return Ok(json!({"ok": true, "pending": true}));
    }
    let _guard = FlagGuard(&state.probing);
    let epoch = state.epoch.load(Ordering::Acquire);
    let endpoint = state.model.lock().map_err(err)?.endpoint.clone();
    let Some(endpoint) = endpoint else {
        return Ok(json!({"ok": true}));
    };
    let response = state.client.probe(&endpoint);
    let mut m = state.model.lock().map_err(err)?;
    if state.epoch.load(Ordering::Acquire) != epoch
        || m.endpoint.as_deref() != Some(endpoint.as_str())
    {
        return Ok(json!({"ok": true, "stale": true}));
    }
    let was_connected = m.connected;
    match response {
        Ok(h) => {
            m.connected = true;
            m.connection_error.clear();
            m.latency_ms = Some(h.latency_ms);
        }
        Err(e) => {
            m.connected = false;
            m.connection_error = e.to_string();
            m.latency_ms = None;
        }
    }
    let recovered = !was_connected && m.connected;
    drop(m);
    if recovered {
        navigate_all(app)?;
    }
    notify(app);
    Ok(json!({"ok": true, "state": state.snapshot()}))
}

fn execute(app: &tauri::AppHandle, kind: &str, value: Value) -> Result<Value> {
    let state = app.state::<Runtime>();
    match kind {
        "state" => return Ok(json!({"ok": true, "state": state.snapshot()})),
        "ready" => {
            smoke_event(app, "shell-ready-command", json!({}));
            let restore = {
                let mut m = state.model.lock().map_err(err)?;
                if m.shell_ready {
                    return Ok(json!({"ok": true}));
                }
                m.shell_ready = true;
                (m.prefs.endpoint.clone(), m.prefs.profile.is_some())
            };
            if restore.1
                && let Err(e) = import_profile(app, true, false)
            {
                alert(app, e);
            }
            if let Some(endpoint) = restore.0
                && let Err(e) = connect(app, &endpoint)
            {
                alert(app, format!("Saved MO2 session is unavailable: {e}"));
            }
        }
        "new" => {
            let mode = serde_json::from_value(value).unwrap_or_default();
            open_tab(app, mode)?;
        }
        "activate" => activate(app, value.as_u64().ok_or("Invalid tab")?)?,
        "close" => {
            let id = value.as_u64().ok_or("Invalid tab")?;
            let (tab, next) = {
                let mut m = state.model.lock().map_err(err)?;
                let at = m
                    .tabs
                    .iter()
                    .position(|t| t.id == id)
                    .ok_or("Tab already closed")?;
                let tab = m.tabs.remove(at);
                let next = if m.active_id == Some(id) {
                    m.tabs
                        .get(at.min(m.tabs.len().saturating_sub(1)))
                        .map(|t| t.id)
                } else {
                    m.active_id
                };
                m.active_id = next;
                (tab, next)
            };
            if let Some(view) = app.get_webview(&tab.label) {
                view.close().map_err(err)?;
            }
            match next {
                Some(id) => activate(app, id)?,
                None => {
                    open_tab(app, Mode::Dashboard)?;
                }
            }
        }
        "importMo2" => return import_profile(app, false, false),
        "refreshProfile" => return import_profile(app, true, false),
        "pluginAudit" => return import_profile(app, true, true),
        "configureIni" => {
            let picked = app
                .dialog()
                .file()
                .set_title("Choose this instance's ModOrganizer.ini (Cancel keeps current setting)")
                .add_filter("MO2 configuration", &["ini"])
                .blocking_pick_file();
            if let Some(file) = picked {
                let path = file.into_path().map_err(err)?;
                {
                    let mut m = state.model.lock().map_err(err)?;
                    state.epoch.fetch_add(1, Ordering::AcqRel);
                    m.prefs.ini_path = Some(path);
                }
                state.save()?;
                alert(
                    app,
                    "Instance configuration saved. Import or refresh your profile to apply it.",
                );
            }
        }
        "configureMods" => {
            if let Some(file) = app
                .dialog()
                .file()
                .set_title("Choose the configured MO2 mods directory")
                .blocking_pick_folder()
            {
                let path = file.into_path().map_err(err)?;
                {
                    let mut m = state.model.lock().map_err(err)?;
                    state.epoch.fetch_add(1, Ordering::AcqRel);
                    m.prefs.mods_path = Some(path);
                }
                state.save()?;
                alert(
                    app,
                    "Mods directory saved. Import or refresh your profile to apply it.",
                );
            }
        }
        "clearPaths" => {
            let mut m = state.model.lock().map_err(err)?;
            state.epoch.fetch_add(1, Ordering::AcqRel);
            m.prefs.ini_path = None;
            m.prefs.mods_path = None;
            drop(m);
            state.save()?;
            alert(
                app,
                "Using automatically detected instance paths on the next import.",
            );
        }
        "configureEslifierData" => {
            let picked = app
                .dialog()
                .file()
                .set_title("Choose ESLifier_Data or ESLifier_FO4_Data beside ESLifier's executable")
                .blocking_pick_folder();
            if let Some(file) = picked {
                let path = policy::eslifier_data_directory(&file.into_path().map_err(err)?)?;
                let audit_now = state.set_eslifier_data_path(Some(path))?;
                refresh_catalog_views(app)?;
                notify(app);
                if audit_now && !state.importing.load(Ordering::Acquire) {
                    return import_profile(app, true, true);
                }
                alert(
                    app,
                    "ESLifier data folder saved for offline plugin audits. Live MO2 uses its own ESLifier configuration.",
                );
            }
        }
        "clearEslifierData" => {
            let audit_now = state.set_eslifier_data_path(None)?;
            refresh_catalog_views(app)?;
            notify(app);
            if audit_now && !state.importing.load(Ordering::Acquire) {
                return import_profile(app, true, true);
            }
            alert(
                app,
                "ESLifier data folder cleared. Offline conversion history will be unknown until a data folder is selected.",
            );
        }
        "connect" => return connect(app, value.as_str().ok_or("Enter the MO2 loopback address")?),
        "probe" => return probe(app),
        "discover" => {
            let epoch = state.epoch.fetch_add(1, Ordering::AcqRel) + 1;
            let directory = std::env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .ok_or("Windows LOCALAPPDATA directory is unavailable")?
                .join("Modhunter")
                .join("mo2-sessions");
            let client =
                BridgeClient::with_timeout(std::time::Duration::from_millis(800)).map_err(err)?;
            let sessions = crate::discovery::discover(&directory, &client).map_err(err)?;
            if state.epoch.load(Ordering::Acquire) != epoch {
                return Err("A newer connection request replaced this discovery.".into());
            }
            match sessions.len() {
                0 => return Err("No live Modhunter session found. Start MO2 and open Tools → Update Arsenal, then use Find MO2 again.".into()),
                1 => return connect_at_epoch(app, &sessions[0].endpoint, epoch),
                _ => return Ok(json!({"ok":true,"sessions":sessions,"message":"Multiple MO2 sessions found. Choose a profile address from the address field, then Connect."})),
            }
        }
        "disconnect" => {
            state.epoch.fetch_add(1, Ordering::AcqRel);
            let mut m = state.model.lock().map_err(err)?;
            m.endpoint = None;
            m.prefs.endpoint = None;
            m.connected = false;
            m.connection_error.clear();
            m.latency_ms = None;
            drop(m);
            state.save()?;
            navigate_all(app)?;
        }
        "reload" | "back" | "forward" | "copy" | "external" => {
            let tab = {
                let m = state.model.lock().map_err(err)?;
                m.tabs
                    .iter()
                    .find(|t| Some(t.id) == m.active_id)
                    .cloned()
                    .ok_or("No active tab")?
            };
            let view = app.get_webview(&tab.label).ok_or("Tab unavailable")?;
            match kind {
                "reload" => view.reload().map_err(err)?,
                "back" => view.eval("history.back()").map_err(err)?,
                "forward" => view.eval("history.forward()").map_err(err)?,
                "copy" => app.clipboard().write_text(&tab.url).map_err(err)?,
                "external" => {
                    let url = Url::parse(&tab.url).map_err(err)?;
                    if policy::local_asset(&url, "/Modhunter.html") {
                        let folder = app.path().app_cache_dir().map_err(err)?;
                        std::fs::create_dir_all(&folder).map_err(err)?;
                        let path = folder.join("Modhunter.html");
                        std::fs::write(&path, include_bytes!("../../../ui/Modhunter.html"))
                            .map_err(err)?;
                        app.opener()
                            .open_path(path.to_string_lossy().as_ref(), None::<&str>)
                            .map_err(err)?;
                    } else {
                        handle_external(app, &url);
                    }
                }
                _ => unreachable!(),
            }
        }
        _ => return Err("Unknown desktop command".into()),
    }
    notify(app);
    Ok(json!({"ok": true, "state": state.snapshot()}))
}

#[tauri::command]
async fn shell_command(
    app: tauri::AppHandle,
    webview: Webview,
    kind: String,
    value: Option<Value>,
) -> Result<Value> {
    // Also enforce origin in Rust: native authority never belongs to a live MO2 page.
    if webview.label() != "chrome"
        || !webview
            .url()
            .map_err(err)
            .is_ok_and(|u| policy::local_asset(&u, "/shell.html"))
    {
        return Err("Native commands are limited to the bundled Modhunter toolbar.".into());
    }
    tauri::async_runtime::spawn_blocking(move || execute(&app, &kind, value.unwrap_or(Value::Null)))
        .await
        .map_err(err)?
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![shell_command])
        .setup(|app| {
            let prefs_path = app.path().app_config_dir()?.join("native-preferences.json");
            let mut prefs: Preferences = std::fs::File::open(&prefs_path)
                .ok()
                .and_then(|f| {
                    let mut bytes = Vec::new();
                    f.take(64_001).read_to_end(&mut bytes).ok().map(|_| bytes)
                })
                .filter(|b| b.len() <= 64_000)
                .and_then(|b| serde_json::from_slice(&b).ok())
                .unwrap_or_default();
            let mut smoke_output = None;
            for arg in std::env::args().skip(1) {
                if let Some(path) = arg.strip_prefix("--smoke-profile=") {
                    prefs.profile = Some(ImportRequest {
                        modlist_path: PathBuf::from(path),
                        ini_path: None,
                        mods_path: None,
                        workers: Some(4),
                    });
                    prefs.ini_path = None;
                    prefs.mods_path = None;
                    prefs.endpoint = None;
                    prefs.eslifier_data_path = None;
                }
                if let Some(path) = arg.strip_prefix("--smoke-output=") {
                    smoke_output = Some(PathBuf::from(path));
                }
            }
            app.manage(Runtime {
                save_guard: Mutex::new(()),
                model: Mutex::new(Model {
                    prefs,
                    tabs: Vec::new(),
                    next_id: 1,
                    active_id: None,
                    endpoint: None,
                    connected: false,
                    connection_error: String::new(),
                    latency_ms: None,
                    catalog: None,
                    stats: None,
                    warnings: Vec::new(),
                    shell_ready: false,
                }),
                importer: ProfileImporter::new(),
                client: BridgeClient::new()?,
                prefs_path,
                epoch: AtomicU64::new(0),
                importing: AtomicBool::new(false),
                auditing: AtomicBool::new(false),
                probing: AtomicBool::new(false),
                smoke_output,
                smoke_sent: AtomicBool::new(false),
                smoke_ui: Mutex::new(None),
                smoke_export: Mutex::new(None),
            });
            smoke_event(app.handle(), "runtime-created", json!({}));
            let window = WindowBuilder::new(app, "main")
                .title("Modhunter · Rust")
                .inner_size(1480.0, 960.0)
                .min_inner_size(760.0, 550.0)
                .build()?;
            let handle = app.handle().clone();
            window.on_window_event(move |event| {
                if matches!(
                    event,
                    tauri::WindowEvent::Resized(_) | tauri::WindowEvent::ScaleFactorChanged { .. }
                ) {
                    resize(&handle);
                }
            });
            let chrome = WebviewBuilder::new("chrome", WebviewUrl::App("shell.html".into()))
                .on_navigation(|u| policy::local_asset(u, "/shell.html"))
                .on_new_window(|_, _| NewWindowResponse::Deny);
            window.add_child(
                chrome,
                tauri::LogicalPosition::new(0.0, 0.0),
                tauri::LogicalSize::new(1480.0, BAR_HEIGHT),
            )?;
            smoke_event(app.handle(), "chrome-created", json!({}));
            open_tab(app.handle(), Mode::Dashboard).map_err(std::io::Error::other)?;
            smoke_event(app.handle(), "dashboard-created", json!({}));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect(
            "Could not start Modhunter. Install Microsoft Edge WebView2 Runtime and try again.",
        );
}
