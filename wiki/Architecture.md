# ⛓️ Architecture
[Home](Home.md) / Engineering

```mermaid
flowchart TB
  H[Standalone HTML: offline dashboard + games]
  D[Rust desktop: WebView2 tabs and native operations]
  I[Rust parallel importer and metadata cache]
  M[MO2 Python Tool: live profile + update scanner + server]
  S[(MO2 authoritative identity & verified rewards)]
  H -. Optional 127.0.0.1 session .-> M
  D -. Optional 127.0.0.1 session .-> M
  D --> I
  M --> S
```

All three packages have an independent entry point. If MO2 is absent, the HTML and desktop client continue offline. The compiled Rust desktop requires WebView2, which the Windows installer can bootstrap. The Python plugin continues inside MO2 for its organizer and Nexus APIs. The shared dashboard/game assets remain HTML/CSS/JavaScript. The previous Electron source is preserved separately.

The native toolbar and dashboard use separate child webviews. Only the bundled toolbar has native command capabilities; the MO2/game pages cannot invoke file dialogs or shell operations. External source links open through the operating system. Local exports are restricted to the current page's JSON, CSV and PowerShell blobs. See [Rust Desktop](Rust-Desktop.md) for lifecycle, performance and verification details.

| Source | Purpose |
| :-- | :-- |
| `native/crates/modhunter-desktop/` | Rust desktop state, tabs, commands, MO2 discovery and native exports |
| `native/crates/modhunter-core/` | Parallel read-only profile importer, cache and strict bridge client |
| `native/ui/` | Bundled desktop toolbar; canonical dashboard copied at build time |
| `mo2_update_arsenal/plugin.py` | MO2 lifecycle and Tool entry |
| `mo2_update_arsenal/bridge.py` | Loopback HTTP / bridge |
| `mo2_update_arsenal/scanner.py` | Metadata and update scan |
| `mo2_update_arsenal/gameplay.py` | Game/reward logic |
| `mo2_update_arsenal/web/` | Editable game/UI web assets |
| `desktop/` | Preserved previous Electron app and comparison importer |
| `web/Modhunter.html` | Portable generated self-contained HTML |
| `tests/` | Python and Chromium QA |

### Build and test
```bash
python build.py
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend_runtime.test.cjs
cd native
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cd ..
node --check desktop/main.cjs
node --check desktop/preload.cjs
node --check desktop/chrome.js
```

The v0.8 recovered archive includes all source files and the test suite. Publishing GitHub content does not itself count as a native Windows integration test.
