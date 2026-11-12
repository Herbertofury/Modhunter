# Shiverfell Chronicles | Survival Realm v0.5

Shiverfell is the original **playable 2D top-down, Skyrim-inspired** survival / roguelite realm inside MO2 Update Arsenal. The complete game is bundled into the same self-contained HTML as the update dashboard, opened with the **Survival Realm** link using `?realm=1` in a separate browser tab. The original mod list and game have independent DOM trees; switching windows does not rerender the mod list. No Skyrim media, models, code, audio or textures were copied.

## How the two systems work together

```
Verified MO2 version change
 -> Chronicle's per-profile unique update event (existing anti-repeat logic)
 -> 10 Forge Sigils issued for that unique upgrade
 -> native GET /api/realm authenticated scoped wallet
 -> native POST /api/realm/purchase (transaction ID, profile ID, balance/receipt validation)
 -> world inventory/permanent gear reconciled from canonical purchase receipts
 -> native GET/POST /api/realm/world with revision/conflict checks
 -> Shiverfell `?realm=1` game tab
```

**The authority for ranked currency is the native MO2 Chronicle**, not the browser. The older local checklist and finisher preview create no currency. Reinstalling identical versions creates no additional Sigils. Purchases are idempotent, survive restart, and are scoped to the active MO2 profile; a profile switch cannot spend another profile's wallet. Server-side revision guards prevent stale world overwrites. The offline version shows **30 clearly labeled practice Sigils**, kept separately in browser local storage and never imported into the ranked wallet. Changes made offline are not silently promoted into the live profile.

## What you can play

- A deterministic, effectively unbounded Canvas wilderness with forests, snowy mountains, tundra, lakes, settlements, ruins and changing time of day. New terrain is generated around the camera from world coordinates and a stable seed, without a finite world list/viewport content cap.
- Survival: hunger, warmth, stamina, health, campfire recovery and food.
- Real-time sword, bow, fire/frost, dodge, enemy attacks and loot. Threats include wolves, bandits and wraiths; enemies respawn on a world-clock schedule.
- Gather timber, stone and herbs; craft bandages, meals, arrows, frost runes and campfires. Level up, earn XP, complete quests, and explore a responsive cartographer map.
- Buy persistent equipment and upgrades such as a steel sword, ranger bow, winter cloak, wolf companion and Hearth Hall with Sigils; consumables include rift keys and supplies.
- **Frostbound Rift** is a 5-wave combat roguelite with difficulty escalation, blessing choices, and an arena boss; purchased rift keys enter this mode.
- **Wilderness Ironman** is a free one-life relic expedition with seeded relic sites, its own challenge and records.
- Game state is isolated by profile, debounced/saved, and pauses when its tab is hidden.

### Controls

WASD/arrows movement; Shift sprint; Space dodge; F or click sword; Q bow; 1 ember magic; 2 frost magic; E gather/interact; M map; Esc pause. Clicking empty ground sets a destination. Narrow screens include touch directional/attack buttons. The game may use a small, original optional synthesized sound.

## Item-local effects (important)

AetherFX selection highlights are DOM children of the **actual `.mod-row`** and are clipped to its own border box. No separate viewport-fixed sparks land away from the row when the list scrolls. The inventory `.realm-item` selection, `.realm-recipe` craft charge and `.realm-shop-item` purchase charge use local pseudo-elements and are clipped/contained in the selected card, instead of global coordinates. The Fx system stops drawing when idle and respects `prefers-reduced-motion`.

## Evidence / limits

- Python `tests/test_realm.py` verifies wallet issuance, idempotency, forbidden double spend, profile isolation, persistence/revision conflicts and invalid reward attempts. Earlier suite coverage is preserved.
- `tests/realm_qa.py` uses the actual compiled HTML in Chromium and exercises player movement, rift combat mode, Ironman entry, world resource gathering, camp crafting, save+reload, purchase ledger, live bridge calls, correct item-local effects, responsive mobile, and JS error absence. The prior three browser suites cover the existing mod list, Chronicle and all eight AetherFX finishers.
- The Chromium fixture in this controlled environment cannot navigate to local HTTP or file URLs; script injection is tested with real compiled HTML and an in-memory storage fixture that simulates persistence across page reconstruction. The native loopback bridge is tested over real local HTTP in Python.
- A live Windows MO2 2.5.x/Qt integration test is **still required**. No claim is made that this is a 3D game or that it has Skyrim's full scope/content. It is a self-contained original 2D survival/roguelite prototype intended to be evolved without losing the existing MO2 tooling.
