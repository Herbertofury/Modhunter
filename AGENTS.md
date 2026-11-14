# Repository instructions

Read `.agents/foundry/AGENTS-adapter.md` for the pinned shared engineering core.
Keep this file and nested instructions project-owned: setup, commands, constraints and acceptance belong here.

## Modhunter-local engineering contract

- Preserve the complete dashboard/game modes, offline profile import and optional MO2 live link. Rust desktop/core, Python MO2 adapter and web presentation have separate ownership; Watchtower and game modes must not become mandatory dependencies for importing or maintaining mods.
- Read native/Cargo.toml and .github/workflows/native-rust.yml for current versions and supported verification, not historical README release headings. Core Linux tests do not establish Windows WebView2/MO2 native acceptance.
- Keep Nexus authentication and native downloads/installers with MO2's supported adapter. Browser sign-in is not a provider API grant. Keep session material private and preserve last-good observations on failures.
- Honor profile generation, command expiry and cancellation boundaries. An old queued result must not execute against a newly selected profile; failed or canceled work must not replace a complete catalog with partial state.
- Use .agents/skills/modhunter-profile-lifecycle/SKILL.md for import, native bridge, Watchtower and release-regression work. Keep this repository's skill local, not in the shared core.
- Run applicable exact-head checks; preserve full result sets when comparing performance and record fixture versus real setup evidence. Never treat update-page text or a changed version string as permission to overwrite custom mods.
