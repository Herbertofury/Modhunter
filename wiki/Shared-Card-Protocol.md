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

## Optional native save transport (follow-on draft)

`POST /api/shared-cards/invoke` uses the existing same-origin/session-token authorization. It accepts only load or commit protocol objects, fixes writer attribution to ModHunter, and exposes no executable/path/SQL choice. The full v3 profile remains opaque and goes through the same native transactional/CAS/idempotent receipt protocol. A client must durably journal the exact pending commit before sending. A timeout, lost response, or transport failure must replay the same operation, never silently rebase. This endpoint does not install mods, establish real update completion, or award verified maintenance rewards.

The optional independently installed native helper lives in the same per-user SharedGames directory under `runtime-v1/gs-card-save` (`.exe` on Windows). `manifest.json` identifies protocol1, that exact filename, and its SHA256 digest. This checks installation consistency, **not publisher authentication** or containment against a process already controlling the trusted user directory. No helper binary or private engine is included in this public repository. A reviewed private runtime can be packaged independently for either host without requiring the other app.

Runtime absence fails explicitly before execution. Input and in-memory reply sizes are bounded; replies spool to a temporary file. This is trusted installed code, not a disk-quota sandbox. The child process deadline can terminate it mid-operation; SQLite recovery and exact operation receipts handle uncertain completion. Pending data must stay journaled until readback, even when the child was killed. The existing full-profile adapter implements those client obligations.

Local private integration tested the existing game engine's start, mulligan, card play, native save and exact reopen through actual authenticated ModHunter HTTP, then confirmed the same snapshot with an independent native reader process. That private test/source is not published here. The visible game host and Windows runtime packaging/real gameplay acceptance remain unresolved. This follow-on is a backend transport, not a claim that a rendered playable ModHunter game has shipped.

## Optional visible private-runtime host (follow-on draft)

`python -m mo2_update_arsenal.shared_card_host` launches a dedicated local browser host after verifying the installed helper and private `cardgame.html` against the same bounded manifest receipt. The manifest must additionally contain `htmlSha256`. No private HTML/engine/assets are included in this public repository. A private Linux preview package has been built and exercised at the HTTP/engine level; it is not a Windows package or accepted user release.

The production origin is fixed at `http://127.0.0.1:47771/`. Port conflicts fail rather than select another origin: browser recovery journals must remain accessible after host restart. Session tokens rotate on restart, but the origin remains unchanged. This dedicated server exposes only the card protocol, not mod installation/maintenance commands, and runs without MO2 or another game host. It checks exact Host, same Origin and its token for writes; serves no arbitrary file paths; blocks framing and external script/connect origins. Public artwork can load as HTTPS images. The private runtime is trusted application code, not an untrusted script sandbox.

The private UI provides Start/resume, recovery, preserve-conflict/reload, and Save/stop. It requires an existing real shared source catalog and does not invent demo cards or silently convert other game schemas. Unfinished/error saves, including progress retained only in memory after local storage failure, keep the browser's leave warning active. Closing a page cannot guarantee completion of asynchronous writes; exact pending-operation recovery remains required.

Verification: five original-host HTTP/security/restart tests, private lifecycle tests, and actual existing-engine/native play-save-reopen passed. A real lost acknowledgment followed by host restart at the same origin, rotated token and exact replay preserved revision/progress. The packaged HTML is served and bundle compilation passes. Local Chromium launch fails with `socket() failed: Operation not permitted`; no sandbox bypass attempted. Thus rendering, mouse/keyboard gameplay, screenshots/audio, installed Windows/MO2 acceptance and visual polish remain **unverified**, not silently counted as done.
