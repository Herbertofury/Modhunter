//! Optional app-wide Windows privacy requests. Window visibility and capture
//! affinity are changed only on the GUI thread. This is not screenshot immunity.
use super::*;
use std::collections::BTreeSet;
use tauri::Window;
use windows_sys::Win32::{
    System::Threading::GetCurrentThreadId,
    UI::{
        Input::KeyboardAndMouse::{
            MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, RegisterHotKey, UnregisterHotKey,
        },
        WindowsAndMessaging::{
            GetMessageW, GetWindowDisplayAffinity, MSG, PM_NOREMOVE, PeekMessageW,
            PostThreadMessageW, SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE, WDA_NONE, WM_APP,
            WM_HOTKEY,
        },
    },
};
const HOTKEY_ID: i32 = 0x4d48;
const DRIVER_WAKE: u32 = WM_APP + 0x4d;
#[derive(Default)]
struct Model {
    quick_hide: bool,
    capture_protection: bool,
    shortcut_registered: bool,
    app_hidden: bool,
    visible_intent: BTreeSet<String>,
    focus_intent: Option<String>,
    protected: BTreeSet<String>,
    registration_error: String,
    capture_errors: BTreeMap<String, String>,
    visibility_error: String,
}
enum DriverCommand {
    Configure(bool, mpsc::SyncSender<Result<()>>),
    Stop,
}
struct Privacy {
    gui_thread: u32,
    driver_thread: u32,
    commands: mpsc::Sender<DriverCommand>,
    model: Mutex<Model>,
}
fn on_gui<T: Send + 'static>(
    app: &tauri::AppHandle,
    action: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    let state = app
        .try_state::<Privacy>()
        .ok_or("Native privacy has not initialized.")?;
    if unsafe { GetCurrentThreadId() } == state.gui_thread {
        return action();
    }
    let (send, receive) = mpsc::sync_channel(1);
    let alive = Arc::new(AtomicBool::new(true));
    let pending = alive.clone();
    let deadline = Instant::now() + Duration::from_secs(15);
    app.run_on_main_thread(move || {
        if !pending.swap(false, Ordering::AcqRel) || Instant::now() >= deadline {
            let _ = send.send(Err("The privacy action expired before GUI dispatch.".into()));
            return;
        }
        let _ = send.send(action());
    })
    .map_err(err)?;
    receive
        .recv_timeout(Duration::from_secs(15))
        .unwrap_or_else(|_| {
            alive.store(false, Ordering::Release);
            Err("The privacy action timed out before its result was confirmed.".into())
        })
}
pub(crate) fn setup(app: &tauri::AppHandle) -> Result<()> {
    if app.try_state::<Privacy>().is_some() {
        return Ok(());
    }
    let (commands, receive) = mpsc::channel();
    let (ready, started) = mpsc::sync_channel(1);
    let handle = app.clone();
    std::thread::spawn(move || {
        // RegisterHotKey with a null HWND belongs to this message queue. The
        // window GUI thread is never blocked waiting for keyboard messages.
        let thread = unsafe { GetCurrentThreadId() };
        let mut message: MSG = unsafe { std::mem::zeroed() };
        unsafe {
            PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_NOREMOVE);
        }
        let _ = ready.send(thread);
        let mut registered = false;
        loop {
            let got = unsafe { GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) };
            if got <= 0 {
                break;
            }
            if message.message == WM_HOTKEY && message.wParam == HOTKEY_ID as usize && registered {
                let app = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    let _ = toggle_on_gui(&app);
                });
            }
            if message.message == DRIVER_WAKE {
                while let Ok(command) = receive.try_recv() {
                    match command {
                        DriverCommand::Configure(enabled, reply) => {
                            let result = if enabled == registered {
                                Ok(())
                            } else if enabled {
                                if unsafe {
                                    RegisterHotKey(
                                        std::ptr::null_mut(),
                                        HOTKEY_ID,
                                        MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                                        u32::from(b'H'),
                                    )
                                } != 0
                                {
                                    registered = true;
                                    Ok(())
                                } else {
                                    Err("Ctrl+Alt+H could not be registered. Another application may already use it; quick hide is not active.".into())
                                }
                            } else if unsafe { UnregisterHotKey(std::ptr::null_mut(), HOTKEY_ID) }
                                != 0
                            {
                                registered = false;
                                Ok(())
                            } else {
                                Err(
                                    "Windows did not confirm removal of the quick-hide shortcut."
                                        .into(),
                                )
                            };
                            let _ = reply.send(result);
                        }
                        DriverCommand::Stop => {
                            if registered {
                                unsafe {
                                    UnregisterHotKey(std::ptr::null_mut(), HOTKEY_ID);
                                }
                            }
                            return;
                        }
                    }
                }
            }
        }
        if registered {
            unsafe {
                UnregisterHotKey(std::ptr::null_mut(), HOTKEY_ID);
            }
        }
    });
    let driver_thread = started
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| "Could not initialize the privacy shortcut message queue.")?;
    app.manage(Privacy {
        gui_thread: unsafe { GetCurrentThreadId() },
        driver_thread,
        commands,
        model: Mutex::new(Model::default()),
    });
    Ok(())
}
pub(crate) fn snapshot(app: &tauri::AppHandle) -> Value {
    let Some(state) = app.try_state::<Privacy>() else {
        return json!({"available":false,"status":"Not initialized"});
    };
    let model = state.model.lock().unwrap_or_else(|e| e.into_inner());
    json!({"available":true,"quickHide":model.quick_hide,"shortcut":"Ctrl+Alt+H","shortcutRegistered":model.shortcut_registered,"appHidden":model.app_hidden,"captureProtection":model.capture_protection,
        "protectedWindowCount":model.protected.len(),"registrationError":model.registration_error,"captureErrors":model.capture_errors,"visibilityError":model.visibility_error,
        "status":if !model.registration_error.is_empty()||!model.capture_errors.is_empty()||!model.visibility_error.is_empty(){"Needs attention"}else if model.app_hidden{"Windows hidden"}else{"Ready"},
        "captureScope":"Windows capture exclusion is a platform request. It does not prevent cameras, all capture tools, or privileged software."})
}
pub(crate) fn is_hidden(app: &tauri::AppHandle) -> bool {
    app.try_state::<Privacy>()
        .is_some_and(|s| s.model.lock().unwrap_or_else(|e| e.into_inner()).app_hidden)
}
fn affinity(window: &Window, enabled: bool) -> Result<()> {
    let hwnd = window.hwnd().map_err(err)?.0;
    let expected = if enabled {
        WDA_EXCLUDEFROMCAPTURE
    } else {
        WDA_NONE
    };
    let mut actual = 0;
    if unsafe { SetWindowDisplayAffinity(hwnd, expected) } == 0
        || unsafe { GetWindowDisplayAffinity(hwnd, &mut actual) } == 0
        || actual != expected
    {
        return Err(
            "Windows did not confirm the requested capture exclusion for this window.".into(),
        );
    }
    Ok(())
}
fn apply_on_gui(app: &tauri::AppHandle, window: &Window) -> Result<()> {
    let state = app.state::<Privacy>();
    let enabled = state.model.lock().map_err(err)?.capture_protection;
    let result = affinity(window, enabled);
    let mut model = state.model.lock().map_err(err)?;
    match &result {
        Ok(()) => {
            model.capture_errors.remove(window.label());
            if enabled {
                model.protected.insert(window.label().into());
            } else {
                model.protected.remove(window.label());
            }
        }
        Err(error) => {
            model.protected.remove(window.label());
            model
                .capture_errors
                .insert(window.label().into(), error.clone());
        }
    }
    result
}
pub(crate) fn apply_window(app: &tauri::AppHandle, window: &Window) -> Result<()> {
    let a = app.clone();
    let w = window.clone();
    on_gui(app, move || apply_on_gui(&a, &w))
}
pub(crate) fn guarded_show(app: &tauri::AppHandle, window: &Window, focus: bool) -> Result<()> {
    let a = app.clone();
    let w = window.clone();
    on_gui(app, move || {
        apply_on_gui(&a, &w)?;
        let state = a.state::<Privacy>();
        let mut model = state.model.lock().map_err(err)?;
        if model.app_hidden {
            model.visible_intent.insert(w.label().into());
            if focus {
                model.focus_intent = Some(w.label().into());
            }
            w.hide().map_err(err)?;
            return Ok(());
        }
        drop(model);
        w.show().map_err(err)?;
        if focus {
            w.set_focus().map_err(err)?;
        }
        Ok(())
    })
}
fn toggle_on_gui(app: &tauri::AppHandle) -> Result<()> {
    let state = app.state::<Privacy>();
    let mut model = state.model.lock().map_err(err)?;
    if !model.quick_hide {
        return Err("Enable the optional quick-hide shortcut first.".into());
    }
    let windows = app.windows();
    model.visibility_error.clear();
    if !model.app_hidden {
        model.visible_intent.clear();
        model.focus_intent = None;
        for (label, window) in &windows {
            if window.is_visible().map_err(err)? {
                model.visible_intent.insert(label.clone());
            }
            if window.is_focused().unwrap_or(false) {
                model.focus_intent = Some(label.clone());
            }
        }
        model.app_hidden = true;
        for (label, window) in &windows {
            if let Err(error) = window.hide() {
                model.visibility_error = format!("Could not hide {label}: {error}");
            }
        }
        drop(model);
    } else {
        let wanted = model.visible_intent.clone();
        let focus = model.focus_intent.clone();
        drop(model);
        // Keep the hidden flag until every intended window has capture policy.
        for label in &wanted {
            if let Some(window) = windows.get(label) {
                apply_on_gui(app, window)?;
            }
        }
        let mut model = state.model.lock().map_err(err)?;
        model.app_hidden = false;
        for label in &wanted {
            if let Some(window) = windows.get(label)
                && let Err(error) = window.show()
            {
                model.visibility_error = format!("Could not restore {label}: {error}");
            }
        }
        if let Some(window) = focus.and_then(|label| windows.get(&label)) {
            let _ = window.set_focus();
        }
        model.visible_intent.clear();
        model.focus_intent = None;
        drop(model);
    }
    let _ = app.emit("modhunter-privacy", snapshot(app));
    Ok(())
}
pub(crate) fn toggle(app: &tauri::AppHandle) -> Result<()> {
    let a = app.clone();
    on_gui(app, move || toggle_on_gui(&a))
}
pub(crate) fn configure(
    app: &tauri::AppHandle,
    quick_hide: bool,
    capture_protection: bool,
) -> Result<()> {
    let state = app
        .try_state::<Privacy>()
        .ok_or("Native privacy has not initialized.")?;
    if !quick_hide && is_hidden(app) {
        toggle(app)?;
    }
    let (send, receive) = mpsc::sync_channel(1);
    state
        .commands
        .send(DriverCommand::Configure(quick_hide, send))
        .map_err(err)?;
    if unsafe { PostThreadMessageW(state.driver_thread, DRIVER_WAKE, 0, 0) } == 0 {
        return Err("Could not contact the quick-hide shortcut message queue.".into());
    }
    let registered = receive
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| "Shortcut registration timed out.")?;
    {
        let mut model = state.model.lock().map_err(err)?;
        model.registration_error = registered.as_ref().err().cloned().unwrap_or_default();
        if registered.is_ok() {
            model.quick_hide = quick_hide;
            model.shortcut_registered = quick_hide;
        }
        model.capture_protection = capture_protection;
    }
    let a = app.clone();
    let captures = on_gui(app, move || {
        let mut errors = Vec::new();
        for window in a.windows().values() {
            if let Err(error) = apply_on_gui(&a, window) {
                errors.push(error);
            }
        }
        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors.join(" "))
        }
    });
    let _ = app.emit("modhunter-privacy", snapshot(app));
    registered.and(captures)
}
pub(crate) fn forget_window(app: &tauri::AppHandle, label: &str) {
    if let Some(state) = app.try_state::<Privacy>()
        && let Ok(mut model) = state.model.lock()
    {
        model.visible_intent.remove(label);
        model.protected.remove(label);
        model.capture_errors.remove(label);
        if model.focus_intent.as_deref() == Some(label) {
            model.focus_intent = None;
        }
    }
}
pub(crate) fn shutdown(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Privacy>() {
        if let Ok(mut model) = state.model.lock() {
            model.quick_hide = false;
        }
        let _ = state.commands.send(DriverCommand::Stop);
        unsafe {
            PostThreadMessageW(state.driver_thread, DRIVER_WAKE, 0, 0);
        }
    }
}

#[path = "watchtower_privacy_smoke.rs"]
mod smoke;
pub(super) fn smoke_check(app: &tauri::AppHandle) -> Result<Value> {
    smoke::check(app)
}
