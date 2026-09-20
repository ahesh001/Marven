# Marven Release Gates

Use these gates for every release candidate. A gate passes only with observable evidence. Use `not applicable` when the gate genuinely does not apply, and explain why.

## Release dossier

Capture the following in the response or in a persisted release dossier when the user requests one:

| Field | Required content |
| --- | --- |
| Candidate | Version, release-candidate identifier, date, and requested destinations |
| Source | Repository, branch, full commit SHA, baseline tag or commit, and working-tree state |
| Scope | Included features and fixes, excluded work, migrations, and known limitations |
| Verification | Commands run, results, environment, and intentionally omitted checks |
| Artifacts | Filename, platform, build number, size, SHA-256, and source commit |
| Privacy and security | Data-flow changes, permissions, secrets scan, policy changes, and disclosure impact |
| Compatibility | Supported platforms, upgrade path, data migration, and backward compatibility |
| Rollout | Destination, channel, audience, staged percentage if any, and success signals |
| Rollback | Stop condition, recovery owner, prior stable version, and data recovery approach |
| Decision | Per-destination status, blockers, approver, and external actions still pending |

## Gate 1: source integrity

- The target commit and branch are explicit.
- The working tree state is understood; unrelated changes are preserved.
- The change inventory is based on a known tag or commit rather than memory.
- Version changes are intentional and consistent across relevant manifests.
- A released version or build number is never reused and an existing tag is never force-moved.

## Gate 2: functional verification

- Tests relevant to changed components pass.
- Production builds complete for each requested artifact.
- Startup, health, and one representative user flow are exercised where practical.
- Upgrade and migration paths are tested when persistent state changes.
- Failures, flaky tests, skipped tests, and unavailable environments remain visible blockers or documented exceptions approved by the user.

## Gate 3: publication boundary

Review staged files and packaged artifacts for:

- `.env` files, credentials, tokens, certificates, provisioning profiles, or keystores;
- `history/`, `memory/`, `audit/`, awareness logs, transcripts, and user-specific exports;
- model weights, caches, local absolute paths, debugging output, and generated development builds;
- `ShadowBox_Framework/`, private design assets, and any file excluded by repository policy;
- third-party assets, models, datasets, or code without confirmed distribution rights.

Do not merely trust ignore rules. Inspect the actual staged changes and release archive contents.

## Gate 4: security and privacy

- Authentication, authorization, network, file, device, memory, and self-update changes receive focused review.
- External input remains untrusted and durable memory writes remain validated and auditable.
- Secrets stay in approved secret stores or local signing systems.
- User deletion and correction propagate through derived indexes and graph projections where those systems are present.
- `PRIVACY.md`, permission prompts, store disclosures, and runtime data flows agree.
- New data collection, cloud processing, analytics, microphone, camera, file, location, or account use is called out explicitly.

## Gate 5: product truth

- Release notes describe only functionality present in the candidate.
- Experimental components and known limitations are labeled.
- Roadmap work is not presented as shipped behavior.
- Health, safety, psychological, security, intelligence, and autonomy claims are appropriately bounded.
- User-facing version, support, privacy, and licensing information are current.

## Gate 6: operability

- Health checks, logs, crash reporting, and support paths needed for the chosen channel are available or the gap is explicit.
- The rollout has measurable success and stop conditions.
- A rollback or forward-fix path exists for code, data, and store distribution.
- The prior stable artifact or tag can be identified.

## Status rules

- `ready`: every required gate passed and the exact external action is identified.
- `blocked`: at least one required gate failed, is unknown, or lacks access/evidence.
- `not applicable`: the destination or gate does not apply to this candidate, with a stated reason.
- `released`: the approved external operation succeeded and post-release verification confirmed it.

Never convert `unknown` into `ready`. A user may explicitly accept a documented non-critical exception, but record the exception and its impact.
