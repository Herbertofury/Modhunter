//! The renderer approves a digest; native code chooses every filesystem path.
//! A separate invocation of this exact executable runs only after shutdown.
use super::*;
use modhunter_core::app_reset::{self, AppPaths, Coordinator, Pending};
use std::{os::windows::process::CommandExt, process::Command};
use windows_sys::Win32::{
    Foundation::{CloseHandle, WAIT_OBJECT_0},
    System::Threading::{OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject},
};

struct ResetRuntime {
    coordinator: Coordinator,
    ownership_error: String,
    scheduled: AtomicBool,
    lock: Mutex<()>,
}

pub(super) fn setup(app: &tauri::AppHandle, paths: AppPaths) -> Result<()> {
    if app.config().identifier != app_reset::APPLICATION {
        return Err("Application identity does not match its reset owner.".into());
    }
    // The helper cannot depend on a running Tauri/WebView2 environment. Confirm
    // its independent Known Folder resolution matches the native app resolver.
    let expected = native_paths()?;
    let same = |a: &Path, b: &Path| -> Result<bool> {
        Ok(
            std::fs::canonicalize(a.parent().ok_or("Missing app path parent.")?).map_err(err)?
                == std::fs::canonicalize(b.parent().ok_or("Missing app path parent.")?)
                    .map_err(err)?
                && a.file_name() == b.file_name(),
        )
    };
    if !same(&paths.config, &expected.config)?
        || !same(&paths.data, &expected.data)?
        || !same(&paths.local_data, &expected.local_data)?
    {
        return Err("Custom application storage requires a matching reset path resolver.".into());
    }
    let coordinator = Coordinator::open(paths)?;
    // An unfamiliar historical file disables reset without preventing normal
    // access to that user's app. It never acquires deletion authority.
    let ownership_error = coordinator.adopt_roots().err().unwrap_or_default();
    app.manage(ResetRuntime {
        coordinator,
        ownership_error,
        scheduled: AtomicBool::new(false),
        lock: Mutex::new(()),
    });
    Ok(())
}

pub(super) fn pending(app: &tauri::AppHandle) -> bool {
    app.try_state::<ResetRuntime>()
        .is_some_and(|state| state.scheduled.load(Ordering::Acquire))
}
pub(super) fn status(app: &tauri::AppHandle) -> Value {
    let Some(state) = app.try_state::<ResetRuntime>() else {
        return json!({"available":false});
    };
    json!({"available":state.ownership_error.is_empty(),"ownershipError":state.ownership_error,
        "scheduled":state.scheduled.load(Ordering::Acquire),"lastCompletion":state.coordinator.last_report().ok().flatten()})
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    let state = app
        .try_state::<ResetRuntime>()
        .ok_or("Application reset is unavailable in this isolated fixture.")?;
    let _guard = state.lock.lock().map_err(err)?;
    if !state.ownership_error.is_empty() {
        return Err(state.ownership_error.clone());
    }
    if state.scheduled.load(Ordering::Acquire) {
        return Err("Application reset is already scheduled; Modhunter is closing.".into());
    }
    review::ensure_resettable(app)?;
    match kind {
        "previewAppReset" => {
            let mut reset = state.coordinator.preview()?;
            reset["lastCompletion"] = state.coordinator.last_report()?.unwrap_or(Value::Null);
            Ok(json!({"reset":reset}))
        }
        "applyAppReset" => {
            let expected = value["expectedDigest"]
                .as_str()
                .ok_or("Review the application reset inventory first.")?;
            let confirmation = value["confirm"].as_str().unwrap_or_default();
            let executable = std::env::current_exe().map_err(err)?;
            let scheduled = state.coordinator.schedule(
                expected,
                confirmation,
                &executable,
                std::process::id(),
            )?;
            state.scheduled.store(true, Ordering::Release);
            let spawned = Command::new(&executable)
                .arg(format!("--finish-app-reset={}", scheduled.plan.display()))
                .creation_flags(0x0800_0000 | 0x0000_0200)
                .spawn();
            let mut child = match spawned {
                Ok(child) => child,
                Err(error) => {
                    state.scheduled.store(false, Ordering::Release);
                    state.coordinator.cancel_schedule(
                        &scheduled,
                        &format!("Reset helper could not start: {error}"),
                    )?;
                    return Err(format!(
                        "Reset helper could not start. Application data was preserved: {error}"
                    ));
                }
            };
            let deadline = Instant::now() + Duration::from_secs(8);
            let ready = loop {
                if scheduled.ready.exists() {
                    let valid = std::fs::read(&scheduled.ready)
                        .ok()
                        .filter(|b| b.len() <= 16 * 1024)
                        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                        .is_some_and(|v| {
                            v["schema"] == 1
                                && v["id"] == scheduled.id
                                && v["helperPid"] == child.id()
                        });
                    if valid {
                        break true;
                    }
                }
                if child.try_wait().map_err(err)?.is_some() || Instant::now() >= deadline {
                    break false;
                }
                std::thread::sleep(Duration::from_millis(40));
            };
            if !ready {
                let _ = child.kill();
                let _ = child.wait();
                state.scheduled.store(false, Ordering::Release);
                state.coordinator.cancel_schedule(
                    &scheduled,
                    "Reset helper did not confirm readiness. Application data was preserved.",
                )?;
                return Err("Reset helper did not confirm readiness. Modhunter remains open and its data was preserved.".into());
            }
            let handle = app.clone();
            std::thread::spawn(move || {
                // Let the initiating command deliver its report location before
                // the normal shutdown closes site views and cancels downloads.
                std::thread::sleep(Duration::from_millis(750));
                shutdown(&handle);
                handle.exit(0);
            });
            Ok(
                json!({"reset":{"state":"scheduled","completionReport":scheduled.completion_report,"requiresExit":true},
                "message":"Modhunter is closing to reset its reviewed application data. Changed or locked files will be retained and listed in the completion report."}),
            )
        }
        _ => Err("Unknown application reset command.".into()),
    }
}

#[repr(C)]
struct Guid {
    data1: u32,
    data2: u16,
    data3: u16,
    data4: [u8; 8],
}
#[link(name = "shell32")]
unsafe extern "system" {
    fn SHGetKnownFolderPath(
        id: *const Guid,
        flags: u32,
        token: *mut std::ffi::c_void,
        path: *mut *mut u16,
    ) -> i32;
}
#[link(name = "ole32")]
unsafe extern "system" {
    fn CoTaskMemFree(pointer: *mut std::ffi::c_void);
}
fn folder(id: &Guid) -> Result<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    let mut pointer = std::ptr::null_mut();
    // The Shell allocates a terminated UTF-16 path; every success frees it with
    // the paired allocator, including an invalid/unbounded returned string.
    let result = unsafe { SHGetKnownFolderPath(id, 0, std::ptr::null_mut(), &mut pointer) };
    if result < 0 || pointer.is_null() {
        return Err(format!(
            "Windows application folder lookup failed ({result:#x})."
        ));
    }
    let mut len = 0;
    while len < 32768 && unsafe { *pointer.add(len) } != 0 {
        len += 1;
    }
    let path = if len == 32768 {
        Err("Windows application path exceeds its bound.".into())
    } else {
        Ok(PathBuf::from(std::ffi::OsString::from_wide(unsafe {
            std::slice::from_raw_parts(pointer, len)
        })))
    };
    unsafe { CoTaskMemFree(pointer.cast()) };
    path
}
fn native_paths() -> Result<AppPaths> {
    const ROAMING: Guid = Guid {
        data1: 0x3eb685db,
        data2: 0x65f9,
        data3: 0x4cf6,
        data4: [0xa0, 0x3a, 0xe3, 0xef, 0x65, 0x72, 0x9f, 0x3d],
    };
    const LOCAL: Guid = Guid {
        data1: 0xf1b32785,
        data2: 0x6fba,
        data3: 0x4fcf,
        data4: [0x9d, 0x55, 0x7b, 0x8e, 0x7f, 0x15, 0x70, 0x91],
    };
    let config = folder(&ROAMING)?.join(app_reset::APPLICATION);
    Ok(AppPaths {
        data: config.clone(),
        config,
        local_data: folder(&LOCAL)?.join(app_reset::APPLICATION),
    })
}
fn helper(plan: PathBuf) -> Result<()> {
    let paths = native_paths()?;
    let pending = Pending::load(&paths, &plan, &std::env::current_exe().map_err(err)?)?;
    // Open before acknowledging readiness, so PID reuse can never turn the wait
    // into permission to operate while the initiating process is still alive.
    let parent = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pending.parent_pid()) };
    if parent.is_null() {
        return pending
            .failed("The initiating process identity could not be opened; reset did not run.")
            .map(|_| ());
    }
    if let Err(error) = pending.ready() {
        unsafe { CloseHandle(parent) };
        return Err(error);
    }
    let outcome = unsafe { WaitForSingleObject(parent, 180_000) };
    unsafe { CloseHandle(parent) };
    if outcome != WAIT_OBJECT_0 {
        return pending
            .failed("Modhunter did not exit before the reset deadline. Its data was preserved.")
            .map(|_| ());
    }
    // WebView2 child processes can release their database handles shortly after
    // their parent exits. A remaining lock still produces a retained-data report.
    std::thread::sleep(Duration::from_millis(750));
    pending.finish(Duration::from_secs(30)).map(|_| ())
}
/// Runs before Tauri setup, so the helper never recreates a webview or app data.
pub(crate) fn dispatch_helper() -> bool {
    let Some(path) = std::env::args()
        .skip(1)
        .find_map(|arg| arg.strip_prefix("--finish-app-reset=").map(PathBuf::from))
    else {
        return false;
    };
    if let Err(error) = helper(path) {
        eprintln!("Modhunter reset did not complete: {error}");
        std::process::exit(2);
    }
    true
}
