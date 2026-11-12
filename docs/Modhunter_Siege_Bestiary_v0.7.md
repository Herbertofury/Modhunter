# Modhunter Siege v0.7: The Unfinished Bestiary

This release extends **MO2 Update Arsenal**, not the separate `MO2R` placeholder repository. The Modhunter Siege tab is an original 2D, survivor-style roguelite with a three-member auto-attacking squad, 22 upgrade lines, wave progression, XP choices, boss bounties, and saved per-profile progress. The normal mod-dashboard, Forge Sigil economy, Shiverfell world, AetherFX finishers, MO2 profile navigation, and Questbound Chronicle remain.

## New bestiary and named enemies

An **outdated mod is a faction**: lesser enemies, rare tougher champions and a large named boss use the same underlying MO2 mod identity. Archetypes and names depend on content evidence:

| Source type | Example original mod | Example lesser enemy | Example boss |
| --- | --- | --- | --- |
| Weapon | `Electron Maul - SSE` | `Rustbound Electron Maul Duelist` | `The Electron Maul Blade Tyrant` |
| Armor | `Iron Rose Armor 2k Complex Material` | `Dented Iron Rose Armor Sentinel` | `The Ironclad Iron Rose Armor Colossus` |
| NPC/follower | `Celuna - Dark Elf Follower` | `Echo of Celuna` | `Celuna, the Unfinished` |
| Magic | `Storm Calling Magic 2` | `Storm Calling Magic 2 Hexer` | `The Arch-Hexer of Storm Calling Magic 2` |
| Creature | `Goblins Pack` | `Rabid Goblins Pack` | `The Goblins Pack Broodmother` |
| Quest | `A Dragonknight's Tale` | `A Dragonknight's Tale Oathbreaker` | `The Unfinished Legend of A Dragonknight's Tale` |
| World | `Animated Ice Floes` | `Mossbound Animated Ice Floes Stalker` | `The Animated Ice Floes Worldroot` |
| Framework | `Dynamic String Distributor (DSD)` | `Dynamic String Distributor (DSD) Gremlin` | `The Unresolved Dynamic String Distributor (DSD) Overlord` |

**Factual content names vs lore inspired by mod titles:** If the source has explicit item/NPC/content metadata, the explicit name takes precedence. Otherwise this release transparently invents a *fantasy name inspired by the mod's title/category*. The latter is not a claim that an actual item or character with that name exists inside an ESP or game. The live MO2 snapshot currently supplies titles, groups, version/source metadata and notes; it does **not** parse every ESP/NIF asset. MO2R or a future indexed content provider can populate `contentEntities`/`contentNames` on a mod record with `weapons`, `armors`, `npcs`, `creatures`, or `spells`, and the backend will use those names ahead of title heuristics. A real asset-derived content index remains a separate unverified improvement.

For an existing exact mod ID, the server returns a deterministic `lore` theme with `kind`, `subject`, `glyph`, `color`, `foes`, `bossTitle`, `provenance`, and `sourceMod` in `/api/siege/targets`. Nonmatching mods are not invented as update candidates. Unknown or ambiguous version results cannot spawn a ranked target.

During gameplay, normal enemy avatars inherit themed colors/icons, with dynamic movement/combat differences for archetypes. Named champions have readable overhead labels; the rest expose names via a pointer-anchored tooltip on the canvas. Full enemy rosters appear in **The Unfinished Bestiary**, accessible both before battle and in the War Council. Each entry links straight to the exact mod in MO2. This UI renders all targets, not a truncated sample.

## Verified rewards and navigation

- Slay an actual live mod boss: **+100 unranked in-game Hunt Points**; the native MO2 server records an idempotent bounty **without minting Sigils or ranked XP**.
- Subsequently install a newer version of that exact mod in the same MO2 profile: native Chronicle observes the version transition and awards its normal 100 ranked points / 10 Forge Sigils, **plus 200 bonus ranked points and 10 bonus Sigils** for that previously defeated boss.
- Boss result dialog offers **Reveal Exact Mod in MO2**, **Nexus Files**, and **Continue Surviving**. The Bestiary also provides direct-to-MO2 navigation. No game-only payload can fake a verified update.
- Offline practice has a real survivor game, but no trusted MO2 economy, and does not create ranked rewards.

## Safety and performance

- Exact MO2 internal IDs are the identity anchor; titles and enemy names never substitute for them in native calls.
- Source text is escaped for DOM output; game render uses canvas text. Even the rare floating enemy labels have a constrained render width, while full names remain available in the Bestiary/hover.
- Enemy lore is computed when the target list is retrieved, **not per frame or on hover**. The particle/animation systems from AetherFX remain unchanged.
- No mod files, load order, drag/drop model, or installs are changed by enemy naming. Native MO2 still handles any actual mod install through its existing prompts.
- The same screenshot-baseline samples (227 mods, 115 sections, 223 direct links) remain preserved in the offline HTML.

## QA and limitations

Development run tested: unit, bridge authorization/profile isolation/version rules, Chrome desktop/mobile, battle/upgrade/squad save, metadata-anchored names and exact focus, hover tooltip coordinates, Questbound and AetherFX animation regression, Shiverfell/Realm regression. The Windows MO2 2.5.x / PyQt6 application itself was **not launched inside this Linux runner**, so native plugin installation and selection must be confirmed on a Windows MO2 instance before treating the plugin as production-qualified.
