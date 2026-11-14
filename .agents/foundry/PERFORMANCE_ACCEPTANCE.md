# Performance Acceptance

Agent Foundry treats performance, quality, completeness, and user experience as simultaneous requirements.

## Always-on free-speed pass

Follow [product invariant 14](PRODUCT_INVARIANTS.md#14-continuous-zero-loss-performance-ratchet) throughout ordinary feature, fix, refactor and integration work, not only at closeout. Always strive to improve runtime speed and cold/warm startup, initialization and time-to-ready alongside the primary task on affected paths. Preserve every capability and the complete workload: artificial caps, feature loss, weaker quality, regressions and shifting startup cost into first use are not wins. Record measured before/after evidence and regression checks, or a specific no-safe-opportunity / measurement-blocked outcome; never claim an unmeasured speedup.
Performance is an always-on engineering objective for substantive work that can affect runtime cost or responsiveness. Before closeout, inspect the touched or causally related path for plausible **no-loss** improvements: eliminate unnecessary work, improve algorithms/data structures, batch work, reduce round trips and allocations, use incremental state, correct caching/indexing, safe concurrency, async I/O, better scheduling, native/runtime fast paths, and hardware acceleration where useful.

This pass is bounded and evidence-driven. Do not wander into unrelated micro-optimization when there is no plausible causal opportunity. But when a verified no-loss win is available in the affected path, leaving the slower implementation in place is incomplete engineering.

## Continuous performance ratchet
A verified better equivalent-work implementation becomes the new baseline. Later changes must preserve or improve that baseline unless the user explicitly accepts a documented tradeoff.

For an explicitly requested performance task, a candidate passes only when:
1. equivalent workload and result identity are proven;
2. at least one requested metric, causal hot path, or representative scenario materially improves; and
3. no relevant protected behavior or performance/resource dimension materially regresses beyond measurement noise or an explicitly accepted budget.

For substantive non-performance changes that touch a hot path, a measured speedup is not mandatory when no safe opportunity exists, but the bounded free-speed pass and no-material-regression rule still apply.

## Multi-dimensional zero-loss gate
Protect every relevant dimension, not only the metric being optimized. Depending on the workload this can include:
- correctness, complete result count, feature coverage, fidelity, compatibility, determinism, data safety, and QoL;
- p50/p95/p99 latency;
- first-useful-result and full-completion latency;
- FPS/frame-time and TPS/tick-time distributions;
- throughput and scalability;
- cold and warm startup/load time;
- memory footprint, allocations, and GC pressure;
- CPU and GPU utilization/time;
- disk I/O;
- network request count and bytes transferred;
- power/thermal behavior when relevant.

A candidate that improves one dimension by materially worsening another protected dimension is a tradeoff, not a zero-loss optimization.

## Root-cause requirement
Repair and optimization must address the actual causal bottleneck. A workaround that makes the symptom disappear by moving cost into end-to-end latency, throughput, memory/CPU/GPU/network use, startup, responsiveness, blocking, repeated work, or future maintenance is containment—not a completed fix. If containment is temporarily necessary, label it unresolved and keep the causal repair active.

## Invalid shortcuts
The following do not count as optimization:
- rendering or processing fewer required things;
- silently reducing search/data breadth;
- dropping compatibility paths;
- lowering quality/fidelity;
- skipping validation or runtime proof;
- caching stale or wrong results;
- disabling expensive required features rather than engineering them efficiently;
- adding sleeps, delays, polling, retries, or timeout inflation to hide races/state bugs;
- forcing serialization or blocking solely to avoid fixing concurrency/ownership defects;
- duplicating scans, requests, transforms, or validation to paper over bad state flow;
- moving work to a background thread/process while total completion time, resources, responsiveness, or correctness regress;
- optimizing only a sub-step while the end-to-end workflow becomes slower;
- accepting a new dependency/runtime/version solely because its headline benchmark is faster when the real representative workload regresses.

## Measurement and equivalence
Prefer representative before/after evidence on the same workload and result identity:
- multiple runs after warm-up where practical;
- distributions/percentiles rather than one lucky timing for noisy paths;
- cold and warm measurements when both matter;
- end-to-end and causal hot-path measurements;
- CPU/GPU/memory/I/O/network evidence when a speedup could merely shift cost.

Measurement noise may justify a small explicit tolerance, but it must not become a hidden regression budget.

## Ratchet rule
Once a candidate wins the zero-loss gate, promote it as the reusable baseline and preserve the benchmark/regression evidence when practical. Future work compares against the strongest verified baseline, not an older slower one.

A preservation-only result with no measured gain is not completion for an explicitly requested performance task. A faster result produced by doing less, lowering quality, moving the cost elsewhere, or materially regressing another protected dimension is also not complete.
