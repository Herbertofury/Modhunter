# Nexus exact-file preflight, 2026-11-17

Publication base: 3231bb689bebb050e597044c4f001e006f7ab82a, refreshed from remote main. This change does not
include audit or provider-download integrity branches.

## Reproduced defects on unchanged source-adapter base 458449d
- Files [{fileId:11},{fileId:"11"}]: native dispatch [11], queued 1 despite ambiguity.
- Files [{fileId:11.9}]: native dispatch [11], queued 1 despite different identity.
- Files [null,{fileId:11}]: raised AttributeError instead of usable queue result.

## Repair and acceptance
Shared exact identity parser/index feeds the advisor, full-batch preflight and
final dispatch check. Duplicate identities fail closed; malformed unrelated
entries are ignored without suppressing valid entries. Full-list review retains
all dictionary entries. Retry with corrected evidence succeeds. A duplicate that
appears after preflight prevents native dispatch. No stale-profile protections or
per-dispatch freshness rechecks removed.

8 focused new tests pass. Publication-base full Python discovery: 497 tests, 29 skipped, OK.
Original source-adapter composition: 500 tests, 29 skipped, OK.
Six Node runtime suites: 134 passed, none skipped. git diff --check passed.
Two selected files in one project require 3 fetch_files calls instead of 4
(one shared preflight plus two retained dispatch checks), with both exact files
queued. This is a counted fixture-work reduction, not a network/MO2 speed claim.

No Rust code changed. Rust/native build, installed Windows/MO2 2.5.3b12, real
Nexus downloads, rendered UI, remote CI/publication and live Wiki parity are not
verified. The user's reported MO2 lag/error is not declared fixed.
