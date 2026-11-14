# Agent Interoperability Standard

Agent Foundry treats protocol interoperability as an architectural capability, not an afterthought.

## Separate the planes

Do not force one protocol to solve every boundary.

| Plane | Preferred compatibility target | Purpose |
|---|---|---|
| Agent ↔ tools/context | MCP | Tool/resource/prompt/context access and extensions |
| Agent ↔ agent | A2A | Capability discovery, collaboration, task exchange |
| Coding agent ↔ editor/client | ACP | Standard client/agent session and permission interaction |
| Agent ↔ application UI | AG-UI | Streaming agent/user interface events and shared UI state |
| Runtime control/guardian | ACS | Runtime policy hooks, decisions, trace and agent inventory |

The machine-readable status of these targets lives in <code>registry/protocols.json</code>. Version-specific claims must be checked there or refreshed from primary sources.

## Interoperability rules

1. **Use the narrowest protocol for the boundary.** Do not wrap an agent-to-agent workflow in a tool protocol merely because a tool client already exists.
2. **Preserve native semantics.** Adapters must not silently flatten cancellation, permissions, task state, streaming, UI state, provenance, or authorization.
3. **Capability negotiation beats guessing.** Discover supported features before using optional protocol behavior.
4. **Degrade explicitly.** When an extension/profile is unavailable, expose the fallback and the semantic loss.
5. **Keep adapters thin.** Protocol adapters translate into Foundry's canonical state model; they do not fork governance.
6. **Conformance before branding.** A claimed protocol integration should pass the relevant conformance tests or documented interoperability fixture when available.
7. **Track deprecations.** Version-sensitive adapters must record the protocol version and deprecated features they still depend on.
8. **Security boundaries remain explicit.** Protocol support does not itself grant tool, data, filesystem, network, or user-delegated authority.

## MCP profile

For current MCP implementations:
- prefer the current final specification recorded in the registry;
- model long-running work with the Tasks extension when the peer supports it;
- preserve explicit cancellation and update/input-required states;
- use protocol-native authorization behavior rather than inventing hidden credential forwarding;
- treat extensions as negotiated capabilities;
- use MCP Apps only when interactive tool UI is actually useful.

## A2A profile

Use A2A when independent agents need to discover capabilities, negotiate modalities, exchange artifacts, or collaborate on tasks without exposing internal memory/tools.

## ACP profile

Use ACP compatibility for editor/client ↔ coding-agent integrations. Keep stable support separate from experimental draft features so an experimental client does not redefine canonical behavior.

## AG-UI profile

Use AG-UI for application-facing streaming UI and shared interaction state. UI protocol events are presentation/control-plane messages, not evidence that the underlying domain operation succeeded.

## ACS profile

Treat OWASP Agent Control Standard as a **runtime-control interoperability target**. Adopt its strongest portable concepts now:
- pre/post lifecycle hooks;
- explicit Guardian decisions;
- traceability;
- agent bill of materials;
- provenance profiles.

Do not assume a young reference implementation is a complete security boundary. Foundry keeps its own default-deny and evidence requirements even when an ACS bridge is present.

## Acceptance

A new adapter must document:
- protocol + version/profile;
- capability negotiation;
- auth/identity boundary;
- cancellation/task semantics;
- failure and retry mapping;
- observability mapping;
- conformance evidence;
- unsupported semantics.
