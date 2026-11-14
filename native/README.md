# Modhunter 0.10.2 — Rust desktop

The Windows desktop, profile import/cache engine, connection probing, tab lifecycle and native file dialogs run in Rust. Microsoft Edge WebView2 renders the existing Modhunter dashboard, Questbound, AetherFX, Shiverfell, Siege and Atlas. The shipped desktop does not require Node or Electron.

MO2's Python/Qt plugin remains the supported in-process adapter for MO2's authenticated Nexus bridge, native downloads, FOMOD installer, focus and verified rewards. A small Qt Core C++ helper bridges the protected callback-sender binding needed by MO2 2.5.2's public file-list integration. The dashboard/game presentation remains HTML/CSS/JavaScript. Public site adapters, import/cache and plugin analysis run in Rust.

## MO2 2.5.3b12 / Skyrim SE setup

Run **Check_MO2_Readiness.bat** before installing and read **START-HERE-MO2-253.md** in the portable folder. The checker is read-only with respect to MO2/game data and can save a credential-free report. This release adds a self-tested Qt 6.11 runtime path, validated version-label strategies and persisted service-outage cooldowns. It retains the actual MO2 2.5.2 acceptance suite and adds separate compiled Qt 6.11.1/PyQt 6.11.0 tests; exact beta-12 application and personal account acceptance are not implied. Do not replace MO2's runtime DLLs.

See the repository [setup guide](../wiki/MO2-253-Testing.md) and [GameSync technique map](../wiki/GameSync-Update-Techniques.md).

## Repairs in 0.10.2

Automatic live discovery, current dictionary callbacks, native download handle 0, live toolbar refresh/audit, and credential-free connection details are repaired. The start page distinguishes the optional demo from your actual profile. Publication now requires the compiled desktop connected to actual MO2, not only independent desktop and plugin tests.

## Run

1. Extract **Modhunter-Rust-Windows.zip**. Run **Modhunter.exe**. Windows must have Microsoft Edge WebView2 Runtime; the separate NSIS installer bootstraps it if missing.
2. For a local profile, choose **Import MO2**, then its `modlist.txt`. For a configuration/data directory in another location, select **Instance INI** first. **Mods path** is an explicit override; **Auto paths** clears the overrides. Import is read-only.
3. Use **Refresh** to reload profile metadata. **Plugin audit** performs deeper record and fresh-hash inspection in the background. **CANCEL** interrupts either operation; **CANCELING…** stays visible while workers settle, preserving the last complete catalog and allowing another refresh/audit afterward. **ESLIFIER DATA** selects the converter data folder for provenance checks. Unchanged `meta.ini` files are cached; DLL folders are checked again. The toolbar displays actual import duration and cache hits. The engine does not recursively traverse mod assets.
4. For live updates and live Rust analysis, close MO2 and run **Install_MO2_Plugin.bat**. Select the folder containing `ModOrganizer.exe`. The installer preserves the prior plugin in `ModhunterBackups`, carries forward any regular fallback `data` folder, and rolls back the replacement if installation fails.
5. Start MO2 and wait for its profile to finish loading, then launch Modhunter. The bridge starts automatically; **Find MO2** also reconnects on demand. Opening the legacy Quest Board tool is no longer required. One validated running session connects immediately; if several are running, select the intended session. You can also paste its `http://127.0.0.1:PORT/` address into **Connect**. Use MO2's Nexus sign-in. **Update now**, background queueing, retry, and sequential ready installations retain their established workflow. Native MO2 FOMOD/replace decisions remain available.

Dashboard JSON, CSV and PowerShell exports are saved in your Windows **Downloads** folder with a unique filename. The native toolbar shows the saved path. Use **Export backup** before moving progress from the older Electron edition, then import that backup in the Rust edition.

The MO2 package includes its matching `bin/modhunter-cli.exe` and `bin/modhunter-qt-bridge.dll`. A manually copied Python-only plugin can still show native MO2 facts, but source/deep-analysis and native file-list capabilities report missing helpers explicitly. For a released Windows package, the installer verifies every installed plugin file against the included `SHA256SUMS.txt` and matches `BUILD_INFO.json` to the plugin version before touching an existing installation. Keep the extracted package together; an incomplete, damaged or mixed-version package is rejected while the old plugin and its data stay in place. Source-tree installations remain supported after both compiled helpers are copied into the documented locations. The native app requires the included protocol-1 plugin. Old plugins receive a clear upgrade message. Local MO2 connections have bounded deadlines, bypass HTTP proxies, and cannot redirect to another server. Public source adapters use system certificate trust and proxy configuration. A dropped connection is visibly marked; recovered read-only sessions reconnect without replaying update commands.

Existing Electron/standalone outputs remain available. Browser progress lives in each runtime's local storage; moving from Electron to WebView2 does not automatically migrate Electron's private browser database. Export/import existing Modhunter progress with the dashboard controls when moving runtimes. Live MO2 Chronicle progress remains owned by the plugin.

## Watchtower: pages and site sign-ins

Open **Watchtower** in the native toolbar to manage tracked pages and site sign-ins. **Add page** accepts a public HTTPS address, optional name, folder, tags and notes. A selected CSS region and ignored regions keep the comparison focused. Choose a site session for account-only pages; the first successful observation establishes a baseline and later changes remain unread until acknowledged. Page details retain bounded text differences/history, and **Seen history** supports guarded undo. Pause/mute controls affect checks; removed pages can be restored.

In **Site sign-ins**, add a website and session name, then sign in using the site's own browser window. Separate WebView2 profiles isolate sessions from one another and from the privileged local app. **Remember this sign-in** preserves that profile across launches. With remembering disabled, the session uses a temporary on-disk profile for the current run and starts fresh next launch; this is not an in-memory password vault or a guarantee of forensic erasure. Reset closes that session's windows, rotates its storage generation and rejects older observations. User confirmation means ready to recheck, not independently verified membership or entitlement. Nexus authentication and downloads continue through MO2.

Checks are manual by default. Preferences can enable launch and background due checks while the app is running, with bounded work and per-site pacing. A blocked, incomplete, canceled or stale read preserves the last successful baseline. **Import** previews validated JSON or one URL per line; **Export** offers importable JSON or a readable CSV configuration report in Downloads. JSON can optionally include the retained observation history; imports always establish fresh baselines. Neither operation transfers cookies or passwords. URLs, notes and observed text can still be private: review the data before sharing it.

Read [Watchtower](../wiki/Watchtower.md) for the workflow and [the pinned WhimWatch reference audit](evidence/whimwatch-parity-0.10.0.md) for independently implemented behavior and excluded Sims-specific features.

## Build from source

The toolchain is pinned in `rust-toolchain.toml`; dependencies are pinned in `Cargo.lock`. Release tooling requires Python 3.11 or later.

```powershell
python native/scripts/prepare_release.py --print-version
python native/scripts/prepare_assets.py
cd native
cargo test --workspace --locked
cargo build --release --locked -p modhunter-core
cd crates/modhunter-desktop
npx --yes @tauri-apps/cli@2 build
```

### Build the MO2 Qt helper

The supported native fixture is official **MO2 2.5.2 with Qt/PyQt 6.7.1**. Install the Qt **6.7.1 `win64_msvc2019_64` Qt Base SDK** and CMake, then run these commands from the repository root in a Visual Studio developer PowerShell. Adjust the SDK path to its actual location:

```powershell
$qtSdk = 'C:\Qt\6.7.1\msvc2019_64'
cmake -S native/qt_bridge -B native/target/qt-bridge -A x64 "-DCMAKE_PREFIX_PATH=$qtSdk" -DBUILD_TESTING=ON
cmake --build native/target/qt-bridge --config Release --parallel 2
cmake -E env "PATH=$qtSdk\bin;$env:PATH" ctest --test-dir native/target/qt-bridge -C Release --output-on-failure
New-Item -ItemType Directory -Force mo2_update_arsenal/bin
Copy-Item native/target/release/modhunter-cli.exe mo2_update_arsenal/bin/
Copy-Item native/target/qt-bridge/Release/modhunter-qt-bridge.dll mo2_update_arsenal/bin/
```

Copy these two matching helpers before running `native/Install_MO2_Plugin.ps1` from a source tree. CI performs the same assembly for released packages. The helper dynamically uses **MO2's own Qt Core**; do not copy SDK Qt DLLs into MO2. Runtime ABI/version and GUI-thread checks reject an incompatible helper. The standalone Rust app does not need the Qt SDK.

The real portable-MO2 fixture takes both binaries explicitly:

```powershell
./native/scripts/mo2_native_smoke.ps1 -RustCliPath "$PWD/native/target/release/modhunter-cli.exe" -QtBridgePath "$PWD/native/target/qt-bridge/Release/modhunter-qt-bridge.dll" -EvidenceDirectory "$PWD/native/target/mo2-proof"
```

Use a new, empty evidence directory for each run. This fixture checks the actual MO2-created QObject and raw QVariant binding using owned synthetic callbacks; it performs no login, authenticated Nexus request or mod download. See [`qt_bridge/README.md`](qt_bridge/README.md) for the narrow helper contract and compiled binding tests.

Windows builds need the Rust MSVC toolchain and Visual Studio C++ build tools. The resulting executable is `native/target/release/Modhunter.exe`; installer output is under `native/target/release/bundle/nsis`. Node is used only to run the build-time Tauri CLI. `cargo install tauri-cli --locked --version '^2'` and `cargo tauri build` can replace that build-time step.

Cross-platform engine commands:

```shell
cd native
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo run --release --locked -p modhunter-core --bin modhunter-cli -- import --modlist /path/to/profiles/Default/modlist.txt --ini /path/to/ModOrganizer.ini
cargo run --release --locked -p modhunter-core --bin modhunter-cli -- benchmark --mods 2500 --rounds 5 --workers 8
```

## Release identity and upgrades

`native/Cargo.toml` defines the release version inherited by both Rust crates. The release preflight checks the Tauri installer version, both workspace entries in `Cargo.lock`, and `mo2_update_arsenal/version.py` against it. The MO2 plugin and loopback health response share that packaged Python version. `--print-version` fails when any version is stale; CI uses its validated result for `BUILD_INFO.json` and the release tag.

When preparing a new release, update the workspace version, Tauri version, packaged MO2 version and workspace lockfile entries together, then run the preflight and release self-tests. Package validation rejects an installer or portable archive labeled for a different version. The MO2 version module is covered by the same tested-source hash checks as the plugin code and rebuilt dashboard.

Published release assets remain immutable. A new version is required to distribute new application code; passing CI for an already published version does not replace its downloads. The workflow tests `upgrade/**` branches and pull requests, then repeats the required gates on `main` before publishing a new version.

## Evidence and release boundaries

The [0.10.0 reference and acceptance evidence](evidence/whimwatch-parity-0.10.0.md) records the exact WhimWatch 0.4.2 artifact/tag, all first-party source modules, transfer decisions and new Watchtower gates. The [0.9.2 readiness evidence](evidence/readiness-0.9.2.md) remains the historical record of complete-catalog response reuse, row-markup reuse, source-edit synchronization, cancellation and package-integrity measurements. Each benchmark retains its original baseline and scope. `prepare_release.py --benchmark-files` prints the unchanged historical evidence allowlist shared by the Windows package and release verification ZIP.

The `Rust desktop and MO2 readiness` workflow builds the Windows executable and installer, runs Rust tests on Linux and Windows, exercises the compiled dashboard in Chromium, and starts the real Windows executable. Its WebView2 smoke test imports a fixture profile, checks rendered rows, saves and reads an actual exported backup, and verifies that the game view cannot invoke the toolbar's native commands. A separate Windows job loads the production plugin in an official MO2 2.5.2 installation with a disposable fixture game/profile. Workflow artifacts include the app, plugin, installer and native JSON receipts.

An additional required `watchtower_smoke.ps1` run launches the same compiled executable against its own loopback pages. It checks actual WebView2 session isolation, closing/reopening a remembered profile within one process, generation-selected fresh profiles, same-session popups, authenticated observations and changed content, selected-region capture, incomplete/stale/canceled rejection and denied remote IPC. Its reopen check does not restart Modhunter. A separate anonymous `https://example.com/` check uses production page capture and durable WatchStore baseline completion. The wrapper requires all thirteen checks and a clean process exit within 120 seconds. `prepare_release.py` requires its complete passing receipt to match `Evidence/watchtower-smoke.json` inside the portable ZIP and includes it in the public verification ZIP. Real Chromium tests separately exercise the shipped Watchtower interface using a fixture command adapter; they do not substitute for native session tests.

Run the additional native fixture from the repository root after building:

```powershell
./native/scripts/watchtower_smoke.ps1 -Executable "$PWD/native/target/release/Modhunter.exe" -Output "$PWD/native/target/watchtower-smoke"
```

Fixture-based Windows smoke is separate from a user's actual populated MO2 installation. Real Nexus account download behavior, account-specific callbacks, FOMOD choices and in-game results require testing against that installation; no automated fixture is labeled as that evidence. Provider sign-in and network failures cannot be guaranteed absent. The application is not code-signed; the Windows installer is built from the linked repository workflow.

See [`Mod Intelligence`](../wiki/Mod-Intelligence.md) for ESL, provenance and source-health controls, and [`Source Coverage`](../wiki/Updater-Source-Coverage.md) for GitHub/GitLab/Modrinth/Thunderstore adapters. [`Rust Desktop`](../wiki/Rust-Desktop.md) and [`evidence/`](evidence/) record the tests and measured performance associated with this release.
