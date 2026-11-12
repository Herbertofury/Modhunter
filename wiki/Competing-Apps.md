# Competing apps and differentiation
Source review: **2026-11-12**.

Modhunter's maintenance game and verified-update rewards sit alongside a real mod-maintenance workflow. These comparisons concern trustworthy maintenance; they do not imply other tools offer or need its game modes.

| Comparable product | Official reference | What to compare |
| --- | --- | --- |
| Mod Organizer 2 | [Project](https://github.com/ModOrganizer2/modorganizer) | Profile/installation workflows and the exact native handoff. MO2 is also an integration dependency, not a feature to remove. |
| Vortex | [Nexus Mods project](https://github.com/Nexus-Mods/Vortex) | Discovery, update review and install recovery across supported games. |
| Wabbajack | [Project documentation](https://github.com/wabbajack-tools/wiki) | Repeatable modlist installation and actionable failures; distinguish whole-list provisioning from individual-mod maintenance. |

## Differentiation hypotheses to test
- A source-aware update workshop should reduce wrong-file choices without concealing ambiguous releases or compatibility warnings.
- Verified maintenance rewards must correspond to completed real updates, never checklist clicks or game victories.
- Source links, unresolved identities and stale/unknown bug data should remain visible rather than becoming misleading green success states.

## Comparison backlog
- [ ] Replay the same valid, ambiguous, missing and failed release cases through Modhunter and the applicable baseline.
- [ ] Record time to review/install, number of manual steps and recovery success, including FOMOD choices.
- [ ] Verify profile identity, cancellation, reconnect/restart and retained load order; a text-file backup is not a backup of old mod contents.
- [ ] Test large-profile UI responsiveness and memory while preserving every mod.
- [ ] Link findings to [Update Workshop](Update-Workshop.md), [source coverage](Updater-Source-Coverage.md) and [testing](Development-and-Testing.md), avoiding duplicate feature specifications.

## Evidence and refresh rules
Review official release notes, documentation and issue trackers daily when the scheduled review runs. Record the source URL, check date, version/commit, relevant change, user impact and next experiment; retain last-good information and mark inaccessible sources. No change is a valid result. A scheduled review is not continuous monitoring.

This is a source-backed research shortlist, **not a head-to-head benchmark or a claim of feature/performance superiority**. Before recommending adoption, compare the current application and candidate on the same real workloads, machine, versions and correctness requirements. Retain raw timings, p50/p95 latency, CPU/memory, failures and rollback evidence. Check licensing and privacy before reuse; an open repository does not grant unrestricted reuse. New features remain proposals until approved, implemented and accepted.
