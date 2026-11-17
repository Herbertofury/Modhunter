//! Native ownership of MO2 discovery, review and verified plugin installation.
//! The renderer selects native identities; it never supplies installer paths.
use crate::{
    installer_bundle::{self, VerifiedBundle},
    mo2_discovery::{self, AvailableRoot, Installation, Inventory, KnownSeeds, ScanMode, ScanSpec},
};
use modhunter_core::BridgeClient;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::windows::{fs::OpenOptionsExt, process::CommandExt},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{
    Emitter, Manager, Webview, WebviewUrl, WebviewWindowBuilder,
    webview::{NewWindowResponse, PageLoadEvent},
};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

type Result<T> = std::result::Result<T, String>;
const LABEL: &str = "mo2-instances";
const VERSION: &str = env!("CARGO_PKG_VERSION");
const PINNED_MANIFEST: &[u8] = include_bytes!("../../../resources/mo2-installer/SHA256SUMS.txt");
const REVIEW_LIFETIME_MS: u64 = 120_000;

fn err(error: impl std::fmt::Display) -> String {
    error.to_string()
}
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}
fn path_key(path: &Path) -> String {
    let original = path.to_string_lossy().replace('/', "\\");
    let normalized = if original
        .get(..8)
        .is_some_and(|s| s.eq_ignore_ascii_case(r"\\?\UNC\"))
    {
        format!(r"\\{}", &original[8..])
    } else if let Some(rest) = original.strip_prefix(r"\\?\") {
        rest.into()
    } else {
        original
    };
    normalized.trim_end_matches(['\\', '/']).to_lowercase()
}
fn same_path(left: &Path, right: &Path) -> bool {
    path_key(left) == path_key(right)
}
fn active_scan(scan: &Value) -> bool {
    matches!(scan["status"].as_str(), Some("running" | "cancelling"))
}
fn idle_install() -> Value {
    json!({"id":"","status":"idle","total":0,"completed":0,"stopRequested":false,"results":[]})
}
fn mark_stopping(install: &mut Value) -> bool {
    if !matches!(install["status"].as_str(), Some("running" | "stopping")) {
        return false;
    }
    install["status"] = json!("stopping");
    install["stopRequested"] = json!(true);
    true
}

#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Persisted {
    #[serde(default)]
    roots: Vec<PathBuf>,
    #[serde(default)]
    install: Value,
}
struct ReviewedTarget {
    installation: Installation,
    fingerprint: String,
}
struct Review {
    token: String,
    expires_at: u64,
    targets: Vec<ReviewedTarget>,
    manifest_sha256: String,
}
struct Model {
    inventory: Inventory,
    scan: Value,
    install: Value,
    package: Value,
    roots: Vec<AvailableRoot>,
    remembered: Vec<PathBuf>,
    sessions: Vec<Value>,
    review: Option<Review>,
    error: String,
}
struct Runtime {
    model: Mutex<Model>,
    commands: Mutex<()>,
    save: Mutex<()>,
    revision: AtomicU64,
    sequence: AtomicU64,
    started: AtomicBool,
    admission: Arc<crate::install_admission::Admission>,
    scan_cancel: Mutex<Option<(String, Arc<AtomicBool>)>>,
    config: PathBuf,
    resource: PathBuf,
    fixture_root: Option<PathBuf>,
}
impl Runtime {
    fn snapshot(&self, app: &tauri::AppHandle) -> Value {
        let model = self.model.lock().unwrap_or_else(|e| e.into_inner());
        json!({
            "revision":self.revision.fetch_add(1, Ordering::AcqRel)+1,
            "version":VERSION,"package":model.package,"inventory":model.inventory,
            "scan":model.scan,"install":model.install,"roots":model.roots,
            "sessions":model.sessions,"error":model.error,
            "settings":crate::watchtower_windows::appearance_settings(app)
        })
    }
    fn operation_id(&self, kind: &str) -> String {
        format!(
            "{kind}-{}-{}",
            now_ms(),
            self.sequence.fetch_add(1, Ordering::AcqRel)
        )
    }
    fn persist(&self) -> Result<()> {
        let _save = self.save.lock().map_err(err)?;
        let document = {
            let model = self.model.lock().map_err(err)?;
            Persisted {
                roots: model.remembered.clone(),
                install: model.install.clone(),
            }
        };
        let parent = self
            .config
            .parent()
            .ok_or("Invalid instance settings path.")?;
        fs::create_dir_all(parent).map_err(err)?;
        let mut pending = tempfile::NamedTempFile::new_in(parent).map_err(err)?;
        pending
            .write_all(&serde_json::to_vec_pretty(&document).map_err(err)?)
            .map_err(err)?;
        pending.as_file().sync_all().map_err(err)?;
        pending.persist(&self.config).map_err(err)?;
        Ok(())
    }
}

pub(crate) fn setup(app: &tauri::AppHandle) -> Result<()> {
    let requested_fixture = std::env::args().find_map(|arg| {
        arg.strip_prefix("--instances-smoke-root=")
            .map(PathBuf::from)
    });
    if requested_fixture.is_some()
        && !(std::env::var("MODHUNTER_NATIVE_SMOKE").as_deref() == Ok("1")
            && std::env::var("GITHUB_ACTIONS").as_deref() == Ok("true")
            && std::env::var("RUNNER_OS").as_deref() == Ok("Windows"))
    {
        return Err(
            "Instance smoke roots are restricted to the disposable native CI fixture.".into(),
        );
    }
    let fixture_root = requested_fixture
        .map(fs::canonicalize)
        .transpose()
        .map_err(err)?;
    let config = match &fixture_root {
        Some(root) => root.join("manager-state/settings.json"),
        None => app
            .path()
            .app_config_dir()
            .map_err(err)?
            .join("mo2-instances.json"),
    };
    let (mut saved, warning) = match File::open(&config) {
        Ok(file) => {
            let mut bytes = Vec::new();
            match file.take(8_388_609).read_to_end(&mut bytes) {
                Ok(_) if bytes.len() <= 8_388_608 => match serde_json::from_slice::<Persisted>(&bytes) {
                    Ok(saved) => (saved, String::new()),
                    Err(_) => (Persisted::default(), "Saved instance settings could not be read. Your original settings file is retained until a new explicit discovery or installation is saved.".into()),
                },
                _ => (Persisted::default(), "Saved instance settings could not be read completely.".into()),
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            (Persisted::default(), String::new())
        }
        Err(error) => (
            Persisted::default(),
            format!("Could not read instance settings: {error}"),
        ),
    };
    if let Some(root) = &fixture_root {
        saved.roots = vec![root.clone()];
    }
    if !saved.install.is_object() {
        saved.install = idle_install();
    } else if matches!(
        saved.install["status"].as_str(),
        Some("running" | "stopping")
    ) {
        saved.install["status"] = json!("failed");
        saved.install["message"] = json!(
            "The previous desktop session ended during installation. Refresh and review the recorded target and backup before retrying."
        );
        if let Some(results) = saved.install["results"].as_array_mut() {
            for result in results {
                if matches!(result["status"].as_str(), Some("installing" | "queued")) {
                    result["status"] = json!("failed");
                    result["message"] =
                        json!("Interrupted session; installation outcome has not been verified.");
                }
            }
        }
    }
    let mut roots = if fixture_root.is_some() {
        Vec::new()
    } else {
        mo2_discovery::available_roots()
    };
    for path in &saved.roots {
        append_root(&mut roots, path);
    }
    let resource = app
        .path()
        .resource_dir()
        .map_err(err)?
        .join("mo2-installer");
    app.manage(Runtime {
        model: Mutex::new(Model {
            inventory: Inventory::default(),
            scan: json!({"id":"","status":"idle","mode":"known","directoriesVisited":0,
                "installationsFound":0,"instancesFound":0,"currentPath":null,
                "elapsedMs":0,"roots":[],"message":"Open the manager to discover MO2."}),
            install: saved.install,
            package: json!({"available":resource.is_dir(),"valid":false,"version":VERSION,
                "commit":"","message":"The matching plugin bundle will be verified when discovery starts."}),
            roots, remembered:saved.roots, sessions:Vec::new(),review:None,error:warning,
        }),
        commands:Mutex::new(()),save:Mutex::new(()),revision:AtomicU64::new(0),
        sequence:AtomicU64::new(0),started:AtomicBool::new(false),
        admission:Arc::new(crate::install_admission::Admission::default()),
        scan_cancel:Mutex::new(None),config,resource,fixture_root,
    });
    Ok(())
}

fn append_root(roots: &mut Vec<AvailableRoot>, path: &Path) {
    if path.is_dir() && !roots.iter().any(|root| same_path(&root.path, path)) {
        roots.push(AvailableRoot {
            path: path.to_path_buf(),
            label: path.to_string_lossy().into_owned(),
            kind: "folder".into(),
        });
    }
}
fn notify(app: &tauri::AppHandle) {
    let _ = app.emit_to(
        LABEL,
        "modhunter-instances-state",
        app.state::<Runtime>().snapshot(app),
    );
}
fn record_error(app: &tauri::AppHandle, message: String) {
    app.state::<Runtime>()
        .model
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .error = message;
    notify(app);
}
fn save_or_warn(app: &tauri::AppHandle) {
    if let Err(error) = app.state::<Runtime>().persist() {
        record_error(
            app,
            format!("The current result remains visible, but could not be saved: {error}"),
        );
    }
}

pub(crate) fn open(app: &tauri::AppHandle) -> Result<()> {
    if let Some(window) = app.get_webview_window(LABEL) {
        let native = app
            .get_window(window.label())
            .ok_or("The instance manager closed.")?;
        crate::watchtower_windows::privacy::guarded_show(app, &native, true)?;
        notify(app);
    } else {
        let appearance = app.clone();
        let window =
            WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("instances.html".into()))
                .visible(false)
                .focused(false)
                .title("Modhunter · MO2 Instances")
                .inner_size(1280.0, 880.0)
                .min_inner_size(760.0, 560.0)
                .on_navigation(|url| crate::policy::local_asset(url, "/instances.html"))
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .on_page_load(move |_, payload| {
                    if payload.event() == PageLoadEvent::Finished {
                        let _ = crate::watchtower_windows::apply_global_appearance(&appearance);
                        notify(&appearance);
                    }
                })
                .build()
                .map_err(err)?;
        let handle = app.clone();
        window.on_window_event(move |event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                crate::watchtower_windows::privacy::forget_window(&handle, LABEL);
            }
        });
        let native = app
            .get_window(LABEL)
            .ok_or("The instance manager closed.")?;
        crate::watchtower_windows::privacy::guarded_show(app, &native, true)?;
    }
    let state = app.state::<Runtime>();
    if !state.started.swap(true, Ordering::AcqRel) {
        let _gate = state.commands.lock().map_err(err)?;
        start_scan(app, ScanSpec::default())?;
    }
    Ok(())
}

/// Closing the main app must not terminate a plugin replace/rollback transaction.
pub(crate) fn guard_close(app: &tauri::AppHandle) -> bool {
    let Some(state) = app.try_state::<Runtime>() else {
        return false;
    };
    if state.admission.request_close() == crate::install_admission::CloseDecision::Close {
        if let Some((_, cancel)) = state
            .scan_cancel
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
        {
            cancel.store(true, Ordering::Release);
        }
        return false;
    }
    {
        let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
        if mark_stopping(&mut model.install) {
            state.admission.request_stop();
            model.error = "Finishing the current plugin installation safely. Remaining targets will stop; close Modhunter again after the result appears.".into();
        } else {
            model.error = "Saving the completed installation result. Close Modhunter again after it finishes.".into();
        }
    }
    let _ = open(app);
    notify(app);
    true
}

struct LockedBundle {
    verified: VerifiedBundle,
    _files: Vec<File>,
}
fn lock_bundle(resource: &Path) -> Result<LockedBundle> {
    installer_bundle::reject_link_ancestors(resource)?;
    // Windows handles deny writes/deletes while PowerShell reads these bytes.
    // Lock every pinned file, not just the script and editable manifest.
    let names = installer_bundle::parse_manifest(PINNED_MANIFEST)?;
    let mut files = Vec::with_capacity(names.len() + 1);
    for name in std::iter::once("SHA256SUMS.txt").chain(names.keys().map(String::as_str)) {
        let path = resource.join(name);
        installer_bundle::reject_link_ancestors(&path)?;
        files.push(
            OpenOptions::new()
                .read(true)
                .share_mode(1)
                .open(&path)
                .map_err(|e| {
                    format!(
                        "Could not lock the matching plugin resource {}: {e}",
                        path.display()
                    )
                })?,
        );
    }
    let verified = installer_bundle::verify(resource, PINNED_MANIFEST, VERSION)?;
    Ok(LockedBundle {
        verified,
        _files: files,
    })
}
fn package_value(result: &Result<LockedBundle>, resource: &Path) -> Value {
    match result {
        Ok(bundle) => {
            json!({"available":true,"valid":true,"version":bundle.verified.identity.version,
            "commit":bundle.verified.identity.commit,"message":"Matching plugin and installer verified."})
        }
        Err(error) => json!({"available":resource.is_dir(),"valid":false,"version":VERSION,
            "commit":"","message":error}),
    }
}

fn merge_inventory(mut prior: Inventory, found: Inventory) -> Inventory {
    // Canceled scans may contribute valid new observations, never erase the
    // previous complete inventory or silently call partial coverage complete.
    for row in found.installations {
        prior.installations.retain(|old| old.id != row.id);
        prior.installations.push(row);
    }
    for mut row in found.instances {
        if let Some(previous) = prior.instances.iter().find(|old| old.id == row.id) {
            let mut paths: HashSet<_> = row
                .profiles
                .iter()
                .map(|profile| path_key(&profile.path))
                .collect();
            for profile in &previous.profiles {
                if paths.insert(path_key(&profile.path)) {
                    row.profiles.push(profile.clone());
                }
            }
        }
        prior.instances.retain(|old| old.id != row.id);
        prior.instances.push(row);
    }
    for row in found.associations {
        if !prior.associations.iter().any(|old| {
            old.installation_id == row.installation_id
                && old.instance_id == row.instance_id
                && old.kind == row.kind
        }) {
            prior.associations.push(row);
        }
    }
    prior.issues.extend(found.issues);
    prior.scope = found.scope;
    prior.complete = false;
    prior
}

fn start_scan(app: &tauri::AppHandle, spec: ScanSpec) -> Result<()> {
    let state = app.state::<Runtime>();
    if state.admission.installing() {
        return Err("Wait for the current installation to finish before discovering again.".into());
    }
    let operation = state.operation_id("scan");
    let cancel = Arc::new(AtomicBool::new(false));
    let remembered = {
        let mut model = state.model.lock().map_err(err)?;
        if active_scan(&model.scan) {
            return Err(
                "A discovery scan is already running. Stop it before starting another.".into(),
            );
        }
        model.review = None;
        model.error.clear();
        model.scan = json!({"id":operation,"status":"running","mode":spec.mode,
            "directoriesVisited":0,"installationsFound":0,"instancesFound":0,
            "currentPath":null,"elapsedMs":0,"roots":spec.roots,
            "message":"Discovering MO2 applications and instance configurations."});
        model.remembered.clone()
    };
    *state.scan_cancel.lock().map_err(err)? = Some((operation.clone(), cancel.clone()));
    let hints = crate::windows::instance_discovery_hints(app);
    let fixture = state.fixture_root.clone();
    let handle = app.clone();
    notify(app);
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<Runtime>();
        let bundle = lock_bundle(&state.resource);
        state
            .model
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .package = package_value(&bundle, &state.resource);
        drop(bundle);
        notify(&handle);
        let seeds = if let Some(root) = fixture {
            let mut seeds = KnownSeeds::for_roots(std::slice::from_ref(&root));
            let globals = root.join("global-instances");
            if globals.is_dir() {
                seeds.global_instance_roots.push(globals);
            }
            seeds
        } else {
            mo2_discovery::known_seeds(&remembered, &hints)
        };
        let started = Instant::now();
        let mut last_emit = Instant::now() - Duration::from_secs(1);
        let mut last_counts = (0, 0);
        let inventory = mo2_discovery::discover(&spec, seeds, &cancel, |progress, partial| {
            let counts = (progress.installations_found, progress.instances_found);
            if last_emit.elapsed() < Duration::from_millis(200) && counts == last_counts {
                return;
            }
            last_emit = Instant::now();
            last_counts = counts;
            {
                let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
                if model.scan["id"].as_str() != Some(&operation) {
                    return;
                }
                model.scan["directoriesVisited"] = json!(progress.directories_visited);
                model.scan["installationsFound"] = json!(progress.installations_found);
                model.scan["instancesFound"] = json!(progress.instances_found);
                model.scan["currentPath"] = json!(progress.current_path);
                model.scan["elapsedMs"] = json!(progress.elapsed_ms);
                model.scan["partialInventory"] = json!(partial);
            }
            notify(&handle);
        });
        let mut sessions = Vec::new();
        let mut session_issue = None;
        if state.fixture_root.is_none()
            && !cancel.load(Ordering::Acquire)
            && let Some(local) = std::env::var_os("LOCALAPPDATA")
        {
            let result = BridgeClient::with_timeout(Duration::from_millis(800))
                .map_err(err)
                .and_then(|client| {
                    crate::discovery::discover_all(
                        &PathBuf::from(local).join("Modhunter/mo2-sessions"),
                        &client,
                        &cancel,
                    )
                    .map_err(err)
                });
            match result {
                Ok(found) => {
                    for session in found {
                        let id = installer_bundle::digest(
                            format!("{}\n{}", session.endpoint, session.profile_id).as_bytes(),
                        );
                        // A descriptor PID is not proof that an app owns a live
                        // session. Keep it unassociated unless the bridge proves it.
                        sessions.push(json!({"id":id,"endpoint":session.endpoint,
                        "profileId":session.profile_id,"profileName":session.profile_name,
                        "version":session.version,"pid":session.pid}));
                    }
                }
                Err(error) => session_issue = Some(error),
            }
        }
        let _gate = state.commands.lock().unwrap_or_else(|e| e.into_inner());
        {
            let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
            if model.scan["id"].as_str() != Some(&operation) {
                return;
            }
            let stopped = cancel.load(Ordering::Acquire);
            model.inventory = if stopped {
                merge_inventory(std::mem::take(&mut model.inventory), inventory)
            } else {
                inventory
            };
            if let Some(error) = session_issue {
                model.inventory.issues.push(mo2_discovery::Issue::new(
                    None,
                    "liveSessions",
                    format!("Live session discovery could not finish: {error}"),
                ));
            }
            if !stopped {
                model.sessions = sessions;
            }
            let discovered: Vec<_> = model
                .inventory
                .installations
                .iter()
                .map(|item| item.directory.clone())
                .collect();
            for path in discovered {
                if !model.remembered.iter().any(|old| same_path(old, &path)) {
                    model.remembered.push(path.clone());
                }
                append_root(&mut model.roots, &path);
            }
            model.scan["status"] = json!(if stopped { "cancelled" } else { "complete" });
            model.scan["installationsFound"] = json!(model.inventory.installations.len());
            model.scan["instancesFound"] = json!(model.inventory.instances.len());
            model.scan["elapsedMs"] = json!(started.elapsed().as_millis() as u64);
            model.scan["currentPath"] = Value::Null;
            model.scan["partialInventory"] = Value::Null;
            model.scan["message"] = json!(if stopped {
                "Discovery stopped. Valid findings and the previous inventory are retained; this scope is incomplete."
            } else {
                "Discovery finished for the selected scope. Review any skipped paths listed below."
            });
        }
        {
            let mut current = state.scan_cancel.lock().unwrap_or_else(|e| e.into_inner());
            if current.as_ref().is_some_and(|(id, _)| id == &operation) {
                *current = None;
            }
        }
        save_or_warn(&handle);
        notify(&handle);
    });
    Ok(())
}

fn selected_installation(model: &Model, id: &str) -> Result<Installation> {
    model
        .inventory
        .installations
        .iter()
        .find(|item| item.id == id)
        .cloned()
        .ok_or_else(|| {
            "This MO2 application is no longer in the native inventory. Discover it again.".into()
        })
}
fn field<'a>(value: &'a Value, name: &str) -> Result<&'a str> {
    value
        .get(name)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("Choose a valid {name}."))
}
fn run_installer(
    bundle: &LockedBundle,
    installation: &Installation,
    fingerprint: Option<&str>,
) -> Result<Value> {
    installer_bundle::reject_link_ancestors(&installation.directory)?;
    installer_bundle::reject_link_ancestors(&installation.executable)?;
    let windows = std::env::var_os("WINDIR")
        .map(PathBuf::from)
        .ok_or("Windows system directory is unavailable.")?;
    let powershell = windows.join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut command = Command::new(powershell);
    command
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ])
        .arg(bundle.verified.root.join("Install_MO2_Plugin.ps1"))
        .arg("-MO2Directory")
        .arg(&installation.directory)
        .arg("-Json")
        .arg("-ExpectedVersion")
        .arg(&bundle.verified.identity.version)
        .arg("-ExpectedCommit")
        .arg(&bundle.verified.identity.commit)
        .arg("-ExpectedManifestSha256")
        .arg(&bundle.verified.manifest_sha256)
        .creation_flags(0x08000000);
    match fingerprint {
        Some(value) => {
            command.arg("-ExpectedInstalledFingerprint").arg(value);
        }
        None => {
            command.arg("-Preview");
        }
    }
    // Never kill a transaction after a timeout: it may be preserving data or
    // rolling back. The verified script produces one bounded JSON receipt.
    let output = command
        .output()
        .map_err(|e| format!("Could not start the Windows plugin installer: {e}"))?;
    if output.stdout.len() > 262_144 || output.stderr.len() > 262_144 {
        return Err("The installer returned an oversized response. Review the target and recorded backups before retrying.".into());
    }
    let bytes = output
        .stdout
        .strip_prefix(&[0xef, 0xbb, 0xbf])
        .unwrap_or(&output.stdout);
    let result: Value = serde_json::from_slice(bytes).map_err(|_| {
        let detail = String::from_utf8_lossy(&output.stderr);
        format!(
            "The installer did not return a valid result (exit {}). {}",
            output
                .status
                .code()
                .map_or("unknown".into(), |code| code.to_string()),
            detail.chars().take(1500).collect::<String>()
        )
    })?;
    if result["schemaVersion"] != 1 {
        return Err("The installer returned an unsupported receipt.".into());
    }
    if result["ok"] == true {
        let target = result["targetDirectory"]
            .as_str()
            .map(PathBuf::from)
            .ok_or("The installer did not identify its target.")?;
        if !output.status.success()
            || result["version"] != bundle.verified.identity.version
            || result["commit"] != bundle.verified.identity.commit
            || !same_path(&target, &installation.directory)
            || !matches!(
                result["status"].as_str(),
                Some("preview" | "installed" | "updated")
            )
        {
            return Err(
                "The installer receipt did not match the reviewed release and target.".into(),
            );
        }
    }
    Ok(result)
}

fn preview(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let state = app.state::<Runtime>();
    if state.admission.installing() {
        return Err("Wait for the current installation to finish.".into());
    }
    if let Ok(processes) = mo2_discovery::running_processes() {
        let mut model = state.model.lock().map_err(err)?;
        for installation in &mut model.inventory.installations {
            installation.running_pids = processes
                .iter()
                .filter_map(|process| {
                    process
                        .executable
                        .as_ref()
                        .filter(|path| same_path(path, &installation.executable))
                        .map(|_| process.pid)
                })
                .collect();
        }
    }
    let requested = value["installationIds"]
        .as_array()
        .filter(|v| !v.is_empty())
        .ok_or("Select one or more MO2 applications.")?;
    let targets = {
        let mut model = state.model.lock().map_err(err)?;
        model.review = None;
        if active_scan(&model.scan) {
            return Err("Wait for discovery to finish, or stop it first.".into());
        }
        let mut ids = HashSet::new();
        let mut targets = Vec::with_capacity(requested.len());
        for id in requested {
            let id = id.as_str().ok_or("Invalid MO2 application identity.")?;
            if !ids.insert(id) {
                return Err("Select each MO2 application only once.".into());
            }
            targets.push(selected_installation(&model, id)?);
        }
        targets
    };
    let result = lock_bundle(&state.resource);
    state.model.lock().map_err(err)?.package = package_value(&result, &state.resource);
    let bundle = result?;
    let mut rows = Vec::with_capacity(targets.len());
    let mut reviewed = Vec::with_capacity(targets.len());
    for target in targets {
        let checked = run_installer(&bundle, &target, None);
        let (receipt, blockers) = match checked {
            Ok(receipt)
                if receipt["ok"] == true
                    && receipt["status"] == "preview"
                    && receipt["installedFingerprint"].as_str().is_some_and(|s| {
                        s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
                    }) =>
            {
                (receipt, Vec::new())
            }
            Ok(receipt) => {
                let message = receipt["message"]
                    .as_str()
                    .unwrap_or("The target is not ready for installation.")
                    .to_owned();
                (receipt, vec![message])
            }
            Err(error) => (Value::Null, vec![error]),
        };
        let affected = {
            let model = state.model.lock().map_err(err)?;
            model
                .inventory
                .associations
                .iter()
                .filter(|association| association.installation_id == target.id)
                .filter_map(|association| {
                    model
                        .inventory
                        .instances
                        .iter()
                        .find(|instance| instance.id == association.instance_id)
                        .map(|instance| {
                            json!({"id":instance.id,"name":instance.name,
                        "kind":instance.kind,"relation":association.kind})
                        })
                })
                .collect::<Vec<_>>()
        };
        let previous = receipt["previousVersion"]
            .as_str()
            .or(target.plugin.version.as_deref());
        let action = if !target.directory.join("plugins/mo2_update_arsenal").exists() {
            "install"
        } else if previous == Some(VERSION) {
            "repair"
        } else {
            "update"
        };
        rows.push(
            json!({"installationId":target.id,"directory":target.directory,
            "pluginDirectory":target.directory.join("plugins/mo2_update_arsenal"),
            "backupRoot":target.directory.join("ModhunterBackups"),
            "previousVersion":previous,"version":VERSION,"action":action,
            "canInstall":blockers.is_empty(),"blockers":blockers,
            "affectedInstances":affected,"message":receipt["message"]}),
        );
        if blockers.is_empty() {
            reviewed.push(ReviewedTarget {
                installation: target,
                fingerprint: receipt["installedFingerprint"]
                    .as_str()
                    .unwrap_or_default()
                    .into(),
            });
        }
    }
    let can_install = reviewed.len() == rows.len();
    let token = state.operation_id("review");
    let expires_at = now_ms() + REVIEW_LIFETIME_MS;
    if can_install {
        state.model.lock().map_err(err)?.review = Some(Review {
            token: token.clone(),
            expires_at,
            targets: reviewed,
            manifest_sha256: bundle.verified.manifest_sha256.clone(),
        });
    }
    let preview = json!({"token":token,"expiresAt":expires_at,
        "package":bundle.verified.identity,"canInstall":can_install,"targets":rows});
    notify(app);
    Ok(json!({"ok":true,"state":state.snapshot(app),"preview":preview}))
}

fn start_install(app: &tauri::AppHandle, token: &str) -> Result<()> {
    let state = app.state::<Runtime>();
    if state.admission.installing() {
        return Err("An installation is already running.".into());
    }
    let review = {
        let mut model = state.model.lock().map_err(err)?;
        if active_scan(&model.scan) {
            return Err("Finish or stop discovery before installing.".into());
        }
        let review = model
            .review
            .take()
            .ok_or("Review the selected installations again.")?;
        if review.token != token || now_ms() >= review.expires_at {
            return Err(
                "The installation review expired or was replaced. Review the targets again.".into(),
            );
        }
        for target in &review.targets {
            let current = selected_installation(&model, &target.installation.id)?;
            if !same_path(&current.directory, &target.installation.directory)
                || !same_path(&current.executable, &target.installation.executable)
            {
                return Err(
                    "An installation identity changed. Discover and review it again.".into(),
                );
            }
        }
        review
    };
    if review.manifest_sha256 != installer_bundle::digest(PINNED_MANIFEST) {
        return Err("The reviewed release changed. Reopen the instance manager.".into());
    }
    let lease = state.admission.try_start().map_err(str::to_owned)?;
    let operation = state.operation_id("install");
    {
        let mut model = state.model.lock().map_err(err)?;
        model.error.clear();
        model.install = json!({"id":operation,"status":"running","total":review.targets.len(),
            "completed":0,"currentId":null,"stopRequested":false,
            "results":review.targets.iter().map(|target| json!({
                "installationId":target.installation.id,"status":"queued",
                "pluginDirectory":target.installation.directory.join("plugins/mo2_update_arsenal"),
                "message":"Waiting for this installation."})).collect::<Vec<_>>()});
    }
    // A durable journal is required before changing any target.
    if let Err(error) = state.persist() {
        state.model.lock().map_err(err)?.install["status"] = json!("failed");
        return Err(format!(
            "Could not save the installation journal; no target was changed: {error}"
        ));
    }
    notify(app);
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<Runtime>();
        let source = lock_bundle(&state.resource);
        {
            state
                .model
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .package = package_value(&source, &state.resource);
        }
        let mut failures = false;
        let mut stopped = false;
        for (index, target) in review.targets.into_iter().enumerate() {
            let cancelled = state.admission.stop_requested();
            if cancelled {
                stopped = true;
                let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
                model.install["results"][index]["status"] = json!("cancelled");
                model.install["results"][index]["message"] =
                    json!("Stopped before touching this installation.");
                continue;
            }
            {
                let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
                model.install["currentId"] = json!(target.installation.id);
                model.install["results"][index]["status"] = json!("installing");
                model.install["results"][index]["message"] =
                    json!("Rechecking, staging and verifying the matching plugin.");
            }
            notify(&handle);
            // If the journal cannot advance, stop before the next mutation.
            let transaction = state.persist().and_then(|()| {
                let bundle = source.as_ref().map_err(Clone::clone)?;
                let mut receipt =
                    run_installer(bundle, &target.installation, Some(&target.fingerprint))?;
                if receipt["ok"] == true
                    && let Err(error) = installer_bundle::verify_installed(
                        &bundle.verified,
                        &target.installation.directory,
                    )
                {
                    receipt["ok"] = json!(false);
                    receipt["status"] = json!("failed");
                    receipt["message"] = json!(error);
                }
                Ok(receipt)
            });
            {
                let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
                let mut result = match transaction {
                    Ok(receipt) => {
                        let success = receipt["ok"] == true;
                        failures |= !success;
                        let mut receipt = receipt;
                        if !success {
                            receipt["status"] = json!("failed");
                        }
                        receipt
                    }
                    Err(error) => {
                        failures = true;
                        json!({"status":"failed","message":error,
                            "pluginDirectory":target.installation.directory.join("plugins/mo2_update_arsenal")})
                    }
                };
                result["installationId"] = json!(target.installation.id);
                let success = matches!(result["status"].as_str(), Some("installed" | "updated"));
                model.install["results"][index] = result;
                model.install["completed"] = json!(index + 1);
                if success
                    && let Some(installation) = model
                        .inventory
                        .installations
                        .iter_mut()
                        .find(|item| item.id == target.installation.id)
                {
                    installation.plugin.version = Some(VERSION.into());
                    installation.plugin.status = "matching".into();
                }
            }
            if let Err(error) = state.persist() {
                state.admission.request_stop();
                record_error(
                    &handle,
                    format!(
                        "Installation result could not be saved; remaining targets will stop. {error}"
                    ),
                );
            }
            notify(&handle);
        }
        {
            let mut model = state.model.lock().unwrap_or_else(|e| e.into_inner());
            model.install["status"] = json!(if failures {
                "failed"
            } else if stopped {
                "cancelled"
            } else {
                "complete"
            });
            model.install["currentId"] = Value::Null;
            model.install["stopRequested"] = json!(state.admission.stop_requested());
        }
        save_or_warn(&handle);
        drop(lease);
        notify(&handle);
    });
    Ok(())
}

/// Serialize application reset with installation admission.
pub(crate) fn with_idle_installation<T>(
    app: &tauri::AppHandle,
    action: impl FnOnce() -> Result<T>,
) -> Result<T> {
    let Some(state) = app.try_state::<Runtime>() else {
        return action();
    };
    let _gate = state.commands.lock().map_err(err)?;
    if state.admission.installing() || state.admission.closing() {
        return Err("Finish the current MO2 plugin installation or close operation before resetting Modhunter.".into());
    }
    action()
}

fn execute(app: &tauri::AppHandle, kind: &str, value: Value) -> Result<Value> {
    let state = app.state::<Runtime>();
    if kind == "state" {
        return Ok(json!({"ok":true,"state":state.snapshot(app)}));
    }
    let _gate = state.commands.lock().map_err(err)?;
    if state.admission.closing() || crate::watchtower_windows::reset_pending(app) {
        return Err("Modhunter is closing. Reopen it before starting another operation.".into());
    }
    match kind {
        "scan" => {
            let spec: ScanSpec = serde_json::from_value(value).map_err(err)?;
            if spec.mode == ScanMode::Known && !spec.roots.is_empty() {
                return Err("Known-location discovery uses native hints. Choose a drive or add a folder for a deeper search.".into());
            }
            if spec.mode == ScanMode::Deep {
                let model = state.model.lock().map_err(err)?;
                if spec.roots.is_empty()
                    || spec
                        .roots
                        .iter()
                        .any(|path| !model.roots.iter().any(|root| same_path(&root.path, path)))
                {
                    return Err(
                        "Choose scan locations from the native drives or add a folder.".into(),
                    );
                }
            }
            start_scan(app, spec)?;
        }
        "cancelScan" => {
            let requested = field(&value, "operationId")?;
            let mut model = state.model.lock().map_err(err)?;
            if model.scan["id"].as_str() != Some(requested) || !active_scan(&model.scan) {
                return Err("That discovery operation is no longer active.".into());
            }
            let current = state.scan_cancel.lock().map_err(err)?;
            if let Some((operation, cancel)) = current.as_ref() {
                if operation != requested {
                    return Err("That discovery operation is no longer active.".into());
                }
                cancel.store(true, Ordering::Release);
                model.scan["status"] = json!("cancelling");
            }
        }
        "addFolder" => {
            if state.admission.installing() || active_scan(&state.model.lock().map_err(err)?.scan) {
                return Err(
                    "Finish or stop the current operation before adding a location.".into(),
                );
            }
            if let Some(file) = app
                .dialog()
                .file()
                .set_title(crate::watchtower_windows::localized(
                    app,
                    "Choose a folder to discover MO2",
                ))
                .blocking_pick_folder()
            {
                let chosen = file.into_path().map_err(err)?;
                installer_bundle::reject_link_ancestors(&chosen)?;
                let path = fs::canonicalize(chosen).map_err(err)?;
                installer_bundle::reject_link_ancestors(&path)?;
                {
                    let mut model = state.model.lock().map_err(err)?;
                    if !model.remembered.iter().any(|old| same_path(old, &path)) {
                        model.remembered.push(path.clone());
                    }
                    append_root(&mut model.roots, &path);
                    model.review = None;
                }
                state.persist()?;
                start_scan(
                    app,
                    ScanSpec {
                        mode: ScanMode::Deep,
                        roots: vec![path],
                    },
                )?;
            }
        }
        "previewInstall" => return preview(app, &value),
        "install" => start_install(app, field(&value, "token")?)?,
        "cancelInstall" => {
            let requested = field(&value, "operationId")?;
            let mut model = state.model.lock().map_err(err)?;
            if model.install["id"].as_str() != Some(requested)
                || !state.admission.installing()
                || !mark_stopping(&mut model.install)
            {
                return Err("That installation operation is no longer active.".into());
            }
            state.admission.request_stop();
        }
        "connect" => {
            let id = field(&value, "sessionId")?;
            let session = state
                .model
                .lock()
                .map_err(err)?
                .sessions
                .iter()
                .find(|session| session["id"].as_str() == Some(id))
                .cloned()
                .ok_or("That live session is no longer in the verified inventory.")?;
            crate::windows::connect_verified_instance(
                app,
                field(&session, "endpoint")?,
                field(&session, "profileId")?,
            )?;
        }
        "launch" | "openFolder" => {
            if state.admission.installing() {
                return Err("Wait for plugin installation to finish before opening MO2.".into());
            }
            let model = state.model.lock().map_err(err)?;
            let installation = selected_installation(&model, field(&value, "installationId")?)?;
            installer_bundle::reject_link_ancestors(&installation.executable)?;
            if kind == "openFolder" {
                app.opener()
                    .open_path(installation.directory.to_string_lossy(), None::<&str>)
                    .map_err(err)?;
            } else {
                let mut command = Command::new(&installation.executable);
                command.current_dir(&installation.directory);
                if let Some(id) = value["instanceId"].as_str().filter(|id| !id.is_empty()) {
                    if !model.inventory.associations.iter().any(|association| {
                        association.installation_id == installation.id
                            && association.instance_id == id
                    }) {
                        return Err("This MO2 application cannot open that instance.".into());
                    }
                    let instance = model
                        .inventory
                        .instances
                        .iter()
                        .find(|instance| instance.id == id)
                        .ok_or("The selected instance is no longer available.")?;
                    if instance.kind == "portable" {
                        command.args(["-i", ""]);
                    } else if instance.kind == "global" {
                        if installation.directory.join("portable.txt").exists() {
                            return Err("This application is locked to its portable instance. Refresh discovery before choosing another instance.".into());
                        }
                        command.arg("-i").arg(&instance.name);
                    } else {
                        return Err("This external configuration has no verified MO2 launch choice. Open its folder and choose it in MO2.".into());
                    }
                }
                command
                    .spawn()
                    .map_err(|e| format!("Could not launch this MO2 application: {e}"))?;
            }
        }
        _ => return Err("Unknown MO2 Instances command.".into()),
    }
    notify(app);
    Ok(json!({"ok":true,"state":state.snapshot(app)}))
}

#[tauri::command]
pub(crate) async fn instances_command(
    app: tauri::AppHandle,
    webview: Webview,
    kind: String,
    value: Option<Value>,
) -> Result<Value> {
    if webview.label() != LABEL
        || !webview
            .url()
            .map_err(err)
            .is_ok_and(|url| crate::policy::local_asset(&url, "/instances.html"))
    {
        return Err(
            "MO2 installation commands are limited to the bundled Instances manager.".into(),
        );
    }
    tauri::async_runtime::spawn_blocking(move || {
        let result = execute(&app, &kind, value.unwrap_or(Value::Null));
        if result.is_err() {
            notify(&app);
        }
        result
    })
    .await
    .map_err(err)?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn late_stop_during_terminal_journal_save_cannot_restore_a_running_ui() {
        for status in ["complete", "failed", "cancelled"] {
            let admission = Arc::new(crate::install_admission::Admission::default());
            let lease = admission.try_start().unwrap();
            let record = Arc::new(Mutex::new(
                json!({"id":"current","status":"running","results":[]}),
            ));
            let saving = Arc::new(std::sync::Barrier::new(2));
            let finish = Arc::new(std::sync::Barrier::new(2));
            let worker_record = record.clone();
            let worker_saving = saving.clone();
            let worker_finish = finish.clone();
            let worker = std::thread::spawn(move || {
                worker_record.lock().unwrap()["status"] = json!(status);
                worker_saving.wait();
                worker_finish.wait();
                drop(lease);
            });
            saving.wait();
            assert!(admission.installing());
            assert_eq!(
                admission.request_close(),
                crate::install_admission::CloseDecision::FinishInstallation
            );
            assert!(!mark_stopping(&mut record.lock().unwrap()));
            assert_eq!(record.lock().unwrap()["status"], status);
            finish.wait();
            worker.join().unwrap();
            assert!(!admission.installing());
            assert_eq!(record.lock().unwrap()["status"], status);
        }
    }
    #[test]
    fn windows_native_identity_normalizes_extended_dos_and_unc_forms() {
        assert!(same_path(Path::new(r"\\?\C:\MO2"), Path::new(r"c:\mo2\")));
        assert!(same_path(
            Path::new(r"\\?\UNC\Server\Share\MO2"),
            Path::new(r"\\server\share\mo2\")
        ));
        assert!(!same_path(Path::new(r"C:\MO2"), Path::new(r"D:\MO2")));
        assert!(!same_path(
            Path::new(r"\\server\share\MO2"),
            Path::new(r"UNC\server\share\MO2")
        ));
    }
    #[test]
    fn cancelled_scope_never_erases_previous_native_observations() {
        let prior: Inventory = serde_json::from_value(json!({
            "installations":[{"id":"first","directory":"C:/A","executable":"C:/A/ModOrganizer.exe",
                "portableLocked":false,"plugin":{"status":"missing"},"runningPids":[],"sources":[]}],
            "instances":[],"associations":[],"issues":[],"scope":"known","complete":true
        })).unwrap();
        let found: Inventory = serde_json::from_value(json!({
            "installations":[{"id":"second","directory":"D:/B","executable":"D:/B/ModOrganizer.exe",
                "portableLocked":false,"plugin":{"status":"missing"},"runningPids":[],"sources":[]}],
            "instances":[],"associations":[],"issues":[],"scope":"D drive, cancelled","complete":false
        })).unwrap();
        let merged = merge_inventory(prior, found);
        assert_eq!(merged.installations.len(), 2);
        assert!(!merged.complete);
        assert_eq!(merged.scope, "D drive, cancelled");
    }
    #[test]
    fn cancelling_during_profile_enumeration_retains_unvisited_profiles_and_new_findings() {
        let profiles: Vec<_> = (0..1205)
            .map(|index| {
                json!({
                    "name":format!("Profile {index}"),"path":format!("C:/MO2/profiles/{index}"),
                    "modlistPath":format!("C:/MO2/profiles/{index}/modlist.txt"),"selected":index==0
                })
            })
            .collect();
        let mut source = json!({
            "installations":[],
            "instances":[{"id":"portable","name":"MO2","kind":"portable",
                "configPath":"C:/MO2/ModOrganizer.ini","profiles":profiles,"sources":[]}],
            "associations":[],"issues":[],"scope":"known","complete":true
        });
        let prior: Inventory = serde_json::from_value(source.clone()).unwrap();
        source["instances"][0]["profiles"]
            .as_array_mut()
            .unwrap()
            .truncate(7);
        source["instances"][0]["profiles"]
            .as_array_mut()
            .unwrap()
            .push(json!({
                "name":"New profile","path":"C:/MO2/profiles/new",
                "modlistPath":"C:/MO2/profiles/new/modlist.txt","selected":false
            }));
        source["complete"] = json!(false);
        let found: Inventory = serde_json::from_value(source).unwrap();
        let merged = merge_inventory(prior, found);
        assert_eq!(merged.instances[0].profiles.len(), 1206);
        assert!(
            merged.instances[0]
                .profiles
                .iter()
                .any(|profile| profile.name == "Profile 1204")
        );
        assert!(
            merged.instances[0]
                .profiles
                .iter()
                .any(|profile| profile.name == "New profile")
        );
        assert!(!merged.complete);
    }
}
