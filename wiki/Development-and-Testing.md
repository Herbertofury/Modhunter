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


## SSS Update Workshop acceptance coverage (November 2026)

- `python3 -m unittest discover -s tests -p 'test_*.py' -q` validates source resolution, per-row Nexus file advisory, risk and target-version confirmation, duplicate guards and native install lifecycle via deterministic fakes.
- `python3 build.py` compiles the exact shared dashboard used in the plugin, Electron and standalone editions.
- `PYTHONPATH=. python3 tests/updater_sss_browser_qa.py` uses headless Chromium with in-memory MO2 API fixtures to exercise full UI flows and capture screenshots. This is a developer-only test tool; the shipped app has no Playwright dependency.
- `node scripts/benchmark_workshop_tickets.cjs` checks semantic equivalence and measures performance of full-list ticket lookup over 2,500 mods and 800 tickets. It is not a hard time threshold.
- **Not yet proven:** native Windows MO2 2.5.3/Qt signal integration, premium/free Nexus behavior, actual FOMOD reinstall on a populated profile, post-install game runtime or dependency correctness. CI checks are not substitutes for these gates.

Official upstream reference: [MO2 Python plugin development](https://www.modorganizer.org/python-plugins-doc/).
