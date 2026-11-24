use super::*;
use std::{path::Path, sync::atomic::AtomicU64};
use tauri::WebviewWindowBuilder;

static NEXT_WINDOW: AtomicU64 = AtomicU64::new(1);
const PAGE: &str = "/nexus-history.html";

pub(super) fn close_stale(app: &tauri::AppHandle, state: &Runtime) {
    let stale = state
        .history_window
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take_if(|(epoch, _)| *epoch != state.epoch.load(Ordering::Acquire));
    if let Some((_, label)) = stale
        && let Some(window) = app.get_webview_window(&label)
    {
        let _ = window.close();
    }
}

pub(super) fn open(app: &tauri::AppHandle, path: Option<&Path>) -> Result<()> {
    let state = app.state::<Runtime>();
    let (epoch, catalog) = {
        let m = state.model.lock().map_err(err)?;
        if m.endpoint.is_some() || state.importing.load(Ordering::Acquire) {
            return Err(
                "Open a completed offline profile before viewing historical evidence.".into(),
            );
        }
        (
            state.epoch.load(Ordering::Acquire),
            m.catalog
                .clone()
                .ok_or("Import the matching offline MO2 profile first.")?,
        )
    };
    let path = match path {
        Some(path) => path.to_owned(),
        None => {
            let Some(file) = app
                .dialog()
                .file()
                .set_title("Open historical Nexus evidence for this profile")
                .add_filter("Modhunter evidence JSON", &["json"])
                .blocking_pick_file()
            else {
                return Ok(());
            };
            file.into_path().map_err(err)?
        }
    };
    let encoded = crate::history::read(&path, &catalog)?;
    if state.epoch.load(Ordering::Acquire) != epoch {
        return Err("The offline profile changed while opening historical evidence.".into());
    }
    let label = format!(
        "nexus-history-{}",
        NEXT_WINDOW.fetch_add(1, Ordering::Relaxed)
    );
    let title = format!(
        "Historical Nexus evidence · {}",
        catalog["profileName"].as_str().unwrap_or("MO2 profile")
    );
    let mut builder =
        WebviewWindowBuilder::new(app, &label, WebviewUrl::App("nexus-history.html".into()));
    if let Some(storage) = app.try_state::<DesktopSmokeStorage>() {
        builder = builder.data_directory(storage.0.path().join("dashboard-webview"));
    }
    let host = app.clone();
    let window = builder
        .visible(false)
        .focused(false)
        .title(title)
        .inner_size(1180.0, 810.0)
        .min_inner_size(650.0, 420.0)
        .on_navigation(|url| policy::local_asset(url, PAGE))
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .on_page_load(move |view, payload| {
            if payload.event() == PageLoadEvent::Finished
                && policy::local_asset(payload.url(), PAGE)
                && host.state::<Runtime>().epoch.load(Ordering::Acquire) == epoch
                && let Err(e) = view.eval(format!("renderNexusHistory({encoded}, false)"))
            {
                alert(&host, format!("Could not display historical evidence: {e}"));
            }
        })
        .build()
        .map_err(err)?;
    let previous = state
        .history_window
        .lock()
        .map_err(err)?
        .replace((epoch, label.clone()));
    if let Some((_, old)) = previous
        && let Some(old) = app.get_webview_window(&old)
    {
        let _ = old.close();
    }
    if state.epoch.load(Ordering::Acquire) != epoch {
        let _ = window.close();
        return Err("The offline profile changed while opening historical evidence.".into());
    }
    let host = app.clone();
    let closed_label = label.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            crate::watchtower_windows::privacy::forget_window(&host, &closed_label);
        }
    });
    let native = app
        .get_window(&label)
        .ok_or("Historical evidence window closed.")?;
    crate::watchtower_windows::privacy::guarded_show(app, &native, true)
}

pub(super) fn requested_path(args: impl Iterator<Item = String>) -> Result<Option<PathBuf>> {
    let mut path = None;
    for arg in args {
        let (flag, value) = arg.split_once('=').unwrap_or((&arg, ""));
        if flag != "--historical-evidence" {
            continue;
        }
        let value = PathBuf::from(value);
        if path.is_some() || !value.is_absolute() {
            return Err(
                "Specify --historical-evidence=<absolute JSON file> once with --offline-profile."
                    .into(),
            );
        }
        path = Some(value);
    }
    Ok(path)
}
