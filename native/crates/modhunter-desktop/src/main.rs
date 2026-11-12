#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

mod discovery;
mod policy;
#[cfg(windows)]
mod windows;

#[cfg(windows)]
fn main() {
    windows::run();
}

#[cfg(not(windows))]
fn main() {
    eprintln!(
        "Modhunter Desktop targets Windows / MO2. Use modhunter-cli for portable profile import and diagnostics."
    );
    std::process::exit(2);
}
