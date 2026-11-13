# GameSync update-checking techniques in Modhunter

## Reference and scope

The shipping GameSync repository was inspected at `547238dae77a8160bfc214817cb4df4adc35970e`; GameSync Next was inspected at `1dd15cffde3da14b9ae4a1f829323d7366952ed1`. Next's README identifies the shipping JavaScript extension as its migration/parity baseline. This is a focused update-checking transfer audit, not a claim that every extension module, game, provider or private asset has been ported or run.

Inspected shipping update-related sources include `app/background/background.js`, `app/src/services/fetch-cache-service.js`, `app/src/animation-tracker/CreatorPollEngine.js`, `AnimationPollScheduler.js` and `VersionDetector.js`, together with the source/service directory inventory. The read ranges of the larger polling/version modules cover the relevant queue, caching, strategy and comparison code; this is not an exhaustive source audit. GameSync Next's repository layout/README and shared core/engine inventories were checked to distinguish migration scaffolding from the shipping update implementation.

Modhunter implementations are independent and keep the stronger identity, credential isolation and execution ownership already present. No private GameSync source bundle, personal data, signing keys, browser profiles or assets are redistributed.

## Implemented equivalents and additions

| Useful technique | Modhunter implementation / acceptance |
| --- | --- |
| Exact source identity instead of title-only guessing | `source_registry.py`, Rust `source_adapters/identity.rs`: provider/project identity and canonical URLs; game/loader/runtime constraints remain separate. Fuzzy author/title matches are not install authorization. |
| Multiple version strategies | **0.10.1 addition:** `version_evidence.py` compares complete numeric, validated calendar-date, explicit build and recognized prerelease labels. It rejects prose, prices, sizes, ambiguous dates and excessive inputs. Same-channel beta numbers compare numerically; stable-to-beta and changed unknown channels require review. Shared `version_verdict` is used by Nexus and external source processing. |
| Explainable comparison | Nexus summaries retain the comparison strategy and reason. File selection, project provenance, dependencies, runtime compatibility and conversion evidence are still independent; no confidence score means automatically safe. |
| Conditional requests and bounded caches | Rust public adapters preserve ETag/Last-Modified and TTL caching with bounded responses/cache size. GitLab redirect validators belong to the resolved release, so a newly selected tag cannot reuse an old release's 304. These were present before 0.10.1 and are retained, not advertised as new. |
| Service backoff as well as quota handling | **0.10.1 addition:** HTTP 503 uses bounded Retry-After seconds/dates and a persisted provider-host hold. Existing 429/quota holds remain. Forced checks honor both; service outages are not mislabeled rate limits or current versions. Expired/failed evidence cannot remain a fresh update claim. |
| Bounded concurrency and request deduplication | Existing native adapter workers and project deduplication, Nexus scan bounds, shared exact file lookups, single-flight UI reads, and complete cached MO2 snapshots avoid redundant work. There is no unlimited-worker or rate-limit bypass mode. |
| Cancellation and stale-result isolation | Existing profile/execution epochs, watch/job/session generations, owned callback tickets, cancellation checks and last-good state reject replies belonging to an earlier context. |
| Optional scheduled following | Watchtower manual checks, check-due/check-on-launch and optional app-open background checking provide the desktop equivalent. Pause/mute and interval controls remain explicit. This does not install an always-running OS service. |
| Creator/release/changelog page observation | Watchtower tracks explicit public HTTPS pages, CSS-selected content and ignored regions, with bounded full-content fingerprints and retained difference previews. First reads establish baselines; later unread observations remain tied to exact identities. |
| Account-aware fallback | Named WebView2 sessions support permitted page reads after the user signs in. An access challenge is actionable failure, not no-update. No credential import from Opera/Chrome, paid-access bypass or universal OAuth success claim is made. Nexus authority stays with MO2. |
| Health and provenance next to mods | Existing mod health distinguishes observed zero, disabled/hidden/unavailable bug pages and stale observations. Rust plugin analysis distinguishes winning light/full plugins, recorded ESLifier provenance, changed source/output hashes, master problems and conditional conversion candidates. |
| Recovery, history and data portability | Atomic Watchtower state, explicit preserved-file recovery, credential-free watchlist interchange, exact seen/undo history, and prevalidated progress restores preserve user work. GameSync browser storage and private assets are not silently migrated. |
| Use native installation authority | The Update Workshop retains exact reviewed provider/file fingerprints and MO2 download/FOMOD/replace decisions. A page change, a new date or a version label alone never grants installation permission. |

## Provider coverage is not one blanket claim

Nexus metadata/files/downloads use MO2's native authenticated connection. GitHub, GitLab, Modrinth and Thunderstore have public release-API adapters with provider-specific identity and file evidence. Loader/community/game restrictions still apply; a Minecraft package does not become a Skyrim candidate because the API worked.

The registry also recognizes creator/community sites such as Patreon, LoversLab, Afdian, Tumblr, SubscribeStar, itch.io and others. Recognition, useful source links and user-configured page monitoring are **not** the same as a fully automatic authenticated release/download adapter. CurseForge key requirements and sites with blocked embedded login remain explicit. Live provider-specific login, entitlement and sample-post acceptance are not completed by local fixtures.

## Deliberately not transplanted

Generic regex extraction from a whole page is not allowed to select the installed mod's version: a Skyrim runtime number, dependency version or price can be a false match. The new parser accepts only bounded full-field labels and has a repeated-call regression test rather than an unbounded regex iteration loop. Unknown formats and mixed numbering systems return review/unknown.

A failed platform lookup or empty page does not mean current. Generic cross-account response caches and heuristic fallback identities do not replace Modhunter's origin/session and exact project/file boundaries. GameSync's extension UI, mascots, AI search/ranking infrastructure, Steam ownership features and unrelated games are not dependencies of a Skyrim mod updater.

## New test evidence

`tests/test_update_version_evidence.py` covers numeric compatibility, valid/invalid dates, build numbers, prerelease channels/order, metadata ambiguity, hostile/prose inputs, bounded repeated parsing and unchanged source IDs. Rust `tests/source_adapters.rs` adds service-outage persistence, forced-refresh holds, recovery and Retry-After tests. Existing source/identity/cancellation and Chromium/native-MO2 suites remain mandatory.

The MO2 beta-readiness work is described in [MO2 2.5.3 testing](MO2-253-Testing.md). Exact beta-12 application execution, real Nexus download/install choices and every private-provider login still need the actual environment; they are not represented by a green generic adapter badge.
