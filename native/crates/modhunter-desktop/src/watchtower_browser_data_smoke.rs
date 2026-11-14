//! Owned loopback WebView2 fixtures for cache retention and hidden resources.
use super::*;
use std::net::{TcpListener, TcpStream};
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
) -> Result<(WindowGuard, Arc<Load>)> {
    let load = Arc::new(Load::default());
    let window = WindowGuard(site_window(
        app,
        context,
        url,
        interactive,
        None,
        load.clone(),
    )?);
    wait_loaded(
        &window.0,
        &load,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(18),
    )?;
    Ok((window, load))
}
fn until(window: &WebviewWindow, script: &str) -> Result<Value> {
    let cancel = AtomicBool::new(false);
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let value = eval_json(window, script.into(), &cancel, deadline)?;
        if value == true || value.is_object() {
            return Ok(value);
        }
        if Instant::now() >= deadline {
            return Err("Browser-data fixture did not become ready.".into());
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
    let result = (|| {
        state
            .store
            .command("settings", &json!({"filterHiddenResources":false}), now())?;
        let (a, a_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            server.url("/login-a"),
            false,
        )?;
        until(&a.0, "window.fixtureReady===true")?;
        let (b, b_load) = view(
            app,
            server.context("fixture-browser-data-b"),
            server.url("/login-b"),
            false,
        )?;
        until(&b.0, "window.fixtureReady===true")?;
        navigate(&a.0, &a_load, server.url("/cache?one"))?;
        until(&a.0, "window.fixtureReady===true")?;
        navigate(&b.0, &b_load, server.url("/cache?one"))?;
        until(&b.0, "window.fixtureReady===true")?;
        let before_cached = server.counts()[4];
        if clear_view_cache_until(&a.0, Instant::now()).is_ok() {
            return Err(
                "An expired cleanup budget unexpectedly accepted a cache-clear operation.".into(),
            );
        }
        navigate(&a.0, &a_load, server.url("/cache?two"))?;
        until(&a.0, "window.fixtureReady===true")?;
        if server.counts()[4] != before_cached {
            return Err("The fixture did not establish a disk-cache hit before cleanup.".into());
        }
        clear_view_cache(&a.0)?;
        navigate(&a.0, &a_load, server.url("/cache?after-clear"))?;
        until(&a.0, "window.fixtureReady===true")?;
        let after_cleared = server.counts()[4];
        if after_cleared <= before_cached {
            return Err("The cleared account reused its old cached resource.".into());
        }
        let retained = until(
            &a.0,
            "({authenticated:window.fixtureAuthenticated===true,storage:localStorage.getItem('browserDataRetention')==='kept-a'})",
        )?;
        if retained["authenticated"] != true || retained["storage"] != true {
            return Err("Cache cleanup changed the fixture's sign-in or site storage.".into());
        }
        navigate(&b.0, &b_load, server.url("/cache?other-account"))?;
        until(&b.0, "window.fixtureReady===true")?;
        let other = until(
            &b.0,
            "({authenticated:window.fixtureAuthenticated===true,storage:localStorage.getItem('browserDataRetention')==='kept-b'})",
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
        )?;
        until(
            &full.0,
            "window.requiredScript===true&&window.requiredFetch===true",
        )?;
        let full_capture = capture(
            &full.0,
            &full_load,
            url.as_str(),
            ".release",
            &[],
            &AtomicBool::new(false),
            Instant::now() + Duration::from_secs(8),
        )?;
        drop(full);
        // Let the GUI process close before measuring the next view's requests.
        std::thread::sleep(Duration::from_millis(100));
        let full_end = server.counts();
        if full_end[0] <= full_start[0]
            || full_end[2] <= full_start[2]
            || full_end[3] <= full_start[3]
            || full_capture.status != "ok"
        {
            return Err("The unfiltered fixture did not load its image, script, fetch and complete content.".into());
        }
        state
            .store
            .command("settings", &json!({"filterHiddenResources":true}), now())?;
        let (filtered, filtered_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            url.clone(),
            false,
        )?;
        until(
            &filtered.0,
            "window.requiredScript===true&&window.requiredFetch===true",
        )?;
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
        drop(filtered);
        let failure_url = server.url("/required-failure");
        let (failed, failed_load) = view(
            app,
            server.context("fixture-browser-data-a"),
            failure_url.clone(),
            false,
        )?;
        until(
            &failed.0,
            "window.requiredScript===true&&window.requiredFetch===true",
        )?;
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
        drop(failed);
        let before_interactive = server.counts();
        let (interactive, interactive_load) =
            view(app, server.context("fixture-browser-data-a"), url, true)?;
        until(
            &interactive.0,
            "window.requiredScript===true&&window.requiredFetch===true",
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
    let restored = state.store.command(
        "settings",
        &json!({"filterHiddenResources":original}),
        now(),
    );
    match (result, restored) {
        (Ok(value), Ok(_)) => Ok(value),
        (Err(error), _) => Err(error),
        (_, Err(error)) => Err(error),
    }
}
