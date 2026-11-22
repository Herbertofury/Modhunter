# Cancel superseded queued MO2 audit captures

Baseline: main `3231bb689bebb050e597044c4f001e006f7ab82a`.

## Causal failure

The audit coordinator already sets its cancellation event when a snapshot is
invalidated. However, it previously supplied that event only to the Rust helper,
not to the preceding `plugin-audit-input` Qt request. If MO2 was busy, the worker
could remain waiting on an obsolete queued capture for its full 25-second timeout.
Until delivery/timeout, the replacement audit could not start; a late Qt event
could also walk an obsolete inventory before the epoch check discarded it.

Pass the same cancellation event through the existing `call_ui` read-cancellation
path. Add only `plugin-audit-input` to that read-only allowlist. The production
queue atomically cancels obsolete requests so `begin()` rejects late delivery.
Existing epoch checks still reject superseded results, including A -> B -> A.
No changes to install/download mutation leases, timeout ownership, profile
freshness, scan depth, or disk-analysis policy.

A synchronous native capture already executing cannot be preempted; its result is
discarded if obsolete. This fix does not remove legitimate refresh-triggered
re-analysis or prove that the user's actual MO2 lag/network errors are resolved.

## Verification

- New deterministic queued A -> B -> A regression fails on baseline: no replacement
  capture arrives within one second while Qt delivery is withheld.
- Candidate obtains the replacement within the same one-second test budget, rejects
  old delivery without executing it, and runs only the current helper.
- Additional checks cover pre-cancelled capture (never submitted) and shutdown.
- Existing epoch, cancellation, header/deep-upgrade and native operation tests remain
  part of the suite. These are Python production-code fixtures, not live MO2 tests.

Live Windows/MO2 measurements, exact installed package validation, and attribution
of the reported QSslSocket / HTTP/2 errors remain open. GoldenOreo was offline.
