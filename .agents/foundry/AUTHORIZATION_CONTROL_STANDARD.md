# Authorization & Runtime Control Standard

Prompt instructions are not sufficient authorization boundaries for high-impact agent actions.

## Decision/enforcement separation

Prefer an architecture where:
- the agent proposes an action;
- a policy decision point evaluates principal, action, resource, context, delegation, and risk;
- the enforcement point blocks/modifies/asks/permits the action;
- the decision is traceable.

OPA/Rego, Cedar, ACS Guardians, provider policy engines, or project-native engines can implement this pattern. Agent Foundry does not require one engine.

## Default behavior

For sensitive actions:
- default deny when no applicable authorization exists;
- use least privilege;
- scope credentials/tokens narrowly;
- distinguish read, write, destructive, financial, external-communication, and privilege-changing actions;
- preserve user-delegated authority boundaries;
- require fresh approval when risk materially changes.

## Normalized decision

Use one of:
- <code>allow</code>
- <code>deny</code>
- <code>modify</code>
- <code>ask</code>
- <code>defer</code>

Provider-specific decisions can be retained alongside the normalized value.

## Context inputs

A high-impact decision may include:
- acting principal/agent identity;
- user/delegation identity;
- requested action;
- exact resource;
- environment/project;
- data sensitivity;
- network destination;
- tool provenance;
- artifact trust state;
- prior approvals;
- runtime risk level.

## Runtime hooks

Where supported, policy should intercept sensitive tool/action boundaries rather than relying on the agent to remember a text instruction.

OWASP ACS is a preferred interoperability target for portable hooks/Guardian decisions, but Foundry policy remains valid when a runtime lacks ACS.

## Auditability

Record enough context to explain:
- what was proposed;
- which rule/profile applied;
- what decision was returned;
- whether arguments were modified;
- what actually executed.

Do not log secrets merely to make the audit record complete.
