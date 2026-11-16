# ✦ Standalone HTML

[Download the current compiled standalone dashboard](https://github.com/Herbertofury/Modhunter/raw/refs/heads/main/mo2_update_arsenal/web/index.html) and save it as `Modhunter.html`. It is a **single-file** offline-capable dashboard and game, including JavaScript, CSS, game engines, the embedded sample catalog and AetherFX effects.

## Versioned standalone package

The 0.12.0 release pipeline prepares `Modhunter-0.12.0-Standalone.zip` independently from the Windows app and MO2 plugin packages. Check [GitHub Releases](https://github.com/Herbertofury/Modhunter/releases) for published availability; a source version or CI artifact alone does not establish that the release has passed all final gates.

When available, verify the ZIP against the release's `SHA256SUMS.txt`, extract it, and open `Modhunter.html`. Its `BUILD_INFO.json` identifies the tested version, commit and workflow; its internal `SHA256SUMS.txt` covers the extracted files. The release packager copies the exact tested HTML from the validated portable package. The unversioned source download above follows `main` and may represent a different checkpoint.

For a specific tested commit, the [standalone verification workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/build-workshop.yml) supplies a `Modhunter-Standalone-HTML` artifact with the HTML, build identity and SHA-256 checksum. This is standalone browser evidence; the Windows/MO2 app and matching Rust helper come from the [Windows releases](https://github.com/Herbertofury/Modhunter/releases), after their separate native checks pass. Older HTML and archives under `web/` and `dist/` remain historical outputs.

## Independent browser use

It runs independently in your browser: no MO2 plugin, desktop app, server, or sign-in is required. Its updates and coins are **manual/practice**, never represented as native MO2-verified rewards.

Use **List** or **Gallery**, adjustable density, provider filtering, search, section navigation, update inspection, notes, export/restore, compatibility notices, and **Survival Realm** / **Modhunter Siege** links. Gallery's **All mods** view keeps sparse chapters together; **By chapter** retains the chapter organization. Each game opens a new browser tab while the mod vault remains available. Browser progress remains in that browser's storage; use export/restore when moving it to another runtime.

To earn ranked native bonuses instead, open the *same dashboard* using the MO2 plugin's loopback URL. [[Getting Started]] · [[Rewards and Integrity]]
