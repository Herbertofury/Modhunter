//! Windows-only fixture evidence. Shortcut delivery is a synthetic WM_HOTKEY
//! message after real OS registration, not a claimed physical keyboard test.
use super::*;
const VISIBLE: &str = "privacy-smoke-visible";
const BACKGROUND: &str = "privacy-smoke-background";
const LATE: &str = "privacy-smoke-late";
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
    let result = (|| {
        configure(app, true, true)?;
        let app_clone = app.clone();
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
            apply_on_gui(&app_clone, &background)?;
            guarded_show(&app_clone, &visible, false)?;
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
        on_gui(app, move || {
            let late = tauri::window::WindowBuilder::new(&app_clone, LATE)
                .title("Late fixture")
                .visible(false)
                .focused(false)
                .build()
                .map_err(err)?;
            guarded_show(&app_clone, &late, true)?;
            for label in [VISIBLE, BACKGROUND, LATE] {
                let window = app_clone
                    .get_window(label)
                    .ok_or("Missing privacy fixture window.")?;
                if window.is_visible().map_err(err)? {
                    return Err("Quick hide revealed a visible or newly requested window.".into());
                }
                let mut affinity = 0;
                if unsafe { GetWindowDisplayAffinity(window.hwnd().map_err(err)?.0, &mut affinity) }
                    == 0
                    || affinity != WDA_EXCLUDEFROMCAPTURE
                {
                    return Err(
                        "Windows did not report capture exclusion on every fixture window.".into(),
                    );
                }
            }
            Ok(())
        })?;
        fire(app)?;
        wait_hidden(app, false)?;
        let app_clone = app.clone();
        on_gui(app, move || {
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
            Ok(())
        })?;
        Ok(
            json!({"captureAffinityConfirmed":true,"globalShortcutRegistered":true,"conflictingShortcutRejected":true,"guiThreadWindowMutation":true,"allVisibleWindowsHidden":true,"newWindowHeldWhileHidden":true,"backgroundWindowRemainedHidden":true,"intendedWindowsRestored":true,"shortcutDelivery":"Synthetic WM_HOTKEY after real RegisterHotKey; physical keyboard behavior is not claimed."}),
        )
    })();
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
