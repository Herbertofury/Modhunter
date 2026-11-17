# Compact workspace and launch checking (0.13.0)

The default screen is a dense installed-mod table using the available window width and height. It shows priority, mod name, installed version, detected status, plugin/source health, Bugs evidence, exact source and actions. Notes remain visible beneath the mod name. A compact bottom search/control bar stays reachable while the complete list scrolls, and column headers stay in view. Column choices are saved on the current device. Small filters select updates, maintenance findings, attention items or current installed components. Updates, Downloads, History and Tools are separate tabs; games remain under Extras. Your existing notes, favorites, filters and manual checklist decisions are preserved.

## Launch behavior

After connecting to the real MO2 profile and loading saved preferences, the dashboard requests one automatic check for that profile/execution context. The service deduplicates requests across windows and shares complete project/file evidence between installed components on the same page. Complete recent successful Nexus evidence is compared immediately, and expired or missing entries are fetched with at most five outstanding Nexus lookups. Public release adapters retain their own bounded queues and run independently of Nexus. Source/job/profile identities guard every partial publication. The scan can be cancelled; last completed findings remain.

Automatic checking is enabled by default and can be disabled under Tools. This is read-only inspection of mods and source metadata. It never chooses downloads, installs, changes load order, compacts plugins or overwrites game data. It writes only its own caches/progress. A first check needs network responses; an uncached large profile or an unavailable provider can take longer. **Check for updates** reevaluates all sources and can reuse complete Nexus file evidence for up to four hours. **Tools → Full audit** requests fresh evidence immediately. Cached observations retain their original check time; expired or incomplete evidence cannot authorize a queued update.

## Status meanings

For Nexus, **Up to date** means the installed component has a current result from its exact file evidence. **Update available** means a replacement was identified for that installed file. A main download, optional addon and patch can share a page while following different versions and publisher-declared update histories. Missing, conflicting or ambiguous file identity requires review. A current result does not establish universal runtime compatibility; review the exact archive and requirements.

**Maintenance** identifies observed plugin/master/conversion or explicitly recorded compatibility issues. Sign-in required, provider cooldown, check failure, unverified source, expired evidence and unsupported provider review each keep their own status. Manual checklist notes do not determine detected update status. The **Bugs** column distinguishes checked displayed counts, including zero, from unknown, unavailable, restricted or stale evidence. Select a badge to inspect the source observation or refresh it.

Nexus checks use MO2 authentication (or the optional configured API key). Creator sites without a supported release API still need Watchtower/page review and permitted access. Full plugin eligibility and conversion provenance need the actual record/hash evidence, not merely a matching name or header.

## Acceptance

The Python startup suite exercises cache freshness, source and metadata identity, request deduplication, partial publication, cancellation and profile changes. Chromium checks exercise the shipped compact renderer against deterministic API replies: automatic launch, preference hydration/opt-out, no install requests, preserved manual notes, actionable failure, cancellation, all workspace tabs and narrow layouts. Native release acceptance additionally requires the compiled Windows desktop connected to the official MO2 2.5.2 fixture, including compact layout and an actual automatic scan. These checks do not stand in for executing the user's exact beta-12 application or private account.
