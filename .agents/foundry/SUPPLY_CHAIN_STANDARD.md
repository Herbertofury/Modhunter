# Skill & Agent Supply-Chain Standard

Published Agent Foundry bundles should be reproducible enough to identify **exactly what source produced which artifact** and independently verify that relationship.

## Minimum release receipt

Record:
- artifact name/version;
- source repository + commit;
- deterministic or canonical packaging inputs;
- SHA-256 digest;
- exact byte size;
- build/packaging workflow identity;
- validation/eval status;
- license metadata;
- dependencies when material;
- supersedes/superseded-by relationship.

## Provenance

For GitHub-hosted public builds, prefer GitHub Artifact Attestations for cryptographically signed provenance. Attestations link the artifact to repository, workflow, commit, and triggering event and can include an SBOM.

For higher-assurance reusable build workflows, target SLSA-style provenance and isolation rather than inventing a proprietary signature format.

## Bills of materials

Use the right inventory:
- **SBOM** for software packages/dependencies;
- **AgBOM** for agent runtime composition (models, tools, Skills/plugins, connectors, policy/control components) when practical.

CycloneDX/SPDX are preferred interoperable formats when applicable.

## Dependency hygiene

For code-bearing Skills/tools:
- pin security-sensitive executable dependencies where practical;
- scan known vulnerabilities (OSV or equivalent);
- review newly introduced dependencies and licenses;
- avoid executing installer scripts solely because a package manager permits them;
- update stale pins intentionally after validation.

## Verification before install

A trusted installer should be able to verify:
- expected source/publisher;
- artifact digest;
- attestation/signature when present;
- version/compatibility;
- security scan/review status;
- requested capabilities/permissions.

## No anonymous binaries

Do not distribute binaries inside a Skill without provenance and a reason they cannot be built/obtained from a trusted canonical source.
