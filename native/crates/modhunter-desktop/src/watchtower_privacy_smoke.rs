//! Windows-only fixture evidence. Shortcut delivery is a synthetic WM_HOTKEY
//! message after real OS registration, not a claimed physical keyboard test.
use super::*;
use windows_sys::Win32::{
    Foundation::GetLastError,
    UI::WindowsAndMessaging::{GetWindowLongPtrW, IsWindow},
};
const VISIBLE: &str = "privacy-smoke-visible";
const BACKGROUND: &str = "privacy-smoke-background";
const LATE: &str = "privacy-smoke-late";
type Observations = Arc<Mutex<Vec<Value>>>;

fn observe(
    app: &tauri::AppHandle,
    observations: &Observations,
    stage: &str,
    labels: &[&str],
    expected: Option<bool>,
) -> Result<()> {
    let mut rows = Vec::new();
    for label in labels {
        let window = app
            .get_window(label)
            .ok_or("Missing privacy fixture window.")?;
        let hwnd = window.hwnd().map_err(err)?.0;
        let mut actual = 0;
        let succeeded = unsafe { GetWindowDisplayAffinity(hwnd, &mut actual) } != 0;
        let error = if succeeded {
            None
        } else {
            Some(unsafe { GetLastError() })
        };
        let style = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) } as u32;
        let mut reference = 0;
        let installed = unsafe {
            GetWindowSubclass(
                hwnd,
                Some(capture_style_proc),
                CAPTURE_STYLE_ID,
                &mut reference,
            )
        } != 0;
        rows.push(
            json!({"label":label,"querySucceeded":succeeded,"affinity":actual,
            "win32Error":error,"extendedStyle":style,"layered":style&WS_EX_LAYERED!=0,
            "visible":window.is_visible().map_err(err)?,"guardInstalled":installed,
            "guardEnabled":installed&&reference!=0}),
        );
    }
    let state = app.state::<Privacy>();
    let model = state.model.lock().map_err(err)?;
    let stage_result = json!({"stage":stage,"captureRequested":model.capture_protection,
        "appHidden":model.app_hidden,"windows":rows});
    drop(model);
    observations.lock().map_err(err)?.push(stage_result);
    if let Some(enabled) = expected {
        let affinity = if enabled {
            WDA_EXCLUDEFROMCAPTURE
        } else {
            WDA_NONE
        };
        if rows.iter().any(|row| {
            row["querySucceeded"] != true
                || row["affinity"] != affinity
                || row["guardInstalled"] != enabled
                || row["guardEnabled"] != enabled
        }) {
            return Err(format!(
                "Windows did not report the exact capture policy and guard on every fixture window at {stage}."
            ));
        }
    }
    Ok(())
}
fn fire(app: &tauri::AppHandle) -> Result<()> {
    let state = app.state::<Privacy>();
    if unsafe { PostThreadMessageW(state.driver_thread, WM_HOTKEY, HOTKEY_ID as usize, 0) } == 0 {
        return Err("Could not send the registered shortcut fixture message.".into());
    }
    Ok(())
}
fn wait_hidden(app: &tauri::AppHandle, expected: bool) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(5);
    while is_hidden(app) != expected {
        if Instant::now() >= deadline {
            return Err(
                "The registered shortcut did not apply the requested privacy state.".into(),
            );
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Ok(())
}
pub(super) fn check(app: &tauri::AppHandle) -> Result<Value> {
    let state = app.state::<Privacy>();
    if unsafe { GetCurrentThreadId() } == state.gui_thread {
        return Err("Run the native privacy fixture on its worker thread.".into());
    }
    let original = snapshot(app);
    let observations: Observations = Arc::new(Mutex::new(Vec::new()));
    let result = (|| -> Result<Value> {
        configure(app, true, true)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || {
            let visible = tauri::window::WindowBuilder::new(&app_clone, VISIBLE)
                .title("Privacy fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            let background = tauri::window::WindowBuilder::new(&app_clone, BACKGROUND)
                .title("Hidden fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            observe(&app_clone, &stages, "created-hidden", &[VISIBLE, BACKGROUND], None)?;
            apply_on_gui(&app_clone, &background)?;
            observe(&app_clone, &stages, "background-policy-applied", &[BACKGROUND], Some(true))?;
            guarded_show(&app_clone, &visible, false)?;
            observe(&app_clone, &stages, "after-guarded-show", &[VISIBLE, BACKGROUND], Some(true))?;
            if !visible.is_visible().map_err(err)? || background.is_visible().map_err(err)? {
                return Err("The initial visible/background fixture state is incorrect.".into());
            }
            Ok(())
        })?;
        // A second thread cannot silently steal the configured global shortcut.
        let duplicate = unsafe {
            RegisterHotKey(
                std::ptr::null_mut(),
                HOTKEY_ID + 1,
                MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                u32::from(b'H'),
            )
        };
        if duplicate != 0 {
            unsafe {
                UnregisterHotKey(std::ptr::null_mut(), HOTKEY_ID + 1);
            }
            return Err(
                "The active shortcut unexpectedly allowed a conflicting registration.".into(),
            );
        }
        fire(app)?;
        wait_hidden(app, true)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || {
            observe(&app_clone, &stages, "after-quick-hide", &[VISIBLE, BACKGROUND], Some(true))?;
            let late = tauri::window::WindowBuilder::new(&app_clone, LATE)
                .title("Late fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            guarded_show(&app_clone, &late, true)?;
            observe(&app_clone, &stages, "after-late-window", &[VISIBLE, BACKGROUND, LATE], Some(true))?;
            for label in [VISIBLE, BACKGROUND, LATE] {
                let window = app_clone
                    .get_window(label)
                    .ok_or("Missing privacy fixture window.")?;
                if window.is_visible().map_err(err)? {
                    return Err("Quick hide revealed a visible or newly requested window.".into());
                }
            }
            Ok(())
        })?;
        fire(app)?;
        wait_hidden(app, false)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || {
            observe(&app_clone, &stages, "after-restore", &[VISIBLE, BACKGROUND, LATE], Some(true))?;
            for (label, expected) in [(VISIBLE, true), (BACKGROUND, false), (LATE, true)] {
                let window = app_clone
                    .get_window(label)
                    .ok_or("Missing privacy fixture window.")?;
                if window.is_visible().map_err(err)? != expected {
                    return Err(format!(
                        "Privacy restore changed the intended visibility of {label}."
                    ));
                }
            }
            let window = app_clone.get_window(VISIBLE).ok_or("Missing visible privacy fixture.")?;
            window.set_always_on_top(true).map_err(err)?;
            window.set_resizable(false).map_err(err)?;
            observe(&app_clone, &stages, "after-unrelated-style-changes", &[VISIBLE], Some(true))?;
            if !window.is_always_on_top().map_err(err)? || window.is_resizable().map_err(err)? {
                return Err("Capture preservation changed unrelated native style choices.".into());
            }
            window.set_always_on_top(false).map_err(err)?;
            window.set_resizable(true).map_err(err)?;
            observe(&app_clone, &stages, "after-style-restore", &[VISIBLE], Some(true))?;
            Ok(())
        })?;
        configure(app, true, false)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || observe(&app_clone, &stages, "capture-disabled", &[VISIBLE, BACKGROUND, LATE], Some(false)))?;
        configure(app, true, true)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        let retired = on_gui(app, move || {
            observe(&app_clone, &stages, "capture-reenabled", &[VISIBLE, BACKGROUND, LATE], Some(true))?;
            let late = app_clone.get_window(LATE).ok_or("Missing late privacy fixture.")?;
            let hwnd = late.hwnd().map_err(err)?.0 as usize;
            late.destroy().map_err(err)?;
            forget_window(&app_clone, LATE);
            Ok(hwnd)
        })?;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let app_clone = app.clone();
            let gone = on_gui(app, move || Ok(app_clone.get_window(LATE).is_none()
                && unsafe { IsWindow(retired as HWND) } == 0))?;
            if gone { break; }
            if Instant::now() >= deadline {
                return Err("A protected fixture window did not complete native destruction.".into());
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        Ok(
            json!({"captureAffinityConfirmed":true,"globalShortcutRegistered":true,"conflictingShortcutRejected":true,"guiThreadWindowMutation":true,"allVisibleWindowsHidden":true,"newWindowHeldWhileHidden":true,"backgroundWindowRemainedHidden":true,"intendedWindowsRestored":true,
                "frameworkStyleTransitionsRetained":true,"unrelatedStyleChoicesRetained":true,
                "captureDisableRemovedGuard":true,"captureReenableVerified":true,"protectedWindowDestroyed":true,
                "affinityStages":observations.lock().map_err(err)?.clone(),
                "shortcutDelivery":"Synthetic WM_HOTKEY after real RegisterHotKey; physical keyboard behavior is not claimed."}),
        )
    })().map_err(|error| format!("{error} Owned privacy observations: {}", json!(observations.lock().unwrap_or_else(|e|e.into_inner()).clone())));
    if is_hidden(app) {
        let _ = toggle(app);
    }
    let app_clone = app.clone();
    let _ = on_gui(app, move || {
        for label in [VISIBLE, BACKGROUND, LATE] {
            if let Some(window) = app_clone.get_window(label) {
                let _ = window.destroy();
            }
            forget_window(&app_clone, label);
        }
        Ok(())
    });
    let restored = configure(
        app,
        original["quickHide"].as_bool().unwrap_or(false),
        original["captureProtection"].as_bool().unwrap_or(false),
    );
    match (result, restored) {
        (Ok(value), Ok(())) => Ok(value),
        (Err(error), _) => Err(error),
        (_, Err(error)) => Err(error),
    }
}
