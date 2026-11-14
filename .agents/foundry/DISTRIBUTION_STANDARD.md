# Skill Distribution & Synchronization Standard

Agent Foundry should distribute Skills without locking users to one agent harness.

## Compatibility baseline

Primary package compatibility target:
- the open Agent Skills <code>SKILL.md</code> specification;
- Git repository as canonical source;
- deterministic/pinned release artifact for durable versions.

### Agent Skills compatibility

A publishable Skill should preserve the open specification's required <code>SKILL.md</code> frontmatter and directory conventions rather than inventing a Foundry-only package shape. Keep the main Skill entrypoint concise and progressively disclose longer references/assets/scripts.

Version-specific validation should use a current compatible validator (for example <code>skills-ref validate</code>) in addition to Foundry-specific acceptance checks.

## Instruction compatibility

Repository instructions should remain compatible with the open <code>AGENTS.md</code> convention:
- root instructions apply broadly;
- nested <code>AGENTS.md</code> files may scope behavior to subtrees;
- the nearest applicable file takes precedence for local instructions;
- user/developer authority remains above repository instruction files.

Foundry invariants should be referenced by scoped adapters rather than copy-pasted into every nested file.

## Discovery/install adapters

Support or interoperate with:
- the open <code>skills</code> CLI / skills.sh ecosystem for discovery and installation;
- direct Git/GitHub/GitLab/Forgejo sources;
- local folders for development;
- agent-native directories through thin adapters.

Do not make popularity telemetry a trust score.

## Desired-state synchronization

For multi-agent installations, prefer a desired-state model:
1. declare canonical Skill/instruction sources;
2. calculate a read-only plan;
3. reconcile into each agent's native location;
4. expose status/drift;
5. never clobber unmanaged user files silently.

Botfile's plan/sync/status + symlink-fan-out approach is a strong challenger pattern for this layer. Foundry can adopt the model without requiring Botfile as the only implementation.

## Locking

A lock/receipt should record:
- source URL;
- source commit/tag;
- Skill path/name;
- content digest;
- installed agents/scopes;
- install strategy (symlink/copy/native);
- security review/provenance status.

## Updates

Updates are explicit state transitions:
- show what changed;
- rerun validation/security checks when trust-relevant content changes;
- preserve prior known-good version for rollback;
- do not silently float a production Skill to arbitrary HEAD.

## Packs/collections

Collections are useful for Foundry presets, but a pack is not a security boundary. Private/unlisted links and public installability must be treated according to the provider's actual access semantics.

## Future OCI adapter

OCI artifact distribution is promising for immutable digests, registries, signatures, and enterprise mirroring, but Foundry treats current Agent Skills OCI conventions as an evolving compatibility target rather than a settled official standard.

See [versioned repository adoption](docs/REPOSITORY-GOVERNANCE.md) for the implemented plan/apply/status tool and local/shared boundary.
