# Real MO2 2.5.2 fixture smoke

This test opens the official Windows MO2 release and runs Modhunter's production
Python plugin inside its bundled Python, PyQt and `mobase` runtime. The game and
two mods are synthetic; the native organizer, profile, Qt event loop, HTTP bridge
and left-pane selection are real. These fixture plugins are **test assets only**
and must never be copied into a user release package.

## Run

Use PowerShell 7.2 or newer on Windows, with 7-Zip installed, in a **clean
disposable Windows account** such as a hosted CI runner:

```powershell
./native/scripts/mo2_native_smoke.ps1
```

To require the compiled Rust `BridgeClient` to connect to that same live native
MO2 service, build the CLI and supply it:

```powershell
cargo build --manifest-path native/Cargo.toml --release --locked -p modhunter-core
./native/scripts/mo2_native_smoke.ps1 -RustCliPath native/target/release/modhunter-cli.exe
```

The harness runs the real `probe --endpoint` command with a 12 second subprocess
deadline. The Rust client validates the application's identity and protocol; the
harness compares its returned endpoint, profile, version and generation with the
native service. No session token is passed to the CLI.

The same CLI is installed at the production plugin's `bin/modhunter-cli.exe`.
The HTTP worker then requests a deep `/api/plugin-audit` for the observed native
profile and polls its completion for at most 35 seconds, with each HTTP timeout
limited to the remaining deadline. MO2's Qt event loop remains free to capture
the inventory through the production `plugin-audit-input` dispatcher. The check
requires schema 1, `records` depth, both exact fixture mods, and the fresh SHA256
and header facts of a 42-byte inert `ModhunterSmoke.esp` in the disabled mod.
There are no content records or masters. The synthetic game is unsupported for
ESL eligibility; this check makes no conversion-safety claim. Helper hashes must
match the supplied CLI before and after the audit.

When `-RustCliPath` is supplied, both `rust_cli_live_probe` and
`rust_plugin_audit` are required for the run to pass. Release preparation also
requires those checks, the three intelligence modules' tested source hashes,
and a packaged MO2 helper matching the portable package's CLI. The separate
native MO2 and portable build jobs may produce different executable hashes;
each installed/copied helper must match its own job's compiled CLI.

The script creates a new temporary working directory, downloads the pinned
[official MO2 2.5.2 portable archive](https://github.com/ModOrganizer2/modorganizer/releases/tag/v2.5.2),
and checks its release asset size and executable version. It records the
downloaded archive's SHA256. The upstream asset metadata does not publish an
independent digest, so that recorded hash is evidence rather than an additional
upstream integrity guarantee. The release requires the Microsoft Visual C++
runtime documented on its release page; hosted GitHub Windows runners already
provide a current runtime. Missing runtime support must fail native startup.

No existing MO2 installation is discovered or modified. The working directory
must not already exist. Only the child process created by the script may be
stopped on timeout. The extracted files remain in that temporary directory for
diagnosis until runner cleanup.

MO2 also starts its actual `nxmhandler.exe` during UI startup. The script refuses
an account with an existing global MO2 data directory or registered `nxm` handler.
For the fresh fixture account, it writes the helper's supported
`[General] noregister=true` preference both next to the portable executable and
in the new global MO2 directory. This prevents a separate association dialog or
protocol registration without replacing the helper. Those fixture preferences
remain until the disposable account/runner is cleaned up; use a fresh account
for another run. No existing handler settings are edited.

Results are copied to `native/evidence/windows-mo2-smoke/` by default. Set
`-EvidenceDirectory` to choose another evidence destination. A CI job should
always upload this directory, including when the command fails. The test does
not require Rust compilation or owned game files and can run as a separate
Windows job. The evidence destination must be empty so an earlier receipt or
screenshot cannot be mistaken for evidence from the current run.

```yaml
- name: Exercise the real MO2 2.5.2 runtime
  shell: pwsh
  run: ./native/scripts/mo2_native_smoke.ps1 -RustCliPath native/target/release/modhunter-cli.exe
- name: Save native MO2 evidence
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: mo2-2.5.2-native-smoke
    path: native/evidence/windows-mo2-smoke/
    if-no-files-found: error
```

## What must actually pass

The smoke tool is an instrumented subclass of the unmodified production
`ModUpdateArsenal`. It invokes production `init()`, `display()` and `_dispatch()`.
It does not construct substitute organizer objects or swap out bridge handlers.
The regular production plugin package is also copied into the fixture's plugins
directory, with core source hashes included in the evidence.

The harness intercepts the Qt `http` URL opener only, to capture the real local
dashboard address without opening the runner's browser. An HTTP worker uses the
actual in-memory session token. All organizer and widget operations continue
through the production queued Qt dispatcher.

Required observations include:

- Native plugin initialization and Nexus gateway construction/signal binding.
- The real UI-initialized callback, expected fixture instance/game/profile, and
  an actual native `QTreeView` containing the fixture mods.
- A live unauthenticated `/api/health` response and rejection of an
  unauthenticated protected snapshot request.
- A native profile snapshot with the two exact installed versions and distinct
  enabled/disabled states.
- Successful HTTP refresh and focus commands executing on the Qt GUI thread.
- The actual native view's current index matching the requested disabled mod,
  as well as the selected ID reported by the HTTP revision endpoint.
- The compact game-state endpoint and a captured native window screenshot.
- A real compiled Rust CLI probe against that same native MO2 service, when the
  optional CLI path is supplied (required in the CI integration job).
- The production plugin-audit request and Qt inventory capture, a completed
  deep Rust job, both decorated fixture rows, and an exact inert-plugin header
  and fresh SHA256, when the CLI path is supplied.
- A complete passing receipt and a clean zero exit code from MO2 itself.

An unexpected modal dialog, failed assertion, missing callback, missing native
receipt, timeout or abnormal process exit fails the command. There is no mock
fallback and no conversion of native failures into skipped or passing checks.

## Evidence

`result.json` records orchestration status, the pinned release URL/version/size,
archive hash, native executable version, production source hashes, process exit
code and the native receipt. `native-receipt.json` records the actual Python,
PyQt and Qt versions, passing/failing checks, queued command thread evidence,
endpoint timings and test scope. `mo2-fixture.png` captures the actual native
window. The initial fixture settings, extraction output and native MO2 logs are
also retained. The loopback session token is never written to evidence.

The bounded audit protocol and release validators also have local self-tests:

```sh
python native/scripts/mo2_smoke_plugin/modhunter_native_smoke/audit_probe.py --self-test
python native/scripts/prepare_release.py --self-test
```

These use synthetic responses/artifacts and never launch a GUI. They verify
that incomplete, stale-profile, header-only or missing-helper evidence fails;
they cannot substitute for the real Windows run and its passing receipt.

## Source basis and remaining scope

The fixture uses the release's own `basic_games` implementation; it does not
replace it with a newer copy. The supplied subclass declares a marker binary,
data and save directories, and a fixed fixture version. It never launches the
marker file. The supported BasicGame subclass contract and path validation are
documented in the [official basic-games repository](https://github.com/ModOrganizer2/modorganizer-basic_games)
and [implementation](https://github.com/ModOrganizer2/modorganizer-basic_games/blob/master/basic_game.py).

The pinned MO2 source establishes the remaining startup contract:

- [Settings at v2.5.2](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/settings.cpp):
  `gameName`, QByteArray `gamePath` and `selected_profile`, custom base paths,
  `first_start`, `check_for_updates`, and category-mapping settings.
- [Instance selection at v2.5.2](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/instancemanager.cpp):
  the configured game name is matched to a real game plugin and its directory is
  validated by that plugin.
- [Application constants at v2.5.2](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/shared/appconfig.inc):
  `portable.txt` and `ModOrganizer.ini`.
- [Plugin startup at v2.5.2](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/plugincontainer.cpp):
  native organizer proxies deliver profile/UI initialization callbacks.
- [Main-window startup at v2.5.2](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/mainwindow.cpp)
  starts the real NXM helper; its [official pre-release implementation](https://github.com/ModOrganizer2/modorganizer-nxmhandler/blob/c95039a8fdaefe81d1fa33edfc54bb74c19c259b/src/main.cpp)
  reads `nxmhandler.ini` and honors `noregister=true` for the normal `reg` command.
- [Qt's URL handler API](https://doc.qt.io/qt-6/qdesktopservices.html#setUrlHandler):
  a registered QObject slot receives URLs instead of the desktop browser.

A passing run validates this specific native connection and dispatch path. It
does not validate Nexus sign-in, remote scan results, downloading a mod archive,
installer/FOMOD dialogs, a real game's runtime or launching a game. Those require
separate authorized integration tests. Local Python compilation of these test
files is not evidence that the Windows native smoke passed; use the CI receipt
and process exit for that conclusion.
