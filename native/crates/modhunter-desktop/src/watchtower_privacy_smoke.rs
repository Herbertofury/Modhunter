//! Windows-only fixture evidence. Shortcut delivery is a synthetic WM_HOTKEY
//! message after real OS registration, not a claimed physical keyboard test.
use super::*;
use windows_sys::Win32::{
    Foundation::{GetLastError, HWND},
    UI::WindowsAndMessaging::{GWL_EXSTYLE, GetWindowLongPtrW, IsWindow, WS_EX_LAYERED},
};
const VISIBLE: &str = "privacy-smoke-visible";
const BACKGROUND: &str = "privacy-smoke-background";
const LATE: &str = "privacy-smoke-late";
type Observations = Arc<Mutex<Vec<Value>>>;
type OwnedWindows = Arc<Mutex<BTreeMap<String, usize>>>;

fn remember_window(owned: &OwnedWindows, window: &Window) -> Result<()> {
    let hwnd = window.hwnd().map_err(err)?.0 as usize;
    owned
        .lock()
        .map_err(err)?
        .insert(window.label().into(), hwnd);
    Ok(())
}

fn destroy_windows(app: &tauri::AppHandle, windows: BTreeMap<String, usize>) -> Result<()> {
    let app_clone = app.clone();
    let requested = windows.clone();
    let mut failures = on_gui(app, move || {
        let mut failures = Vec::new();
        for (label, expected) in requested {
            if let Some(window) = app_clone.get_window(&label) {
                // Never destroy a different window if a fixture label was reused.
                match window.hwnd() {
                    Ok(hwnd) if hwnd.0 as usize == expected => {
                        if let Err(error) = window.destroy() {
                            failures
                                .push(format!("Could not request destruction of {label}: {error}"));
                        }
                    }
                    Ok(_) => failures.push(format!(
                        "The owned native window identity changed for {label}."
                    )),
                    Err(error) => failures.push(format!(
                        "Could not read the retiring fixture {label}: {error}"
                    )),
                }
            }
        }
        Ok(failures)
    })?;
    // Unlike show/hide, Tauri destroy always posts an event even on the GUI
    // thread. A label may briefly outlive its usable dispatcher handle. Wait
    // for both independent lifetime signals before configuring all app windows.
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let app_clone = app.clone();
        let pending = windows.clone();
        let remaining = on_gui(app, move || {
            let mut remaining = Vec::new();
            for (label, hwnd) in pending {
                let registered = app_clone.get_window(&label).is_some();
                let native_alive = unsafe { IsWindow(hwnd as HWND) } != 0;
                if registered || native_alive {
                    remaining.push(
                        json!({"label":label,"registered":registered,"nativeAlive":native_alive}),
                    );
                }
            }
            Ok(remaining)
        })?;
        if remaining.is_empty() {
            break;
        }
        if Instant::now() >= deadline {
            failures.push(format!(
                "The owned privacy fixture windows did not finish destruction: {}",
                json!(remaining)
            ));
            return Err(failures.join(" "));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    for label in windows.keys() {
        forget_window(app, label);
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(failures.join(" "))
    }
}

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
        rows.push(
            json!({"label":label,"querySucceeded":succeeded,"affinity":actual,
            "win32Error":error,"extendedStyle":style,"layered":style&WS_EX_LAYERED!=0,
            "visible":window.is_visible().map_err(err)?}),
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
        if rows
            .iter()
            .any(|row| row["querySucceeded"] != true || row["affinity"] != affinity)
        {
            return Err(format!(
                "Windows did not report the exact capture policy on every fixture window at {stage}."
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
    let owned: OwnedWindows = Arc::new(Mutex::new(BTreeMap::new()));
    let result = (|| -> Result<Value> {
        configure(app, true, true)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        let created = owned.clone();
        on_gui(app, move || {
            let visible = tauri::window::WindowBuilder::new(&app_clone, VISIBLE)
                .title("Privacy fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            remember_window(&created, &visible)?;
            let background = tauri::window::WindowBuilder::new(&app_clone, BACKGROUND)
                .title("Hidden fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            remember_window(&created, &background)?;
            observe(
                &app_clone,
                &stages,
                "created-hidden",
                &[VISIBLE, BACKGROUND],
                None,
            )?;
            apply_on_gui(&app_clone, &background)?;
            observe(
                &app_clone,
                &stages,
                "background-policy-applied",
                &[BACKGROUND],
                Some(true),
            )?;
            guarded_show(&app_clone, &visible, false)?;
            observe(
                &app_clone,
                &stages,
                "after-guarded-show",
                &[VISIBLE, BACKGROUND],
                Some(true),
            )?;
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
        let created = owned.clone();
        on_gui(app, move || {
            observe(
                &app_clone,
                &stages,
                "after-quick-hide",
                &[VISIBLE, BACKGROUND],
                Some(true),
            )?;
            let late = tauri::window::WindowBuilder::new(&app_clone, LATE)
                .title("Late fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            remember_window(&created, &late)?;
            guarded_show(&app_clone, &late, true)?;
            observe(
                &app_clone,
                &stages,
                "after-late-window",
                &[VISIBLE, BACKGROUND, LATE],
                Some(true),
            )?;
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
            observe(
                &app_clone,
                &stages,
                "after-restore",
                &[VISIBLE, BACKGROUND, LATE],
                Some(true),
            )?;
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
            let window = app_clone
                .get_window(VISIBLE)
                .ok_or("Missing visible privacy fixture.")?;
            window.set_always_on_top(true).map_err(err)?;
            window.set_resizable(false).map_err(err)?;
            observe(
                &app_clone,
                &stages,
                "after-unrelated-style-changes",
                &[VISIBLE],
                Some(true),
            )?;
            if !window.is_always_on_top().map_err(err)? || window.is_resizable().map_err(err)? {
                return Err("Capture preservation changed unrelated native style choices.".into());
            }
            window.set_always_on_top(false).map_err(err)?;
            window.set_resizable(true).map_err(err)?;
            observe(
                &app_clone,
                &stages,
                "after-style-restore",
                &[VISIBLE],
                Some(true),
            )?;
            Ok(())
        })?;
        configure(app, true, false)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || {
            observe(
                &app_clone,
                &stages,
                "capture-disabled",
                &[VISIBLE, BACKGROUND, LATE],
                Some(false),
            )
        })?;
        configure(app, true, true)?;
        let app_clone = app.clone();
        let stages = observations.clone();
        on_gui(app, move || {
            observe(
                &app_clone,
                &stages,
                "capture-reenabled",
                &[VISIBLE, BACKGROUND, LATE],
                Some(true),
            )?;
            Ok(())
        })?;
        let retired_hwnd = *owned
            .lock()
            .map_err(err)?
            .get(LATE)
            .ok_or("The protected late fixture has no recorded native lifetime.")?;
        let retired = BTreeMap::from([(LATE.to_owned(), retired_hwnd)]);
        destroy_windows(app, retired)?;
        owned.lock().map_err(err)?.remove(LATE);
        Ok(
            json!({"captureAffinityConfirmed":true,"globalShortcutRegistered":true,"conflictingShortcutRejected":true,"guiThreadWindowMutation":true,"allVisibleWindowsHidden":true,"newWindowHeldWhileHidden":true,"backgroundWindowRemainedHidden":true,"intendedWindowsRestored":true,
                "frameworkStyleTransitionsRetained":true,"unrelatedStyleChoicesRetained":true,
                "captureDisabledVerified":true,"captureReenableVerified":true,"protectedWindowDestroyed":true,
                "affinityStages":observations.lock().map_err(err)?.clone(),
                "shortcutDelivery":"Synthetic WM_HOTKEY after real RegisterHotKey; physical keyboard behavior is not claimed."}),
        )
    })();
    let mut failures = Vec::new();
    if let Err(error) = &result {
        failures.push(format!("Privacy checks: {error}"));
    }
    if is_hidden(app)
        && let Err(error) = toggle(app)
    {
        failures.push(format!("Privacy visibility cleanup: {error}"));
    }
    let retired = owned
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .clone();
    match destroy_windows(app, retired) {
        Ok(()) => {
            if let Err(error) = configure(
                app,
                original["quickHide"].as_bool().unwrap_or(false),
                original["captureProtection"].as_bool().unwrap_or(false),
            ) {
                failures.push(format!("Privacy settings restoration: {error}"));
            }
        }
        Err(error) => failures.push(format!("Privacy fixture destruction: {error}; settings restoration was not attempted before destruction was confirmed.")),
    }
    if failures.is_empty() {
        let mut value = result?;
        value["allFixtureWindowsDestroyed"] = json!(true);
        value["originalSettingsRestored"] = json!(true);
        Ok(value)
    } else {
        Err(format!(
            "{} Owned privacy observations: {}",
            failures.join(" "),
            json!(
                observations
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .clone()
            )
        ))
    }
}
