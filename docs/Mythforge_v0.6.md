# Mythforge v0.6 technical release note

Canonical predecessor: Shiverfell Chronicles v0.5. All previously supported MO2 modules, Chronicle accounting, AetherFX visual effects, route behavior, and catalog entries remain.

## New cross-boundary state
- MO2 native `Chronicle.save_realm_world` now explicitly allows `story` and `perks` as world-only keys (the ranked wallet remains server-derived and never accepts wallet as world data).
- `player.skillPoints` and `perks` grant gameplay modifiers, never ranked score or Forge Sigils.
- Existing world objects default new keys in the browser initialization with v1 save compatibility.
- Three story landmarks can each reward a Dawnshard only once; Warden clear is persistent.

## Rendering and performance
- Terrain biome maps and object lookups use deterministic coordinate caches. Painted terrain is held in disposable 8x8 world chunk pixel caches; world resources and mod lists are not culled/capped.
- Day/night atmosphere and weather render after live objects. Textures use original browser-code procedural art with no model-generated assets or remote downloads.
- Equivalent-work Chromium baseline: old v0.5 median 1.7 ms, p90 5.8 ms; Mythforge median 1.1 ms, p90 2.6 ms over 180 rendered frames in headless Chromium; reevaluate on target Windows/MO2 hardware.

## Release acceptance
- Full existing Python unit tests and all five UI browser flows plus native fake MO2 bridge test.
- Story quest idempotence, world boss persistence, talent point allocation, wallet untouched, world save, controller start/pause/analog movement, mobile.
- Zip validation, HTML parse/render, source manifest/hash, remote Drive publication.

## Remaining AAAA-scale work
- Native Windows MO2/PyQt host-run proof and high-count update scanning benchmark; a production-grade 3D world, full authored RPG campaigns, character animation pipeline, voice/audio, richer NPC systems, save migration stress testing, and dedicated gamepad QA on hardware.
- This release is not represented as a fully AAAA game; it is a substantial tested prototype milestone.
