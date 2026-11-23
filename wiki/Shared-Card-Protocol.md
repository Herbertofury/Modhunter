# Shared card-game integration checkpoint

Goal: standalone ModHunter and GameSync card-game hosts read the same durable progress without manual exports or requiring the other app to run. Existing ModHunter game schemas are separate and must not be silently converted.

This first ModHunter adapter is **read-only discovery**, not playable integration. `python -m mo2_update_arsenal.shared_cards` works using Python's standard library without either app installed/running. The existing authenticated MO2 loopback bridge also offers `GET /api/shared-cards/status`; no new public-origin endpoint or filesystem selector is exposed. Normal mod maintenance remains independent of this optional capability.

It selects the shared per-user location: Windows `%LOCALAPPDATA%/Noxviola/SharedGames/cardgame.sqlite3`, macOS `$HOME/Library/Application Support/Noxviola/SharedGames/cardgame.sqlite3`, or Linux `$XDG_DATA_HOME/Noxviola/SharedGames/cardgame.sqlite3` with the standard `$HOME/.local/share` fallback only when XDG is absent. Invalid bases are errors rather than alternate saves.

The reader uses SQLite read-only mode and a consistent transaction; it checks application identity, known storage versions and profile shape. It never creates, upgrades, rewrites, truncates, resets or awards anything. The 64 MiB reader profile budget rejects oversized input explicitly, not partially. Locks/corruption/unsupported formats remain unavailable or unsupported, never false absence. No paths, arbitrary profile data, credentials, or raw database errors are returned. Leaf symlinks are rejected as a basic check; this is not a claim of race-resistant isolation from a malicious process already controlling the user's data directory.

Status reports `playable: false` even when a save is readable. Catalog snapshot presence/count are descriptive metadata, not validation of card-rule compatibility. No private game source, assets or binaries are bundled. No Nexus/card statistics network request is made.

## Verification

- `python -m unittest discover -s tests -p test_shared_cards.py -q`
- `python -m unittest discover -s tests -p test_bridge.py -q`
- Explicit cross-host acceptance: `python tests/shared_cards_cross_host.py /path/to/independently-built-protocol-cli` exercises two native writer processes and fresh ModHunter reader processes at the same location. It confirms the complete profile survives the reads. This optional acceptance command requires an independently available protocol CLI, not a GameSync application installation; ordinary reader operation has no CLI dependency.

## Still unresolved

Native Rust desktop integration and visible UI, reviewed engine/asset distribution, compatible card rules, write/recovery adapter, update-reward receipts, large-profile storage, and real Windows/MO2 gameplay acceptance. Do not call this a playable shared game or full parity. Work is isolated from active release branches; merge/release requires the normal review and acceptance gates.
