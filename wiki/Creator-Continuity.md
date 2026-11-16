# Creator Continuity

Creator Continuity and the **Creators** workspace keep the people behind a mod library in view. Successful downloads can follow verified creators automatically; the directory brings their projects, new releases, account notices and reviewed moves together for the active MO2 profile.

The interface labels this feature **Premium**. This implementation does not add billing, subscription verification, license validation or an entitlement gate. The label describes the feature's intended product tier; it does not establish a paid account or a released premium offering.

## Follow creators from downloads

**Follow creators from downloads** is enabled by default for each profile. A successful native download or installation can add a creator once the exact project and its provider-published identity are verified. Starting, cancelling or failing a download does not create a follow. Repeated downloads merge into the existing creator and project records.

Nexus discovery uses the uploader's numeric account ID and matching public profile from the existing native metadata path. The interface calls that relationship **uploader** because an uploader can differ from the original author. ModHold binds its published author handle and profile to an exact game and project. Other supported source adapters can supply verified publishing profiles or namespaces; these are labeled by their actual relationship. An ordinary display-name credit, filename or matching username is insufficient.

Unknown, incomplete, expired or conflicting metadata becomes a **Discovery** to review. The original project remains accessible, and no account is invented from a filename. Use **Discover from library** to backfill existing installed projects, or **Follow this creator** in an individual mod's details to resolve that exact project. Turning automatic following off retains existing follows and lets explicit discovery continue.

These are local Modhunter follows. They do not follow accounts on Nexus, ModHold or a social network. Following does not change an installed mod, its source, or a download queue.

For downloads started elsewhere in MO2, its public completion callback does not identify the profile that originally started the download. The plugin captures the active native profile when completion is reported, then checks that context and the exact completed-download receipt again before accepting metadata. A subsequent profile change, failed/removed download state or explicit unfollow prevents a late lookup from adding a follow. This does not claim origin-profile attribution that the native callback cannot provide.

## Manage everything in Creators

Open **Creators** in the workspace rail. The directory includes searchable creator cards, project previews, readable provider/role labels, unseen-update counts and a selected-creator panel with complete project access. Search checks the full saved collection, including projects beyond the currently rendered page. Project lists reveal further entries on demand rather than silently truncating the underlying collection.

Use **Following**, **New updates**, **Source notices**, **Discoveries**, **Paused** and **Unfollowed** to focus the view. Filters by site and sorting by activity, name or follow date work with search. Routine successful checks do not move quiet creators to the top of the activity order.

An unseen-activity badge stays on the **Creators** navigation button while you use other workspaces. Outside Creators, the open dashboard reads saved follow activity once a minute and when its profile snapshot changes; these reads do not initiate provider checks. Account notices and release observations remain unseen until acknowledged.

| Control | What it does |
| --- | --- |
| **Check follows / Check now** | Refreshes supported creator evidence and linked project metadata. Work runs in the background; ordinary mod source scans retain priority. |
| **Cancel check** | Stops the current creator job while retaining saved follows and earlier evidence. |
| **Pause / Resume** | Pauses creator monitoring independently of normal installed-mod checks. |
| **Unfollow / Follow again** | Saves the deliberate choice. Future downloads cannot silently re-follow an unfollowed creator. The immediate Undo action restores the follow. |
| **Mark seen / Mark this view seen** | Acknowledges observed changes without changing a mod version or source. The latter applies to all matching creators in the filtered view. |
| **Refresh view** | Reads the saved service state without scheduling a provider check. |

Changes appear saved only after the service accepts them. Failed writes retain the previous setting and show a retryable error. Late responses from a previous profile cannot replace the current view. Offline and disconnected views keep available evidence readable while disabling changes that need MO2. Keyboard focus, reduced motion, light/dark styling and desktop/tablet/narrow layouts are covered by the interface and browser fixtures.

### New projects from followed creators

ModHold follows can discover projects that are not installed. A complete public catalog is fetched once per service cache window and shared across followed handles. The collector verifies every page and project identity before committing the snapshot; a failed or incomplete page retains the last-good discoveries. The first catalog establishes a baseline, and later new projects or version changes become unseen observations.

Catalog discoveries are labeled **From the creator's catalog · not installed**. They offer the original project page for review and never become an installation or a source replacement automatically. Project/main-file metadata remains the stronger evidence for an exact release; learning a main-file ID for an already observed version does not produce a duplicate update.

Nexus follows currently monitor their linked projects and pinned account status; they do not claim a complete account-wide catalog. The creator panel describes coverage explicitly. A handle-only provider cannot prove an invisible reassignment of that handle: ModHold currently exposes its author handle/profile rather than a stable numeric account ID. Cross-site usernames are not merged into one identity.

## Understand an author notice

An author notice identifies the original provider, the observed status, the observation date and a public evidence link. Imported claims are identified as imported. A saved observation older than seven days asks for a fresh check.

For Nexus, the public-profile reader binds the profile heading, avatar and adjacent status badge to the requested handle. It also records the stable numeric account ID from that header. A banned commenter on a mod page cannot establish the author's status. A missing page, challenge, rate limit or network failure leaves the status unknown and retains earlier verified evidence.

A later check that finds no banned badge is reported as **No ban badge observed**. It does not establish why moderation changed. If a handle now resolves to another numeric account, the old creator's observation is retained and the new account is not silently substituted.

### Dated SethSaidThat example

On **2026-11-16**, the [Nexus profile](https://www.nexusmods.com/profile/SethSaidThat) displayed **Banned**, bound to Nexus account **199502561**. The moderation reason was not established. The [ModHold author profile](https://modholdmods.com/u/SethSaidThat) linked back to that Nexus profile and listed the author's mods.

The bundled registry contains these exact projects as **candidates requiring review**:

| Mod | Original project | Destination to review |
| --- | --- | --- |
| Estates of Skyrim | [Nexus Skyrim SE #196696](https://www.nexusmods.com/skyrimspecialedition/mods/196696) | [ModHold Skyrim SE #11](https://modholdmods.com/g/skyrim-se/mods/11/estates-of-skyrim-immersive-settlement-and-survival-overhaul) |
| Word Travels | [Nexus Skyrim SE #194692](https://www.nexusmods.com/skyrimspecialedition/mods/194692) | [ModHold Skyrim SE #4](https://modholdmods.com/g/skyrim-se/mods/4/word-travels-friends-talk-about-your-deeds-19560-voiced-line) |

The profile backlink supports the creator relationship. The linked author and matching titles support offering these candidates. A direct relocation declaration for each individual mod was not independently verified, so each destination still needs the user's review. Warband's ModHold metadata was also checked during development, but an unverified original Nexus ID was not added to the migration registry.

## Review and follow a destination

1. Connect the dashboard to MO2 and open an affected mod's inspector, or use the **Creator Continuity** section to show affected mods.
2. Open the status evidence, original mod, destination mod and linked author profile. Check the game, edition, required variant, dependencies and main file.
3. Select **I reviewed this author, mod, game and variant**, then **Track reviewed destination**.
4. The choice is saved for the active profile. After the connected service accepts the source change, the destination is included in the next source check. The destination author handle is pinned to the reviewed choice.
5. Inspect the resulting version evidence. A matching project is still subject to version, main-file, prerelease and author checks.

Tracking keeps the original source, prior explicit override and review record. It changes the source used by Modhunter's update checks; it does not rewrite the installed version, native Nexus ID, installed files or installer choices. An old Nexus ID therefore does not prevent the approved ModHold project from being checked.

| Action | Result |
| --- | --- |
| **Pause destination tracking** | Restores the source override that existed before approval, or lets the native original source take effect when there was no override. The reviewed destination remains saved. |
| **Resume destination tracking** | Reuses the reviewed destination only if the source still matches the state left by pausing. A manual source change requires review again. |
| **Restore original source** | Restores the previous source and marks the saved choice undone. The review record remains available. |

Saving or checking against an obsolete profile cannot apply results to a newly selected profile. A source change invalidates its old update verdict, and author expectations are checked separately for each mod even when the network request is shared.

## Check author status with source updates

Use **Check creator status** or **Refresh creator status** for a direct public-profile check. For an additional Nexus author whose handle is saved with a mod, enable **Check this creator with mod source updates**. Authors already matched by a dated notice are also refreshed with connected source checks, including an enabled startup check.

These checks run through the active MO2 plugin. Automatic creator monitoring follows a normal source check with changed owned metadata or a successful creator discovery; it reuses installed-project results and refreshes uninstalled followed projects plus supported account/catalog evidence. A session-owned timer wakes once a minute and admits creators whose checks are due every 30 minutes, with a five-minute retry after metadata errors. Ordinary source scans retain priority. Closing the Creators workspace does not stop monitoring; closing the plugin cancels its timer and in-flight work.

Automatic checks respect existing provider caches, so a due check does not promise a new network read every 30 minutes. Nexus project metadata currently has a four-hour freshness window; Nexus account observations and the shared ModHold catalog have 15-minute windows. **Check follows** explicitly requests fresh linked-project metadata through the existing source scanners. Public-profile reads are coalesced, and unverified results can retry after two minutes. Each caller's stable account pin is applied separately. There is no independent scheduling service while MO2 is closed.

Only the Nexus public-profile layout observed during development currently supports fresh author-status detection. Imported notices can describe other providers, but importing a notice does not add a new provider's account-status parser.

## Import additional creator notices

Use **Import reviewed registry** to load a UTF-8 JSON document, at most 1 MiB, with kind `creator-continuity-registry` and `schemaVersion: 1`. This is an **app-local projection of reviewed notices and migration candidates**. The same initial seed is bundled in Modhunter, MO2R and Noxviola; that shared seed does not make this format the canonical interchange contract. **Export registry** provides the bundled document or the currently imported document as a starting point. The bundled source is `shared/creator-continuity.v1.json`.

The registry records creator identities, dated status evidence, destination profile evidence and exact original/destination project mappings. Every migration must require review. Known Nexus and ModHold URLs must agree with their declared provider, game and project ID; duplicate original projects and conflicting creator identities are rejected. Evidence links must be public HTTPS URLs without embedded credentials or secret query parameters.

Generic public destinations can be recorded for review, while live version support depends on the [source adapter](Updater-Source-Coverage). A registry import is a local claim to inspect, not a provider attestation or permission to change sources. There is no automatic web-wide discovery of every moved mod.

Invalid replacements retain existing progress. If saved imported data becomes unreadable, the dashboard reports the problem and shows the dated bundled observations. The original imported data remains available for export and recovery; an unrelated note edit does not erase it.

## Integration boundary with canonical evidence

The canonical cross-app evidence work is tracked in [Modding-Knowledge PR #6](https://github.com/Herbertofury/Modding-Knowledge/pull/6), using `schema_version: 1`. Ingestion of that canonical format and reconciliation of its evidence lifecycle are **not wired into this new tracking path**. A successful app-local registry import does not establish compatibility with that contract or a shared automatic feed.

Related Modhunter work already exists in [PR #24, `upgrade/modhold-public-metadata`](https://github.com/Herbertofury/Modhunter/pull/24), for the earlier public adapter, and [PR #25, `feat/author-source-continuity`](https://github.com/Herbertofury/Modhunter/pull/25), for the pure continuity evaluator. Those drafts target `main` and are not incorporated into this candidate's active-upgrade base. This implementation does not supersede them or claim that their integration is complete.

Noxviola retains its existing canonical consumer for review; its new tracking path, like the other two implementations, currently uses the local notice projection. Canonical ingestion, lifecycle reconciliation and compatibility with the existing adapter/evaluator work must be reconciled before this path is promoted as a shared automatic feed. The local tests and public metadata reads below do not establish release readiness across that boundary.

## ModHold version evidence

The exact provider is **modholdmods.com**. The Rust adapter reads its [documented public metadata API](https://modholdmods.com/developers) at `GET /api/v1/mods/{id}`. It validates the returned project ID, game, canonical page and author against the selected destination.

Main-file identity and version drive the result. Optional files, counters and virus-scan timestamps do not establish a main-mod update. Conflicting or ambiguous main files, a changed author, and alpha/beta releases require review. Published timestamps are retained with their undeclared timezone identified rather than silently treated as UTC.

ModHold support is **metadata only**. File identities, sizes and provider-published hashes can be inspected, but its files are not marked installable or added to an automatic download queue. Open the provider's page and use its normal account and download workflow. The public metadata reader does not read Nexus credentials, provider cookies or download tokens.

Public checks retain last-good metadata, respect provider cooldowns and reuse the shared source cache. A failed refresh never establishes a current version or an author ban.

## Validation and release limits

The development checkpoint is recorded in `native/evidence/creator-continuity-20261116.json`. It separates live public metadata, synthetic protocol fixtures and local tests. The live ModHold sample read three exact projects and repeated them from cache. Estates was correctly returned for review because its main archive was marked Beta. Word Travels and Warband returned release metadata. Those observations do not certify a game installation.

The final local code gates passed **179 JavaScript tests** and **287 Rust workspace tests**, plus formatting and strict workspace Clippy. The full Python run completed **569 tests: 538 passed and 31 skipped**. The focused creator-follow suite passed **45 tests**, and the earlier creator-continuity suite passed **28**. Compiled Python-to-Rust fixture checks passed **17 plugin checks** and **28 source checks**. The directory fixture searches all **12,002 creators** across pagination. Node tests exercise JavaScript behavior; they do not establish Chromium rendering or native MO2 execution.

At this checkpoint, canonical evidence integration, local Chromium interaction, Windows application/WebView2 behavior, official MO2 runtime integration and native package acceptance remain pending. The required hosted Chromium gate includes five Creator Hub workflows using fixture transport with production dashboard assets, including keyboard focus, durable saves, profile isolation, cancellation and 1440/800/390-pixel layouts. Local Chromium launch is blocked by the environment's socket restriction and was not bypassed. No ModHold archive was downloaded, no FOMOD was exercised and no game was launched by the live metadata proof. Consult the [current build results](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) for acceptance of the published commit.

**Continue:** [Updater sources](Updater-Source-Coverage) · [Update Workshop](Update-Workshop) · [MO2 Plugin](MO2-Plugin).
