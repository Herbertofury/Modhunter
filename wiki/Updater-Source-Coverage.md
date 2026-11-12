# Modhunter Source Intelligence

Modhunter checks exact projects, retains every installed mod in the profile, and explains what each provider can establish. Open a mod's inspector for release details; open **Source coverage for this profile** in the Workshop for the provider breakdown.

## Provider support

| Provider | Release checks | Exact file workflow | Context and limits |
| --- | --- | --- | --- |
| **Nexus Mods** | MO2's authenticated native Nexus integration | Review actual Nexus file IDs; MO2 handles downloads and its interactive installer | Account permissions apply. Project versions do not identify the correct runtime or FOMOD choices. |
| **GitHub** | Public stable release API, exact owner/repository | Review published ZIP/7z/RAR assets, with release and asset IDs; queue through MO2 | Private repositories, missing releases, prereleases and non-archive files need source-page review. Published SHA-256 is displayed when supplied. |
| **GitLab** | Public releases for the exact project, including nested groups | Review release archive links bound to that project and tag; queue vetted links through MO2 | The original link target is checked as well as any provider download alias. Arbitrary external links do not become trusted assets. |
| **Modrinth** | Project and release API with explicit game-version and loader context | Inspect release files, published hashes, declared dependencies and release notes; vetted archive formats can use MO2 | Wrong game, absent context and loader mismatch require review. JAR and MRPACK files remain metadata/source-page workflows; this tool does not become a Minecraft installer. |
| **Thunderstore** | Exact namespace/package and community API context | Inspect package releases, dependencies and ZIP archives; vetted files can use MO2 | Community/game mismatch and deprecated packages require review. Moderation status is displayed separately; an active unreviewed listing can supply release facts without being represented as approved. Provider declarations do not prove game compatibility or an installed dependency graph. |
| **CurseForge** | Capability shown as **Provider API key required** | Open the original project | This build does not configure a CurseForge API session. It does not claim automatic checks or bypass the provider's key requirements. |
| **Patreon, LoversLab, Mod DB, Modding Guild, Dwemer Mods** | Exact source retained; **Source-page review** | Open the original release using the user's browser and ordinary provider workflow | A source link does not establish authentication or a newer downloadable release. |
| **Ko-fi, Boosty, Afdian, SubscribeStar and other creator sites** | Source-page review | Open the original project or post | Post dates and creator names are not treated as installed-mod versions. |
| **Bethesda Creations and Steam Workshop** | Provider-managed workflow | Open the official client or original item | Entitlements, subscriptions and delivery remain with the provider. |
| **Discord, social links and arbitrary websites** | Creator/community link retained | Follow the original release link | No title-based project guesses or invented update verdicts. |
| **Unlinked mods** | Unknown until an exact project is linked | Add the source in the inspector, then check again | Unlinked records stay visible. |

The Rust helper performs the four public API integrations. MO2's small in-process Python adapter owns its native Qt/account/download/installer integration. Standalone HTML and an offline desktop import can inspect their existing information; live checking and queueing use the connected plugin.

## Information on each mod

Source chips show the provider, freshness or cooldown state and declared dependency count. The inspector adds:

- Installed and published versions, release identity, source-check time and release-publication time.
- Adapter capabilities and an actionable explanation when a check is manual, unavailable, stale or missing context.
- Project summary and release notes as inert text. Provider markup cannot run in the dashboard.
- Separate **Author** and **Release publisher** fields when the API supplies them. A publisher, package namespace or uploader is not inferred to be the original author.
- Provider-declared dependencies, version constraints when supplied, and game/loader context. These declarations are separate from the actual local plugin-master audit.
- Exact release files, file sizes, provider-primary markers and published hashes when available.
- Original project, release, avatar and media links. Remote images are linked rather than loaded automatically.

The **Mod health** menu includes source refresh, manual/account review, declared dependencies and game/loader mismatch filters. `source:gitlab` and `provider:modrinth` search the provider. CSV exports add release, freshness, capability and count columns; a live JSON plan export requests the complete source evidence explicitly.

Large release notes, file lists and media stay outside the normal catalog and background update-plan responses. Opening an inspector or file picker loads the requested row once, with profile checks and overlapping-read coalescing. Late responses from an old profile cannot replace current details or erase unsaved inspector notes.

## Source identity and version decisions

Modhunter obtains source identity from MO2's URL, limited `meta.ini` metadata, notes or the explicit original URL saved in the inspector. A real native Nexus ID can supply its game-specific project link when no other verified source is present. A verified current non-Nexus source owns its file review even if old metadata still contains a Nexus ID.

Project URLs use HTTPS and reject credential-bearing identities. A changed source invalidates the previous verdict. Dependency links, search results and similar titles do not establish the installed mod's identity.

| State | Meaning |
| --- | --- |
| **Candidate** | A fresh observed release version compares newer than the installed version. The exact file still needs the correct game/runtime, edition and installation choices. |
| **Current / newer installed** | Fresh release evidence permits that version comparison. It is not inferred from a failed request or an empty page. |
| **Review** | Versions conflict or cannot be compared, compatibility context is missing, or provider metadata requires investigation. |
| **Manual / unverified** | The provider needs its original page, a key, authentication or an exact source identity. |
| **Refresh needed** | Prior evidence expired or a failed refresh made it stale. It cannot authorize a fresh source download. |
| **Provider cooldown** | The provider refused or deferred requests. The retry time is retained; forced scans respect that cooldown. |
| **Partial check** | Some projects succeeded while others could not establish release evidence. Successful observations remain useful and failed ones remain explicit. |

## Review, queue and install

1. Open **Tools → Update Arsenal - Quest Board** in MO2, or connect with **Find MO2** in the Rust app. Use MO2's own Nexus login when checking Nexus projects.
2. Choose **Check all mod sources**. Inspect the Workshop's candidates, review and manual lanes, and expand source coverage to identify missing capabilities.
3. Open **Choose file** or the inspector's **Review release files**. Compare the release notes, dependencies, game/runtime and exact archive. A uniquely eligible file can also use **Update now**.
4. Select an archive. The browser submits its stable identity and a review fingerprint; it does not choose a download URL. The server rechecks provider, project, release, filename, URL, size and published hashes against its owned record.
5. Queue selected files. That association is checked again at native dispatch. Newly observed file changes under the same release/asset ID invalidate the old review. Native MO2 handles the download.
6. Open the completed file with MO2. Install preflight repeats the profile, installed/target-version and reviewed-file checks. The normal MO2 installer owns FOMOD and overwrite choices. A per-mod **Update now** request may open that file's installer when its download completes; batch queueing remains download-only.
7. Check activation, conflicts, plugin masters and patches after installation. Refresh the profile to establish whether the installed version advanced. ESLifier provenance is rechecked separately when relevant files change.

Published digests in release metadata are labeled as such. Their presence is not a claim that downloaded archive bytes have been independently verified by Modhunter. A profile backup preserves load-order **text files**, not a prior copy of every mod or save.

## Responsiveness and bounded work

Public checks deduplicate shared projects and use four workers by default, capped at eight. Requests have connection and total deadlines; a normal scan has a 45-second budget. The cache retains ETag/Last-Modified evidence, supports 304 revalidation, expires ordinary fresh entries after 15 minutes, and observes provider cooldowns. Failed refreshes do not silently keep an old verdict current.

API responses, retained cache data and detailed output have explicit size limits. A single helper request accepts up to 25,000 rows and 2,048 distinct public projects. Limits return an explicit result/error instead of dropping installed rows from the dashboard. Provider availability, scan budgets and rate limits still govern network speed.

No provider credentials or account cookies are read by the public adapters. API targets are fixed HTTPS provider hosts. Metadata redirects are blocked except for one validated GitLab latest-release redirect whose final project and tag are checked; its cache validators stay bound to that resolved endpoint. Archive links must match the established source. Scans stop on profile/runtime/loader changes and plugin shutdown.

## Verification

Rust parser/cache/identity tests, Python request and queue tests, actual Python-to-Rust protocol checks and Chromium interaction tests cover this workflow. The browser fixture specifically checks lazy source details, retained notes, escaped release notes, exact archive identity, an obsolete Nexus ID on a GitLab mod, and mobile layout. The Windows workflow separately exercises the actual app and an official portable MO2 fixture.

See [Build and test results](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) for the tested commit and its receipts. Public live API observations record exactly which providers responded; a synthetic fixture or a metadata read does not prove account-specific downloads, FOMOD choices or stability of a particular game setup.

**Continue:** [Update Workshop](Update-Workshop.md) · [Mod Intelligence](Mod-Intelligence.md) · [Rust Desktop](Rust-Desktop.md) · [MO2 Plugin](MO2-Plugin.md).
