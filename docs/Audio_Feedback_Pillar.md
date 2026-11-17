# Audio feedback pillar

Status: implemented first shared sound layer; native listening and UI placement acceptance pending.

Every application and game should provide distinctive, satisfying, high-quality audio feedback as well as visual feedback. Audio is optional, never the only way information is conveyed, and never a dependency of imports, updates, downloads, accessibility or gameplay. Quality is judged by listening in the actual application, not by a test count. Expand and refine the palette against that standard.

## Implemented scope

- Dashboard selection and victory, Aether interactions, Realm's existing combat/loot/spell cues, Siege hits, collected gems, level-up choices and run outcomes use one shared WebAudio engine.
- All sounds are original synthesized envelopes and harmonics, without external samples, licensing dependencies, network requests or MO2 work.
- **Default off.** Accessible shared Audio feedback controls provide persisted opt-in, master volume and a preview. Existing dashboard/Realm audio buttons control the same master setting. Old per-mode preferences do not silently opt the new engine in.
- No AudioContext until an opted-in trusted user interaction; blocked/suspended audio is skipped rather than queued. One reused context replaces dashboard per-click contexts. Silent unsupported environments remain functional.
- At most 12 oscillators, short envelopes, per-cue cooldowns, immediate mute/zero-volume stop, hidden-page silence and page-exit cleanup. No frame polling, downloads or bridge callbacks.
- Motion settings and audio settings are independent. Visual state remains authoritative. Sounds are not proof that a download/install/update succeeded.

## Acceptance still required

Listen to rendered sounds on Windows WebView2 with headphones and speakers; check loudness, repetition fatigue and visual sync. Check control placement at small windows, overlays, dashboard and both game modes, keyboard operation and screen reader labels. Assess simultaneous windows and OS device interruption. This first palette is not a claim that sound design is finished or that all projects have been updated.

## Next expansion

Preserve this contract across the user's apps: opt-in, master and optional category controls, volume normalization, meaningful cues, accessible equivalent visuals, bounded runtime cost and no repetitive noise floods. Add richer original game-specific palettes after listening review; keep progress/error/confirmation cues semantically distinct. Do not add autoplay music or obscure functional controls.
