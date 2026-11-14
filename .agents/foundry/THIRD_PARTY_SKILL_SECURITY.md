# Third-Party Skill Security Standard

A third-party Skill is **untrusted code + untrusted instructions + untrusted dependencies** until reviewed.

A scanner result is evidence, not certification.

## Intake pipeline

1. **Quarantine.** Do not load/install into a privileged live agent before inspection.
2. **Resolve provenance.** Record repository/source, commit/tag, author/publisher, license, and requested permissions.
3. **Validate format.** Check Agent Skills structure/frontmatter and reject malformed packages from the trusted path.
4. **Static/security scan.** Use a layered scanner when available; Cisco AI Defense Skill Scanner is a strong current candidate because it combines signatures, AST/dataflow, optional semantic analysis, SARIF/CI support, and explicitly documents its limitations.
5. **Manual semantic review.** Inspect SKILL.md, scripts, downloads, binary blobs, network destinations, credential handling, shell execution, and encoded/obfuscated content.
6. **Permission manifest.** Declare filesystem, network, subprocess, connector, credential, and external side-effect needs.
7. **Cross-skill privilege analysis.** Risk can emerge from composition (for example, one Skill reads secrets while another has unrestricted egress).
8. **Sandbox evaluation.** Exercise trigger behavior and side effects in an isolated workspace when risk warrants it.
9. **Pin and attest.** Record digest/source revision; use verified provenance where available.
10. **Promote intentionally.** Only trusted versions enter the normal install/sync path.

## Never trust these alone

- marketplace popularity;
- stars/install counts;
- a green automated scan;
- a reputable repository name;
- an LLM review;
- valid syntax;
- a signature whose signer is not itself trusted.

## High-risk signals

Treat these as requiring stronger review:
- hidden/obfuscated payloads;
- runtime downloads/execution;
- credential discovery or broad home-directory reads;
- unrestricted network egress;
- persistence/startup modification;
- destructive filesystem commands;
- permission bypass instructions;
- prompt-injection attempts against the host agent;
- binaries without provenance;
- dependency confusion or unpinned executable dependencies.

## Update policy

An updated Skill is a new trust event when executable/instruction content or dependencies changed. Re-run the relevant review against the new digest instead of inheriting trust forever.
