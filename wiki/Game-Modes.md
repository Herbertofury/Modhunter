# ⚔️ Game Modes
[Home](Home.md) / Modes

![Battle menu](https://raw.githubusercontent.com/Herbertofury/Modhunter/main/screenshots/modhunter-menu.png)

## Modhunter Siege
A survivor-style combat roguelite with **5 hero classes**, automatic attacks, **22 upgrade paths**, **6 evolutions**, timed bosses and mod-themed enemies. Installed outdated mods can become in-world bounties only in a connected MO2 session.

## Atlas Campaign
**12 regions × 8 contracts × 5 mastery tiers = 480 first-clear milestones.** Missions carry distinct objectives, hazards and lore. See [Atlas Campaign](Atlas-Campaign.md).

## Shiverfell
A top-down persistent survival world with exploration, resource gathering, crafting, quests, weather, companions and the Dawnshard world-boss storyline. Offline saved game progress is separate from MO2 authorized maintenance rewards.

## Update Arsenal
The practical mod-management interface: current profile inspection, update candidates, versions and supported download/install operations, with gamification tied to verified actions.


## Roaming creature reward POIs — approved goal, not implemented (2026-11-19)

Add optional moving points of interest on large creatures: airborne birds and eventually colossal roaming giants. Reach an attached, clearly marked object by platforming/climbing, then **hit that object to collect a substantial in-game XP/points reward**. The intended feel mixes playful traversal with roguelite run decisions, especially Megabonk-like experiences; Shadow of the Colossus is a scale/climbing inspiration, not permission to copy characters, art or code. This records a requested goal, not verified capabilities of either reference game.

- [ ] Reconcile existing movement, moving-platform collision, creature rigs, reward ledgers and run lifecycle before implementation; research reusable existing solutions first.
- [ ] Prototype one optional slow ground giant with a marked reward object, two approach routes, safe exit and seed-varied route placement. Reuse suitable authorized assets/data for variation without treating reuse as a ceiling on quality or new features.
- [ ] Make boarding, movement-relative footing, climbing/jumping, hit registration and payoff legible and fun; provide reachable alternatives for differing mobility/builds and accessibility needs. Missing an optional encounter must not block a run.
- [ ] Define meaningful high rewards balanced against time, traversal difficulty and combat risk. Commit one reward per stable run/creature/object identity; repeated hits, reconnect, reload and overlapping collection must not duplicate it.
- [ ] Test missed jumps, safe recovery, camera/readability, creature bounds/despawn, pause/death/restart, simultaneous collection where multiplayer exists, and low/high mobility. Preserve saves, unrelated world/player state and prior progression.
- [ ] Record exact-build performance and actual player enjoyment/playtests; expand to flying creatures and larger multi-stage giants only after the bounded first slice works. Do not mark the goal complete from a mockup, menu, static giant or simulated reward alone.

**Modhunter scope:** track separately for Siege and Shiverfell's roguelite modes, adapting traversal to each mode's actual camera/movement rather than claiming existing 3D climbing. Preserve earlier moving geode-node worms, loot goblins, side-run return continuity and run-duration choices. Collected rewards are game-only XP/run points, never verified mod-update rewards, Forge Sigils, automatic installation or penalties for pinned mods. Keep the practical updater unaffected.
