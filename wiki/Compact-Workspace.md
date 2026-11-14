# Compact workspace and launch checking (0.10.2)

The default screen is a dense installed-mod table, not a campaign dashboard. It shows priority, mod name, installed version, detected status, exact source and actions. Small filters select updates, maintenance findings, attention items or verified matching project versions. Updates, Downloads, History and Tools are separate tabs; games remain under Extras. Your existing notes, favorites, filters and manual checklist decisions are preserved.

## Launch behavior

After connecting to the real MO2 profile and loading saved preferences, the dashboard requests one automatic check for that profile/execution context. The service deduplicates requests across windows. Complete recent successful Nexus metadata is compared immediately, and expired or missing entries are fetched with at most five outstanding Nexus lookups. Public release adapters retain their own bounded queues and run independently of Nexus. Source/job/profile identities guard every partial publication. The scan can be cancelled; last completed findings remain.

Automatic checking is enabled by default and can be disabled under Tools. This is read-only inspection of mods and source metadata. It never chooses downloads, installs, changes load order, compacts plugins or overwrites game data. It writes only its own caches/progress. A first check needs network responses; it is not claimed to complete instantly for an uncached large profile or an unavailable provider. Clicking Check for updates performs a fresh explicit all-source scan.

## Status meanings

Up to date means the installed project version matches recently checked metadata, not universal file/runtime compatibility. Update available means a newer comparable project version was observed; review exact archives and requirements. Maintenance identifies observed plugin/master/conversion or explicitly recorded compatibility issues. Sign-in required, provider cooldown, check failure, unverified source, expired evidence and unsupported provider review are explicit instead of an all-purpose Inspect state. Manual checklist notes do not determine detected update status.

Nexus checks use MO2 authentication (or the optional configured API key). Creator sites without a supported release API still need Watchtower/page review and permitted access. Full plugin eligibility and conversion provenance need the actual record/hash evidence, not merely a matching name or header.

## Acceptance

The Python startup suite exercises cache freshness, source and metadata identity, request deduplication, partial publication, cancellation and profile changes. Chromium checks exercise the shipped compact renderer against deterministic API replies: automatic launch, preference hydration/opt-out, no install requests, preserved manual notes, actionable failure, cancellation, all workspace tabs and narrow layouts. Native release acceptance additionally requires the compiled Windows desktop connected to the official MO2 2.5.2 fixture, including compact layout and an actual automatic scan. These checks do not stand in for executing the user's exact beta-12 application or private account.
