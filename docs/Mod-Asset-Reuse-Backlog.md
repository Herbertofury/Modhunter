# Local mod assets: follower voices, spells and full followers

Requested 2026-11-17; active, not delivered end-to-end.

- Use recruited followers' actual installed voice lines in both ModHunter game modes. Categorize attack, help, heal and other contexts from evidence; unknown remains unknown, with manual mapping and corrections.
- Bring custom spells, their assets and sounds into roguelite gameplay. Resolve actual plugin records and dependencies, not just filenames or arbitrary guessed effects.
- Investigate the user's “Phatasm” lead for rendering full followers; verified candidate is Severause/Phantasm, pending user identity confirmation.
- Read installed files and archive members in place without extracting into or modifying mods. Bounded temporary processing only where necessary. Never distribute user mod assets in releases.
- Rust-first native work; cancellation, quotas, bounded background work, cache invalidation by profile/load order and source change, no MO2 UI-thread scans. Measure performance with equivalent outputs. No zero-lag claim without real tests.
- Preserve source mod/plugin/member identity, load-order winner, rights metadata and confidence. Originals remain untouched. No uploaded voices or third-party transcription without separate authorization.

## First implemented slice

Rust read/seek FUZ metadata probe: validates container and RIFF/xWMA boundaries, returns source-relative byte ranges without extracting or reading the payload; cancellation checked between reads. Synthetic tests and fixture benchmark only. This is a primitive, NOT an integrated scanner, audio decoder, playback UI or semantic dialogue classifier.

## Remaining gates

1. Worker scheduling and user selected installed mod roots; safe handle opening and archive index adapters (BSA/BA2), compressed-member streaming quotas, private incremental index.
2. Resolve NPC_/VTYP/DIAL/INFO/QUST conditions and localization before assigning speaker or gameplay context. Filename candidates alone are not verified dialogue intent. Manual bindings and preview.
3. Decode only selected clips with resource-limited native xWMA support; audio settings, concurrency/cooldowns, cache quotas and muted fallback. Preserve separate LIP range for later lip sync.
4. Resolve SPEL -> MGEF and associated sounds/projectiles/art/meshes/textures/scripts. Design explicit supported effect adapters; do not execute Papyrus or native mod DLLs. Report unsupported effects.
5. Evaluate Phantasm in-game still capture bridge separately from offline NIF rendering. No claim that its game-device texture is a portable web renderer.
6. Native Windows/MO2 p95/p99 frame-time, I/O/CPU/memory comparisons for idle, scan, cancellation, playback and large real installs. Profile changes must invalidate old jobs and mappings.

## Next implemented primitive: in-place audio stream

`FuzAudio` now exposes only the embedded audio through Rust Read/Seek. It validates the envelope first, enforces a configurable byte budget with a 64 MiB hard maximum, caps each read at 64 KiB, refuses out-of-range seeks, keeps cancellation terminal and reports source truncation as an error. No writes, extraction, decoding or automatic scanning. Synthetic disk-file tests compare every streamed byte and verify source immutability. Integrating this with a resource-limited decoder and safe profile-bound handle provider is still required.
