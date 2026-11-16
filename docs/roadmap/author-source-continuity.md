# Premium author/source continuity (requested 2026-11-16)

Goal: warn users of verified author bans/suspensions or departures, discover author-controlled replacement sources and continue exact installed-mod update tracking across sites. Shared product capability requested for Noxviola, MO2R and ModHunter; no mandatory Athena dependency.

## Current implementation

`modhunter-core::author_continuity` is an additive, pure evidence evaluator. It separates provider-confirmed status from source unavailability, author-controlled migration evidence, exact project mapping, freshness and adapter capability. It never installs, overwrites, removes an old source or treats a display-name match as author identity. It requires explicit user choice to activate destination tracking. Evidence booleans are inputs from a trusted review layer, not a verification service.

This is **staged core work**, not connected automatic detection, paid entitlement or a shipped feature. No real-world ban allegation is embedded as fact. The user's SethSaidThat/Nexus/ModHold example remains a research/acceptance case, not a blanket mapping of all mods.

## Shared contract and provider evidence

The authoritative cross-app contract is [Modding-Knowledge author/source transitions](https://github.com/Herbertofury/Modding-Knowledge/pull/6). This Rust evaluator is an internal projection, not a competing wire schema. Before attaching it to a live route, translate only active, unambiguous reviewed records; enforce shared lifecycle/retraction, game-scoped project mapping and user-consent gates. Creator-reported enforcement must remain attributed, not promoted to provider-confirmed status.

Read the [ModHold developer documentation](https://modholdmods.com/developers) and verified public `GET /api/v1/mods/12` on 2026-11-16. The response contains `id`, `game`, `version`, `page`, author handle and individual file IDs, versions and SHA-256 values. This confirms public metadata access; it does not establish cross-site identity, a ban, or a migration. Downloads use a separate player-key endpoint and are outside this feature slice.

## Open acceptance work

- [ ] Verified provider status ingestion, creator cross-links, timestamped provenance and correction/retraction lifecycle.
- [ ] Exact old/new project mapping by game/edition, with conflicting identity evidence held for review.
- [ ] ModHold public metadata adapter: official developer documentation distinguishes public metadata from keyed downloads; inspect actual endpoints and fixtures before activation.
- [ ] Native warning/review UI plus user-controlled enable/undo, original-source history, last-good state and profile-generation checks.
- [ ] Destination update polling through supported adapters; authenticated access remains separately scoped.
- [ ] Premium entitlement integration through existing billing architecture, without new purchases/subscriptions here.
- [ ] Shared schema/contracts reconciled with Noxviola/MO2R, no duplicated independent reputational database.
- [ ] Real provider and Windows/MO2 end-to-end acceptance.

## Validation boundary

Official Rust 1.90.0 installed into the cloud scratch workspace without changing shell/system settings. Nine focused new Rust unit tests passed. Full `cargo test --manifest-path native/Cargo.toml -p modhunter-core --locked` passed: 174 unit tests plus 67 integration tests (241 total). `cargo fmt --manifest-path native/Cargo.toml --all -- --check` passed. Clippy and Windows/native GUI tests remain unexecuted. No runtime-performance claim: evaluator is linear in a constant number of evidence records, with no network requests. Existing UI and updater routes untouched to avoid overlap with the ongoing visual overhaul.
