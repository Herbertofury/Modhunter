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


## Foundations, reuse and project interoperation
Added **2026-11-12**. These are scoped research directions, not completed integrations.

| Foundation / reuse mode | Concrete reuse target | What to improve and verify |
| --- | --- | --- |
| [MO2 uibase](https://github.com/ModOrganizer2/modorganizer-uibase) and [Python bridge](https://github.com/ModOrganizer2/modorganizer-plugin_python), supported adapter contracts | Existing download/install/profile APIs; preserve MO2's installer and FOMOD interaction | Fewer uncertain handoffs, exact target/profile identity, real completion events and safe retry |
| Existing Modhunter Rust profile/cache engine and Update Workshop | Extract stable internal contracts before adding replacement engines | Separate offline observations from authenticated native actions; stale evidence must never become success |
| [Wabbajack](https://github.com/wabbajack-tools/wabbajack), workflow/reference oracle | Reproducible list manifests and actionable installation failures | Test resumable maintenance without confusing whole-list provisioning with individual updates |

Before copying or linking upstream code, pin the component revision, inspect its actual license and dependencies, retain required notices, and prove compatibility with the targeted MO2/Python/Qt/runtime versions. Referencing an API does not grant access to a user's account.

**Shared-component candidates:** a versioned mod/source identity, observed bug status, update transaction and evidence schema could let related mod-manager clients consume consistent results. Do not introduce a second authoritative installer or silently synchronize profiles. [Hover](https://github.com/Herbertofury/Hover-App) could consume read-only archive/asset metadata; [Ferrum](https://github.com/Herbertofury/Ferrum-Browser) could replay suitable desktop/web verification lanes. These are proposals, not existing cross-app guarantees.

- [ ] Inventory current implementations and owners; propose a narrow schema with migration fixtures.
- [ ] Test old/new clients, conflicting profile state, offline data, cancellation and idempotent retry.
- [ ] Benchmark current workflow versus proposed reuse on the same real profile without reducing review fidelity.


## WhimWatch: user-requested page-tracking reference

Added **2026-11-13** at the owner's request. [WhimWatch source/README](https://github.com/forthewhimsy/whimwatch/blob/1ec5386f41a485108a67c62732aef17f982298fb/README.md), [package 0.4.2](https://github.com/forthewhimsy/whimwatch/blob/1ec5386f41a485108a67c62732aef17f982298fb/package.json), [MIT license](https://github.com/forthewhimsy/whimwatch/blob/1ec5386f41a485108a67c62732aef17f982298fb/LICENSE). This is a source-reviewed UX/architecture reference, not a proven replacement, runtime test or comparative-performance recommendation.

Useful patterns to evaluate:
- Creator-owned source links with add/remove/restore and per-site/per-creator opt-outs.
- Supported links are specific wicked.cc pages, LoversLab file pages and Patreon creator pages, **not arbitrary website monitoring**.
- Keep installed-pack identity, source evidence and seen state distinct. Timestamp/title heuristics can miss releases or report page changes; do not treat them as authoritative file-version identity.
- Reuse an explicitly authorized site session within an isolated app browser, with persistent versus memory-only choice, sign-out and visible reauthentication. Cookies can expire; cookie presence is not proof of a valid login or entitlement. Browser login is not an API grant.
- Preserve last-good observations and distinguish unavailable, login-required, inaccessible, unchanged and verified-new-release states.

Do not copy upstream's account-password workaround, assume access to gated content, export browser credentials, or weaken this project's existing install/profile safeguards. MIT code reuse requires notices and dependency-license review; behavioral reference alone does not authorize a service's automation route.

### Acceptance before adoption
- [ ] Inventory existing adapters, session owners and user-added-link handling before replacing anything.
- [ ] Compare existing behavior against the reference using identical public and separately authorized account cases.
- [ ] Test link removal/opt-out, expired sessions, restart, source changes, cancellation, unchanged downloads and ambiguous releases.
- [ ] Retain exact release/file identity, failed evidence and last-good data; record latency/memory and correctness together.

Modhunter application: compare against the existing Rust source adapters and Update Workshop; retain MO2's authoritative profile/download/interactive installer path. This reference does not add an adapter or change updater behavior.
