# Boss victory → exact source, two browsers

Requested: after defeating a real installed-mod boss, open its correct source in the external browser and Modhunter's own browser so the player can manually select the exact update/add-on. No auto-download or guessed file choice.

Implemented on top of companion-recruitment PR #31:
- Rust/Tauri 2 + Windows WebView2 production host, not the historical Electron shell.
- A confirmed victory in the still-active run automatically requests both destinations when the native host is present. Explicit retry is also available. Browser-only hosting offers an external verified link and honestly states that integrated browsing requires native Modhunter.
- `modhunter-siege://source` accepts only profile and mod IDs. It never carries a renderer-supplied URL, native endpoint, token, filename or download instruction.
- Native code requires an owned current Siege tab, connected MO2, exact current profile, exact installed mod identity, confirmed defeated/forged bounty, verified public source, unchanged snapshot generation and connection epoch. A guarded single in-flight handoff prevents concurrent duplicate launches. Dashboard-only Watchtower tracking authority is unchanged.
- The page is resolved from current authoritative source metadata. Nexus pages select their Files tab; other verified providers keep their own source URL. Both native opening outcomes are checked independently and partial failure is reported. External sites use the existing isolated Watchtower website surface, without creating a watched page or automatically choosing an account.
- Opening a source does not download, install, change load order, claim update completion or award update currency. Existing native installer/download controls remain in charge of subsequent choices. Existing boss eligibility is still the pre-existing Nexus-backed eligible set; this change does not claim provider-wide boss support.

Validation: six Node engine tests, including identity-only handoff, source/profile validation, private-host rejection and delayed-victory/new-run regression. Fifty portable Rust desktop policy tests pass, including strict request parsing and authoritative source/bounty validation. Windows-specific Tauri integration has not been locally compiled or exercised; cloud browser launch remains blocked by OS socket EPERM. Final native CI and real Windows dual-browser acceptance remain required. Browser fallback intentionally rejects IP-literal sources instead of pretending to implement native IP classification.
