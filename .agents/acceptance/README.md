# Scoped Athena shared guidance transition

Source: Athena `11e212bb88abd7e010c8974f35ec629032a2a843`, guidance 2.0.1. Consumer baseline: Modhunter `1a99a66d346b1593273afc7c2b12e2d493c8d5e0`. Reviewed input digest: `b73618f05109c2423195f6ae520d5f28b01a4c7632672cd9b0ba98a73875ddcd`.

The reviewed root router changes only `.agents/foundry/AGENTS-adapter.md` to `.agents/athena/AGENTS-adapter.md`. All project-owned MO2/auth/profile/cancellation instructions, local lifecycle skill, CLAUDE import and legacy core/lock remain intact. Sixteen canonical Athena snapshots and an exact source lock are added; no skill installed, provider connected or application code changed.

`python -m unittest discover -s .agents/acceptance -v` checks the exact router, complete pins and 419 original non-router files. Preservation hashes describe the original checkout bytes under the unchanged repository `.gitattributes`. Four `.bat` files intentionally use CRLF on all platforms; an initial baseline comparison against raw Git LF blobs failed on Install_to_MO2.bat. The corrected receipt derives those four expected checkout bytes from their existing explicit `eol=crlf` rule, verifies every file and does not normalize test inputs or weaken hash equality. No file was changed to satisfy it.

Dedicated CI tests on Windows/Linux do not trigger native-rust release, source-import or wiki-sync paths. The existing source-checkpoint workflow may retain the new commit after main merge; its behavior is unchanged. No workflow is manually dispatched. This verifies repository routing and bytes, not coding-agent behavioral superiority, actual MO2 execution, game/UI functionality or installed-client propagation.

The explicit transition helper retains its generic legacy refusal and exception rollback tests; original legacy data remains for normal Git recovery. Ongoing coexistence updates, live harness acceptance, skills/history/releases and Agent-Foundry retirement remain open. Do not remove the legacy lock to bypass the generic tool's safety guard.

## Ongoing acceptance after reviewed product changes

CI now runs the unchanged original suite on the exact adoption commit
`662f2507ad1816efc0da1d8ac3acb66c8e55797f`. That suite still checks the same
419 source bytes, original router and pinned policy. In a separate current-source
check, the live router keeps the original owner instructions in order, retains
the Athena snapshots, host binding and ModHunter lifecycle skill, and points to
the current owner-required policy and interface-acceptance links. Product and
wiki changes since the original adoption are not reverted to make historical
checks pass. Both historical and live checks must pass.
