# Failure Intelligence Standard

Failures are state transitions, not excuses and not deliverables.

## State model
Use explicit states:
- <code>verified</code>
- <code>unverified</code>
- <code>unresolved-active</code>
- <code>blocked-user-action</code>
- <code>superseded</code>

Do not translate “first search missed it” into “does not exist.”

## Blocker escalation
When a required path fails:
1. capture the decisive failure signature;
2. preserve partial valid work;
3. classify the failure family;
4. change route, parameters, environment, implementation, or authorization state;
5. retry only when new evidence or a state transition exists;
6. continue independent acceptance work when possible.

Never loop an unchanged failure.

## Reusable incident knowledge
A nontrivial verified recovery should capture signature, environment/version, root cause, failed routes, successful route, verification evidence, invalidation conditions, and regression protection.

## Completeness
For exhaustive external data, reconcile expected, discovered, accepted, rejected, and unresolved counts and prove terminal pagination/coverage before claiming completeness.

## Anti-stall rule
Two consecutive execution waves that add no evidence, reduce no uncertainty, advance no dependency, mutate no required deliverable, and satisfy no acceptance item force a strategy change.
