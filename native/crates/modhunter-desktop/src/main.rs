#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

mod catalog;
mod discovery;
mod import_operation;
mod install_admission;
mod installer_bundle;
#[cfg(windows)]
mod installer_script_windows;
#[cfg(windows)]
mod instances_windows;
#[cfg_attr(not(windows), allow(dead_code))]
mod locale;
mod mo2_discovery;
mod policy;
mod watchtower_policy;
#[cfg(windows)]
mod watchtower_windows;
#[cfg(windows)]
mod windows;

#[cfg(windows)]
fn main() {
    if watchtower_windows::dispatch_reset_helper() {
        return;
    }
    windows::run();
}

#[cfg(not(windows))]
fn main() {
    eprintln!(
        "Modhunter Desktop targets Windows / MO2. Use modhunter-cli for portable profile import and diagnostics."
    );
    std::process::exit(2);
}
