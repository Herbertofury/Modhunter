//! Narrow WebView2 cache cleanup and optional hidden-view resource policy.
//! Cookies, credentials and site storage remain inside the browser profile.
use super::*;
use webview2_com::Microsoft::Web::WebView2::Win32::{
    COREWEBVIEW2_BROWSING_DATA_KINDS_BROWSING_HISTORY, COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE,
    COREWEBVIEW2_BROWSING_DATA_KINDS_DOWNLOAD_HISTORY, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT,
    COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA,
    ICoreWebView2, ICoreWebView2_13, ICoreWebView2Environment, ICoreWebView2Profile2,
};
/// Called on the WebView2 GUI thread before navigation. Only heavy display
/// resources are intercepted; scripts, documents, fetch and XHR are untouched.
pub(super) fn install_filter(
    view: &ICoreWebView2,
    environment: ICoreWebView2Environment,
    load: Arc<Load>,
    enabled: bool,
) -> windows_core::Result<()> {
    load.filtered_resources.store(enabled, Ordering::Release);
    if !enabled {
        return Ok(());
    }
    for context in [
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT,
    ] {
        unsafe {
            view.AddWebResourceRequestedFilter(windows_core::w!("*"), context)?;
        }
    }
    let handler =
        webview2_com::WebResourceRequestedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else {
                return Ok(());
            };
            let result = (|| -> windows_core::Result<()> {
                unsafe {
                    let mut context = Default::default();
                    args.ResourceContext(&mut context)?;
                    if ![
                        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE,
                        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA,
                        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT,
                    ]
                    .contains(&context)
                    {
                        return Ok(());
                    }
                    let response = environment.CreateWebResourceResponse(
                        None,
                        204,
                        windows_core::w!("No Content"),
                        windows_core::w!("Cache-Control: no-store\r\n"),
                    )?;
                    args.SetResponse(&response)?;
                    load.blocked_resources.fetch_add(1, Ordering::AcqRel);
                    Ok(())
                }
            })();
            if let Err(error) = result
                && let Ok(mut failure) = load.native_error.lock()
            {
                *failure = Some(format!(
                    "Hidden resource filtering failed; the previous baseline is retained: {error}"
                ));
            }
            Ok(())
        }));
    let mut token = 0;
    unsafe { view.add_WebResourceRequested(&handler, &mut token) }
}
pub(super) fn clear_view_cache(view: &WebviewWindow) -> Result<()> {
    clear_view_cache_until(view, Instant::now() + Duration::from_secs(20))
}
fn clear_view_cache_until(view: &WebviewWindow, deadline: Instant) -> Result<()> {
    if Instant::now() >= deadline {
        return Err("Cache cleanup time budget expired before this profile was touched.".into());
    }
    let (send, receive) = mpsc::sync_channel(1);
    view.with_webview(move |platform| {
        if Instant::now() >= deadline {
            let _ = send.try_send(Err(
                "Cache cleanup time budget expired before this profile was touched.".into(),
            ));
            return;
        }
        let completion = send.clone();
        let result = (|| -> windows_core::Result<()> {
            unsafe {
                let profile = platform
                    .controller()
                    .CoreWebView2()?
                    .cast::<ICoreWebView2_13>()?
                    .Profile()?
                    .cast::<ICoreWebView2Profile2>()?;
                let handler = webview2_com::ClearBrowsingDataCompletedHandler::create(Box::new(
                    move |result| {
                        let _ = completion.try_send(result.map_err(err));
                        Ok(())
                    },
                ));
                profile.ClearBrowsingData(
                    COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE
                        | COREWEBVIEW2_BROWSING_DATA_KINDS_BROWSING_HISTORY
                        | COREWEBVIEW2_BROWSING_DATA_KINDS_DOWNLOAD_HISTORY,
                    &handler,
                )
            }
        })();
        if let Err(error) = result {
            let _ = send.try_send(Err(format!(
                "This WebView2 runtime could not clear the selected cache: {error}"
            )));
        }
    })
    .map_err(err)?;
    receive
        .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        .map_err(|_| {
            "Browser cache cleanup did not complete in time. Its result is unconfirmed.".to_string()
        })?
}
fn clear_context(app: &tauri::AppHandle, context: SiteContext, deadline: Instant) -> Result<()> {
    if Instant::now() >= deadline {
        return Err("Cleanup time budget expired; this profile was not touched.".into());
    }
    let state = app.state::<WatchRuntime>();
    if !context.current(&state) {
        return Err("The browser session changed before cache cleanup.".into());
    }
    let existing = state
        .windows
        .lock()
        .map_err(err)?
        .iter()
        .find(|(_, window)| {
            window.session_id == context.session.as_ref().map(|s| s.id.clone())
                && window.generation == context.session.as_ref().map_or(0, |s| s.storage_generation)
                && window.origin == context.origin
        })
        .and_then(|(label, _)| app.get_webview_window(label));
    let temporary = if existing.is_none() {
        Some(WindowGuard(site_window(
            app,
            context.clone(),
            Url::parse("about:blank").map_err(err)?,
            false,
            None,
            Arc::new(Load::default()),
        )?))
    } else {
        None
    };
    let view = existing
        .as_ref()
        .or_else(|| temporary.as_ref().map(|guard| &guard.0))
        .ok_or("Could not open the selected browser profile for cleanup.")?;
    clear_view_cache_until(view, deadline)?;
    if !context.current(&state) {
        return Err(
            "The browser session changed during cleanup. The new session was not cleared.".into(),
        );
    }
    Ok(())
}
pub(super) fn clear_session_cache(app: &tauri::AppHandle, session_id: &str) -> Result<Value> {
    let state = app.state::<WatchRuntime>();
    state.writable()?;
    let session = state
        .store
        .session(session_id)
        .ok_or("Choose an existing site session.")?;
    cancel_session_work(app, session_id, true)?;
    let url = Url::parse(&session.origin).map_err(err)?;
    clear_context(
        app,
        SiteContext::production(Some(session), &url)?,
        Instant::now() + Duration::from_secs(20),
    )?;
    Ok(
        json!({"message":"Cleared this account's disk cache and browsing/download history. Cookies, credentials and site storage were retained; no other account profile was changed."}),
    )
}
pub(super) fn clear_all_caches(app: &tauri::AppHandle) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(20);
    let state = app.state::<WatchRuntime>();
    state.writable()?;
    cancel(app, None)?;
    let snapshot = state.store.snapshot();
    let sessions: Vec<Session> =
        serde_json::from_value(snapshot["sessions"].clone()).map_err(err)?;
    let mut contexts = Vec::new();
    for session in sessions {
        let identity = format!("session:{}", session.id);
        let url = Url::parse(&session.origin).map_err(err)?;
        contexts.push((identity, SiteContext::production(Some(session), &url)));
    }
    // Public external hosts are ephemeral too, but a view can close long before
    // the application. Remember every origin opened in this run so cleanup
    // includes those profiles without walking or guessing browser directories.
    let mut origins = state.anonymous_origins.lock().map_err(err)?.clone();
    origins.extend(
        snapshot["watches"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|watch| watch["sessionId"].is_null())
            .filter_map(|watch| watch["url"].as_str())
            .map(page_origin)
            .collect::<std::result::Result<Vec<_>, _>>()?,
    );
    for origin in origins {
        let url = Url::parse(&origin).map_err(err)?;
        contexts.push((
            format!("public:{origin}"),
            SiteContext::production(None, &url),
        ));
    }
    let mut cleared = Vec::new();
    let mut failures = Vec::new();
    for (identity, context) in contexts {
        let result = if Instant::now() >= deadline {
            Err("not started: the total 20-second cleanup budget expired".into())
        } else {
            context.and_then(|context| clear_context(app, context, deadline))
        };
        match result {
            Ok(()) => cleared.push(identity),
            Err(error) => failures.push(format!("{identity}: {error}")),
        }
    }
    if !failures.is_empty() {
        return Err(format!(
            "Browser cleanup was incomplete. Confirmed cleared profiles ({}): {}. Uncleared or unconfirmed profiles ({}): {}. Cookies, credentials and site storage were retained.",
            cleared.len(),
            if cleared.is_empty() {
                "none".into()
            } else {
                cleared.join(", ")
            },
            failures.len(),
            failures.join("; ")
        ));
    }
    Ok(
        json!({"clearedProfiles":cleared.len(),"clearedProfileIds":cleared,"message":format!("Cleared disk cache and browsing/download history for {} known site profiles. Cookies, credentials and site storage were retained.",cleared.len())}),
    )
}

#[path = "watchtower_browser_data_smoke.rs"]
mod smoke;
pub(super) fn smoke_check(app: &tauri::AppHandle) -> Result<Value> {
    smoke::check(app)
}
