# Repository instructions

Read `.agents/athena/AGENTS-adapter.md` for the pinned shared engineering core.
Keep this file and nested instructions project-owned: setup, commands, constraints and acceptance belong here.

## Modhunter-local engineering contract

- Preserve the complete dashboard/game modes, offline profile import and optional MO2 live link. Rust desktop/core, Python MO2 adapter and web presentation have separate ownership; Watchtower and game modes must not become mandatory dependencies for importing or maintaining mods.
- Read native/Cargo.toml and .github/workflows/native-rust.yml for current versions and supported verification, not historical README release headings. Core Linux tests do not establish Windows WebView2/MO2 native acceptance.
- Keep Nexus authentication and native downloads/installers with MO2's supported adapter. Browser sign-in is not a provider API grant. Keep session material private and preserve last-good observations on failures.
- Honor profile generation, command expiry and cancellation boundaries. An old queued result must not execute against a newly selected profile; failed or canceled work must not replace a complete catalog with partial state.
- Use .agents/skills/modhunter-profile-lifecycle/SKILL.md for import, native bridge, Watchtower and release-regression work. Keep this repository's skill local, not in the shared core.
- Run applicable exact-head checks; preserve full result sets when comparing performance and record fixture versus real setup evidence. Never treat update-page text or a changed version string as permission to overwrite custom mods.

## Mandatory pre-release optimization gate

Before finalizing every release, follow the owner-mandated [Athena zero-loss pre-release optimization standard](https://github.com/Herbertofury/Athena/blob/main/guidance/PRE_RELEASE_OPTIMIZATION.md). Evaluate shrink-anything, fast-anything and rust-anything using reviewed pinned tools in isolated worktrees; run applicable passes, compare equivalent complete workloads, keep only independently verified improvements, restore rejected changes, and record per-tool evidence or explicit inapplicability/blockers. Never weaken tests, delete required content, force a Rust rewrite or regress functionality, quality, portability, compatibility, security or protected performance metrics. Data/documentation-only releases and existing native Rust paths require honest applicability decisions, not artificial ports. Existing local and pinned governance requirements remain intact.

## Mandatory CLI-first / non-interruptive execution

For every task, follow the owner-mandated [Athena CLI-first execution standard](https://github.com/Herbertofury/Athena/blob/main/guidance/CLI_FIRST_EXECUTION.md). Prefer fast official CLI/headless commands, supported APIs/SDKs/connectors and tested local automation. If a required application has no usable structured interface, discover or build a reviewed [CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness over the real supported backend (or a native command surface for first-party code). Use isolated browser/renderer/test sessions for necessary UI proof. Never take over the user's active desktop, mouse, keyboard, focus or browser profile without explicit authorization for unavoidable foreground interaction. Preserve real runtime acceptance, data, permissions, feature parity and portable mode. Record reusable command coverage and unresolved gaps; no fake CLI parity or unnecessary release delay.

## Cross-project Game Systems Exchange

For Siege, Atlas Campaign, Shiverfell or any future card-game/roguelike mode, consult [Athena's Game Systems Exchange](https://github.com/Herbertofury/Athena/blob/main/docs/GAME-SYSTEMS-EXCHANGE.md), [Athena #364](https://github.com/Herbertofury/Athena/issues/364), and the gameplay entries in [Athena's shared capability registry](https://github.com/Herbertofury/Athena/blob/main/guidance/registry/shared-capabilities.json). Compare JetSetCraft and CrownFold source/tests before implementing another run lifecycle, upgrade/evolution system, encounter graph, reward ledger or save mechanism.

Keep Siege, Atlas Campaign and Shiverfell's run/persistent-world progression distinct. Keep game-only XP, coins, bounties and rewards separate from Forge Sigils and other rewards that require verified native MO2 update evidence. Never let browser/game claims mint maintenance currency or affect practical mod management. Contribute reusable rules, fixtures, exact source revision, test commands, evidence and limitations to Athena at material closeout; share contracts across languages and only share code after two compatible real hosts accept it.
