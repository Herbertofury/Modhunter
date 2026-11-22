# Siege companion recruitment

## Requested goal / backlog
Installed follower and companion mods should supply recruitable companions. Discover by name **and** categories, then require travel to points of interest to recruit, rather than granting allies on level-up. Inspiration: travelling to recruit survivors in Yet Another Zombie Survivors, not copying its art or code.

## Implemented slice
- The read-only MO2 snapshot preserves category labels from `IModInterface.categories()` ([official API](https://www.modorganizer.org/python-plugins-doc/autoapi/mobase/index.html#mobase.IModInterface.categories)).
- Local name, category and separator-group discovery retains every unique installed mod ID. Support-mod names (patches, frameworks, outfits etc.) remain visible but explicitly flagged for review. Category membership alone is not proof of an NPC.
- A new run snapshots that roster. Watchfire Refuge and Lost Waystation are actual world-space camps; approach within two world units, then press E or use the recruitment button. The HUD gives direction and distance. Each camp recruits once, within the existing three-person squad limit.
- Choosing a mod opens the full existing five-role selection. Its mod name and exact ID remain attached to the ally; existing automatic combat uses the chosen original Siege archetype. Choices and recruited camp state persist in the existing profile-bound save. Each level retains an upgrade choice.
- Original Siege allies remain available alongside candidates, so the player can reject false-positive matches. No fake installed-mod identities, ranked rewards, installation, enable/disable, or load-order mutations are introduced.

## Honest boundaries / remaining backlog
This is **metadata-inspired recruitment**, not an importer for arbitrary Bethesda NPC meshes, animations, spells or voice files. Asset adapters, portrait ingestion and verified NPC records remain separate work. A saved run intentionally retains its initial roster; updated installations are reflected on the next run. Older runs missing a roster receive a one-time roster migration after the current profile loads.

Browser gameplay verification script: `scripts/verify_siege_companions.cjs`. Actual Windows MO2 category collection and Windows WebView2 gameplay still require native acceptance. Cloud Chromium currently aborts before startup with process-singleton `socket() failed: Operation not permitted`, including the permitted escalation attempt; no browser visual pass is claimed. Node tests execute the actual engine closure with a stub DOM and cover gameplay state, not rendering.

Kept in existing Python MO2 bridge and JavaScript game engine to preserve their supported ownership; no standalone Rust rewrite or claimed performance benefit.
