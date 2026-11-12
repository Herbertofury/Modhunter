# 🛠 Development & Testing

## Developer quick start

```shell
python build.py
python -m pytest -q tests/test_core.py tests/test_bridge.py tests/test_gameplay.py tests/test_siege.py tests/test_lore.py tests/test_realm.py tests/test_focus.py
python tests/atlas_qa.py
cd desktop && npm run check
```

The browser tests use Chromium/Playwright. See `tests/` for actual game flows and screenshot checks; they do not substitute for testing native MO2 on Windows.

## What the tests cover

- Standalone boot and mobile layouts.
- Actual mod rows, source and compatibility indicators.
- Exact MO2 focus and profile separation.
- Verified bounties cannot be forged or double-claimed.
- Siege auto-combat, upgrades, boss names, Bestiary and pending-update prompt.
- Atlas 12-region / 96-chapter uniqueness, 480 first-clear slots, unlocks, idempotent rewards and save/reload.
- Shiverfell movement, crafting, survival, world-boss quest and roguelite entry.
- AetherFX combat finishers and reduced-motion behavior.

## Known unverified gates

- Native Windows MO2 plugin installation and Qt live-focus testing.
- Compiled Windows Electron portable `.exe` verification (source build is available).
- Real Nexus auth/download workflows on a connected MO2 installation.
- Long-form unique-content quality and 80-hour player study.

## Release layout

The GitHub repository holds source code as normal text files and the complete release under `dist/`. The separate GitHub Wiki is sourced from `wiki/` and can be synchronized after its first Home page is initialized. The intended GitHub Wiki URL is [`Herbertofury/Modhunter/wiki`](https://github.com/Herbertofury/Modhunter/wiki).

[[Architecture]] · [[Frequently Asked Questions]]
