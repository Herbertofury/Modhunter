//! Observe ordinary browser-visible account indicators without extracting
//! credentials. These facts never authorize a file or an installation.
use super::*;
static OBSERVING: std::sync::OnceLock<Mutex<std::collections::BTreeSet<String>>> =
    std::sync::OnceLock::new();
struct ObservationGuard(String);
impl Drop for ObservationGuard {
    fn drop(&mut self) {
        if let Some(rows) = OBSERVING.get() {
            rows.lock()
                .unwrap_or_else(|e| e.into_inner())
                .remove(&self.0);
        }
    }
}

pub(super) fn page_loaded(app: tauri::AppHandle, label: String, context: SiteContext, url: Url) {
    let Some(session) = context.session.clone() else {
        return;
    };
    if context.fixture_origin.is_some() || url.scheme() != "https" {
        return;
    }
    let origin = url.origin().ascii_serialization();
    if origin != session.origin && url.host_str() != Some("accounts.google.com") {
        return;
    }
    if !OBSERVING
        .get_or_init(|| Mutex::new(std::collections::BTreeSet::new()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(label.clone())
    {
        return;
    }
    std::thread::spawn(move || {
        let _guard = ObservationGuard(label.clone());
        // One bounded follow-up observes sites whose account controls render
        // after document load. Confirmation remains an explicit user action.
        for attempt in 0..3 {
            if attempt > 0 {
                std::thread::sleep(Duration::from_millis(800));
            }
            let state = app.state::<WatchRuntime>();
            if state.stopping.load(Ordering::Acquire) || !context.current(&state) {
                return;
            }
            let Some(view) = app.get_webview_window(&label) else {
                return;
            };
            let Ok(current_url) = view.url() else {
                return;
            };
            let origin = current_url.origin().ascii_serialization();
            if origin != session.origin && current_url.host_str() != Some("accounts.google.com") {
                return;
            }
            let cfg = json!({"origin":origin,"sessionOrigin":session.origin});
            let script = format!(
                "(()=>{{const cfg={cfg};return ({});}})()",
                include_str!("../../../ui/observe-access.js")
            );
            let cancel = AtomicBool::new(false);
            let Ok(result) = eval_json(
                &view,
                script,
                &cancel,
                Instant::now() + Duration::from_secs(4),
            ) else {
                return;
            };
            if !context.current(&state) || result["origin"].as_str() != Some(origin.as_str()) {
                return;
            }
            let (Some(login), Some(status), Some(guidance)) = (
                result["loginState"].as_str(),
                result["status"].as_str(),
                result["guidance"].as_str(),
            ) else {
                return;
            };
            if origin != session.origin && status != "embeddedLoginBlocked" {
                return;
            }
            if state
                .store
                .observe_session_access(
                    &session.id,
                    session.storage_generation,
                    login,
                    status,
                    guidance,
                    now(),
                )
                .unwrap_or(false)
            {
                let _ = cancel_ineligible(&app);
                notify(&app);
            }
            if matches!(
                status,
                "challenge" | "signInRequired" | "embeddedLoginBlocked"
            ) || login == "signedIn"
            {
                return;
            }
        }
    });
}
