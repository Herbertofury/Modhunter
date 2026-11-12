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
    webview::{NewWindowResponse, PageLoadEvent, WebviewBuilder},
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
    probing: AtomicBool,
    smoke_output: Option<PathBuf>,
    smoke_sent: AtomicBool,
}

impl Runtime {
    fn snapshot(&self) -> Value {
        let m = self.model.lock().unwrap_or_else(|e| e.into_inner());
        json!({"tabs": m.tabs, "activeId": m.active_id, "connected": m.connected,
            "endpoint": m.endpoint, "connectionError": m.connection_error, "latencyMs": m.latency_ms,
            "offlineProfile": m.catalog.as_ref().and_then(|c| c.get("profileName")),
            "offlineModCount": m.catalog.as_ref().and_then(|c| c.get("items")).and_then(Value::as_array).map_or(0, Vec::len),
            "stats": m.stats, "warnings": m.warnings, "importing": self.importing.load(Ordering::Acquire),
            "configPath": m.prefs.ini_path, "modsPath": m.prefs.mods_path,
            "version": env!("CARGO_PKG_VERSION"), "runtime": "Rust · WebView2"})
    }
    fn save(&self) -> Result<()> {
        let _save = self.save_guard.lock().map_err(err)?;
        let prefs = self.model.lock().map_err(err)?.prefs.clone();
        let parent = self
            .prefs_path
            .parent()
            .ok_or("Invalid preferences directory")?;
        std::fs::create_dir_all(parent).map_err(err)?;
        let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(err)?;
        serde_json::to_writer_pretty(&mut tmp, &prefs).map_err(err)?;
        tmp.flush().map_err(err)?;
        tmp.as_file().sync_all().map_err(err)?;
        tmp.persist(&self.prefs_path).map_err(err)?;
        Ok(())
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
    let _ = app.emit_to("chrome", "modhunter-message", message.into());
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
    if policy::external_url(url) {
        if let Err(e) = app.opener().open_url(url.as_str(), None::<&str>) {
            alert(app, format!("Could not open source: {e}"));
        }
    }
}

fn smoke_result(app: &tauri::AppHandle, url: &Url, view_label: &str) {
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
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let written =
        std::fs::write(path, serde_json::to_vec_pretty(&report).unwrap_or_default()).is_ok();
    app.exit(if pass && written { 0 } else { 1 });
}

fn inject_catalog(app: &tauri::AppHandle, label: &str) -> Result<()> {
    let state = app.state::<Runtime>();
    let catalog = {
        let m = state.model.lock().map_err(err)?;
        if m.endpoint.is_some() {
            return Ok(());
        }
        if !m
            .tabs
            .iter()
            .any(|t| t.label == label && t.mode == Mode::Dashboard)
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
            window.location.href='modhunter-proof://result?ok='+(ok?'1':'0')+'&denied='+(denied?'1':'0')
                +'&items='+(c?.items?.length||0)+'&profile='+encodeURIComponent(c?.profileName||'');
        })();"#).map_err(err)?;
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
    let builder = WebviewBuilder::new(&label, WebviewUrl::External(Url::parse(&url).map_err(err)?))
        .on_navigation(move |url| {
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
        .on_page_load(move |view, payload| {
            let done = payload.event() == PageLoadEvent::Finished;
            {
                let state = load_app.state::<Runtime>();
                if let Ok(mut m) = state.model.lock() {
                    if let Some(tab) = m.tabs.iter_mut().find(|t| t.label == view.label()) {
                        tab.loading = !done;
                    }
                }
            }
            if done {
                let _ = inject_catalog(&load_app, view.label());
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

fn import_profile(app: &tauri::AppHandle, refresh: bool) -> Result<Value> {
    let state = app.state::<Runtime>();
    if state.importing.swap(true, Ordering::AcqRel) {
        return Err("A profile is already loading.".into());
    }
    let guard = FlagGuard(&state.importing);
    let (old, ini_path, mods_path, live) = {
        let m = state.model.lock().map_err(err)?;
        (
            m.prefs.profile.clone(),
            m.prefs.ini_path.clone(),
            m.prefs.mods_path.clone(),
            m.endpoint.is_some(),
        )
    };
    if live {
        return Err("Unlink live MO2 before importing a local profile.".into());
    }
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
    let epoch = state.epoch.fetch_add(1, Ordering::AcqRel) + 1;
    notify(app);
    let result = state.importer.import(&request).map_err(err);
    drop(guard);
    let result = match result {
        Ok(r) => r,
        Err(e) => {
            notify(app);
            return Err(e);
        }
    };
    let count = result.catalog["items"].as_array().map_or(0, Vec::len);
    let profile = result.catalog["profileName"]
        .as_str()
        .unwrap_or("Profile")
        .to_owned();
    let dashboard = {
        let mut m = state.model.lock().map_err(err)?;
        if state.epoch.load(Ordering::Acquire) != epoch {
            return Err("Connection changed while loading; the old result was discarded.".into());
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
    let labels = state
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
    notify(app);
    Ok(
        json!({"ok": true, "count": count, "message": format!("Loaded {count} mods from {profile}."), "state": state.snapshot()}),
    )
}

fn connect(app: &tauri::AppHandle, endpoint: &str) -> Result<Value> {
    let state = app.state::<Runtime>();
    let epoch = state.epoch.fetch_add(1, Ordering::AcqRel) + 1;
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
            let restore = {
                let mut m = state.model.lock().map_err(err)?;
                if m.shell_ready {
                    return Ok(json!({"ok": true}));
                }
                m.shell_ready = true;
                (m.prefs.endpoint.clone(), m.prefs.profile.is_some())
            };
            if restore.1 {
                if let Err(e) = import_profile(app, true) {
                    alert(app, e);
                }
            }
            if let Some(endpoint) = restore.0 {
                if let Err(e) = connect(app, &endpoint) {
                    alert(app, format!("Saved MO2 session is unavailable: {e}"));
                }
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
        "importMo2" => return import_profile(app, false),
        "refreshProfile" => return import_profile(app, true),
        "configureIni" => {
            let picked = app
                .dialog()
                .file()
                .set_title("Choose this instance's ModOrganizer.ini (Cancel keeps current setting)")
                .add_filter("MO2 configuration", &["ini"])
                .blocking_pick_file();
            if let Some(file) = picked {
                state.model.lock().map_err(err)?.prefs.ini_path =
                    Some(file.into_path().map_err(err)?);
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
                state.model.lock().map_err(err)?.prefs.mods_path =
                    Some(file.into_path().map_err(err)?);
                state.save()?;
                alert(
                    app,
                    "Mods directory saved. Import or refresh your profile to apply it.",
                );
            }
        }
        "clearPaths" => {
            let mut m = state.model.lock().map_err(err)?;
            m.prefs.ini_path = None;
            m.prefs.mods_path = None;
            drop(m);
            state.save()?;
            alert(
                app,
                "Using automatically detected instance paths on the next import.",
            );
        }
        "connect" => return connect(app, value.as_str().ok_or("Enter the MO2 loopback address")?),
        "probe" => return probe(app),
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
                probing: AtomicBool::new(false),
                smoke_output,
                smoke_sent: AtomicBool::new(false),
            });
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
            open_tab(app.handle(), Mode::Dashboard).map_err(std::io::Error::other)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect(
            "Could not start Modhunter. Install Microsoft Edge WebView2 Runtime and try again.",
        );
}
