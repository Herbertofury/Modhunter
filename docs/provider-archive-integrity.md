# Provider-published archive integrity

The public-source install queue now compares downloaded archive bytes with the
exact reviewed provider file's published size and supported digests before any
content backup or native installer starts. Expected values are re-resolved from
server-owned source records using the queued asset identity and review proof;
browser-supplied hashes cannot authorize installation.

The existing `Modhunter-reviewed-install-queue` worker computes SHA-256 plus any
published SHA-512/SHA-1 in one bounded read, checking cancellation and file change.
Every published supported digest must match. Size is checked when provided.
Providers without a published digest retain local byte-consistency checks, but
this is not described as a provider-digest match. A digest match is not a malware,
compatibility, creator-identity or license guarantee.

The existing recovery workflow rehashes before handoff, preserves the exact
mod-content backup, and checks native profile, game, source evidence, lease and
cancellation before opening MO2. The handoff stamp is captured before that final
hash, not after intervening backup verification, so a changed file cannot acquire
a new accepted stamp. On Windows, an additional worker-owned read handle denies write/delete sharing
from before verification through the actual native installer result. A conflicting
writer fails closed. Other platforms retain metadata/rehash checks only. The
Windows lease blocks ordinary OS file writes/replacements; it is not a claim of
protection against kernel-level attackers, all memory-mapped-writer scenarios or
hostile path-ancestor replacement.

Public-source installation fails closed if the native content worker is
unavailable; it does not fall back to unverified GUI-thread installation. Nexus
continues its supported native authorization path. Interrupted journal entries
remain interrupted and do not replay automatically. No new token, browser-cookie,
network-authentication or download-bypass mechanism is introduced.

## Evidence and remaining gates

The tests use real local ZIP bytes and the existing asynchronous queue with a
fixture MO2 organizer/content helper. They cover SHA-512 and SHA-256 success,
size/digest failure, all-hashes-must-match, direct verifier rejection of malformed
algorithms, absent provider
digests, off-GUI execution, mutation during hashing and between verification and
handoff (including restored mtime when the filesystem exposes a changing ctime), cancellation, context invalidation and interrupted-journal restart.
Unmodified main is independently shown to invoke the fixture installer for a
wrong published hash/size and a post-hash mutated archive.

Real provider CDN retrieval, Windows MO2 2.5.3b12, user Skyrim SE profiles and
actual installer/game acceptance remain separate gates. Do not infer these from
local fixtures or CI success. No user archive was installed by these tests.

Official Modrinth version/file schema: https://docs.modrinth.com/api/operations/getversion/
provides version/project IDs, file URL/name/size and SHA-512/SHA-1 metadata. The
existing GitHub adapter's published SHA-256 and other supported provider records
reuse this verification contract without inventing missing checksums.

Normalization retains only supported valid published digests; unknown or malformed
upstream digest fields are not passed to this worker. The direct verifier itself
rejects unsupported/malformed expectations. These are distinct contracts. The
handoff stamp also includes device and ctime, but ctime semantics vary by platform;
this does not provide immutable-handle protection on non-Windows platforms.
Windows `CreateFileW` sharing semantics are tested separately in the Windows CI
job. Until that exact-head job passes, Windows lease behavior remains unqualified.
The lease does not modify filesystem permissions, install software or persist an
access grant. It is released after the native workflow returns, including errors.

Windows sharing contract (official Microsoft documentation):
https://learn.microsoft.com/en-us/windows/desktop/api/fileapi/nf-fileapi-createfilew
and https://learn.microsoft.com/en-us/windows/desktop/FileIO/creating-and-opening-files .
