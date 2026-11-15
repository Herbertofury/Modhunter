# Runtime & Workflow Proof

Static correctness is useful. Real execution is stronger evidence.

## Proof ladder
1. syntax / schema / formatting
2. static analysis / lint / types
3. unit tests
4. integration tests
5. build/package
6. real runtime launch
7. exact affected workflow exercise
8. persistence/restart/reload proof when stateful
9. challenge/regression pass

Use the strongest practical level available for the task.

## Rules
- Do not claim runtime success from a successful compile alone.
- Prove the tested artifact is the current artifact, not a stale build.
- Exercise the exact changed user path when available.
- Inspect relevant logs/errors rather than relying only on UI optimism.
- Stateful features should be tested through save/reload/restart when that behavior matters.
- If real runtime proof is unavailable, state the actual proof boundary rather than implying more.
