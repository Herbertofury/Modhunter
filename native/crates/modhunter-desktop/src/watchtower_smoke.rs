//! Executable WebView2 acceptance fixture. Its loopback capability is constructed
//! here only and is never reachable through a website or manager command.
use super::*;
use std::net::{TcpListener, TcpStream};

struct FixtureServer {
    origin: String,
    version: Arc<AtomicU64>,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl FixtureServer {
    fn start() -> Result<Self> {
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).map_err(err)?;
        listener.set_nonblocking(true).map_err(err)?;
        let origin = format!("http://{}", listener.local_addr().map_err(err)?);
        let version = Arc::new(AtomicU64::new(1));
        let stop = Arc::new(AtomicBool::new(false));
        let server_version = version.clone();
        let server_stop = stop.clone();
        let token = format!("fixture-{}-{}", std::process::id(), now());
        let thread = std::thread::spawn(move || {
            while !server_stop.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, peer)) if peer.ip().is_loopback() => {
                        let _ = respond(stream, &token, server_version.load(Ordering::Acquire));
                    }
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            origin,
            version,
            stop,
            thread: Some(thread),
        })
    }
    fn url(&self, path: &str) -> Url {
        Url::parse(&format!("{}{path}", self.origin)).expect("owned fixture URL")
    }
    fn context(&self, id: &str, generation: u64) -> SiteContext {
        SiteContext {
            session: Some(Session {
                id: id.into(),
                label: "Owned acceptance fixture".into(),
                origin: self.origin.clone(),
                remember: true,
                state: "opened".into(),
                storage_generation: generation,
                created_at: now(),
                updated_at: now(),
                last_opened_at: None,
                last_confirmed_at: None,
            }),
            origin: self.origin.clone(),
            fixture_origin: Some(self.origin.clone()),
            lease: None,
            download_watch: None,
            cancellation: None,
        }
    }
}
impl Drop for FixtureServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn respond(mut stream: TcpStream, token: &str, version: u64) -> Result<()> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(err)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(err)?;
    let mut request = Vec::new();
    let mut buffer = [0_u8; 2048];
    while request.len() < 16_384 && !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
        let count = stream.read(&mut buffer).map_err(err)?;
        if count == 0 {
            break;
        }
        request.extend_from_slice(&buffer[..count]);
    }
    let request = String::from_utf8_lossy(&request);
    let path = request
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
            .any(|(name, value)| {
                name.eq_ignore_ascii_case("cookie")
                    && value
                        .split(';')
                        .any(|cookie| cookie.trim() == format!("fixture_session={token}"))
            });
    let mut cookie = String::new();
    let body = match path {
        "/login" => {
            cookie = format!(
                "Set-Cookie: fixture_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600\r\n"
            );
            "<main>Fixture sign-in completed.</main>".to_owned()
        }
        "/tracked" if authenticated => format!(
            "<main><div class=outside>Outside selected release</div><article class=release><p>Authenticated release version {version}</p><p>Build ready for review</p><span class=noise>Changing noise {}</span><span hidden>Hidden private debug must be ignored</span><form><label>Private form text<input value=private-fixture-field></label><textarea>Private textarea text</textarea></form></article></main>",
            now()
        ),
        "/tracked" => {
            "<main><h1>Sign in required</h1><label>Password <input type=password></label></main>"
                .into()
        }
        "/large" => format!(
            "<main><article class=release>{}</article></main>",
            "x".repeat(270_000)
        ),
        "/popup" => format!(
            "<main>Owned OAuth-shaped popup</main><script>window.fixturePopup={{authenticated:{authenticated},opener:!!window.opener}};if(window.opener)window.opener.postMessage({{kind:'owned-popup',...window.fixturePopup}},location.origin);</script>"
        ),
        _ => "<main>Owned fixture</main>".into(),
    };
    let html = format!(
        "<!doctype html><html><head><meta charset=utf-8><title>Owned Watchtower fixture</title></head><body>{body}</body></html>"
    );
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n{cookie}Content-Length: {}\r\n\r\n{html}",
        html.len()
    );
    stream.write_all(response.as_bytes()).map_err(err)
}

fn event(output: &Path, phase: &str) {
    let path = output.with_file_name("watchtower-smoke.events.jsonl");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{}", json!({"at":now(),"phase":phase}));
    }
}

fn record(
    checks: &mut Vec<Value>,
    output: &Path,
    name: &str,
    passed: bool,
    scope: &str,
) -> Result<()> {
    checks.push(json!({"name":name,"passed":passed,"scope":scope}));
    event(output, name);
    if passed {
        Ok(())
    } else {
        Err(format!("Native fixture check failed: {name}"))
    }
}

fn until(
    window: &WebviewWindow,
    script: &str,
    deadline: Instant,
    test: impl Fn(&Value) -> bool,
) -> Result<Value> {
    let cancel = AtomicBool::new(false);
    while Instant::now() < deadline {
        let value = eval_json(window, script.into(), &cancel, deadline)?;
        if test(&value) {
            return Ok(value);
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err(
        "The native fixture did not observe the expected browser result before its deadline."
            .into(),
    )
}

fn fixture_view(
    app: &tauri::AppHandle,
    context: SiteContext,
    url: Url,
    interactive: bool,
) -> Result<(WindowGuard, Arc<Load>)> {
    let load = Arc::new(Load::default());
    let window = site_window(app, context, url, interactive, None, load.clone())?;
    wait_loaded(
        &window,
        &load,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(18),
    )?;
    Ok((WindowGuard(window), load))
}

fn observe(
    window: &WebviewWindow,
    load: &Load,
    url: &Url,
    selector: &str,
    ignore: &[String],
) -> Result<PageObservation> {
    capture(
        window,
        load,
        url.as_str(),
        selector,
        ignore,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(10),
    )
}

fn wait_closed(app: &tauri::AppHandle, label: &str) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if !app
            .state::<WatchRuntime>()
            .windows
            .lock()
            .map_err(err)?
            .contains_key(label)
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    Err("A fixture browser did not close in time.".into())
}

fn run(app: &tauri::AppHandle, output: &Path, checks: &mut Vec<Value>) -> Result<()> {
    event(output, "opening-manager");
    open(app, None)?;
    let manager = app
        .get_webview_window("watchtower")
        .ok_or("Fixture manager was not created.")?;
    until(
        &manager,
        "!!window.__TAURI__?.core?.invoke",
        Instant::now() + Duration::from_secs(15),
        |value| value == true,
    )?;
    manager.eval("window.__fixtureManager=null;window.__TAURI__.core.invoke('watchtower_command',{kind:'state',value:{}}).then(result=>window.__fixtureManager=!!(result.ok&&Array.isArray(result.state.watches)&&Array.isArray(result.state.sessions))).catch(()=>window.__fixtureManager=false);").map_err(err)?;
    let manager_result = until(
        &manager,
        "window.__fixtureManager??null",
        Instant::now() + Duration::from_secs(5),
        |value| value.is_boolean(),
    )?;
    record(
        checks,
        output,
        "trusted_manager_ipc",
        manager_result == true,
        "Bundled manager invokes the actual native command through Tauri.",
    )?;

    let server = FixtureServer::start()?;
    let context = server.context("fixture-a", 1);
    event(output, "opening-owned-session");
    let (login, _) = fixture_view(app, context.clone(), server.url("/login"), true)?;
    login.0.eval("window.__fixtureRemote=null;(async()=>{const invoke=window.__TAURI__?.core?.invoke||window.__TAURI_INTERNALS__?.invoke;let denied=0;for(const command of ['watchtower_command','shell_command']){if(!invoke){denied++;continue;}try{await invoke(command,{kind:'state',value:{}});}catch{denied++;}}window.__fixtureRemote=denied===2;})();").map_err(err)?;
    let denied = until(
        &login.0,
        "window.__fixtureRemote??null",
        Instant::now() + Duration::from_secs(5),
        |value| value.is_boolean(),
    )?;
    record(
        checks,
        output,
        "remote_ipc_denied",
        denied == true,
        "A real website view cannot invoke either manager or shell native authority.",
    )?;
    let login_label = login.0.label().to_owned();
    login.0.close().map_err(err)?;
    wait_closed(app, &login_label)?;

    let tracked = server.url("/tracked");
    let (page, load) = fixture_view(app, context.clone(), tracked.clone(), true)?;
    let baseline = observe(&page.0, &load, &tracked, ".release", &[".noise".into()])?;
    let authenticated = baseline.status == "ok"
        && baseline.text.contains("Authenticated release version 1")
        && baseline.http_status == Some(200);
    record(
        checks,
        output,
        "session_persistent_reopen",
        authenticated,
        "HttpOnly fixture cookie survives closing all account views and reopening the same on-disk profile in this process.",
    )?;
    record(
        checks,
        output,
        "authenticated_page_capture",
        authenticated,
        "Actual rendered authenticated HTML is read by the host-side WebView2 script completion callback.",
    )?;
    record(
        checks,
        output,
        "selected_text_capture",
        baseline.text == "Authenticated release version 1\nBuild ready for review",
        "A chosen CSS region excludes ignored noise, unrelated regions, hidden text, and form fields.",
    )?;

    let (other, other_load) =
        fixture_view(app, server.context("fixture-b", 1), tracked.clone(), false)?;
    let other_result = observe(&other.0, &other_load, &tracked, "", &[])?;
    record(
        checks,
        output,
        "session_account_isolation",
        other_result.status == "signInRequired"
            && !other_result.text.contains("Authenticated release"),
        "Another account profile cannot read the first account's authenticated fixture page.",
    )?;
    let (reset, reset_load) =
        fixture_view(app, server.context("fixture-a", 2), tracked.clone(), false)?;
    let reset_result = observe(&reset.0, &reset_load, &tracked, "", &[])?;
    record(
        checks,
        output,
        "session_reset_generation",
        reset_result.status == "signInRequired",
        "Rotating the account storage generation selects a fresh browser profile without the old sign-in.",
    )?;

    server.version.store(2, Ordering::Release);
    load.navigation.lock().map_err(err)?.invalidate();
    page.0.navigate(tracked.clone()).map_err(err)?;
    let changed = observe(&page.0, &load, &tracked, ".release", &[".noise".into()])?;
    record(
        checks,
        output,
        "authenticated_page_change",
        changed.status == "ok"
            && changed.text.contains("Authenticated release version 2")
            && changed.text != baseline.text,
        "A second actual authenticated navigation captures the mutated server content.",
    )?;

    page.0.eval("window.__fixturePopup=null;window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data?.kind==='owned-popup')window.__fixturePopup=event.data;});const popup=window.open('about:blank','owned-oauth-fixture','width=520,height=540');if(popup)popup.location.href=new URL('/popup',location.href).href;").map_err(err)?;
    let popup = until(
        &page.0,
        "window.__fixturePopup??null",
        Instant::now() + Duration::from_secs(10),
        |value| value.is_object(),
    )?;
    record(
        checks,
        output,
        "same_session_popup",
        popup["authenticated"] == true && popup["opener"] == true,
        "An actual about:blank window.open popup then navigates within the opener's WebView2 environment and authenticated session, and can message its real opener.",
    )?;

    let large_url = server.url("/large");
    let (large, large_load) = fixture_view(app, context, large_url.clone(), false)?;
    let oversized = observe(&large.0, &large_load, &large_url, ".release", &[])?;
    let incomplete = oversized.truncated && oversized.status == "tooLarge";

    // The production URL policy deliberately cannot store the private loopback
    // fixture. Exercise durable job ownership on a separate public-shaped URL,
    // without rewriting the actual observation's source address.
    let state = app.state::<WatchRuntime>();
    let saved = state.store.command("saveWatch", &json!({"url":"https://mods.example.com/native-fixture","title":"Operation fixture","enabled":true}), now())?;
    let watch_id = saved["watchId"]
        .as_str()
        .ok_or("Fixture watch identity missing.")?;
    let incomplete_job = state.store.begin_check(watch_id, now())?;
    finish_check(app, &incomplete_job, Ok(oversized), &AtomicBool::new(false));
    let incomplete_watch = state
        .store
        .watch(watch_id)
        .ok_or("Fixture watch vanished.")?;
    record(
        checks,
        output,
        "incomplete_capture_rejected",
        incomplete && incomplete_watch.state == "tooLarge" && incomplete_watch.latest.is_none(),
        "An actual oversized rendered region is classified incomplete, and its unchanged failure observation cannot become a durable page baseline.",
    )?;
    let stale = state.store.begin_check(watch_id, now())?;
    state.store.command(
        "saveWatch",
        &json!({"id":watch_id,"title":"Edited operation fixture"}),
        now(),
    )?;
    let before = state.store.snapshot();
    finish_check(app, &stale, Ok(changed), &AtomicBool::new(false));
    record(
        checks,
        output,
        "stale_operation_rejected",
        !state.store.is_current(&stale) && before == state.store.snapshot(),
        "A superseded durable job rejects the real local observation unchanged; no source URL is relabelled for this test.",
    )?;

    let cancelled = state.store.begin_check(watch_id, now())?;
    let cancel = Arc::new(AtomicBool::new(false));
    let signal = cancel.clone();
    let signal_thread = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(75));
        signal.store(true, Ordering::Release);
    });
    let result = eval_json(&page.0, "(()=>{window.__fixtureLateDone=false;const start=performance.now();while(performance.now()-start<350){};window.__fixtureLateDone=true;return {late:true};})()".into(), &cancel, Instant::now() + Duration::from_secs(5));
    let _ = signal_thread.join();
    let rejected = result.is_err();
    finish_check(
        app,
        &cancelled,
        Err("Fixture capture cancelled.".into()),
        &cancel,
    );
    let cancelled_snapshot = state.store.snapshot();
    until(
        &page.0,
        "window.__fixtureLateDone===true",
        Instant::now() + Duration::from_secs(5),
        |value| value == true,
    )?;
    let watch = state
        .store
        .watch(watch_id)
        .ok_or("Fixture watch vanished.")?;
    record(
        checks,
        output,
        "cancelled_capture_rejected",
        rejected
            && !state.store.is_current(&cancelled)
            && watch.state == "cancelled"
            && watch.latest.is_none()
            && cancelled_snapshot == state.store.snapshot(),
        "Cancelling while an actual WebView2 ExecuteScript callback is outstanding discards the late result and clears the durable checking state.",
    )?;
    event(output, "checking-public-https-production-path");
    let public = state.store.command("saveWatch", &json!({"url":"https://example.com/","title":"Public HTTPS acceptance check","enabled":true}), now())?;
    let public_id = public["watchId"]
        .as_str()
        .ok_or("Public HTTPS fixture watch identity missing.")?;
    let public_job = state.store.begin_check(public_id, now())?;
    let public_cancel = Arc::new(AtomicBool::new(false));
    let public_result = run_check(app, &public_job, &public_cancel)?;
    let meaningful = public_result.status == "ok"
        && public_result.final_url == "https://example.com/"
        && public_result.title == "Example Domain"
        && public_result.http_status == Some(200)
        && public_result.text.len() > 20;
    // This fixture is an anonymous public document. Retain a bounded diagnostic
    // so CI distinguishes a blocked public response from a persistence failure.
    let mut public_diagnostic = json!({"status":public_result.status,"httpStatus":public_result.http_status,
        "finalUrl":public_result.final_url,"title":public_result.title,"textBytes":public_result.text.len(),
        "textPreview":public_result.text.chars().take(600).collect::<String>(),"message":public_result.message});
    finish_check(app, &public_job, Ok(public_result), &public_cancel);
    let public_watch = state
        .store
        .watch(public_id)
        .ok_or("Public HTTPS fixture watch vanished.")?;
    public_diagnostic["watchState"] = json!(public_watch.state);
    public_diagnostic["watchError"] = json!(public_watch.last_error);
    public_diagnostic["baselinePresent"] = json!(public_watch.latest.is_some());
    std::fs::write(
        output.with_file_name("public-https-observation.json"),
        serde_json::to_vec_pretty(&public_diagnostic).map_err(err)?,
    )
    .map_err(err)?;
    record(
        checks,
        output,
        "public_https_end_to_end",
        meaningful
            && public_watch.state == "baseline"
            && public_watch.latest.as_ref().is_some_and(|latest| {
                !latest.hash.is_empty() && latest.final_url == "https://example.com/"
            }),
        "A real anonymous example.com HTTPS navigation uses the production saved-job, browser, extraction, and durable-baseline path without rewriting the returned address.",
    )?;
    Ok(())
}

pub fn start(app: &tauri::AppHandle, output: PathBuf) -> Result<()> {
    if !output.is_absolute() {
        return Err("The native fixture output path must be absolute.".into());
    }
    let parent = output
        .parent()
        .ok_or("The fixture output has no parent directory.")?;
    std::fs::create_dir_all(parent).map_err(err)?;
    let root = tempfile::Builder::new()
        .prefix("watchtower-fixture-")
        .tempdir_in(parent)
        .map_err(err)?;
    setup_paths(
        app,
        root.path().join("config"),
        root.path().join("data"),
        false,
    )?;
    // Keep the event loop alive while the worker closes/reopens every site view.
    tauri::window::WindowBuilder::new(app, "watchtower-fixture-keeper")
        .visible(false)
        .build()
        .map_err(err)?;
    let handle = app.clone();
    std::thread::spawn(move || {
        let mut checks = Vec::new();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run(&handle, &output, &mut checks)
        }))
        .unwrap_or_else(|_| Err("The native fixture stopped unexpectedly.".into()));
        let ok = result.is_ok();
        let receipt = json!({"ok":ok,"nativeWebView2":true,"watchtowerSchema":1,"version":env!("CARGO_PKG_VERSION"),
            "scope":"Owned local authenticated fixture plus one anonymous public example.com HTTPS production-path check. No real account/provider sign-in claim. Reopen is within one process. Local durable lease checks use a separate canonical public-shaped URL without relabelling captured URLs.",
            "checks":checks,"error":result.err(),"completedAt":now()});
        let written = (|| -> Result<()> {
            let mut file = tempfile::NamedTempFile::new_in(
                output.parent().ok_or("Fixture output parent missing.")?,
            )
            .map_err(err)?;
            serde_json::to_writer_pretty(&mut file, &receipt).map_err(err)?;
            file.as_file().sync_all().map_err(err)?;
            file.persist(&output).map_err(err)?;
            Ok(())
        })()
        .is_ok();
        event(
            &output,
            if ok && written {
                "fixture-passed"
            } else {
                "fixture-failed"
            },
        );
        shutdown(&handle);
        drop(root);
        handle.exit(if ok && written { 0 } else { 1 });
    });
    Ok(())
}
