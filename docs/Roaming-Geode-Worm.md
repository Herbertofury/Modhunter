# Roaming geode worm: implemented gameplay slice

Both Siege and Shiverfell/Realm now use an original articulated six-node roaming mining encounter. Move near a glowing node and press E; Siege also exposes a nearby mining button and Y-controller action, while Realm uses its existing interact controls. Three strikes fracture a node with a250ms simulation-clock cooldown. The moving body and remaining-strike numerals make the target and its state visible. Depleted nodes become dark; the next worm arrives after90simulation seconds once every node is depleted.

Rewards remain simulation-only:6XP and one additional useful reroll charge in Siege;6adventurerXP,2stone and1scrap in Realm. No native Forge Sigils, ranked points, provider downloads or updates are granted. Optional existing audio provides mining/completion feedback and respects mute.

The versioned six-element state is saved inside the current Siege run or Realm story/run holder, not a growing global history. Movement time, partial damage, cooldown, depletion and encounter serial survive ordinary game saves. Realm overworld and expedition encounters do not share depletion. Pause/loading/modal and stale-menu guards reject mining. This does not change the existing save cadence or promise crash-proof writes before a save completes.

Validation covers source-driven adapters and pure state behavior: movement, proximity, three-hit mining, cooldown, exactly-once depletion reward, JSON reload,90-second renewal, independent holders, useful local resource grants, paused Realm touch/controller interaction and stale Siege menu action. Existing source/consent/audio/duration regressions remain intact. Canvas/DOM stubs are not a native visual or controller-device acceptance test.

Not included: loot goblins, nested portals, shared cross-mode economy, deep endgame progression or final visual polish. Native Windows/WebView2 playtesting, terrain/path usability and visual/audio review remain open. No full-feature or performance-speedup claim: the encounter has one compact fixed-size state, six rendered nodes and no network/native bridge calls.
