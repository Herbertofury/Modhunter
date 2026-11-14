//! Global preferences, exact support previews and explicitly scoped upkeep.
use super::*;
use modhunter_core::{
    acquisition::{atomic_json, reject_link},
    app_maintenance,
};
use serde::{Deserialize, Serialize};
use tauri_plugin_clipboard_manager::ClipboardExt;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", default, deny_unknown_fields)]
pub(super) struct Settings {
    pub theme: String,
    pub locale: String,
    pub privacy_preset: String,
    pub quick_hide: bool,
    pub capture_protection: bool,
    pub check_app_updates: bool,
    pub external_private: bool,
    pub private_browser: String,
    pub retain_sign_ins: bool,
    pub clear_cache_on_exit: bool,
    pub backup_retention_days: u64,
    pub backup_retention_count: usize,
    pub backup_retention_bytes: u64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            theme: "system".into(),
            locale: "en".into(),
            privacy_preset: "standard".into(),
            quick_hide: false,
            capture_protection: false,
            check_app_updates: true,
            external_private: false,
            private_browser: "edge".into(),
            retain_sign_ins: true,
            clear_cache_on_exit: false,
            backup_retention_days: 30,
            backup_retention_count: 5,
            backup_retention_bytes: 64 * 1024 * 1024 * 1024,
        }
    }
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", default)]
struct Document {
    schema: u32,
    settings: Settings,
    last_release: Option<Value>,
    last_checked: u64,
    last_attempt: u64,
    release_error: String,
    dismissed_version: Option<String>,
}
struct Maintenance {
    path: PathBuf,
    config: PathBuf,
    data: PathBuf,
    document: Mutex<Document>,
    save: Mutex<()>,
    reports: Mutex<BTreeMap<String, (u64, String)>>,
    cleanup: Mutex<Option<(String, Vec<Value>)>>,
    checking: AtomicBool,
}
pub(super) fn setup(app: &tauri::AppHandle, config: PathBuf, data: PathBuf) -> Result<()> {
    let path = config.join("maintenance.json");
    let document = if path.exists() {
        reject_link(&path)?;
        if std::fs::metadata(&path).map_err(err)?.len() > 1024 * 1024 {
            return Err("Global preferences exceed their storage bound.".into());
        }
        serde_json::from_slice(&std::fs::read(&path).map_err(err)?).map_err(err)?
    } else {
        Document {
            schema: 1,
            ..Default::default()
        }
    };
    let settings = document.settings.clone();
    app.manage(Maintenance {
        path,
        config,
        data,
        document: Mutex::new(document),
        save: Mutex::new(()),
        reports: Mutex::new(BTreeMap::new()),
        cleanup: Mutex::new(None),
        checking: AtomicBool::new(false),
    });
    super::privacy::setup(app)?;
    super::privacy::configure(app, settings.quick_hide, settings.capture_protection)?;
    Ok(())
}
pub(super) fn settings(app: &tauri::AppHandle) -> Settings {
    app.try_state::<Maintenance>()
        .map(|s| {
            s.document
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .settings
                .clone()
        })
        .unwrap_or_default()
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let Some(state) = app.try_state::<Maintenance>() else {
        return json!({"settings":Settings::default()});
    };
    let document = state
        .document
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let visible = document.last_release.as_ref().is_some_and(|r| {
        r["newer"] == true
            && r["availableVersion"].as_str() != document.dismissed_version.as_deref()
    });
    json!({"settings":document.settings,"release":document.last_release,"lastChecked":document.last_checked,"lastAttempt":document.last_attempt,"releaseError":document.release_error,"dismissedVersion":document.dismissed_version,"updateVisible":visible,"checking":state.checking.load(Ordering::Acquire),"privacy":super::privacy::snapshot(app)})
}
pub(super) fn tick(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<Maintenance>() else {
        return;
    };
    let due = {
        let doc = state.document.lock().unwrap_or_else(|e| e.into_inner());
        doc.settings.check_app_updates
            && now().saturating_sub(doc.last_attempt) > 24 * 60 * 60 * 1000
    };
    if due && !state.checking.load(Ordering::Acquire) {
        let app = app.clone();
        std::thread::spawn(move || {
            let _ = check_release(&app);
        });
    }
}
fn persist(state: &Maintenance, doc: Document) -> Result<()> {
    atomic_json(&state.path, &doc)?;
    *state.document.lock().map_err(err)? = doc;
    Ok(())
}
fn check_release(app: &tauri::AppHandle) -> Result<Value> {
    let state = app.state::<Maintenance>();
    if state.checking.swap(true, Ordering::AcqRel) {
        return Err("An app release check is already running.".into());
    }
    struct Guard<'a>(&'a AtomicBool);
    impl Drop for Guard<'_> {
        fn drop(&mut self) {
            self.0.store(false, Ordering::Release);
        }
    }
    let _guard = Guard(&state.checking);
    let result = app_maintenance::check_release(env!("CARGO_PKG_VERSION"));
    let _save = state.save.lock().map_err(err)?;
    let mut doc = state.document.lock().map_err(err)?.clone();
    doc.last_attempt = now();
    match result {
        Ok(release) => {
            doc.last_checked = now();
            doc.last_release = Some(release);
            doc.release_error.clear();
        }
        Err(error) => {
            doc.release_error = error;
        }
    }
    persist(&state, doc)?;
    notify(app);
    Ok(json!({"maintenance":snapshot(app)}))
}
pub(super) fn apply_appearance(app: &tauri::AppHandle) -> Result<()> {
    let settings = settings(app);
    let theme = match settings.theme.as_str() {
        "dark" => Some(tauri::Theme::Dark),
        "light" => Some(tauri::Theme::Light),
        _ => None,
    };
    let payload = json!({"theme":settings.theme,"locale":settings.locale});
    for window in app.windows().values() {
        window.set_theme(theme).map_err(err)?;
    }
    for view in app.webviews().values() {
        let url = view.url().map_err(err)?;
        if crate::policy::local_asset(&url, "/shell.html")
            || policy::trusted_manager(view.label(), &url)
            || crate::policy::local_asset(&url, "/index.html")
        {
            let _=view.eval(format!("window.dispatchEvent(new CustomEvent('modhunter-preferences',{{detail:{payload}}}));"));
        }
    }
    let _ = app.emit("modhunter-preferences", payload);
    Ok(())
}
fn save_settings(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let mut next: Settings = serde_json::from_value(value.clone()).map_err(err)?;
    if !matches!(next.theme.as_str(), "system" | "light" | "dark")
        || !matches!(next.locale.as_str(), "en" | "es" | "it" | "zh-TW")
        || !matches!(
            next.private_browser.as_str(),
            "edge" | "chrome" | "firefox" | "brave"
        )
        || !matches!(
            next.privacy_preset.as_str(),
            "standard" | "discreet" | "custom"
        )
        || next.backup_retention_days == 0
        || next.backup_retention_days > 3650
        || next.backup_retention_count == 0
        || next.backup_retention_count > 1000
        || next.backup_retention_bytes < 1024 * 1024
        || next.backup_retention_bytes > 1024 * 1024 * 1024 * 1024
    {
        return Err("Choose valid global preferences and nonzero backup retention bounds.".into());
    }
    let standard = !next.quick_hide
        && !next.capture_protection
        && !next.external_private
        && !next.clear_cache_on_exit
        && next.retain_sign_ins;
    let discreet = next.quick_hide
        && next.capture_protection
        && next.external_private
        && next.clear_cache_on_exit
        && !next.retain_sign_ins;
    next.privacy_preset = if standard {
        "standard"
    } else if discreet {
        "discreet"
    } else {
        "custom"
    }
    .into();
    let state = app.state::<Maintenance>();
    let _save = state.save.lock().map_err(err)?;
    let mut doc = state.document.lock().map_err(err)?.clone();
    let previous = doc.settings.clone();
    super::privacy::configure(app, next.quick_hide, next.capture_protection)?;
    doc.settings = next;
    if let Err(error) = persist(&state, doc) {
        let _ = super::privacy::configure(app, previous.quick_hide, previous.capture_protection);
        return Err(error);
    }
    apply_appearance(app)?;
    Ok(json!({"maintenance":snapshot(app),"message":"Global preferences saved."}))
}
fn browsers() -> Vec<(String, String, PathBuf)> {
    let mut result = Vec::new();
    for (id, label, relative) in [
        (
            "edge",
            "Microsoft Edge",
            "Microsoft/Edge/Application/msedge.exe",
        ),
        (
            "chrome",
            "Google Chrome",
            "Google/Chrome/Application/chrome.exe",
        ),
        (
            "brave",
            "Brave",
            "BraveSoftware/Brave-Browser/Application/brave.exe",
        ),
        ("firefox", "Firefox", "Mozilla Firefox/firefox.exe"),
    ] {
        for variable in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"] {
            if let Some(root) = std::env::var_os(variable) {
                let path = PathBuf::from(root).join(relative);
                if path.is_absolute() && path.is_file() && reject_link(&path).is_ok() {
                    result.push((id.into(), label.into(), path));
                    break;
                }
            }
        }
    }
    result
}
pub(super) fn open_external(
    app: &tauri::AppHandle,
    url: &str,
    force_private: bool,
) -> Result<Value> {
    let url = canonical_page_url(url)?;
    let prefs = settings(app);
    if force_private || prefs.external_private {
        let (id,_,path)=browsers().into_iter().find(|(id,_,_)|id==&prefs.private_browser).ok_or("The selected private browser was not found. Choose an installed browser in Preferences.")?;
        let flag = match id.as_str() {
            "edge" => "--inprivate",
            "firefox" => "-private-window",
            _ => "--incognito",
        };
        std::process::Command::new(path)
            .arg(flag)
            .arg(url)
            .spawn()
            .map_err(err)?;
        Ok(json!({"message":"Opened the exact source in a separate private browser session."}))
    } else {
        app.opener().open_url(url, None::<&str>).map_err(err)?;
        Ok(
            json!({"message":"Opened in your regular browser. Its sign-in stays separate from Modhunter."}),
        )
    }
}
fn storage(app: &tauri::AppHandle) -> Result<Value> {
    let state = app.state::<Maintenance>();
    let mut groups = Vec::new();
    for (id, label, path) in [
        (
            "downloads",
            "Verified downloads and transfer receipts",
            state.data.join("watchtower-acquisitions"),
        ),
        (
            "reviews",
            "Prepared file reviews",
            state.data.join("watchtower-prepared"),
        ),
        (
            "accounts",
            "Remembered site sign-ins",
            state.data.join("watchtower-site-profiles"),
        ),
        (
            "temporary",
            "Temporary site sessions",
            state.data.join("watchtower-temporary-profiles"),
        ),
        (
            "watchlist",
            "Page observations and source choices",
            state.config.join("watchtower.json"),
        ),
        ("preferences", "Global preferences", state.path.clone()),
    ] {
        let result = match app_maintenance::usage(&path) {
            Ok(usage) => json!({"id":id,"label":label,"usage":usage,"complete":true}),
            Err(error) => json!({"id":id,"label":label,"complete":false,"error":error}),
        };
        groups.push(result);
    }
    Ok(
        json!({"storage":{"groups":groups,"scope":"Modhunter-owned desktop data. MO2 content snapshots are listed separately in Recovery and retain their native ownership checks."}}),
    )
}
fn diagnostics(app: &tauri::AppHandle) -> Result<Value> {
    let watch = app.state::<WatchRuntime>();
    let raw = watch.store.snapshot();
    let mut watch_states = BTreeMap::<String, usize>::new();
    if let Some(watches) = raw["watches"].as_array() {
        for row in watches {
            let name = row["state"].as_str().unwrap_or("unknown");
            if matches!(
                name,
                "baseline"
                    | "changed"
                    | "checking"
                    | "cancelled"
                    | "signInRequired"
                    | "challenge"
                    | "unavailable"
                    | "error"
                    | "interrupted"
                    | "selectorMissing"
                    | "tooLarge"
                    | "new"
            ) {
                *watch_states.entry(name.to_string()).or_default() += 1;
            }
        }
    }
    let acquisition = super::acquisition::snapshot(app);
    let mut transfers = BTreeMap::<String, usize>::new();
    if let Some(rows) = acquisition["jobs"].as_array() {
        for row in rows {
            let name = row["state"].as_str().unwrap_or("unknown");
            if matches!(
                name,
                "pending"
                    | "downloading"
                    | "validating"
                    | "ready"
                    | "cancelled"
                    | "failed"
                    | "interrupted"
            ) {
                *transfers.entry(name.to_string()).or_default() += 1;
            }
        }
    }
    let prefs = settings(app);
    let report = json!({"schema":1,"application":"Modhunter","version":env!("CARGO_PKG_VERSION"),"platform":"Windows","generatedAt":now(),"redaction":"Allowlisted facts only. No source URLs, names, account labels, paths, tokens, cookies, page text or raw errors are included.","connectedToMo2":crate::windows::mo2_connection(app).is_ok(),"watchtower":{"watches":raw["watches"].as_array().map_or(0,Vec::len),"sessions":raw["sessions"].as_array().map_or(0,Vec::len),"states":watch_states,"readOnly":!watch.ownership_error.is_empty()},"transfers":transfers,"preferences":{"theme":prefs.theme,"locale":prefs.locale,"privacyPreset":prefs.privacy_preset,"quickHide":prefs.quick_hide,"captureProtection":prefs.capture_protection,"backgroundChecking":watch.store.settings().background_checking},"proofScope":"Support facts, not a claim of personal-game acceptance."});
    let text = serde_json::to_string_pretty(&report).map_err(err)? + "\n";
    let id = format!("report-{:x}", Sha256::digest(text.as_bytes()));
    let state = app.state::<Maintenance>();
    let mut reports = state.reports.lock().map_err(err)?;
    reports.retain(|_, (time, _)| now().saturating_sub(*time) < 10 * 60 * 1000);
    if reports.len() >= 8 {
        reports.clear();
    }
    reports.insert(id.clone(), (now(), text.clone()));
    Ok(json!({"reportId":id,"text":text}))
}
fn report_export(app: &tauri::AppHandle, value: &Value, copy: bool) -> Result<Value> {
    let state = app.state::<Maintenance>();
    let id = value["reportId"]
        .as_str()
        .ok_or("Preview the support report first.")?;
    let text = {
        let reports = state.reports.lock().map_err(err)?;
        let (time, text) = reports
            .get(id)
            .ok_or("Support preview expired. Generate a new preview.")?;
        if now().saturating_sub(*time) > 10 * 60 * 1000 {
            return Err("Support preview expired.".into());
        }
        text.clone()
    };
    if copy {
        app.clipboard().write_text(text).map_err(err)?;
        return Ok(json!({"message":"Copied the exact previewed support report."}));
    }
    let Some(file) = app
        .dialog()
        .file()
        .set_title("Save previewed Modhunter support report")
        .set_file_name("Modhunter-Support.json")
        .add_filter("JSON report", &["json"])
        .blocking_save_file()
    else {
        return Ok(json!({"cancelled":true}));
    };
    let path = file.into_path().map_err(err)?;
    if path.exists() {
        reject_link(&path)?;
    }
    std::fs::write(path, text.as_bytes()).map_err(err)?;
    Ok(json!({"message":"Saved the exact previewed support report."}))
}
fn cleanup_preview(app: &tauri::AppHandle) -> Result<Value> {
    let state = app.state::<Maintenance>();
    let snapshot = super::acquisition::snapshot(app);
    let rows = snapshot["jobs"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|r| {
            matches!(
                r["state"].as_str(),
                Some("ready" | "failed" | "cancelled" | "interrupted")
            ) && r["id"]
                .as_str()
                .is_some_and(|id| super::review::ensure_removable(app, id).is_ok())
        })
        .collect::<Vec<_>>();
    let id = format!(
        "cleanup-{:x}",
        Sha256::digest(serde_json::to_vec(&rows).map_err(err)?)
    );
    *state.cleanup.lock().map_err(err)? = Some((id.clone(), rows.clone()));
    Ok(
        json!({"cleanup":{"id":id,"count":rows.len(),"bytes":rows.iter().filter_map(|r|r["bytes"].as_u64()).sum::<u64>(),"scope":"Unowned completed or stopped downloads only. Active reviews, native operations and installed mods are excluded."}}),
    )
}
fn cleanup_apply(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    if value["confirm"] != "DELETE DOWNLOADS" {
        return Err("Confirm DELETE DOWNLOADS for the displayed cleanup preview.".into());
    }
    let state = app.state::<Maintenance>();
    let preview = state
        .cleanup
        .lock()
        .map_err(err)?
        .clone()
        .ok_or("Preview storage cleanup first.")?;
    if value["previewId"] != preview.0 {
        return Err("The cleanup preview changed.".into());
    }
    let current = super::acquisition::snapshot(app);
    let rows = current["jobs"]
        .as_array()
        .ok_or("Downloads are unavailable.")?;
    for saved in &preview.1 {
        if !rows.iter().any(|row| row == saved) {
            return Err("A previewed download changed. Preview cleanup again.".into());
        }
        super::review::ensure_removable(app, saved["id"].as_str().ok_or("Invalid receipt.")?)?;
    }
    let mut removed = Vec::new();
    let mut failed = Vec::new();
    for saved in preview.1 {
        let id = saved["id"].as_str().unwrap_or("");
        match super::acquisition::execute(
            app,
            "removeDownload",
            &json!({"id":id,"confirm":"REMOVE"}),
        ) {
            Ok(_) => removed.push(id.to_string()),
            Err(error) => failed.push(json!({"id":id,"error":error})),
        }
    }
    *state.cleanup.lock().map_err(err)? = None;
    Ok(
        json!({"cleanup":{"removed":removed,"failed":failed},"message":"Scoped download cleanup finished. Inspect any reported failures."}),
    )
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    match kind {
        "maintenance" => Ok(json!({"maintenance":snapshot(app)})),
        "globalSettings" => save_settings(app, value),
        "checkAppRelease" => check_release(app),
        "dismissAppRelease" => {
            let state = app.state::<Maintenance>();
            let _save = state.save.lock().map_err(err)?;
            let mut doc = state.document.lock().map_err(err)?.clone();
            let version = value["version"]
                .as_str()
                .ok_or("Choose the displayed release version.")?;
            if doc
                .last_release
                .as_ref()
                .and_then(|v| v["availableVersion"].as_str())
                != Some(version)
            {
                return Err("The available release changed.".into());
            }
            doc.dismissed_version = Some(version.into());
            persist(&state, doc)?;
            Ok(json!({"maintenance":snapshot(app)}))
        }
        "openAppRelease" => {
            let state = app.state::<Maintenance>();
            let url = state
                .document
                .lock()
                .map_err(err)?
                .last_release
                .as_ref()
                .and_then(|v| v["url"].as_str())
                .ok_or("Check for an app release first.")?
                .to_string();
            app.opener().open_url(url, None::<&str>).map_err(err)?;
            Ok(json!({}))
        }
        "storage" => storage(app),
        "previewDownloadCleanup" => cleanup_preview(app),
        "applyDownloadCleanup" => cleanup_apply(app, value),
        "diagnosticsPreview" => diagnostics(app),
        "diagnosticsCopy" => report_export(app, value, true),
        "diagnosticsSave" => report_export(app, value, false),
        "browserChoices" => Ok(
            json!({"browsers":browsers().into_iter().map(|(id,label,_)|json!({"id":id,"label":label})).collect::<Vec<_>>()}),
        ),
        "openPrivate" => {
            let watch = app
                .state::<WatchRuntime>()
                .store
                .watch(id(value)?)
                .ok_or("Source no longer exists.")?;
            open_external(app, &watch.url, true)
        }
        "licenses" => Ok(
            json!({"licenses":[{"name":"Modhunter","text":include_str!("../../../PROJECT_NOTICE.txt")},{"name":"Third-party components","text":include_str!("../../../resources/THIRD_PARTY_NOTICES.json")},{"name":"7-Zip 26.06","text":include_str!("../../../resources/archive-tools/License.txt")}]}),
        ),
        _ => Err("Unknown maintenance command.".into()),
    }
}
