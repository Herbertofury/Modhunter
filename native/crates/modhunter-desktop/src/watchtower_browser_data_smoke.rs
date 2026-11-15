//! Owned loopback WebView2 fixtures for cache retention and hidden resources.
use super::*;
use std::net::{TcpListener, TcpStream};
use windows_sys::Win32::{Foundation::HWND, UI::WindowsAndMessaging::IsWindow};

#[derive(Default)]
struct OwnedViews {
    pending: BTreeMap<String, usize>,
    created: usize,
    retired: usize,
}
impl OwnedViews {
    fn remember(&mut self, window: &WebviewWindow) -> Result<()> {
        let hwnd = window.hwnd().map_err(err)?.0 as usize;
        if self.pending.insert(window.label().into(), hwnd).is_some() {
            return Err("The browser-data fixture repeated a window identity.".into());
        }
        self.created += 1;
        Ok(())
    }
    fn retire(&mut self, app: &tauri::AppHandle, labels: &[String]) -> Result<()> {
        let mut expected = BTreeMap::new();
        for label in labels {
            let hwnd = self
                .pending
                .get(label)
                .ok_or("The browser-data fixture lost an owned window identity.")?;
            expected.insert(label.clone(), *hwnd);
        }
        // WindowGuard::drop requests an asynchronous close. The next fixture
        // must not configure all app windows while these dispatchers retire.
        // Read-only IsWindow is safe on this worker; the GUI stays unblocked.
        let deadline = Instant::now() + Duration::from_secs(5);
        while !expected.is_empty() {
            let mut remaining = Vec::new();
            let mut retired = Vec::new();
            for (label, hwnd) in &expected {
                let window_registered = app.get_window(label).is_some();
                let webview_registered = app.get_webview_window(label).is_some();
                let runtime_registered = app
                    .state::<WatchRuntime>()
                    .windows
                    .lock()
                    .map_err(err)?
                    .contains_key(label);
                let native_alive = unsafe { IsWindow(*hwnd as HWND) } != 0;
                if window_registered || webview_registered || runtime_registered || native_alive {
                    remaining.push(json!({"label":label,"windowRegistered":window_registered,
                        "webviewRegistered":webview_registered,"runtimeRegistered":runtime_registered,
                        "nativeAlive":native_alive}));
                } else {
                    retired.push(label.clone());
                }
            }
            // A later view may reuse a retired HWND. Forget each proven-dead
            // identity immediately instead of querying it after new creation.
            for label in retired {
                expected.remove(&label);
                self.pending.remove(&label);
                self.retired += 1;
            }
            if expected.is_empty() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(format!(
                    "Owned browser-data windows did not finish retirement: {}",
                    json!(remaining)
                ));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        Ok(())
    }
    fn retire_all(&mut self, app: &tauri::AppHandle) -> Result<()> {
        self.retire(app, &self.pending.keys().cloned().collect::<Vec<_>>())
    }
}

struct Probe {
    started: Instant,
    stage_started: Instant,
    stage: &'static str,
    load: Option<Arc<Load>>,
    counts: Arc<[AtomicU64; 6]>,
    completed: Vec<Value>,
    readiness: Value,
}
impl Probe {
    fn new(server: &Server) -> Self {
        Self {
            started: Instant::now(),
            stage_started: Instant::now(),
            stage: "start",
            load: None,
            counts: server.counts.clone(),
            completed: Vec::new(),
            readiness: Value::Null,
        }
    }
    fn enter(&mut self, stage: &'static str, load: Option<&Arc<Load>>) {
        if self.completed.len() < 48 {
            self.completed.push(self.current());
        }
        self.stage = stage;
        self.stage_started = Instant::now();
        self.load = load.cloned();
    }
    fn current(&self) -> Value {
        let navigation = self.load.as_ref().and_then(|load| {
            load.navigation.lock().ok().map(|progress| {
                json!({"revision":progress.revision,"website":progress.website,
                    "completed":progress.completed,"success":progress.success,
                    "httpStatus":progress.http_status,"webError":progress.web_error,
                    "filterEnabled":load.filtered_resources.load(Ordering::Acquire),
                    "blockedResources":load.blocked_resources.load(Ordering::Acquire),
                    "nativeErrorRecorded":load.native_error.lock().map(|error|error.is_some()).unwrap_or(true)})
            })
        });
        let counts: [u64; 6] = std::array::from_fn(|i| self.counts[i].load(Ordering::Acquire));
        json!({"stage":self.stage,"elapsedMs":self.stage_started.elapsed().as_millis(),
            "totalElapsedMs":self.started.elapsed().as_millis(),"requestCounts":counts,
            "navigation":navigation,"lastReadiness":self.readiness})
    }
    fn evidence(&self) -> Value {
        json!({"completedStages":self.completed,"current":self.current(),
            "counterOrder":["image","font","script","fetch","cache-script","media"]})
    }
    fn failure(&self, error: &str) -> String {
        format!(
            "Browser-data fixture: {error} Owned browser-data observations: {}",
            self.evidence()
        )
    }
}

struct Server {
    origin: String,
    counts: Arc<[AtomicU64; 6]>,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Server {
    fn start() -> Result<Self> {
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).map_err(err)?;
        listener.set_nonblocking(true).map_err(err)?;
        let origin = format!("http://{}", listener.local_addr().map_err(err)?);
        let counts = Arc::new(std::array::from_fn(|_| AtomicU64::new(0)));
        let stop = Arc::new(AtomicBool::new(false));
        let server_counts = counts.clone();
        let server_stop = stop.clone();
        let thread = std::thread::spawn(move || {
            while !server_stop.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, peer)) if peer.ip().is_loopback() => {
                        let counts = server_counts.clone();
                        std::thread::spawn(move || {
                            let _ = respond(stream, &counts);
                        });
                    }
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(10))
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            origin,
            counts,
            stop,
            thread: Some(thread),
        })
    }
    fn url(&self, path: &str) -> Url {
        Url::parse(&format!("{}{path}", self.origin)).expect("owned browser-data fixture")
    }
    fn context(&self, id: &str) -> SiteContext {
        SiteContext {
            session: Some(Session {
                id: id.into(),
                label: "Browser-data fixture".into(),
                origin: self.origin.clone(),
                remember: true,
                state: "opened".into(),
                storage_generation: 1,
                created_at: now(),
                updated_at: now(),
                last_opened_at: None,
                last_confirmed_at: None,
            }),
            origin: self.origin.clone(),
            fixture_origin: Some(self.origin.clone()),
            lease: None,
            download_watch: None,
            download_file_key: None,
            download_scope: None,
            cancellation: None,
        }
    }
    fn counts(&self) -> [u64; 6] {
        std::array::from_fn(|i| self.counts[i].load(Ordering::Acquire))
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
fn respond(mut stream: TcpStream, counts: &[AtomicU64; 6]) -> Result<()> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(err)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(err)?;
    let mut bytes = Vec::new();
    let mut buffer = [0; 2048];
    while bytes.len() < 16384 && !bytes.windows(4).any(|x| x == b"\r\n\r\n") {
        let size = stream.read(&mut buffer).map_err(err)?;
        if size == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..size]);
    }
    let request = String::from_utf8_lossy(&bytes);
    let route = request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("/")
        .split('?')
        .next()
        .unwrap_or("/");
    let authenticated =
        request
            .lines()
            .filter_map(|line| line.split_once(':'))
            .any(|(key, value)| {
                key.eq_ignore_ascii_case("cookie")
                    && value
                        .split(';')
                        .any(|part| matches!(part.trim(), "browser_data=a" | "browser_data=b"))
            });
    let mut headers = String::new();
    let mut mime = "text/html; charset=utf-8";
    let mut cache = "no-store";
    let body = match route {
        "/login-a" | "/login-b" => {
            let account = if route == "/login-a" { "a" } else { "b" };
            headers =
                format!("Set-Cookie: browser_data={account}; Path=/; HttpOnly; SameSite=Lax\r\n");
            format!(
                "<main>Fixture sign-in complete.</main><script>localStorage.setItem('browserDataRetention','kept-{account}');window.fixtureReady=true;</script>"
            )
        }
        "/cache" => format!(
            "<main>Cache and account retention fixture</main><script>window.fixtureAuthenticated={authenticated};</script><script src='/cached.js'></script><script>window.fixtureReady=window.cachedAsset===true;</script>"
        ),
        "/cached.js" => {
            counts[4].fetch_add(1, Ordering::AcqRel);
            mime = "text/javascript";
            cache = "public, max-age=3600, immutable";
            "window.cachedAsset=true;".into()
        }
        "/image.png" => {
            counts[0].fetch_add(1, Ordering::AcqRel);
            mime = "image/png";
            "Owned image bytes".into()
        }
        "/font.woff2" => {
            counts[1].fetch_add(1, Ordering::AcqRel);
            mime = "font/woff2";
            "Owned font bytes".into()
        }
        "/media.wav" => {
            counts[5].fetch_add(1, Ordering::AcqRel);
            mime = "audio/wav";
            "Owned media bytes".into()
        }
        "/required.js" => {
            counts[2].fetch_add(1, Ordering::AcqRel);
            mime = "text/javascript";
            "window.requiredScript=true;fetch('/payload').then(r=>r.json()).then(v=>window.requiredFetch=v.ready);".into()
        }
        "/payload" => {
            counts[3].fetch_add(1, Ordering::AcqRel);
            mime = "application/json";
            "{\"ready\":true}".into()
        }
        "/resources" | "/required-failure" => format!(
            "<style>@font-face{{font-family:Fixture;src:url('/font.woff2')}}main{{font-family:Fixture}}</style><main class='release'>A complete resource fixture release description. The game, source, installed mod and compatibility remain stable while display resources are optional.</main><img src='/image.png'><audio preload='auto' src='/media.wav'></audio><script src='/required.js'></script>{}",
            if route == "/required-failure" {
                "<div role='alert'>Required image could not load</div>"
            } else {
                ""
            }
        ),
        _ => "<main>Owned browser-data fixture</main>".into(),
    };
    let reply = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nCache-Control: {cache}\r\nConnection: close\r\n{headers}Content-Length: {}\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(reply.as_bytes()).map_err(err)
}
fn view(
    app: &tauri::AppHandle,
    context: SiteContext,
    url: Url,
    interactive: bool,
    owned: &mut OwnedViews,
    probe: &mut Probe,
    stage: &'static str,
) -> Result<(WindowGuard, Arc<Load>)> {
    let load = Arc::new(Load::default());
    probe.enter(stage, Some(&load));
    let window = WindowGuard(site_window(
        app,
        context,
        url,
        interactive,
        None,
        load.clone(),
    )?);
    owned.remember(&window.0)?;
    wait_loaded(
        &window.0,
        &load,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(18),
    )?;
    Ok((window, load))
}
fn until(window: &WebviewWindow, script: &str, probe: &mut Probe) -> Result<Value> {
    let cancel = AtomicBool::new(false);
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut completed = 0;
    let script = format!(
        "(()=>{{return {{value:({script}),flags:{{ready:window.fixtureReady===true,cache:window.cachedAsset===true,script:window.requiredScript===true,fetch:window.requiredFetch===true,documentReady:document.readyState}}}};}})()"
    );
    loop {
        // A false condition can consume the deadline without any callback
        // stall. Keep those outcomes distinct and retain only fixture booleans.
        if Instant::now() >= deadline {
            return Err("Browser-data readiness condition did not become true before its unchanged 8-second deadline.".into());
        }
        let result = eval_json(window, script.clone(), &cancel, deadline).map_err(|error| {
            format!("Browser-data readiness evaluation failed after {completed} completed callbacks: {error}")
        })?;
        completed += 1;
        let value = result
            .get("value")
            .cloned()
            .ok_or("Browser-data readiness returned an invalid fixture object.")?;
        probe.readiness = json!({"stage":probe.stage,"completedEvaluations":completed,
            "condition":if value==true{"true"}else if value==false{"false"}else if value.is_object(){"object"}else{"other"},
            "flags":result.get("flags")});
        if value == true || value.is_object() {
            return Ok(value);
        }
        if Instant::now() >= deadline {
            return Err("Browser-data readiness condition did not become true before its unchanged 8-second deadline.".into());
        }
        std::thread::sleep(Duration::from_millis(30));
    }
}
fn navigate(window: &WebviewWindow, load: &Load, url: Url) -> Result<()> {
    load.navigation.lock().map_err(err)?.invalidate();
    window.navigate(url).map_err(err)?;
    wait_loaded(
        window,
        load,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(15),
    )
}
pub(super) fn check(app: &tauri::AppHandle) -> Result<Value> {
    let server = Server::start()?;
    let state = app.state::<WatchRuntime>();
    let original = state.store.settings().filter_hidden_resources;
    let mut owned = OwnedViews::default();
    let mut probe = Probe::new(&server);
    let result: Result<Value> = (|| {
        probe.enter("disable-hidden-filter", None);
        state
            .store
            .command("settings", &json!({"filterHiddenResources":false}), now())?;
        let (a, a_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            server.url("/login-a"),
            false,
            &mut owned,
            &mut probe,
            "open-account-a",
        )?;
        probe.enter("account-a-login-ready", Some(&a_load));
        until(&a.0, "window.fixtureReady===true", &mut probe)?;
        let (b, b_load) = view(
            app,
            server.context("fixture-browser-data-b"),
            server.url("/login-b"),
            false,
            &mut owned,
            &mut probe,
            "open-account-b",
        )?;
        probe.enter("account-b-login-ready", Some(&b_load));
        until(&b.0, "window.fixtureReady===true", &mut probe)?;
        probe.enter("account-a-initial-cache", Some(&a_load));
        navigate(&a.0, &a_load, server.url("/cache?one"))?;
        until(&a.0, "window.fixtureReady===true", &mut probe)?;
        probe.enter("account-b-initial-cache", Some(&b_load));
        navigate(&b.0, &b_load, server.url("/cache?one"))?;
        until(&b.0, "window.fixtureReady===true", &mut probe)?;
        let before_cached = server.counts()[4];
        probe.enter("expired-cleanup-budget", Some(&a_load));
        if clear_view_cache_until(&a.0, Instant::now()).is_ok() {
            return Err(
                "An expired cleanup budget unexpectedly accepted a cache-clear operation.".into(),
            );
        }
        probe.enter("account-a-cache-hit", Some(&a_load));
        navigate(&a.0, &a_load, server.url("/cache?two"))?;
        until(&a.0, "window.fixtureReady===true", &mut probe)?;
        if server.counts()[4] != before_cached {
            return Err("The fixture did not establish a disk-cache hit before cleanup.".into());
        }
        probe.enter("clear-account-a-cache", Some(&a_load));
        clear_view_cache(&a.0)?;
        probe.enter("account-a-cache-refetch", Some(&a_load));
        navigate(&a.0, &a_load, server.url("/cache?after-clear"))?;
        until(&a.0, "window.fixtureReady===true", &mut probe)?;
        let after_cleared = server.counts()[4];
        if after_cleared <= before_cached {
            return Err("The cleared account reused its old cached resource.".into());
        }
        probe.enter("account-a-retained-state", Some(&a_load));
        let retained = until(
            &a.0,
            "({authenticated:window.fixtureAuthenticated===true,storage:localStorage.getItem('browserDataRetention')==='kept-a'})",
            &mut probe,
        )?;
        if retained["authenticated"] != true || retained["storage"] != true {
            return Err("Cache cleanup changed the fixture's sign-in or site storage.".into());
        }
        probe.enter("account-b-isolated-cache", Some(&b_load));
        navigate(&b.0, &b_load, server.url("/cache?other-account"))?;
        until(&b.0, "window.fixtureReady===true", &mut probe)?;
        probe.enter("account-b-retained-state", Some(&b_load));
        let other = until(
            &b.0,
            "({authenticated:window.fixtureAuthenticated===true,storage:localStorage.getItem('browserDataRetention')==='kept-b'})",
            &mut probe,
        )?;
        if server.counts()[4] != after_cleared
            || other["authenticated"] != true
            || other["storage"] != true
        {
            return Err("Clearing one account affected another account's cache or sign-in.".into());
        }
        let full_start = server.counts();
        let url = server.url("/resources");
        let (full, full_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            url.clone(),
            false,
            &mut owned,
            &mut probe,
            "open-unfiltered-resources",
        )?;
        probe.enter("unfiltered-resources-ready", Some(&full_load));
        until(
            &full.0,
            "window.requiredScript===true&&window.requiredFetch===true",
            &mut probe,
        )?;
        probe.enter("capture-unfiltered-resources", Some(&full_load));
        let full_capture = capture(
            &full.0,
            &full_load,
            url.as_str(),
            ".release",
            &[],
            &AtomicBool::new(false),
            Instant::now() + Duration::from_secs(8),
        )?;
        let full_label = full.0.label().to_owned();
        drop(full);
        probe.enter("retire-unfiltered-resources", Some(&full_load));
        owned.retire(app, &[full_label])?;
        let full_end = server.counts();
        if full_end[0] <= full_start[0]
            || full_end[2] <= full_start[2]
            || full_end[3] <= full_start[3]
            || full_capture.status != "ok"
        {
            return Err("The unfiltered fixture did not load its image, script, fetch and complete content.".into());
        }
        probe.enter("enable-hidden-filter", None);
        state
            .store
            .command("settings", &json!({"filterHiddenResources":true}), now())?;
        let (filtered, filtered_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            url.clone(),
            false,
            &mut owned,
            &mut probe,
            "open-filtered-resources",
        )?;
        probe.enter("filtered-resources-ready", Some(&filtered_load));
        until(
            &filtered.0,
            "window.requiredScript===true&&window.requiredFetch===true",
            &mut probe,
        )?;
        probe.enter("capture-filtered-resources", Some(&filtered_load));
        let filtered_capture = capture(
            &filtered.0,
            &filtered_load,
            url.as_str(),
            ".release",
            &[],
            &AtomicBool::new(false),
            Instant::now() + Duration::from_secs(8),
        )?;
        let filtered_end = server.counts();
        if filtered_load.blocked_resources.load(Ordering::Acquire) == 0
            || filtered_end[0] != full_end[0]
            || filtered_end[1] != full_end[1]
            || filtered_end[5] != full_end[5]
            || filtered_end[2] <= full_end[2]
            || filtered_end[3] <= full_end[3]
            || filtered_capture.status != "ok"
            || filtered_capture.text != full_capture.text
        {
            return Err("Hidden filtering changed required content or failed to block only heavy resources.".into());
        }
        let filtered_label = filtered.0.label().to_owned();
        drop(filtered);
        probe.enter("retire-filtered-resources", Some(&filtered_load));
        owned.retire(app, &[filtered_label])?;
        let failure_url = server.url("/required-failure");
        let (failed, failed_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            failure_url.clone(),
            false,
            &mut owned,
            &mut probe,
            "open-required-resource-failure",
        )?;
        probe.enter("required-resource-failure-ready", Some(&failed_load));
        until(
            &failed.0,
            "window.requiredScript===true&&window.requiredFetch===true",
            &mut probe,
        )?;
        probe.enter("capture-required-resource-failure", Some(&failed_load));
        let failed_capture = capture(
            &failed.0,
            &failed_load,
            failure_url.as_str(),
            ".release",
            &[],
            &AtomicBool::new(false),
            Instant::now() + Duration::from_secs(8),
        )?;
        if failed_capture.status != "incomplete" {
            return Err(
                "A required-resource failure was accepted as a complete filtered capture.".into(),
            );
        }
        let failed_label = failed.0.label().to_owned();
        drop(failed);
        probe.enter("retire-required-resource-failure", Some(&failed_load));
        owned.retire(app, &[failed_label])?;
        let before_interactive = server.counts();
        let (interactive, interactive_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            url,
            true,
            &mut owned,
            &mut probe,
            "open-interactive-resources",
        )?;
        probe.enter("interactive-resources-ready", Some(&interactive_load));
        until(
            &interactive.0,
            "window.requiredScript===true&&window.requiredFetch===true",
            &mut probe,
        )?;
        if interactive_load.filtered_resources.load(Ordering::Acquire)
            || server.counts()[0] <= before_interactive[0]
        {
            return Err(
                "Interactive sign-in/review unexpectedly used hidden resource filtering.".into(),
            );
        }
        Ok(
            json!({"cacheHitEstablished":true,"clearedAccountRefetched":true,"httpOnlySignInRetained":true,"siteStorageRetained":true,"otherAccountCacheAndSignInUnchanged":true,"hiddenHeavyRequestsBlocked":filtered_load.blocked_resources.load(Ordering::Acquire),"requiredScriptAndFetchRetained":true,"completeCapturePreserved":true,"requiredResourceFailureRejected":true,"interactiveResourcesUnfiltered":true,"requestCountsBeforeFilter":full_end,"requestCountsAfterFilter":filtered_end,"counterOrder":["image","font","script","fetch","cache-script","media"]}),
        )
    })();
    // Every guard in the functional closure has now requested close, including
    // early-error paths. Finish those exact native lifetimes before a later
    // fixture applies app-wide window policy.
    let mut failures = Vec::new();
    if let Err(error) = &result {
        failures.push(probe.failure(error));
    }
    probe.enter("retire-final-account-views", None);
    if let Err(error) = owned.retire_all(app) {
        failures.push(probe.failure(&error));
    }
    if result.is_ok() && (owned.created != 6 || owned.retired != 6 || !owned.pending.is_empty()) {
        failures
            .push(probe.failure("All six exact browser-data fixture windows were not retired."));
    }
    probe.enter("restore-hidden-filter-setting", None);
    let restored = state.store.command(
        "settings",
        &json!({"filterHiddenResources":original}),
        now(),
    );
    if let Err(error) = restored {
        failures.push(probe.failure(&error));
    }
    if failures.is_empty() {
        let mut value = result?;
        value["allOwnedBrowserWindowsDestroyed"] = json!(true);
        value["ownedBrowserWindowsRetired"] = json!(owned.retired);
        value["stageObservations"] = probe.evidence();
        Ok(value)
    } else {
        Err(failures.join(" "))
    }
}
