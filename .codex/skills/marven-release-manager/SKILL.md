---
name: marven-release-manager
description: Prepare, validate, package, and safely publish versioned Marven release batches to GitHub and, when mobile projects are present, Apple App Store Connect or Google Play. Use for release planning, preflight checks, versioning, build and test coordination, release notes, staged rollout, or post-release verification; do not use for ordinary feature development.
---

# Marven Release Manager

## Purpose

Turn a known Marven commit into an auditable release candidate with a separate go/no-go result for each requested destination. Default to preparing and reporting on the candidate. Publishing is a distinct action that requires explicit authorization immediately before the external release step.

## Establish the release scope

Inspect the repository instructions, current branch, working tree, tags, project manifests, build configuration, tests, privacy documentation, and release history before changing anything.

Resolve or clearly report:

- candidate version and build number;
- exact commit SHA and source branch;
- included changes and intentionally excluded changes;
- requested destinations and channels;
- whether the user wants preparation only, a test release, store review, or production availability.

If the request is ambiguous, prepare a candidate without publishing it. Evaluate only detected platforms. Mark a destination `not applicable` or `blocked` when its source project, signing configuration, account access, or required artifact is missing; do not invent it.

Use independent readiness gates for GitHub, Apple, and Google Play. One unavailable mobile target should not prevent preparation for another target. If an unexpected failure occurs during an authorized multi-platform publish, stop the remaining publish operations and report the partial state before continuing.

## Build the release candidate

1. Read [references/release-gates.md](references/release-gates.md) and create the release dossier it defines.
2. Inventory changes since the last relevant release or user-selected baseline. Separate implemented behavior, fixes, migrations, known limitations, and planned work.
3. Preserve all unrelated user changes. Never clean, reset, discard, or overwrite a working tree to make a release pass.
4. Run the repository's documented tests, builds, linters, and packaging steps that apply to the changed surfaces. Do not invent successful results or silently skip a failed gate.
5. Review the candidate for secrets, private memory, personal data, model weights, local paths, generated files, and oversized artifacts before staging or upload.
6. Verify version consistency, artifact identity, checksums, privacy disclosures, permissions, release notes, rollout plan, and rollback plan.
7. Report each destination as `ready`, `blocked`, `not applicable`, or `released`, with evidence and the exact next action.

When GitHub is a destination, read [references/github.md](references/github.md). Read [references/apple.md](references/apple.md) only for Apple/TestFlight/App Store work, and [references/google-play.md](references/google-play.md) only for Android/Google Play work. Store rules change frequently, so verify current requirements against official Apple or Google documentation during an actual store release.

## Marven-specific release rules

- The public repository currently contains Python/Flask services, a React web client, local Ollama and vLLM integrations, memory experiments, capability controls, and a voice prototype. These do not by themselves constitute an iOS or Android application.
- Require the actual mobile project or a traceable signed mobile artifact before declaring an Apple or Google Play target ready.
- Keep runtime conversations, `history/`, `memory/`, `audit/`, local awareness logs, `.env` files, credentials, signing material, private assets, model weights, `ShadowBox_Framework/`, and user-specific data out of public release artifacts.
- Treat changes to memory admission, deletion, migration, provenance, permissions, `policy.yaml`, self-update behavior, networking, microphones, cameras, files, or authentication as security- or privacy-relevant changes that require focused review.
- Test memory migrations with backup, rollback, deletion propagation, and compatibility checks. A retrieval improvement must not silently rewrite canonical memory.
- Keep release notes factual. Distinguish working code from experimental components and roadmap items. Do not claim consciousness, literal self-awareness, mind-reading, emotional diagnosis, clinical care, guaranteed truth, or production-grade security.
- Ensure the README, `PRIVACY.md`, store privacy disclosures, permission prompts, and actual runtime behavior agree before release.
- Use existing configured signing and service credentials only. Never ask for passwords, API keys, certificates, provisioning profiles, or keystore secrets in chat, and never commit them.

## Approval boundary

Preparation may include local edits, tests, build outputs, a proposed version, release notes, and a draft release dossier within the user's requested scope.

Obtain action-time approval before any of these external mutations:

- pushing a release branch or tag;
- publishing or un-drafting a GitHub release;
- uploading a build to TestFlight or Google Play;
- promoting a build between testing tracks;
- submitting an app for store review;
- starting, widening, halting, or completing a production rollout.

State the repository or store, app, version, build number, channel, and rollout scope in the approval request. Recheck that the approved commit and artifacts are unchanged immediately before publishing. Never interpret approval for one destination or channel as approval for another.

## Required handoff

Conclude preparation with:

- release version, build identifiers, branch, and commit SHA;
- concise change summary and generated release notes;
- per-platform status table with evidence;
- tests and builds run, including failures or omissions;
- privacy, security, migration, and compatibility findings;
- artifact names, sizes, and checksums when available;
- known risks and unresolved blockers;
- rollout and rollback plan;
- exact external actions still awaiting approval.

After an authorized release, verify the public or testing destination, published version, artifact identity, release notes, and rollout state. Record URLs or platform identifiers returned by the service and report any divergence from the approved candidate.
