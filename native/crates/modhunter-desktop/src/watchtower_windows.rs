//! Website sessions and rendered checks are separate from every MO2/game webview.
use crate::watchtower_policy as policy;
use modhunter_core::watchtower::{
    CheckJob, PageObservation, Session, WatchStore, canonical_page_url, page_origin,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, VecDeque},
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::windows::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{
    Emitter, Manager, Webview, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    webview::{NewWindowFeatures, NewWindowResponse, PermissionResponse},
};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
use url::Url;
use windows_core::Interface;

type Result<T> = std::result::Result<T, String>;
const MAX_CHECKS: usize = 2;
const CHECK_DEADLINE: Duration = Duration::from_secs(45);

#[path = "watchtower_acquisition.rs"]
mod acquisition;

#[path = "watchtower_smoke.rs"]
mod smoke;
pub use smoke::start as start_smoke;

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

#[derive(Clone)]
struct Active {
    job: CheckJob,
    host: String,
    cancel: Arc<AtomicBool>,
}
#[derive(Clone)]
struct Starting {
    host: String,
    session_id: Option<String>,
    cancel: Arc<AtomicBool>,
}
#[derive(Default)]
struct Work {
    queued: VecDeque<String>,
    starting: BTreeMap<String, Starting>,
    active: BTreeMap<String, Active>,
    host_ready: BTreeMap<String, Instant>,
}
#[derive(Clone)]
struct SiteWindow {
    session_id: Option<String>,
    generation: u64,
    interactive: bool,
    directory: PathBuf,
    origin: String,
}
#[derive(Clone)]
struct SiteContext {
    session: Option<Session>,
    origin: String,
    // Set only by the private, internally owned smoke server. No command accepts it.
    fixture_origin: Option<String>,
    lease: Option<CheckJob>,
    download_watch: Option<(String, u64)>,
    cancellation: Option<Arc<AtomicBool>>,
}
impl SiteContext {
    fn production(session: Option<Session>, url: &Url) -> Result<Self> {
        if !policy::website_navigation(url) {
            return Err("Tracked pages and site sessions require a public HTTPS address.".into());
        }
        let origin = page_origin(url.as_str())?;
        if session.as_ref().is_some_and(|s| s.origin != origin) {
            return Err("This page belongs to a different site session.".into());
        }
        Ok(Self {
            session,
            origin,
            fixture_origin: None,
            lease: None,
            download_watch: None,
            cancellation: None,
        })
    }
    fn current(&self, state: &WatchRuntime) -> bool {
        !state.stopping.load(Ordering::Acquire)
            && self
                .cancellation
                .as_ref()
                .is_none_or(|cancel| !cancel.load(Ordering::Acquire))
            && self
                .lease
                .as_ref()
                .is_none_or(|job| state.store.is_current(job))
            && (self.fixture_origin.is_some()
                || self.session.as_ref().is_none_or(|session| {
                    state.store.session(&session.id).is_some_and(|current| {
                        current.storage_generation == session.storage_generation
                    })
                }))
    }
}
#[derive(Default)]
struct Load {
    navigation: Mutex<policy::NavigationProgress>,
    native_error: Mutex<Option<String>>,
}
pub struct WatchRuntime {
    store: WatchStore,
    work: Mutex<Work>,
    windows: Mutex<BTreeMap<String, SiteWindow>>,
    prefill: Mutex<Option<Value>>,
    profiles: PathBuf,
    ephemeral: tempfile::TempDir,
    ephemeral_owner: Mutex<Option<File>>,
    _ownership: Option<File>,
    ownership_error: String,
    sequence: AtomicU64,
    revision: AtomicU64,
    pumping: AtomicBool,
    stopping: AtomicBool,
    last_notification: Mutex<Option<Instant>>,
    notified_hashes: Mutex<BTreeMap<String, String>>,
    retired: Mutex<Vec<PathBuf>>,
    runtime_warning: Mutex<String>,
}

impl WatchRuntime {
    fn writable(&self) -> Result<()> {
        if self.stopping.load(Ordering::Acquire) {
            return Err("Watchtower is closing.".into());
        }
        if !self.ownership_error.is_empty() {
            return Err(self.ownership_error.clone());
        }
        Ok(())
    }
    fn snapshot(&self) -> Value {
        let mut snapshot = self.store.snapshot();
        let work = self.work.lock().unwrap_or_else(|e| e.into_inner());
        snapshot["work"] = json!({"active":work.active.keys().chain(work.starting.keys()).collect::<Vec<_>>(),"queued":work.queued.iter().collect::<Vec<_>>()});
        drop(work);
        snapshot["runtimeWarning"] = json!(
            self.runtime_warning
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .as_str()
        );
        snapshot["storeRevision"] = snapshot.get("revision").cloned().unwrap_or(Value::Null);
        snapshot["revision"] = json!(self.revision.fetch_add(1, Ordering::AcqRel) + 1);
        snapshot["prefill"] = self
            .prefill
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
            .unwrap_or(Value::Null);
        snapshot["version"] = json!(env!("CARGO_PKG_VERSION"));
        if !self.ownership_error.is_empty() {
            snapshot["readOnly"] = json!(true);
            snapshot["runtimeWarning"] = json!(self.ownership_error);
        }
        snapshot
    }
    fn profile_dir(&self, session: Option<&Session>, origin: &str) -> Result<PathBuf> {
        let directory = match session {
            Some(s) => {
                if s.id.is_empty()
                    || s.id.len() > 100
                    || !s
                        .id
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
                {
                    return Err("This site session has an invalid storage identity.".into());
                }
                let base = if s.remember {
                    self.profiles.clone()
                } else {
                    self.ephemeral.path().join("sessions")
                };
                base.join(&s.id)
                    .join(format!("generation-{}", s.storage_generation))
            }
            None => self
                .ephemeral
                .path()
                .join("anonymous")
                .join(format!("{:x}", Sha256::digest(origin.as_bytes()))),
        };
        std::fs::create_dir_all(&directory).map_err(err)?;
        // An owned marker distinguishes our retired browser generations from arbitrary directories.
        let marker = directory.join("modhunter-profile-owner.json");
        if !marker.exists() {
            std::fs::write(
                &marker,
                b"{\"application\":\"com.modhunter.desktop.watchtower\",\"schema\":1}",
            )
            .map_err(err)?;
        }
        Ok(directory)
    }
}

pub fn setup(app: &tauri::AppHandle) -> Result<()> {
    let config = app.path().app_config_dir().map_err(err)?;
    let data = app.path().app_data_dir().map_err(err)?;
    setup_paths(app, config, data, true)
}

fn setup_paths(
    app: &tauri::AppHandle,
    config: PathBuf,
    data: PathBuf,
    scheduler: bool,
) -> Result<()> {
    std::fs::create_dir_all(&config).map_err(err)?;
    let ownership = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .share_mode(0)
        .open(config.join("watchtower.lock"));
    let (ownership,ownership_error)=match ownership {
        Ok(file)=>(Some(file),String::new()),
        Err(_)=>(None,"Another Modhunter app is using Watchtower. Close that app, then reopen this instance to change or check pages.".into()),
    };
    let profiles = data.join("watchtower-site-profiles");
    let temporary = data.join("watchtower-temporary-profiles");
    std::fs::create_dir_all(&temporary).map_err(err)?;
    if ownership.is_some() {
        cleanup_abandoned(&temporary);
    }
    let ephemeral = tempfile::Builder::new()
        .prefix("run-")
        .tempdir_in(&temporary)
        .map_err(err)?;
    std::fs::write(ephemeral.path().join("run-owner.json"),serde_json::to_vec(&json!({"application":"com.modhunter.desktop.watchtower","schema":1,"pid":std::process::id()})).map_err(err)?).map_err(err)?;
    let ephemeral_owner = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(0)
        .open(ephemeral.path().join("owner.lock"))
        .map_err(err)?;
    let store = if ownership.is_some() {
        WatchStore::open(config.join("watchtower.json"))
    } else {
        WatchStore::open_read_only(config.join("watchtower.json"))
    };
    app.manage(WatchRuntime {
        store,
        work: Mutex::new(Work::default()),
        windows: Mutex::new(BTreeMap::new()),
        prefill: Mutex::new(None),
        profiles,
        ephemeral,
        ephemeral_owner: Mutex::new(Some(ephemeral_owner)),
        _ownership: ownership,
        ownership_error,
        sequence: AtomicU64::new(1),
        revision: AtomicU64::new(0),
        pumping: AtomicBool::new(false),
        stopping: AtomicBool::new(false),
        last_notification: Mutex::new(None),
        notified_hashes: Mutex::new(BTreeMap::new()),
        retired: Mutex::new(Vec::new()),
        runtime_warning: Mutex::new(String::new()),
    });
    acquisition::setup(app, data.join("watchtower-acquisitions"));
    recover_retired(&app.state::<WatchRuntime>());
    if !scheduler {
        return Ok(());
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        let mut first = true;
        let mut scanned = Instant::now();
        loop {
            let state = handle.state::<WatchRuntime>();
            if state.stopping.load(Ordering::Acquire) {
                break;
            }
            if state.writable().is_ok() {
                let settings = state.store.settings();
                if (first && settings.check_on_launch)
                    || (settings.background_checking
                        && (first || scanned.elapsed() >= Duration::from_secs(10)))
                {
                    let candidates = state.store.check_candidates(now(), true);
                    queue(&handle, candidates);
                    scanned = Instant::now();
                }
                pump(&handle);
                cleanup_retired(&state);
            }
            first = false;
            std::thread::sleep(Duration::from_secs(1));
        }
    });
    Ok(())
}

fn notify(app: &tauri::AppHandle) {
    let mut snapshot = app.state::<WatchRuntime>().snapshot();
    snapshot["acquisitions"] = acquisition::snapshot(app);
    let _ = app.emit_to("watchtower", "watchtower-state", snapshot);
}

pub fn open(app: &tauri::AppHandle, prefill: Option<Value>) -> Result<()> {
    if let Some(mut value) = prefill {
        value["revision"] = json!(
            app.state::<WatchRuntime>()
                .sequence
                .fetch_add(1, Ordering::AcqRel)
        );
        *app.state::<WatchRuntime>().prefill.lock().map_err(err)? = Some(value);
    }
    if let Some(window) = app.get_webview_window("watchtower") {
        window.show().map_err(err)?;
        window.set_focus().map_err(err)?;
        notify(app);
        return Ok(());
    }
    WebviewWindowBuilder::new(app, "watchtower", WebviewUrl::App("watchtower.html".into()))
        .title("Modhunter · Watchtower")
        .inner_size(1280.0, 880.0)
        .min_inner_size(760.0, 560.0)
        .on_navigation(|url| crate::policy::local_asset(url, "/watchtower.html"))
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .build()
        .map_err(err)?;
    Ok(())
}

fn id(value: &Value) -> Result<&str> {
    value
        .get("id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or("Choose a saved page or site session.".into())
}

fn ids(value: &Value) -> Result<Vec<String>> {
    if let Some(items) = value.get("ids") {
        let items = items
            .as_array()
            .filter(|items| !items.is_empty() && items.len() <= 10_000)
            .ok_or("Choose between 1 and 10,000 saved pages.")?;
        let mut ids = Vec::with_capacity(items.len());
        for item in items {
            let id = item
                .as_str()
                .filter(|id| !id.is_empty() && id.len() <= 100)
                .ok_or("Invalid saved page identity.")?;
            ids.push(id.to_owned());
        }
        ids.sort();
        ids.dedup();
        Ok(ids)
    } else {
        Ok(vec![id(value)?.to_owned()])
    }
}

fn record_runtime_warning(state: &WatchRuntime, message: String) {
    *state
        .runtime_warning
        .lock()
        .unwrap_or_else(|e| e.into_inner()) = message;
}

fn queue(app: &tauri::AppHandle, ids: Vec<String>) {
    let state = app.state::<WatchRuntime>();
    if state.writable().is_err() {
        return;
    }
    let ids: Vec<_> = ids
        .into_iter()
        .filter(|id| state.store.watch_eligible(id))
        .collect();
    let mut changed = false;
    if let Ok(mut work) = state.work.lock() {
        let mut pending: std::collections::HashSet<_> = work.queued.iter().cloned().collect();
        for id in ids {
            let available = work
                .active
                .get(&id)
                .is_none_or(|a| a.cancel.load(Ordering::Acquire))
                && work
                    .starting
                    .get(&id)
                    .is_none_or(|s| s.cancel.load(Ordering::Acquire));
            if available && pending.insert(id.clone()) {
                work.queued.push_back(id);
                changed = true;
            }
        }
    }
    if changed {
        notify(app);
        pump(app);
    }
}

struct PumpGuard<'a>(&'a AtomicBool);
impl Drop for PumpGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

fn pump(app: &tauri::AppHandle) {
    let state = app.state::<WatchRuntime>();
    if state.writable().is_err() || state.pumping.swap(true, Ordering::AcqRel) {
        return;
    }
    let _guard = PumpGuard(&state.pumping);
    let attempts = state.work.lock().map(|w| w.queued.len()).unwrap_or(0);
    let mut changed = false;
    for _ in 0..attempts {
        // Dequeue and expose a cancellable reservation under one lock. A cancel
        // cannot lose a request while durable begin_check runs outside this lock.
        let candidate = {
            let Ok(mut work) = state.work.lock() else {
                return;
            };
            if state.stopping.load(Ordering::Acquire)
                || work.active.len() + work.starting.len() >= MAX_CHECKS
            {
                break;
            }
            let Some(id) = work.queued.pop_front() else {
                break;
            };
            if !state.store.watch_eligible(&id) {
                changed = true;
                continue;
            }
            let Some((url, session_id)) = state.store.watch_location(&id) else {
                changed = true;
                continue;
            };
            let host = Url::parse(&url)
                .ok()
                .and_then(|u| {
                    u.host_str()
                        .map(|h| h.trim_start_matches("www.").to_owned())
                })
                .unwrap_or_default();
            let human = state
                .windows
                .lock()
                .map(|views| {
                    views.values().any(|v| {
                        v.interactive
                            && v.session_id == session_id
                            && (session_id.is_some()
                                || page_origin(&url).is_ok_and(|origin| v.origin == origin))
                    })
                })
                .unwrap_or(true);
            let blocked = work.active.contains_key(&id)
                || work.starting.contains_key(&id)
                || work.active.values().any(|a| a.host == host)
                || work.starting.values().any(|s| s.host == host)
                || work
                    .host_ready
                    .get(&host)
                    .is_some_and(|t| *t > Instant::now());
            if human || blocked {
                work.queued.push_back(id);
                continue;
            }
            let starting = Starting {
                host,
                session_id,
                cancel: Arc::new(AtomicBool::new(false)),
            };
            work.starting.insert(id.clone(), starting.clone());
            (id, starting)
        };
        let (id, starting) = candidate;
        if starting.cancel.load(Ordering::Acquire) || state.stopping.load(Ordering::Acquire) {
            if let Ok(mut work) = state.work.lock() {
                work.starting.remove(&id);
            }
            changed = true;
            continue;
        }
        let job = match state.store.begin_check(&id, now()) {
            Ok(job) => job,
            Err(error) => {
                if let Ok(mut work) = state.work.lock() {
                    work.starting.remove(&id);
                }
                if state.store.watch_eligible(&id) && !starting.cancel.load(Ordering::Acquire) {
                    record_runtime_warning(
                        &state,
                        format!("A page check could not start: {error}"),
                    );
                }
                changed = true;
                continue;
            }
        };
        changed = true;
        let actual_host = Url::parse(&job.url)
            .ok()
            .and_then(|url| {
                url.host_str()
                    .map(|host| host.trim_start_matches("www.").to_owned())
            })
            .unwrap_or_default();
        let context_matches = actual_host == starting.host && job.session_id == starting.session_id;
        let cancel = starting.cancel;
        let launch = if let Ok(mut work) = state.work.lock() {
            work.starting.remove(&id);
            if cancel.load(Ordering::Acquire)
                || state.stopping.load(Ordering::Acquire)
                || !state.store.is_current(&job)
                || !context_matches
            {
                false
            } else {
                work.active.insert(
                    id.clone(),
                    Active {
                        job: job.clone(),
                        host: starting.host.clone(),
                        cancel: cancel.clone(),
                    },
                );
                true
            }
        } else {
            false
        };
        if !launch {
            if let Err(error)=state.store.fail_check(&job,"cancelled","Check cancelled before browser navigation; the last complete observation is retained.",now()){
                record_runtime_warning(&state,format!("The browser check was stopped, but cancellation could not be saved: {error}. Resolve the storage issue and cancel the page again."));
            }
            continue;
        }
        let handle = app.clone();
        let host = starting.host;
        std::thread::spawn(move || {
            let result = if cancel.load(Ordering::Acquire) {
                Err("Page check cancelled before browser creation.".into())
            } else {
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(||run_check(&handle,&job,&cancel)))
                    .unwrap_or_else(|_|Err("The page check stopped unexpectedly; its last successful result is retained.".into()))
            };
            finish_check(&handle, &job, result, &cancel);
            let state = handle.state::<WatchRuntime>();
            if let Ok(mut work) = state.work.lock() {
                if work.active.get(&job.watch_id).is_some_and(|active| {
                    active.job.operation_id == job.operation_id
                        && active.job.generation == job.generation
                }) {
                    work.active.remove(&job.watch_id);
                }
                let seconds = if host == "patreon.com" || host == "loverslab.com" {
                    3
                } else {
                    1
                };
                work.host_ready
                    .insert(host, Instant::now() + Duration::from_secs(seconds));
            }
            notify(&handle);
            pump(&handle);
        });
    }
    if changed {
        notify(app);
    }
}

fn cancel(app: &tauri::AppHandle, which: Option<&str>) -> Result<()> {
    cancel_selected(app, which.map(|id| vec![id.to_owned()]).as_deref(), true)
}

fn cancel_selected(
    app: &tauri::AppHandle,
    selected: Option<&[String]>,
    persist: bool,
) -> Result<()> {
    let state = app.state::<WatchRuntime>();
    let selected_set = selected.map(|ids| {
        ids.iter()
            .map(String::as_str)
            .collect::<std::collections::HashSet<_>>()
    });
    let matches = |id: &str| selected_set.as_ref().is_none_or(|ids| ids.contains(id));
    let (active, owned) = {
        let mut work = state.work.lock().map_err(err)?;
        work.queued.retain(|id| !matches(id));
        let mut owned = std::collections::HashSet::new();
        // Token writes stay inside the same lock that promotes reservations.
        for (id, starting) in &work.starting {
            if matches(id) {
                starting.cancel.store(true, Ordering::Release);
                owned.insert(id.clone());
            }
        }
        let active = work
            .active
            .values()
            .filter(|item| matches(&item.job.watch_id))
            .cloned()
            .collect::<Vec<_>>();
        for item in &active {
            item.cancel.store(true, Ordering::Release);
            owned.insert(item.job.watch_id.clone());
        }
        (active, owned)
    };
    let mut errors = Vec::new();
    if persist {
        for item in active {
            if let Err(error) = state.store.fail_check(
                &item.job,
                "cancelled",
                "Check cancelled; the last complete observation is retained.",
                now(),
            ) {
                errors.push(error);
            }
        }
        // A prior persistence failure can leave a durable checking row after its
        // native worker has gone. An explicit user cancellation can clear it.
        let orphans = state
            .store
            .checking_ids()
            .into_iter()
            .filter(|id| matches(id) && !owned.contains(id))
            .collect::<Vec<_>>();
        if !orphans.is_empty()
            && let Err(error) = state
                .store
                .command("cancel", &json!({"ids":orphans}), now())
        {
            errors.push(error);
        }
    }
    if errors.is_empty() {
        if persist {
            record_runtime_warning(&state, String::new());
        }
        notify(app);
        Ok(())
    } else {
        let message = format!(
            "Browser work was stopped, but its cancellation could not be saved: {}. Resolve the storage issue, then cancel the page again.",
            errors.join("; ")
        );
        record_runtime_warning(&state, message.clone());
        notify(app);
        Err(message)
    }
}

fn cancel_session_work(app: &tauri::AppHandle, session_id: &str, persist: bool) -> Result<()> {
    let state = app.state::<WatchRuntime>();
    let ids = {
        let work = state.work.lock().map_err(err)?;
        let mut ids = work
            .active
            .values()
            .filter(|item| item.job.session_id.as_deref() == Some(session_id))
            .map(|item| item.job.watch_id.clone())
            .collect::<std::collections::BTreeSet<_>>();
        ids.extend(
            work.starting
                .iter()
                .filter(|(_, item)| item.session_id.as_deref() == Some(session_id))
                .map(|(id, _)| id.clone()),
        );
        ids.extend(
            work.queued
                .iter()
                .filter(|id| {
                    state
                        .store
                        .watch_location(id)
                        .is_some_and(|(_, session)| session.as_deref() == Some(session_id))
                })
                .cloned(),
        );
        ids.into_iter().collect::<Vec<_>>()
    };
    cancel_selected(app, Some(&ids), persist)
}

fn cancel_session(app: &tauri::AppHandle, session_id: &str) -> Result<()> {
    cancel_session_work(app, session_id, false)
}

fn cancel_ineligible(app: &tauri::AppHandle) -> Result<()> {
    let state = app.state::<WatchRuntime>();
    {
        let mut work = state.work.lock().map_err(err)?;
        work.queued.retain(|id| state.store.watch_eligible(id));
        for (id, starting) in &work.starting {
            if !state.store.watch_eligible(id) {
                starting.cancel.store(true, Ordering::Release);
            }
        }
        for active in work.active.values() {
            if !state.store.is_current(&active.job) {
                active.cancel.store(true, Ordering::Release);
            }
        }
    }
    notify(app);
    Ok(())
}

fn close_session(app: &tauri::AppHandle, session_id: &str) {
    let labels = app
        .state::<WatchRuntime>()
        .windows
        .lock()
        .map(|w| {
            w.iter()
                .filter(|(_, v)| v.session_id.as_deref() == Some(session_id))
                .map(|(label, _)| label.clone())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for label in labels {
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.close();
        }
    }
}

fn site_window(
    app: &tauri::AppHandle,
    context: SiteContext,
    url: Url,
    interactive: bool,
    features: Option<NewWindowFeatures>,
    load: Arc<Load>,
) -> Result<WebviewWindow> {
    let state = app.state::<WatchRuntime>();
    if !context.current(&state) {
        return Err("The browser operation was cancelled or its site session changed.".into());
    }
    if state.windows.lock().map_err(err)?.len() >= 16 {
        return Err("Close a website window before opening another.".into());
    }
    let session = context.session.clone();
    let generation = session.as_ref().map_or(0, |s| s.storage_generation);
    let session_id = session.as_ref().map(|s| s.id.clone());
    let serial = state.sequence.fetch_add(1, Ordering::AcqRel);
    let label = format!("site-{serial}");
    let directory = state.profile_dir(session.as_ref(), &context.origin)?;
    let nav_app = app.clone();
    let nav_context = context.clone();
    let popup_app = app.clone();
    let popup_context = context.clone();
    let initial = Url::parse("about:blank").expect("literal");
    let popup = features.is_some();
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(initial))
        .title(if interactive {
            "Modhunter · Website sign-in and review"
        } else {
            "Modhunter · Checking a watched page"
        })
        .inner_size(1050.0, 790.0)
        .min_inner_size(500.0, 500.0)
        .visible(interactive)
        .focused(interactive)
        .data_directory(directory.clone())
        .on_permission_request(|_, _| PermissionResponse::Deny)
        .on_navigation(move |url| {
            let live = nav_context.current(&nav_app.state::<WatchRuntime>());
            let allowed = match &nav_context.fixture_origin {
                Some(origin) => url.origin().ascii_serialization() == *origin,
                None => policy::website_navigation(url),
            };
            live && (allowed || url.as_str() == "about:blank")
        })
        .on_page_load(move |view, event| {
            if interactive && matches!(event.url().scheme(), "http" | "https") {
                // A site's HTML cannot disguise the origin in the native title;
                // OAuth query strings and tokens are deliberately excluded.
                let _ = view.set_title(&format!(
                    "Modhunter · {}",
                    event.url().origin().ascii_serialization()
                ));
            }
        })
        .on_new_window(move |target, features| {
            let allowed = target.as_str() == "about:blank"
                || match &popup_context.fixture_origin {
                    Some(origin) => target.origin().ascii_serialization() == *origin,
                    None => policy::website_navigation(&target),
                };
            if !interactive
                || !allowed
                || !popup_context.current(&popup_app.state::<WatchRuntime>())
            {
                return NewWindowResponse::Deny;
            }
            let count = popup_app
                .state::<WatchRuntime>()
                .windows
                .lock()
                .map(|w| {
                    w.values()
                        .filter(|v| {
                            v.session_id == popup_context.session.as_ref().map(|s| s.id.clone())
                        })
                        .count()
                })
                .unwrap_or(99);
            if count >= 4 {
                return NewWindowResponse::Deny;
            }
            match site_window(
                &popup_app,
                popup_context.clone(),
                Url::parse("about:blank").expect("literal"),
                true,
                Some(features),
                Arc::new(Load::default()),
            ) {
                Ok(window) => NewWindowResponse::Create { window },
                Err(_) => NewWindowResponse::Deny,
            }
        });
    if let Some(features) = features {
        builder = builder.window_features(features);
    }
    let window = builder.build().map_err(err)?;
    let native_load = load.clone();
    let download_app = app.clone();
    let download_context = context.clone();
    let download_label = label.clone();
    window
        .with_webview(move |platform| {
            let starting_load = native_load.clone();
            let completed_load = native_load.clone();
            let result = (|| -> windows_core::Result<()> {
                unsafe {
                    let webview = platform.controller().CoreWebView2()?;
                    acquisition::install(download_app.clone(), &webview, download_context.clone(), download_label.clone(), interactive)?;
                    let starting = webview2_com::NavigationStartingEventHandler::create(Box::new(
                        move |_, args| {
                            if let Some(args) = args {
                                let mut id = 0;
                                args.NavigationId(&mut id)?;
                                let mut uri = windows_core::PWSTR::null();
                                args.Uri(&mut uri)?;
                                let website = webview2_com::take_pwstr(uri) != "about:blank";
                                if let Ok(mut progress) = starting_load.navigation.lock() {
                                    progress.started(id, website);
                                }
                            }
                            Ok(())
                        },
                    ));
                    let mut token = 0;
                    webview.add_NavigationStarting(&starting, &mut token)?;
                    let completed = webview2_com::NavigationCompletedEventHandler::create(Box::new(
                        move |_, args| {
                            if let Some(args) = args {
                                let mut id = 0;
                                args.NavigationId(&mut id)?;
                                let mut success = windows_core::BOOL(0);
                                let valid = args.IsSuccess(&mut success).is_ok();
                                let mut status = 0;
                                let mut web_error = Default::default();
                                let _ = args.WebErrorStatus(&mut web_error);
                                if let Ok(extended) = args.cast::<webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2NavigationCompletedEventArgs2>() {
                                    let _ = extended.HttpStatusCode(&mut status);
                                }
                                if let Ok(mut progress) = completed_load.navigation.lock() {
                                    progress.complete(id, valid && success.as_bool(), u16::try_from(status).unwrap_or(0), web_error.0);
                                }
                            }
                            Ok(())
                        },
                    ));
                    webview.add_NavigationCompleted(&completed, &mut token)?;
                    Ok(())
                }
            })();
            if let Err(error) = result
                && let Ok(mut message) = native_load.native_error.lock()
            {
                *message = Some(format!("Could not observe browser navigation: {error}"));
            }
        })
        .map_err(|error| {
            let _ = window.close();
            err(error)
        })?;
    state.windows.lock().map_err(err)?.insert(
        label.clone(),
        SiteWindow {
            session_id,
            generation,
            interactive,
            directory,
            origin: context.origin.clone(),
        },
    );
    let close_app = app.clone();
    let close_label = label.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            if let Ok(mut windows) = close_app.state::<WatchRuntime>().windows.lock() {
                windows.remove(&close_label);
            }
            notify(&close_app);
        }
    });
    if !context.current(&state) {
        let _ = window.close();
        return Err("The browser operation was cancelled or its site session changed.".into());
    }
    if !popup {
        window.navigate(url).map_err(|error| {
            let _ = window.close();
            err(error)
        })?;
    }
    Ok(window)
}

fn eval_json(
    window: &WebviewWindow,
    script: String,
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<Value> {
    let (send, receive) = mpsc::sync_channel(1);
    window
        .eval_with_callback(script, move |value| {
            let result = if value.len() > policy::MAX_RESULT_BYTES {
                Err("The website returned an oversized result.".into())
            } else {
                serde_json::from_str(&value)
                    .map_err(|_| "The website did not return a readable page result.".to_string())
            };
            let _ = send.try_send(result);
        })
        .map_err(err)?;
    loop {
        if cancel.load(Ordering::Acquire) {
            return Err("Page check cancelled.".into());
        }
        if Instant::now() >= deadline {
            return Err(
                "The website took too long to respond. The last successful result is retained."
                    .into(),
            );
        }
        match receive.recv_timeout(Duration::from_millis(40)) {
            Ok(result) => return result,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(_) => return Err("The website closed before its result was available.".into()),
        }
    }
}

fn wait_loaded(
    window: &WebviewWindow,
    load: &Load,
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<()> {
    while Instant::now() < deadline {
        if cancel.load(Ordering::Acquire) {
            return Err("Page check cancelled.".into());
        }
        if let Some(error) = load.native_error.lock().map_err(err)?.as_ref() {
            return Err(error.clone());
        }
        let progress = load.navigation.lock().map_err(err)?.clone();
        if progress.website
            && progress.completed
            && window.url().map_err(err)?.as_str() != "about:blank"
        {
            let settled = Instant::now() + Duration::from_millis(350);
            while Instant::now() < settled.min(deadline) {
                if cancel.load(Ordering::Acquire) {
                    return Err("Page check cancelled.".into());
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            let current = load.navigation.lock().map_err(err)?.clone();
            if current == progress {
                if current.ready() {
                    return Ok(());
                }
                return Err(format!(
                    "The website connection failed or navigation was blocked (WebView2 error {}, HTTP {}). The last successful result is retained.",
                    current.web_error, current.http_status
                ));
            }
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    Err("The website did not finish loading in time. Open the page to review its connection or sign-in.".into())
}

struct WindowGuard(WebviewWindow);
impl Drop for WindowGuard {
    fn drop(&mut self) {
        let _ = self.0.close();
    }
}

fn capture(
    window: &WebviewWindow,
    load: &Load,
    url: &str,
    selector: &str,
    ignore: &[String],
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<PageObservation> {
    wait_loaded(window, load, cancel, deadline)?;
    let navigation = load.navigation.lock().map_err(err)?.clone();
    let mut before = window.url().map_err(err)?;
    before.set_fragment(None);
    let value = eval_json(
        window,
        policy::capture_script(url, selector, ignore),
        cancel,
        deadline.min(Instant::now() + Duration::from_secs(6)),
    )?;
    let mut result: PageObservation = serde_json::from_value(value)
        .map_err(|_| "The page capture was incomplete. Try a smaller page region.".to_string())?;
    result.http_status = Some(navigation.http_status).filter(|status| *status > 0);
    let mut after = window.url().map_err(err)?;
    after.set_fragment(None);
    if *load.navigation.lock().map_err(err)? != navigation
        || !navigation.ready()
        || before != after
        || result.final_url != before.as_str()
    {
        return Err("The page navigated during its check. The earlier baseline is retained; check the intended page again.".into());
    }
    Ok(result)
}

fn run_check(
    app: &tauri::AppHandle,
    job: &CheckJob,
    cancel: &Arc<AtomicBool>,
) -> Result<PageObservation> {
    if cancel.load(Ordering::Acquire) || !app.state::<WatchRuntime>().store.is_current(job) {
        return Err("This check has been superseded.".into());
    }
    let url = Url::parse(&canonical_page_url(&job.url)?).map_err(err)?;
    let load = Arc::new(Load::default());
    let mut context = SiteContext::production(job.session.clone(), &url)?;
    context.lease = Some(job.clone());
    context.cancellation = Some(cancel.clone());
    let window = WindowGuard(site_window(app, context, url, false, None, load.clone())?);
    let result = capture(
        &window.0,
        &load,
        &job.url,
        &job.selector,
        &job.ignore_selectors,
        cancel,
        Instant::now() + CHECK_DEADLINE,
    )?;
    if cancel.load(Ordering::Acquire) || !app.state::<WatchRuntime>().store.is_current(job) {
        return Err("This check has been superseded or cancelled.".into());
    }
    Ok(result)
}

fn finish_check(
    app: &tauri::AppHandle,
    job: &CheckJob,
    result: Result<PageObservation>,
    cancel: &AtomicBool,
) {
    let state = app.state::<WatchRuntime>();
    let store = &state.store;
    if !store.is_current(job) {
        return;
    }
    if cancel.load(Ordering::Acquire) {
        if let Err(error) = store.fail_check(
            job,
            "cancelled",
            "Check cancelled; the last complete observation is retained.",
            now(),
        ) {
            record_runtime_warning(
                &state,
                format!(
                    "The browser check stopped, but cancellation could not be saved: {error}. Resolve the storage issue and cancel the page again."
                ),
            );
        }
        return;
    }
    let previous = store.latest_hash(&job.watch_id);
    let accepted = match result {
        Ok(observation) => match store.complete_check(job, observation, now()) {
            Ok(accepted) => accepted,
            Err(error) => {
                let message = format!(
                    "The completed page result could not be saved. The last successful observation is retained. {error}"
                );
                if let Err(fallback) = store.fail_check(job, "error", &message, now()) {
                    record_runtime_warning(
                        &state,
                        format!(
                            "{message} The error could not be saved either: {fallback}. Resolve the storage issue, then cancel this page and retry."
                        ),
                    );
                }
                false
            }
        },
        Err(message) => {
            if let Err(error) = store.fail_check(job, "error", &message, now()) {
                record_runtime_warning(
                    &state,
                    format!(
                        "A page check failed, and its error could not be saved: {error}. Resolve the storage issue, then cancel this page and retry."
                    ),
                );
            }
            false
        }
    };
    let latest = store.latest_hash(&job.watch_id);
    if accepted
        && previous.is_some()
        && previous != latest
        && store.settings().notifications
        && let Some(hash) = latest
    {
        let mut hashes = state
            .notified_hashes
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if hashes.get(&job.watch_id) == Some(&hash) {
            return;
        }
        hashes.insert(job.watch_id.clone(), hash);
        drop(hashes);
        let mut last = state
            .last_notification
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if last.is_none_or(|time| time.elapsed() >= Duration::from_secs(60))
            && app
                .notification()
                .builder()
                .title("Modhunter Watchtower")
                .body("A watched page has new content. Open Watchtower to review it.")
                .show()
                .is_ok()
        {
            *last = Some(Instant::now());
        }
    }
}

fn cleanup_retired(state: &WatchRuntime) {
    let Ok(mut retired) = state.retired.lock() else {
        return;
    };
    retired.retain(|path| {
        let in_use = state
            .windows
            .lock()
            .map(|windows| windows.values().any(|window| window.directory == *path))
            .unwrap_or(true);
        if in_use {
            return true;
        }
        let valid = (path.starts_with(&state.profiles) || path.starts_with(state.ephemeral.path()))
            && plain_directory(path)
            && std::fs::read(path.join("modhunter-profile-owner.json"))
                .ok()
                .is_some_and(|b| {
                    b == b"{\"application\":\"com.modhunter.desktop.watchtower\",\"schema\":1}"
                });
        valid && std::fs::remove_dir_all(path).is_err()
    });
}

fn plain_directory(path: &Path) -> bool {
    std::fs::symlink_metadata(path)
        .is_ok_and(|meta| meta.is_dir() && meta.file_attributes() & 0x400 == 0)
}

fn process_alive(pid: u32) -> bool {
    if pid == std::process::id() {
        return true;
    }
    // A denied/unknown process is treated as live, so cleanup cannot race it.
    unsafe {
        use windows_sys::Win32::{
            Foundation::{CloseHandle, GetLastError},
            System::Threading::{
                GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
            },
        };
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process.is_null() {
            return GetLastError() != 87;
        }
        let mut code = 259;
        let known = GetExitCodeProcess(process, &mut code) != 0;
        CloseHandle(process);
        !known || code == 259
    }
}

fn cleanup_abandoned(root: &Path) {
    let Ok(entries) = std::fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !entry.file_name().to_string_lossy().starts_with("run-") || !plain_directory(&path) {
            continue;
        }
        let owner = File::open(path.join("run-owner.json"))
            .ok()
            .and_then(|file| {
                let mut bytes = Vec::new();
                file.take(513).read_to_end(&mut bytes).ok()?;
                if bytes.len() > 512 {
                    return None;
                }
                serde_json::from_slice::<Value>(&bytes).ok()
            });
        let Some(owner) = owner else {
            continue;
        };
        if owner["application"] != "com.modhunter.desktop.watchtower" || owner["schema"] != 1 {
            continue;
        }
        let Some(pid) = owner["pid"]
            .as_u64()
            .and_then(|pid| u32::try_from(pid).ok())
            .filter(|pid| *pid > 0)
        else {
            continue;
        };
        if process_alive(pid) {
            continue;
        }
        let Ok(lock) = OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(0)
            .open(path.join("owner.lock"))
        else {
            continue;
        };
        drop(lock);
        let _ = std::fs::remove_dir_all(path);
    }
}

fn recover_retired(state: &WatchRuntime) {
    if state.writable().is_err() {
        return;
    }
    let snapshot = state.store.snapshot();
    if snapshot.get("readOnly").and_then(Value::as_bool) != Some(false) {
        return;
    }
    let Ok(accounts) = std::fs::read_dir(&state.profiles) else {
        return;
    };
    let Ok(mut retired) = state.retired.lock() else {
        return;
    };
    for account in accounts.flatten() {
        if !plain_directory(&account.path()) {
            continue;
        }
        let id = account.file_name().to_string_lossy().into_owned();
        let session = state.store.session(&id);
        let Ok(generations) = std::fs::read_dir(account.path()) else {
            continue;
        };
        for entry in generations.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(generation) = name
                .strip_prefix("generation-")
                .and_then(|s| s.parse::<u64>().ok())
            else {
                continue;
            };
            if session
                .as_ref()
                .is_none_or(|s| !s.remember || s.storage_generation != generation)
            {
                retired.push(entry.path());
            }
        }
    }
}

fn session_changed(app: &tauri::AppHandle, previous: Option<Session>) {
    let Some(previous) = previous else {
        return;
    };
    let state = app.state::<WatchRuntime>();
    let current = state.store.session(&previous.id);
    if current
        .as_ref()
        .is_some_and(|s| s.storage_generation == previous.storage_generation)
    {
        return;
    }
    let _ = cancel_session(app, &previous.id);
    close_session(app, &previous.id);
    let base = if previous.remember {
        state.profiles.clone()
    } else {
        state.ephemeral.path().join("sessions")
    };
    let path = base
        .join(&previous.id)
        .join(format!("generation-{}", previous.storage_generation));
    if let Ok(mut paths) = state.retired.lock() {
        paths.push(path);
    }
}

fn open_session(app: &tauri::AppHandle, session_id: &str, page: Option<&str>) -> Result<()> {
    let state = app.state::<WatchRuntime>();
    state.writable()?;
    let session = state
        .store
        .session(session_id)
        .ok_or("This site session no longer exists.")?;
    cancel_session_work(app, session_id, true)?;
    let labels = state
        .windows
        .lock()
        .map_err(err)?
        .iter()
        .filter(|(_, v)| {
            v.interactive
                && v.session_id.as_deref() == Some(session_id)
                && v.generation == session.storage_generation
        })
        .map(|(label, _)| label.clone())
        .collect::<Vec<_>>();
    if page.is_none()
        && let Some(window) = labels
            .iter()
            .find_map(|label| app.get_webview_window(label))
    {
        window.show().map_err(err)?;
        window.set_focus().map_err(err)?;
        return Ok(());
    }
    let url = Url::parse(page.unwrap_or(&session.origin)).map_err(err)?;
    if page_origin(url.as_str())? != session.origin {
        return Err("This page belongs to a different site session.".into());
    }
    let context = SiteContext::production(Some(session), &url)?;
    site_window(app, context, url, true, None, Arc::new(Load::default()))?;
    state
        .store
        .command("sessionOpened", &json!({"id":session_id}), now())?;
    notify(app);
    Ok(())
}

fn write_export(app: &tauri::AppHandle, value: &Value) -> Result<Value> {
    let format = value
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("json");
    if !matches!(format, "json" | "csv") {
        return Err("Choose a JSON or CSV export.".into());
    }
    let output=app.state::<WatchRuntime>().store.command("export",&json!({"format":format,"includeHistory":value.get("includeHistory").and_then(Value::as_bool).unwrap_or(false)}),now())?;
    let folder = app.path().download_dir().map_err(err)?;
    std::fs::create_dir_all(&folder).map_err(err)?;
    let path = folder.join(format!("Modhunter-Watchtower-{}.{}", now(), format));
    let bytes = if format == "csv" {
        output
            .get("text")
            .or_else(|| output.get("csv"))
            .and_then(Value::as_str)
            .ok_or("No CSV export was generated.")?
            .as_bytes()
            .to_vec()
    } else {
        serde_json::to_vec_pretty(
            output
                .get("document")
                .or_else(|| output.get("export"))
                .unwrap_or(&output),
        )
        .map_err(err)?
    };
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&path)
        .map_err(err)?;
    file.write_all(&bytes).map_err(err)?;
    file.sync_all().map_err(err)?;
    Ok(json!({"message":format!("Watchtower export saved to {}",path.display())}))
}

fn execute(app: &tauri::AppHandle, kind: &str, value: Value) -> Result<Value> {
    let state = app.state::<WatchRuntime>();
    if kind == "state" {
        let mut snapshot = state.snapshot();
        snapshot["acquisitions"] = acquisition::snapshot(app);
        return Ok(json!({"ok":true,"state":snapshot}));
    }
    if kind == "close" {
        if let Some(window) = app.get_webview_window("watchtower") {
            window.close().map_err(err)?;
        }
        return Ok(json!({"ok":true}));
    }
    if !matches!(kind, "detail" | "export" | "exportFile") {
        state.writable()?;
    }
    let mut result = json!({});
    match kind {
        "approveDownload" | "rejectDownload" | "cancelDownload" | "removeDownload"
        | "verifyDownload" | "showDownload" | "resolveFiles" | "openOffer" => {
            result = acquisition::execute(app, kind, &value)?;
        }
        "check" => {
            let selected = ids(&value)?;
            if selected.len() > 5000 {
                return Err(
                    "Check at most 5,000 selected pages in one batch, or use Check all.".into(),
                );
            }
            queue(app, selected);
        }
        "checkAll" => {
            let ids = state.store.check_candidates(
                now(),
                value
                    .get("dueOnly")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            );
            queue(app, ids);
        }
        "cancel" => {
            let selected = ids(&value)?;
            cancel_selected(app, Some(&selected), true)?;
        }
        "cancelAll" => cancel(app, None)?,
        "openSession" => {
            open_session(app, id(&value)?, None)?;
            result["message"] = json!(
                "Sign in on the website. Return here and confirm the session when you are done. Some providers require their site's email/password flow rather than an embedded Google sign-in."
            );
        }
        "closeSession" => close_session(app, id(&value)?),
        "openPage" | "openExternal" => {
            let watch = state
                .store
                .watch(id(&value)?)
                .ok_or("This watched page no longer exists.")?;
            let url = canonical_page_url(&watch.url)?;
            if kind == "openExternal" {
                app.opener().open_url(url, None::<&str>).map_err(err)?;
                result["message"] = json!(
                    "Opened in your regular browser. Its sign-in stays separate from Modhunter's site session."
                );
            } else {
                let session = watch
                    .session_id
                    .as_ref()
                    .map(|id| {
                        state
                            .store
                            .session(id)
                            .ok_or("This page's site session no longer exists.")
                    })
                    .transpose()?;
                if watch.requires_session && session.is_none() {
                    return Err(
                        "Choose this page's account before opening a download session.".into(),
                    );
                }
                let url = Url::parse(&url).map_err(err)?;
                let mut context = SiteContext::production(session, &url)?;
                context.download_watch = Some((watch.id.clone(), watch.generation));
                site_window(app, context, url, true, None, Arc::new(Load::default()))?;
            }
        }
        "exportFile" => result = write_export(app, &value)?,
        "importFile" => {
            if let Some(file) = app
                .dialog()
                .file()
                .set_title("Preview a Watchtower JSON import")
                .add_filter("Watchtower export", &["json"])
                .blocking_pick_file()
            {
                let path = file.into_path().map_err(err)?;
                let mut bytes = Vec::new();
                File::open(path)
                    .map_err(err)?
                    .take(8 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(err)?;
                if bytes.len() > 8 * 1024 * 1024 {
                    return Err("Watchtower imports must be no larger than 8 MiB.".into());
                }
                let document: Value = serde_json::from_slice(&bytes)
                    .map_err(|_| "This file is not a valid JSON export.".to_string())?;
                result = state.store.command(
                    "import",
                    &json!({"document":document,"preview":true}),
                    now(),
                )?;
                result["document"] = document;
            }
        }
        "saveWatch" | "removeWatch" | "restoreWatch" | "pauseWatch" | "markSeen" | "undoSeen"
        | "saveSession" | "resetSession" | "removeSession" | "confirmSession" | "settings"
        | "import" | "export" | "detail" | "recover" => {
            // Validate before the durable mutation. Cancellation itself happens
            // only after success, so an invalid edit cannot stop an owned check.
            let selected = if matches!(kind, "saveWatch" | "removeWatch" | "pauseWatch")
                && (value.get("id").is_some() || value.get("ids").is_some())
            {
                Some(ids(&value)?)
            } else {
                None
            };
            let previous = if matches!(kind, "saveSession" | "resetSession" | "removeSession") {
                value
                    .get("id")
                    .and_then(Value::as_str)
                    .and_then(|id| state.store.session(id))
            } else {
                None
            };
            result = state.store.command(kind, &value, now())?;
            if let Some(selected) = selected {
                cancel_selected(app, Some(&selected), false)?;
            }
            if kind == "confirmSession" {
                cancel_session(app, id(&value)?)?;
            }
            session_changed(app, previous);
            if kind == "settings" {
                cancel_ineligible(app)?;
            }
            if kind == "confirmSession" {
                let selected = id(&value)?;
                close_session(app, selected);
                let candidates = state.store.check_candidates(now(), false);
                let ids = candidates
                    .into_iter()
                    .filter(|id| {
                        state
                            .store
                            .watch_location(id)
                            .is_some_and(|(_, session)| session.as_deref() == Some(selected))
                    })
                    .collect();
                queue(app, ids);
            }
        }
        _ => return Err("Unknown Watchtower command.".into()),
    }
    if !result.is_object() {
        result = json!({"result":result});
    }
    result["ok"] = json!(true);
    result["state"] = state.snapshot();
    result["state"]["acquisitions"] = acquisition::snapshot(app);
    notify(app);
    Ok(result)
}

#[tauri::command]
pub async fn watchtower_command(
    app: tauri::AppHandle,
    webview: Webview,
    kind: String,
    value: Option<Value>,
) -> Result<Value> {
    if !policy::trusted_manager(webview.label(), &webview.url().map_err(err)?) {
        return Err("Watchtower commands belong only to the bundled manager.".into());
    }
    tauri::async_runtime::spawn_blocking(move || execute(&app, &kind, value.unwrap_or(Value::Null)))
        .await
        .map_err(err)?
}

pub fn shutdown(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<WatchRuntime>() else {
        return;
    };
    acquisition::cancel_all(app);
    if state.stopping.swap(true, Ordering::AcqRel) {
        return;
    }
    let _ = cancel(app, None);
    let labels = state
        .windows
        .lock()
        .map(|w| w.keys().cloned().collect::<Vec<_>>())
        .unwrap_or_default();
    for label in labels {
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.close();
        }
    }
    if let Some(window) = app.get_webview_window("watchtower") {
        let _ = window.close();
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        let closed = state.windows.lock().is_ok_and(|windows| windows.is_empty());
        let idle = state
            .work
            .lock()
            .is_ok_and(|work| work.active.is_empty() && work.starting.is_empty());
        if closed && idle {
            break;
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    if let Ok(mut lock) = state.ephemeral_owner.lock() {
        lock.take();
    }
    cleanup_retired(&state);
    // WebView2 can release file handles shortly after the final view closes.
    while Instant::now() < deadline {
        if std::fs::remove_dir_all(state.ephemeral.path()).is_ok() {
            break;
        }
        std::thread::sleep(Duration::from_millis(60));
    }
}
