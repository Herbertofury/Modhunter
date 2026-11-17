# Optional Siege Auto Update

The boss victory panel now offers **Auto Update · confirm before install**. This is an explicit player action, never an automatic consequence of combat.

1. Reuse the native update selector to find a uniquely supported current archive. It deliberately sends `autoInstall: false`. Existing active downloads are reused only for the current installed/target identity. Old completed archives no longer prevent a newer matching download.
2. Display chosen archive, exact file/asset identifier, provider, versions, target profile/runtime, download status and native preview warnings. Refresh status explicitly while downloading; leaving the panel leaves the non-installing download in MO2.
3. Only a separate **Confirm exact file · open MO2 installer** action can submit installation. The server checks an immutable full-briefing fingerprint, current profile and execution epoch under its native guard. A changed file, warning, runtime, backup scope or A→B→A profile roundtrip requires fresh review. Existing interactive MO2/FOMOD/overwrite prompts remain in charge.
4. **Manual override** immediately disarms this attempt and opens the verified source page to choose another edition or add-on. No queued automatic installation is created; downloaded archives remain visible in MO2. An already accepted native installer cannot be canceled merely by navigating back in the game.

Ambiguous editions, unsupported/gated providers, missing verified metadata, offline mode, blocked native preview or another workflow's already-authorized installer are reported rather than guessed. This feature does not bypass account access, compatibility requirements, verification or backup limitations. The preview reports the native service's actual backup capability.

Tests use synthetic API responses and stubbed native queue dispatch. They do not download or replace real mods. Browser rendering and real Windows/MO2 installer acceptance remain required before release. Stacked after PR #34; combine source with audio/art/release changes and regenerate HTML rather than overwriting other bundles.
