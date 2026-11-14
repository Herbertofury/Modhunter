# Agent & Skill Evaluation Standard

Agent Foundry evaluates **behavioral delta in the real harness**, not just whether a prompt sounds good.

## Core design

For a material Skill or governance change, prefer:
1. a baseline without the change;
2. the candidate with the change;
3. the same representative tasks and environment;
4. repeated runs when stochastic variance matters;
5. deterministic state/result checks wherever possible;
6. explicit negative controls for triggers that should *not* activate the Skill.

## What to measure

### Capability
Did the agent complete more of the actual task, correctly?

### Reliability
Does the Skill trigger when it should and stay dormant when it should not?

### Preservation
Did the change keep required scope, quantity, quality, compatibility, fidelity, and safety behavior?

### Trajectory quality
Inspect tool calls, retries, handoffs, state mutations, recovery routes, and unnecessary work—not only the final answer.

### Efficiency
Record wall-clock time, token/model usage, tool calls, external cost, and avoidable repeated work where available.

### Recovery
Do injected failures lead to a different effective route and eventual verified recovery rather than loops or false completion?

## Real-harness rule

When the Skill is meant for Codex, Claude Code, OpenCode, or another harness, evaluate it through that harness when practical. Raw model/API tests are useful but do not prove harness trigger behavior, filesystem effects, tool routing, or nested instruction precedence.

## Statistical discipline

- Use repeated trials for stochastic tasks.
- Report per-case outcomes plus aggregate pass rate/pass@k when useful.
- Pin or record model/harness/tool versions.
- Separate deterministic failure from variance.
- Keep a holdout set for important reusable Skills.
- Record “not covered” rather than implying untested behavior passed.

## Eval fixture

Each case should capture:
- task/prompt;
- starting workspace/state;
- allowed tools/permissions;
- expected observable result;
- protected invariants;
- forbidden regressions;
- scorer/check;
- cleanup/reset method.

## Release gate

A change fails promotion when it improves one metric by violating a protected invariant. Better headline pass rate does not justify hidden feature loss, unsafe autonomy, or weaker runtime proof.

## Incident ratchet

A real significant failure should become a regression/eval case when deterministic or realistically replayable.
