# Modhunter 0.9 — Rust desktop

The Windows desktop, profile import/cache engine, connection probing, tab lifecycle and native file dialogs run in Rust. Microsoft Edge WebView2 renders the existing Modhunter dashboard, Questbound, AetherFX, Shiverfell, Siege and Atlas. The shipped desktop does not require Node or Electron.

MO2's Python/Qt plugin remains the supported in-process adapter for MO2's authenticated Nexus bridge, native downloads, FOMOD installer, focus and verified rewards. The dashboard/game presentation remains HTML/CSS/JavaScript. This release does not claim those components have been rewritten into Rust.

## Run

1. Extract **Modhunter-Rust-Windows.zip**. Run **Modhunter.exe**. Windows must have Microsoft Edge WebView2 Runtime; the separate NSIS installer bootstraps it if missing.
2. For a local profile, choose **Import MO2**, then its `modlist.txt`. For a configuration/data directory in another location, select **Instance INI** first. **Mods path** is an explicit override; **Auto paths** clears the overrides. Import is read-only.
3. Use **Refresh** to reload profile metadata. **Plugin audit** performs deeper record and fresh-hash inspection in the background. **ESLIFIER DATA** selects the converter data folder for provenance checks. Unchanged `meta.ini` files are cached; DLL folders are checked again. The toolbar displays actual import duration and cache hits. The engine does not recursively traverse mod assets.
4. For live updates and live Rust analysis, close MO2 and run **Install_MO2_Plugin.bat**. Select the folder containing `ModOrganizer.exe`. The installer preserves the prior plugin in `ModhunterBackups`, carries forward any regular fallback `data` folder, and rolls back the replacement if installation fails.
5. Start MO2, open **Tools → Update Arsenal – Quest Board**, and click **Find MO2** in Modhunter. One validated running session connects immediately; if several are running, select the intended session. You can also paste its `http://127.0.0.1:PORT/` address into **Connect**. Use MO2's Nexus sign-in. **Update now**, background queueing, retry, and sequential ready installations retain their established workflow. Native MO2 FOMOD/replace decisions remain available.

Dashboard JSON, CSV and PowerShell exports are saved in your Windows **Downloads** folder with a unique filename. The native toolbar shows the saved path. Use **Export backup** before moving progress from the older Electron edition, then import that backup in the Rust edition.

The MO2 package includes its matching `bin/modhunter-cli.exe` helper. A manually copied Python-only plugin can still show native MO2 facts, but deeper plugin/hash analysis reports the missing helper explicitly. The native build requires the included protocol-1 plugin. Old plugins receive a clear upgrade message. Connections have bounded deadlines, do not use environment HTTP proxies, and cannot redirect to another server. A dropped connection is visibly marked; recovered read-only sessions reconnect without replaying update commands.

Existing Electron/standalone outputs remain available. Browser progress lives in each runtime's local storage; moving from Electron to WebView2 does not automatically migrate Electron's private browser database. Export/import existing Modhunter progress with the dashboard controls when moving runtimes. Live MO2 Chronicle progress remains owned by the plugin.

## Build from source

The toolchain is pinned in `rust-toolchain.toml`; dependencies are pinned in `Cargo.lock`.

```powershell
python native/scripts/prepare_assets.py
cd native
cargo test --workspace --locked
cargo build --release --locked -p modhunter-core
cd crates/modhunter-desktop
npx --yes @tauri-apps/cli@2 build
```

For a manual source-tree MO2 installation, copy the built `native/target/release/modhunter-cli.exe` into `mo2_update_arsenal/bin/modhunter-cli.exe` before running the installer. CI performs this step when assembling the portable package.

Windows builds need the Rust MSVC toolchain and Visual Studio C++ build tools. The resulting executable is `native/target/release/Modhunter.exe`; installer output is under `native/target/release/bundle/nsis`. Node is used only to run the build-time Tauri CLI. `cargo install tauri-cli --locked --version '^2'` and `cargo tauri build` can replace that build-time step.

Cross-platform engine commands:

```shell
cd native
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo run --release --locked -p modhunter-core --bin modhunter-cli -- import --modlist /path/to/profiles/Default/modlist.txt --ini /path/to/ModOrganizer.ini
cargo run --release --locked -p modhunter-core --bin modhunter-cli -- benchmark --mods 2500 --rounds 5 --workers 8
```

## Evidence and release boundaries

The `Rust desktop and MO2 readiness` workflow builds the Windows executable and installer, runs Rust tests on Linux and Windows, exercises the compiled dashboard in Chromium, and starts the real Windows executable. Its WebView2 smoke test imports a fixture profile, checks rendered rows, saves and reads an actual exported backup, and verifies that the game view cannot invoke the toolbar's native commands. A separate Windows job loads the production plugin in an official MO2 2.5.2 installation with a disposable fixture game/profile. Workflow artifacts include the app, plugin, installer and native JSON receipts.

Fixture-based Windows smoke is separate from a user's actual populated MO2 installation. Real Nexus account download behavior, account-specific callbacks, FOMOD choices and in-game results require testing against that installation; no automated fixture is labeled as that evidence. Provider sign-in and network failures cannot be guaranteed absent. The application is not code-signed; the Windows installer is built from the linked repository workflow.

See `wiki/Mod-Intelligence.md` for the new ESL, provenance and source-health controls. See the repository's `wiki/Rust-Desktop.md` and `native/evidence/` for the exact tests and measured performance associated with this release.
