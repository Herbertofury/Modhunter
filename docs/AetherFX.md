# AetherFX v0.4: Power Mode for Modding

## User experience

The visual system borrows the *interaction vocabulary*, not code or copyrighted visual assets, of VS Code **Power Mode**: every meaningful action feels tactile and earns spectacular but truthful feedback. Source inspiration: https://marketplace.visualstudio.com/items?itemName=hoovercj.vscode-power-mode

- **Mod selection:** target scan, traveling cursor beam from the previous selected row, runic halo, cyan/gold sparks, highlighted row, and *Target acquired* text. Clicking the mod **name** opens its original source website in a new tab; a separate **⌖** button (or the non-interactive row background) performs the exact-MO2 reveal action. AetherFX selection animation never changes the selected mod ID.
- **Checklist completion:** 14 clip-path fragments physically break apart from the old row in Glass mode. Sword Slash splits it into two moving panels. Critical hits also fracture it. Dragonfire burns the row using an animated heat film; frost crystallizes it, lightning flashes, arrow cuts across it, and fireworks create a gold flash. The previous row is captured **before** the new completed state is rendered, so the animation overlays the correct stable UI state.
- **Manual combo chain:** consecutive mod-clears within nine seconds produce a short *Forge Momentum* multiplier visual. It never awards ranked XP, streaks, or achievements. Those remain tied exclusively to native MO2 verified version changes.
- **Other interactions:** mod hover leaves optional arcane sparks; bookmarking radiates gold; undo generates a collapsing enchantment effect; a verified native upgrade adds its own celebratory spark. J/K navigation calls the same selection system without modifying MO2's row model.
- **Forge FX Studio:** a new ✧ control in the dashboard header opens Legendary, Vivid, Subtle, and Off visual intensities; toggles for weapon-impact shake and hover sparks; and a practice preview button. Preferences persist per profile via the existing progress storage.
- **Sound:** optional short original synthesized impacts through Web Audio. Never accesses external assets or downloads and never asks for extra permissions.

## State separation and exact MO2 navigation

Only existing state methods (`setStatus`, `locateInMO2`, `focusRow`, `render`, `refreshGame`) own checklist and ranked update state. `window.AetherFX` accepts DOM elements and paints temporary layers; it has no access to bridge credentials, MO2 mutation, source or download tools, or progress writes. The live MO2 exact selected ID remains authoritative. A client-side selection effect is not evidence of an MO2 update.

The original 227 mod / 115 separator archive and all source links remain unchanged. The Live MO2 profile can contain arbitrarily many entries; only the short-lived visual burst is bounded by elapsed time. The canvas runs **only when an effect is active**, releases its objects after their TTL and uses one shared canvas with DPR <= 2. `prefers-reduced-motion: reduce` skips the effects while preserving the checkbox state change and all UI controls.

## Real browser QA

Run:

```sh
python build.py
PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=. python tests/quest_qa.py
PYTHONPATH=. python tests/aether_qa.py
```

`aether_qa.py` exercises the built standalone HTML in real Chromium. It checks the 14 glass fragments, all eight modes, animation cleanup and idle scheduler, selection, the FX Studio and Off/Subtle modes, mobile overflow, reduced-motion accessibility, the 227 mod rows, 115 original chapters, and that no page errors occur. `quest_qa.py` continues to prove that exact MO2 focus requests and backend-ranked progression remain separate.

## Known verification boundary

The package is a working browser demo + MO2 Python plugin source. Chromium tests are real, but test MO2 API responses are fixtures. Native Windows/MO2 UI selection and GPU behavior still need proof on a real MO2 2.5.x host with MO2R's other columns and custom list delegates; the plugin does not alter those delegates.
