# ModHunter same-host MO2 performance pilot

This is a proposed isolated **test-only** entry point for Modhunter current main
`eddeb8553058194a5b18f18406843874b347e16f`. It has not been run on Windows.
No production plugin, existing Windows smoke, CI protection, release job or
release artifact changes. Do not use it on an owner device or real installation.

## Why a separate pilot

The existing exact [MO2 Windows fixture](https://github.com/Herbertofury/Modhunter/blob/eddeb8553058194a5b18f18406843874b347e16f/native/scripts/mo2_native_smoke.ps1)
proves real MO2 2.5.2/mobase/Qt behavior. It loads production outside the
ordinary plugin directory through a native observer subclass, and it doesn't
time an otherwise identical MO2 without the plugin. Retain it unchanged. This
pilot stages the real plugin at `plugins/mo2_update_arsenal` and independently
requires its module path and enabled MO2 Tools action when present, and neither
when absent. The identical minimal Qt observer is present in every arm.
Its own startup signal, real native rows, and PID-bound visible HWND are checked
in every arm; no installed-only API is the readiness gate.

Current public `v0.12.1` is `f96b86969ec9f9c7ba6928284f16223b673d33c0`.
The [official published MO2 plugin ZIP](https://github.com/Herbertofury/Modhunter/releases/expanded_assets/v0.12.1)
SHA-256 is `fe3552411af9d3dbcc218e5146030417be0adf417b22bccf700b473adc07bfd1`.
The exact public ZIP was inspected, not launched: 52 plugin members, published
`plugin.py` blob `c0b94c45208328100628efd8c25214f04c0dc13c` and SHA-256
`30cd4612dc34844a8e3fa6062d9fe7443aa327a72473953c93f70e4f9c2f4b77` match
this main's plugin source. That equality neither proves other files/helpers
are the same nor proves a performance regression. The runner compares every
staged published plugin member against the exact official ZIP, and records
all host/staged candidate/tool hashes. Candidate `plugin.py` also has an
independent staging-time source SHA check.

## First hosted invocation

Proposed repository placement: copy `benchmark.py`, `test_benchmark.py`,
`observer/__init__.py` and this README into `native/scripts/mo2_perf/`, and
`.github/workflows/mo2-performance-pilot.yml` as a **new** workflow. An approved
same-repo PR automatically runs a three-arm 40-mod wiring preflight; it does
not produce a release verdict. The one proposed job has a 55-minute timeout
and read-only contents permissions. After native preflight qualifies, use
the same build/staging command on an independently authorized hosted test job
with the final `--preflight` argument omitted to execute all 36 trials.
There is no full-run automatic trigger or release linkage in this proposal;
existing workflows are untouched. No `pull_request_target`, dispatch,
deployment or publication.

The new job stages:
- Exact current test shim (blob `80cddd4e283e997b867edfad2f9df2670d0a4b49`) and
  the same Qt 6.7.1, pinned helper prep and locked Rust build as existing CI.
- [Upstream portable MO2 2.5.2 release](https://github.com/ModOrganizer2/modorganizer/releases/expanded_assets/v2.5.2),
  `Mod.Organizer-2.5.2.7z` from the existing CI's official HTTPS endpoint.
  Before extracting, it checks 149,660,212 bytes and independent observed
  archive SHA-256 `e6376efd87fd5ddd95aee959405e8f067afa526ea6c2c0c5aa03c5108bf4a815`
  from [prior suite 45053305553](https://github.com/Herbertofury/Modhunter/actions/runs/45053305553),
  not an upstream-published digest. It requires one exact 2.5.2 executable,
  records the EXE digest derived from that pinned archive and checks its
  bytes before launch. Stock files and only scratch copies
  are used. The Python runner refuses pre-existing global MO2 settings,
  NXM registry ownership and already-running MO2.
- Exact v0.12.1 plugin asset from that release, hash checked before expansion;
  normal direct activation path compared to candidate and absent, not merely
  two checkouts of potentially identical sources.

Only JSON results/partial failures are retained on hosted CI, never downloaded
MO2, test game, logs with local state, or executable assets. Failure logs and
raw phase JSON are available for probe integration diagnostics. First hosted
launch must actually confirm module path, QAction naming, plugin loading,
native fixture VFS lookups, filtering, tab navigation and clean exit.
If it does not, identify the precise incompatibility and keep the gate
unqualified; do not weaken checks to get a green badge.

## Measurement and limits

Each full run is 36 sequential fresh portable launches: at 40 and 1,000 mods,
six blocks each include absent, published, and candidate in all six possible
orders. Same runner, upstream binary/SDK, isolated inputs/settings, pristine
per-launch copy, 75% active fixture mods, no overlapping MO2. There are also
40/1,000 inert TES4/HEDR metadata markers spread only across the fixture's
enabled mods; disabled source directories never supply these markers. Every
marker must resolve through real native `organizer.resolvePath` to its expected
enabled mod's fixture path before workload timing; native `pluginNames()` intersection
is reported separately, never called active plugins or installed Skyrim.
This qualifies representative VFS size if executed, not an actual 1,000
gameplay-plugin save or ModHunter-internal path count. Tiny ESP headers contain
no masters or records. The same fixture's `fixture.exe` is a text marker
and never launched. No accounts, downloads through MO2, owner profile or game.

- Real startup until verified visible main UI; then two seconds of independent
  [Win32 message-queue probes](https://learn.microsoft.com/en-us/windows/desktop/api/winuser/nf-winuser-sendmessagetimeoutw)
  catch early stalls. Report warm-host order, not OS cold launch.
- Fifteen-second quiet subwindow measures MO2 process CPU in core-percent
  (kernel + user). No recurring observer, HTTP or native window probes in that
  slice. This cannot prove zero timers, callbacks or shorter bursts outside
  the slice; callback-count verification must be a separate instrumented run.
- 48 actual Qt filtered/cleared list operations, tab loads, Data Refresh dispatches
  and alternate real selections, observed through native widget/model state.
  Data Refresh confirmation bounds UI roundtrip, not every worker completion.
  External `WM_NULL` tests the visible MO2 message queue on a distinct process;
  retain raw samples, p95/p99 and worst per trial, early tails, host scheduling
  gaps/oversleep, native workload times and Win32 error/timeout codes.
  WM_NULL is an event-loop proxy, not end-to-end compositor paint nor a hard-
  real-time freeze duration. One-shot observation keeps callbacks bounded.
- Fail/invalidate on missing/wrong profile/identity/action/status, incomplete
  operations/samples, probe errors, any >=250 ms sampled queue latency, or
  >=150 ms harness scheduling oversleep. Thresholds are conservative **pilot**
  guardrails, not a statistical proof of no lag: require each candidate trial
  within documented paired deltas versus both baselines, retain all raw pairs.
  A noisy runner is unqualified, not silently trimmed into a pass.
- Published/candidate exact plugin hashes do not qualify new functionality.
  Full owned-fixture results, existing native contracts/workflows, and any
  remaining feature/scale verification remain prerequisites before a release.

Local evidence: eight stdlib tests including both fixture counts, exact
42-byte headers, hashes/ZIP safety, all balanced pairs, rejects invalid/noisy
inputs, and tail-versus-average math; Python modules syntax-compiled; proposed
YAML structurally validated. No PowerShell, Win32/MO2 process, Qt widgets,
timings, or packaging/release qualification ran locally.
