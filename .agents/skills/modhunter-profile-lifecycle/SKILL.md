---
name: modhunter-profile-lifecycle
description: Implement and verify Modhunter profile import, optional MO2 bridge and Watchtower lifecycle without stale queued work, incomplete catalogs or misleading native acceptance claims.
---

# Modhunter profile and native lifecycle

Use repository-root paths. Read AGENTS.md, native/Cargo.toml, .github/workflows/native-rust.yml and the affected wiki guide before edits. Cargo/workflow source determines the current release and build contracts; README history includes earlier versions.

## Trace the owner

Rust native/crates/modhunter-core owns core import/cache behavior; native/crates/modhunter-desktop owns the desktop host; mo2_update_arsenal is the in-process Python MO2 adapter. Native UI and existing dashboard/game surfaces remain distinct. Inspect the actual call path before moving responsibilities. Do not make MO2 connectivity mandatory for offline imports, or make Watchtower or a game mode the dependency root for unrelated features.

For MO2 changes, start with wiki/MO2-Plugin.md and native/scripts/verify_plugin_bridge.py. For source-update bridging use native/scripts/verify_source_bridge.py. For page/session tracking read wiki/Watchtower.md. Existing supported adapters are preferred to new authentication/download replacements.

## Exercise interrupted work, not just a happy import

Reproduce the affected behavior with profile identity and exact source/build version recorded. Test switching profiles while import/refresh/deep audit is running; cancel and wait for workers to settle; retry with the same engine. The last complete catalog must remain available after cancellation or failure. Expired commands and delayed UI responses must not mutate a new profile.

Check portable and split-path MO2 discovery, protocol/profile validation and no-connection offline mode separately. Watchtower baseline creation, changed-page history, Mark seen/undo, pause/mute, recoverable removal and session reset have independent state. Reopening a remembered session does not prove upstream access still works. Never export browser credentials or private/paywalled fixtures.

## Apply the existing verification stack

Use the current native-rust workflow for complete prerequisites, browser output locations and Windows commands. The core sequence includes:
- python3 native/scripts/prepare_release.py --print-version
- python3 native/scripts/prepare_assets.py
- cargo fmt --manifest-path native/Cargo.toml --all -- --check
- cargo test --manifest-path native/Cargo.toml --workspace --locked
- both native/scripts/verify_plugin_bridge.py and verify_source_bridge.py against the built modhunter-cli
- cargo clippy --manifest-path native/Cargo.toml --workspace --all-targets --locked -- -D warnings
- python3 -m unittest discover -s tests -p 'test_*.py' -q
- the workflow's Node syntax and runtime suites, including desktop/offline_mo2.test.cjs

For affected UI run the workflow's actual Chromium dashboard, compact-workspace, responsiveness and Watchtower checks. Dependency setup is not test evidence. Never silently skip browser assertions because a dependency is unavailable. Keep runtime evidence and screenshots free of account/session material.

## Report each acceptance layer honestly

Separate local unit/fixture checks, browser dashboard behavior, packaged Windows WebView2 launch, actual MO2 execution and real authorized Nexus downloads/FOMOD decisions. Exact beta MO2 compatibility needs that binary; a different version or Qt receiver test is not a substitute.

For performance retain equivalent complete mod inventories, metadata and audit depth. Record raw samples, cancellations/failures and last-good behavior; synthetic importer timings do not establish GUI startup, network or native installation speed. For delivery verify exact remote source, CI, package identity and actually loaded runtime separately. The source-import and release workflows are not generic test buttons: do not trigger source restoration or publication merely to validate this skill.
