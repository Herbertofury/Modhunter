# Rust public source adapters

`modhunter-cli scan-sources --request PATH|-` accepts a schema-1 JSON request and returns a schema-1 result. It performs public metadata reads only. The MO2 updater compares installed versions and owns reviewed downloads/installation.

## Contract

Request fields:

- `profileId`, `game`, `gameRuntime`, optional `loader`.
- `items`: `{id, installedVersion, source: {url, verified, kind, expectedAuthor?}}`. An approved ModHold author migration can pin its destination handle with `expectedAuthor`; this is checked separately for each row after project reads are deduplicated.
- Optional `force`, `budgetMs` (default 45,000), `workers` (default 4), `cache` from a prior response.

Result fields:

- `profileId`, `schemaVersion: 1`, `checkedAt` (Unix seconds), `itemRecords`, `cache`, `stats`, `errors`.
- Each row has `outcome`, `provider`, `source`, canonical selected `sourceUrl`, availability/freshness, observation time and reason.
- Established releases include exact `projectId`, `releaseId`, `latestVersion`, `releaseUrl`, publication time, bounded plain-text changelog/summary, and declared dependency/file metadata.
- `outcome: release` establishes public release identity, not complete game/save compatibility. `review`, `unavailable`, and `manual` must never become a current/update verdict by version comparison alone.
- Each file binds `assetKey`, `provider`, `projectId`, `releaseId`, filename, URL and any published hashes. `installable` is restricted to ZIP/7z/RAR and compatible source context; it is not an installation approval.
- GitHub `author` and GitLab `author` from a release are exposed as **publisher**. GitHub asset uploader is separate. Modrinth exposes canonical team/publisher IDs without inventing a creator name. Thunderstore identifies the publishing namespace. Project icons/gallery are separate from profile avatars.

## Provider behavior

| Provider | Identity and release checks | File boundary |
|---|---|---|
| GitHub | Exact owner/repository, API release ID, matching page/tag, explicitly non-draft/non-prerelease, published date | Exact repository/tag/filename; uploaded archive assets; numeric asset ID; optional declared SHA-256 |
| GitLab | Complete nested namespace; exact project/tag release page; no upcoming, future or recognizable prerelease tag | Both underlying asset URL and optional redirect wrapper must stay within the selected project's uploads/release paths; other files remain manual |
| Modrinth | Exact requested slug or canonical project ID, project type/status, stable version IDs, declared game/loader context | Exact project/version/filename CDN joins; current version-ID and documented legacy version-number paths; published SHA-1/SHA-256/SHA-512; JAR/MRpack are metadata-only |
| Thunderstore | Exact namespace/name/version, active latest version, exact public community listing and matching selected game; explicit `moderationStatus` | Exact versioned official download route; deprecation becomes review; no fabricated size or checksum |
| ModHold | Exact numeric mod ID, selected game, canonical page, author handle, and one current main file whose version equals the page version | Public file identity/hash metadata only; `installable: false`. Downloads require the site's own player sign-in and manual review. Optional files never determine the main mod's version |

A Modrinth link in a Bethesda profile does not become a compatible update. Missing Minecraft version/loader or Thunderstore community/game evidence becomes review. No new games are automatically installed merely because metadata can be read.

An active Thunderstore listing marked `unreviewed` can establish its public release facts, while the result preserves that moderation status. It does not imply provider approval or file safety. Rejected, unknown or missing community listings remain review-only.

GitLab's latest-release endpoint does not provide a general stable-channel selector. A latest tag recognizable as prerelease is left unavailable for automatic version comparison rather than substituted with a guessed older release.

ModHold uses `https://modholdmods.com/g/{game}/mods/{id}/{slug}` and the public v1 metadata endpoint `https://modholdmods.com/api/v1/mods/{id}`. The optional slug can change without changing the numeric identity, but a different game, mod ID, API `page`, or pinned author becomes unresolved/review. A mod title alone never matches a migration. Its `releaseId` is the current main **file** ID, explicitly labelled `releaseIdentityKind: main-file`; the API does not expose a separate release ID. Alpha/beta names, missing or ambiguous main files, and a different game stay in review. ModHold API timestamps have no declared offset: `updatedAt` and `projectPublishedAt` retain the published strings, and `publishedAt` is used only for the current version's own changelog entry. Virus-scan times are never used as release dates. Public metadata can describe paid/supporter content without granting download entitlement.

Unsupported sites retain explicit manual/account/API-key/official-client states. No browser cookies, credentials, private memberships, challenge bypass or arbitrary caller-provided API endpoints are supported by this public engine.

## Resource and freshness limits

- HTTPS endpoints derived from exact project identities on `api.github.com`, `gitlab.com`, `api.modrinth.com`, `thunderstore.io`, and `modholdmods.com`. ModHold's allowlist includes only a single numeric mod metadata endpoint, never file downloads or caller-defined endpoints.
- One GitLab exception follows its official latest-release redirect to an exact release on the same API host. A numeric project alias is accepted only with final release-body proof of the original complete project path and tag. No second redirect is followed; moved projects require source review.
- TLS certificate verification uses the system trust store, with the system/environment proxy configuration. The local MO2 bridge explicitly bypasses proxies for its loopback connection. No certificate-validation bypass is enabled.
- 2 MiB maximum response; JSON duplicate keys are rejected throughout the payload.
- At most 8 workers, 10 seconds per request, and a configurable 100–120,000 ms scan budget. Requests still waiting when the budget expires remain unknown.
- At most 25,000 requested rows and 2,048 distinct projects. Excessive input fails explicitly.
- Per-process cache bounded during insertion and export to 256 entries / 4 MiB. Entries over 128 KiB are not cached. Only consumed public fields are retained.
- Successful observations last up to 15 minutes; expired entries can use ETag/Last-Modified conditional requests. A 304 requires an existing cached body for the same resolved endpoint and refreshes its observation time. GitLab redirect validators cannot transfer to a different release tag.
- Failed revalidation expires previous success. Provider cooldowns survive cache round-trips, including forced scans. HTTP 429 and GitHub rate-limit 403 respect a bounded retry window of up to one hour.
- Generic 403 is access-denied-or-challenged, not proof that an account is required; 404 does not establish deletion.
- Detailed row metadata is reduced when the response budget is approached. The CLI rejects results beyond 30 MiB and requests beyond 32 MiB.

## Evidence

`tests/source_adapters.rs` uses an injected transport and clock to exercise production normalization/cache logic without depending on live providers. It covers foreign/duplicate identities, source joins, prerelease/future rejection, game/loader/community mismatch, provenance roles, checksums, conditional cache, failure invalidation, rate limits and response bounds.

`tests/modhold_source.rs` and `tests/test_modhold_source_adapter.py` cover exact host/game/ID matching, same-title collisions, per-row author pins on deduplicated requests, main-versus-optional version precedence, game/channel review, metadata-only capabilities, source propagation from migrated Nexus mods, last-good cache retention on network/rate-limit failures, and conditional revalidation. A 403/404/503 cannot become a ban, deletion or current-version finding. The ModHold response schema was inspected live on 2026-11-16 for IDs 11 (Estates of Skyrim), 4 (Word Travels) and 6 (Warband); fixtures retain only the contract's field shapes, not third-party mod descriptions or credentials.

`native/scripts/verify_source_adapters_live.py` is an explicit opt-in public API smoke through the compiled CLI. It saves scalar identity/status/count/timing/hash evidence without publishing third-party changelogs or downloading archives. Network failures remain failures to establish release metadata; this is not a deterministic release gate or proof of native MO2/game behavior.

## Primary implementation references

- [GitHub release API](https://docs.github.com/en/rest/releases/releases), including release identity, draft/prerelease fields and asset digests.
- [GitLab release API](https://docs.gitlab.com/api/releases/) and [release asset redirect semantics](https://docs.gitlab.com/user/project/releases/release_fields/).
- [Modrinth project API](https://docs.modrinth.com/api/operations/getproject/) and [project versions API](https://docs.modrinth.com/api/operations/getprojectversions/).
- [Official Thunderstore serializers](https://github.com/thunderstore-io/Thunderstore/blob/master/django/thunderstore/repository/api/experimental/serializers/main.py) and [official CLI endpoints](https://github.com/thunderstore-io/thunderstore-cli/blob/master/ThunderstoreCLI/API/ApiHelper.cs).
- [ModHold API documentation](https://modholdmods.com/developers) and the [observed public mod metadata endpoint](https://modholdmods.com/api/v1/mods/11).
- Existing [Engineering-Atlas adapter lab](https://github.com/Herbertofury/Engineering-Atlas/tree/main/apps/atlas-lab/src): bounded JSON reading and strict Modrinth version identity informed this implementation.
