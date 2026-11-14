fn main() {
    // Build scripts run on the host. Select asset generation by the target so
    // Windows cross-checks receive the same command capabilities and resources.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["shell_command", "watchtower_command"]),
        ))
        .expect("Could not compile the Modhunter desktop assets and permission manifest");
    }
}
