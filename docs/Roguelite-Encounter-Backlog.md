# Roguelite duration and living-world encounters

User requirements (2026-11-17), preserved for both Shiverfell/Realm and Siege:

- Moving points of interest: a roaming geode worm with individually mineable nodes, resources and XP; pursuit, depletion and readable visual/audio feedback.
- Loot-carrier encounters with evasive behavior, readable escape window and earned resource drops.
- Random portals into independent 10/20/30-minute side runs. Preserve the complete parent run, pause its simulation clock, return to its exact position/state, transfer earned rewards once, and survive save/reload, death, cancellation and repeated interaction without duplication. No ranked currency from simulated rewards.
- Main runs at least 20 minutes, selectable 20/30/60/120/endless. Ten minutes is reserved for the side-run or explicit optional short format, not a silent shortening of the main run.
- Cross-mode shared rewards and meaningful endgame in every mode, consistent with Athena game direction. Preserve existing user progression and native verified currency; design an explicit versioned reward ledger and migration rather than inventing exchange rates or merging balances silently.
- Deep, build-changing endgame inspired by the depth of Path of Exile 1/2 and uncapped paragon-style progression; do not claim longer timers or renamed encounters fulfill this goal.

## Implemented slice: duration contracts

The actual Siege and Realm simulation completion paths use a shared duration contract. New runs default to 30 minutes; both menus expose 20/30/60/120/endless. Endless is persisted as JSON null, not Infinity. Legacy runs without a duration use a 20-minute floor. Siege still requires its boss/objective, Realm Ironman still requires the relics, and Rift continues its five-wave cycles rather than ending after five waves regardless of time. Finite rifts finish on a cleared wave after the time goal. Failure and manual return remain possible before the goal; no waiting period traps a player in a dead run.

These are JavaScript simulation adapters in the existing web engines, not a new native engine. They have no new IPC, filesystem or network calls; moving one clock comparison into Rust would add a needless bridge crossing. Native profile ownership and ranked economy are untouched.

## Unresolved-active, not delivered by this slice

1. Geode-worm source implementation now exists in both engines: see [Roaming-Geode-Worm.md](Roaming-Geode-Worm.md). Native visual/terrain usability remains pending. Loot-carrier source combat and visuals now exist in both engines: see [Treasure-Goblin.md](Treasure-Goblin.md); native visual/pacing acceptance remains open.
2. Siege side-run portal implementation now exists: [Side-Run-Portals.md](Side-Run-Portals.md). Realm source adapter now exists in [Realm-Side-Portals.md](Realm-Side-Portals.md). Native Windows visual/playtesting and combined source integration remain open; source tests do not establish full gameplay acceptance.
3. Long-run pacing, itemization, build diversity, uncapped progression, mastery balancing and measured 2-hour/endless session performance. Repeating wave cycles alone are not claimed as deep endgame.
4. Full-engine long-session gameplay/FPS acceptance remains required. Independent review exposed unrestricted live Siege population; the implemented reinforcement director now bounds concurrent combatants at a user-selected128/256/512 while preserving every pending reinforcement in the existing numeric reserve. Bosses have a reserved slot and admission priority. Existing enemies are never culled; no fake kills/drops occur. UI explicitly shows deferred reinforcements. This intentionally changes arrival timing under saturation; it is not claimed as a zero-loss timing optimization.
5. Windows WebView2/MO2 runtime and visual acceptance, controller selection, save/reload interrupted mid-combat and long-session playtesting.

## Verification

Source-driven VM tests exercise actual Siege tick and Realm simulation/clear-wave functions at each duration boundary, endless non-completion, persisted Siege duration and legacy floor. Existing consent/companion/audio/source regressions stay intact. VM canvas/DOM stubs are not visual/native acceptance. Run `node --test tests/*.test.cjs`, `python -m unittest discover -s tests`, and `MODHUNTER_ARTIFACT_DIR=/tmp/modhunter-build python build.py`.

Director-only fixture:160,000 simulation steps (2hours at45ms), eight modeled nonboss removals about once per second, compact numeric reserve and bounded concurrent population; spawn-debt conservation asserted. It does not run rendering, all combat abilities or real player decisions and is not a2hour gameplay/FPS acceptance. Real frame-time measurements remain required before release. No native performance speedup claimed.

## Follow-up: shared mod/game card mode (pending)

GameSync Next and ModHunter should each independently provide a Hearthstone-like card game based on the user's mods/games. Card power should derive from endorsements, downloads and project health; defeating a card leads to a user-directed mod update for points. Both apps should automatically discover and use the same save location, with versioned compatibility, safe concurrent writes and no mandatory app dependency. Locate and reuse existing work first. This is separate from portal delivery; economy formulas, shared-save implementation and game acceptance remain open.
