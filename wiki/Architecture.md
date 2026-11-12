# ⛓️ Architecture
[Home](Home.md) / Engineering

```mermaid
flowchart TB
  H[Standalone HTML: offline dashboard + games]
  D[Electron: multi-tab Chromium workspace]
  M[MO2 Python Tool: live profile + update scanner + server]
  S[(MO2 authoritative identity & verified rewards)]
  H -. Optional 127.0.0.1 session .-> M
  D -. Optional 127.0.0.1 session .-> M
  M --> S
```

All three packages have a fully independent entrypoint. There is **no required inter-component dependency**. If MO2 is absent, the HTML and desktop client continue offline. If Electron is absent, the HTML and MO2 plugin continue.

| Source | Purpose |
| :-- | :-- |
| `mo2_update_arsenal/plugin.py` | MO2 lifecycle and Tool entry |
| `mo2_update_arsenal/bridge.py` | Loopback HTTP / bridge |
| `mo2_update_arsenal/scanner.py` | Metadata and update scan |
| `mo2_update_arsenal/gameplay.py` | Game/reward logic |
| `mo2_update_arsenal/web/` | Editable game/UI web assets |
| `desktop/` | Electron app and tab chrome |
| `web/Modhunter.html` | Portable generated self-contained HTML |
| `tests/` | Python and Chromium QA |

### Build and test
```bash
python build.py
python -m unittest discover -s tests -p 'test_*.py'
node --check desktop/main.cjs
node --check desktop/preload.cjs
node --check desktop/chrome.js
```

The v0.8 recovered archive includes all source files and the test suite. Publishing GitHub content does not itself count as a native Windows integration test.
