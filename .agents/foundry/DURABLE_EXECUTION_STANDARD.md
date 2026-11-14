# Durable Execution Standard

Long-running agent work must survive process crashes, chat/session boundaries, provider failures, human waits, and retriable infrastructure faults without replaying unsafe side effects or losing accepted state.

## Durable task contract

A durable task has:
- stable task identity;
- accepted requirements and protected invariants;
- current lifecycle state;
- durable checkpoint before the handle is treated as resumable;
- idempotency/replay strategy;
- cancellation semantics;
- human-input state when applicable;
- exact next action;
- terminal result or terminal error evidence.

## State model

Recommended states:

<code>queued</code> → <code>working</code> → <code>input_required</code> / <code>waiting_external</code> → <code>working</code> → <code>succeeded</code> / <code>failed_unresolved</code> / <code>cancelled</code>

A provider-specific state may be preserved alongside this normalized state.

## Rules

1. **Persist before advertising durability.** Do not return a resumable task/checkpoint handle until the state needed to resume is actually durable.
2. **Side effects need idempotency.** Replaying a workflow must not duplicate sends, purchases, releases, destructive writes, or irreversible external actions.
3. **Separate orchestration retry from domain retry.** Infrastructure retries must not blindly repeat a domain mutation.
4. **Cancellation is first-class.** Cancellation requests are cooperative but observable; preserve whether the underlying operation actually stopped.
5. **Human input is state, not failure.** An approval/question moves the task to <code>input_required</code>; it does not erase prior execution.
6. **Polling is bounded.** Honor provider/recommended intervals and avoid busy-waiting. After unchanged observations, advance independent work.
7. **Resume from the last verified boundary.** Never restart the whole job merely because the runtime or chat changed.
8. **Schema/version state is durable.** Store enough version/protocol/tool identity to reject or migrate incompatible resumptions.
9. **Terminal failure stays truthful.** A durable failed task is still unresolved work unless acceptance explicitly permits failure as the requested outcome.

## Remote durability and documentation parity

Conversation continuity is not remote durability. Treat chat history, local scratch state, sandboxes, and convenience libraries as caches around a durable checkpoint, not as the checkpoint itself.

For substantive project work, a verified checkpoint should carry the applicable remote tuple:
- canonical source remote identity: repository, branch/ref, and exact commit;
- connected Google Drive identity for material artifacts and project/checkpoint exports, including file/folder ID and readback evidence when available;
- live GitHub Wiki publication receipt when the repository uses a Wiki, tied back to the canonical `wiki/` source lineage.

Persist at coherent milestones, before handoff/compaction/interruption-prone long gates, and before closeout. Do not spam remotes after every micro-edit.

A repo-side `wiki/` directory is source, not publication. If a required GitHub Wiki is absent, create/bootstrap it through an authorized supported route before documentation work is accepted. If any required remote is stale, missing, or unverified, preserve the exact pending sync operation and keep the checkpoint `unresolved-active`.

## Protocol composition

- MCP Tasks can carry a durable tool-call lifecycle when supported.
- A2A can carry collaborative task state across independent agents.
- Temporal/LangGraph-style durable execution are implementation options, not Foundry requirements.
- Project Brain checkpoints remain the cross-tool continuity source when no runtime supplies native durability.

## Acceptance tests

A durable workflow should challenge:
- process restart mid-task;
- network timeout after side effect but before acknowledgement;
- duplicate delivery/retry;
- human pause then resume;
- cancel during work;
- schema/version mismatch on resume;
- provider outage and later recovery.
