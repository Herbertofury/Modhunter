fn main() {
    #[cfg(windows)]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["shell_command", "watchtower_command"]),
    ))
    .expect("Could not compile the Modhunter desktop assets and permission manifest");
}
