# Mod assets research and implementation boundary

2026-11-17. Local user assets only; no copyrighted examples copied into this repository.

## Follower voice pipeline

Inspected [the FUZ reader implementation](https://github.com/suglasp/pwsh_convert_fuz_to_xwm/blob/main/convert_fuz_to_xwm.ps1): FUZE, little-endian version and LIP length occupy 12 bytes, then lip-sync data, then audio. Its bulk extraction approach is **not** adopted: ModHunter should retain byte ranges and leave source files untouched. The new Rust primitive reads only the FUZ and embedded audio headers and returns ranges. Unknown versions, impossible ranges and mismatching audio envelope lengths fail explicitly.

[FFmpeg's actual xWMA demuxer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/xwma.c) checks RIFF/XWMA and handles WMAv2/WMAPRO, including codec parameter quirks. That makes a bounded native decoder worker a better initial challenger than assuming WebAudio can decode .fuz or implementing WMA ourselves. Dependency licensing and build configuration need review before shipping; no FFmpeg code or binaries were incorporated. Decoder must read only the selected member range, without arbitrary protocol/network access, and enforce output duration, memory and cancellation limits. Container validation is not decoder validation.

Event mapping is a separate semantic problem. Attack/help/heal require dialogue records, speaker/voice type, quest/condition and localized response evidence. Mod filename and voice folder names alone do not prove speaker or intent. Index candidates as unknown or suggested until evidence/manual mapping exists. Keep original dialogue text and IDs plus reasons/confidence; allow preview, mute and per-event overrides. Do not upload voice clips for classification. Data-only mapping must not run Papyrus or DLLs.

## Custom spells

Inspected [Mutagen's Skyrim MagicEffect record schema](https://github.com/Mutagen-Modding/Mutagen/blob/dev/Mutagen.Bethesda.Skyrim/Records/Major%20Records/MagicEffect.xml): effects reference projectiles, explosions, shaders, casting/hit art, sounds, conditions and VM adapters. Therefore grabbing a texture/audio file cannot reproduce an arbitrary spell. Resolve SPEL and MGEF winning records and their dependency graph; retain source provenance and unsupported behavior. Provide explicitly supported roguelite effect adapters rather than pretending engine scripts run unchanged. Mutagen is a schema/oracle candidate, not an adopted runtime dependency.

[libfo76utils](https://github.com/fo76utils/fo76utils/tree/main/libfo76utils) is a challenger for archive/NIF access across Bethesda formats. Evaluate bounded member reads against existing native content inventory before selecting a library. Do not bulk unpack archives. Compressed archive members may need sequential decompression even for a later byte range; the 24-byte FUZ read count does not include that cost.

## “Phatasm”: identified candidate, not assumed identity

The matching project is [Severause/Phantasm](https://github.com/Severause/Phantasm), an MIT SKSE renderer. Its [public API header](https://github.com/Severause/Phantasm/blob/main/api/Phantasm_API.h) and [API guide](https://github.com/Severause/Phantasm/blob/main/docs/API.md) establish that it runs inside Skyrim and exposes asynchronous live actor/item views. Live SRVs belong to the game's D3D11 device, so cannot simply be passed as a browser texture. Cutout/portrait still callbacks supply RGBA pixels: an optional in-game bridge producing private bounded stills is a plausible first integration. Full independent animated follower rendering remains separate research, not solved by this project. Its absence must not break ModHunter. Close views when hidden; release textures on lifecycle transitions; never treat an accepted asynchronous call as a completed render.

## Architecture and acceptance proposal

- Read-only per-selected-profile discovery; one bounded low-priority worker initially, no MO2 UI-thread work. Explicit pause/cancel and visible counts/phase/bytes; budgets defer remaining work, never silently omit results.
- Private persistent metadata index keyed by profile generation, load-order identity, source/member identity and change evidence. Metadata fast path is not proof of identical content; use targeted fingerprints before playback, invalidate stale mappings.
- Asset bytes streamed only for requested previews/active recruits. Small decoded-audio LRU and texture budget; limits exposed, remaining work queued. No public asset server or remote upload.
- Separate archive parsing, record resolution, semantic mapping, decode and game presentation. No native commands exposed to arbitrary external browsing pages.
- Benchmarks compare idle and active MO2 frame times, p95/p99, I/O, CPU, memory and complete indexed counts on real installs. Synthetic header probe results below are deliberately not an end-to-end performance claim.

## Implemented versus pending

Implemented only `mod_assets::probe_fuz`, synthetic tests and a warm-file benchmark example. No archive scanner, classifier, decoder, playback controls or actual follower rendering is integrated yet. See the adjacent backlog for remaining milestones.

## Exact local verification

- `cargo +stable fmt --all -- --check`: passed.
- `cargo +stable test -p modhunter-core --offline`: 236 passed (169 unit, 18 importer, 21 plugin-analysis, 28 source-adapter); zero failures. Four new FUZ tests cover source immutability, byte ranges, malformed/truncated/unknown-version containers, cancellation boundaries and exactly 24 bytes read even with a 1 MB LIP span.
- `cargo +stable run -p modhunter-core --example asset_probe_bench --offline`: synthetic warm sparse 2,000,024-byte file, 10,000 probes in debug build, p50 591 ns, p95 621 ns, p99 1,002 ns on this cloud machine. One open handle reused; no directory enumeration, archive decode, valid audio decoding or native application running. These numbers must not be represented as real mod-scan performance or a speedup over an existing scanner.
- Repository-pinned Rust 1.99.1 finished installing; formatting and the full 236-test core suite were rerun successfully on it. Its benchmark repeated at p50 591 ns, p95 611 ns, p99 851 ns. The first run above used stable 1.100.0. Windows/MO2 integration, real .fuz fixtures and sound playback were not tested.

## Follow-up: bounded audio access

`FuzAudio` is an owned, read-only member view suitable as a future native decoder input. It exposes audio-relative offsets only and does not expose its source handle. Byte-size refusal is explicit, never silent truncation. A decoder may seek only inside the audio range and each read is at most 64 KiB. Observed cancellation remains terminal even if the callback later becomes false. Changed/truncated sources cannot produce a clean premature EOF.

This does not detect a same-size in-place source mutation, prove the source is authorized, or impose a total repeated-read/time budget on a decoder. The caller must provide an authorized stable member handle/identity, reject stale profile work, and isolate/time-limit decoding. Compressed archive adapters remain separate. No disk extraction, decoder or gameplay integration was added. New tests cover byte-exact streaming, relative seeks including extremes, immutable originals, explicit clip budgets, terminal cancellation, truncation and chunk limits, including a real read-only file handle with synthetic bytes.

Follow-up verification on pinned Rust 1.99.1: 242 core tests passed (175 unit + 18 importer + 21 plugin analysis + 28 source adapters), zero failures. Ten asset tests include the original four probe tests and six stream tests. Independent review found that Interrupted is retried by standard Read helpers; stream cancellation now returns a terminal non-retry error, with bounded regressions for read_exact and read_to_end. `cargo fmt --all -- --check` passed. No real mod files or MO2 runtime used.
