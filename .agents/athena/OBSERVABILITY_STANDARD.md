# Agent Observability Standard

Observability exists to reconstruct **what the agent did, why the system allowed it, what changed, and where failure occurred** without leaking unnecessary private data.

## Vendor-neutral trace model

Prefer OpenTelemetry-compatible traces/events so telemetry can flow to Phoenix, Langfuse, other OTel collectors, or local tooling without making one vendor canonical.

Capture, when available:
- task/session/run identity;
- model/harness identity;
- tool/MCP/A2A/ACP calls;
- handoffs/subagents;
- guardrail/policy decisions;
- checkpoints and state transitions;
- retries and failure family;
- latency/token/cost metrics;
- artifact/checksum identities;
- final verification state.

## Span vs event

Use spans for duration-bearing operations. Use events for point-in-time state changes, decisions, checkpoints, or outcomes that belong to a surrounding operation.

## Privacy and secret handling

1. Redact secrets, credentials, auth headers, private keys, session tokens, and user-sensitive values by default.
2. Prefer references/hashes for large or sensitive artifacts over duplicating raw payloads.
3. Make high-fidelity payload capture opt-in and bounded.
4. Record redaction policy/version alongside traces where practical.
5. Observability storage is not a substitute for canonical project state.

## Causal linking

A completion/recovery record should be able to link:
<code>user goal → task/run → tool/action → policy decision → artifact/state mutation → verification → checkpoint</code>.

## Evaluation loop

Observability should feed:
- incident diagnosis;
- performance profiling;
- trajectory evaluation;
- regression fixtures;
- challenger comparisons.

Do not optimize solely for prettier dashboards. Instrumentation is valuable when it shortens diagnosis, strengthens proof, or prevents repeated failure.
