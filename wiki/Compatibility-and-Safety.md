# ⚠ Compatibility and Safety

The dashboard is a careful update **assistant**, not a blind auto-installer. A newer Nexus release may target Skyrim AE/NG, have stricter masters, require a new save, or be incompatible with Skyrim 1.5.97.

## The compatibility checklist

- Match the **exact installed executable/runtime** (Skyrim 1.5.97 versus AE/NG).
- Check **SKSE**, Address Library, BEES/ESL requirements and native DLL builds.
- Verify masters, FOMOD choices, required resources, and hard ESP/ESM restrictions.
- Inspect the specific Nexus file and its changelog, not just the latest project version.
- Back up the MO2 profile's modlist/plugin/load-order text files before installing.
- Keep generated outputs, local patches and unnamed private mods separate from public update candidates.

## Preservation

The app never intentionally reorders MO2 mods, replaces its list widget/delegate, or renames installed mod folders as a gamification action. The **Reveal in MO2** command targets stable identity, not row index; source information includes provider provenance when available.

## Performance

Expensive discovery happens outside UI hover/paint paths, with caching and request deduplication. There are no caps on the number of installed mods available to search. Effects are short-lived and may be reduced or disabled without affecting any game or update data.

[[MO2 Plugin]] · [[Development and Testing]]
