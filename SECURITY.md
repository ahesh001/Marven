# Security Policy

Marven is a Heshware local-first AI research project. This repository contains experimental prototypes, not a hosted production service or a complete security sandbox. We welcome responsible reports that help protect Marven users, their local files, conversations, memory, credentials, and connected systems.

## Supported versions

Marven has not published a stable release channel yet. Security maintenance is currently provided on a best-effort basis for the latest commit on `main`.

| Code line | Security support |
| --- | --- |
| Latest `main` | Supported on a best-effort basis |
| Open feature or experiment branches | Evaluated when the issue also affects `main` or is likely to be merged |
| Older commits, forks, and locally modified copies | Not supported |

When releases begin, this table will be updated with explicit supported-version windows.

## Report a vulnerability privately

Do **not** disclose a suspected vulnerability in a public issue, pull request, discussion, commit message, or social post.

Use GitHub's private vulnerability reporting form:

**[Report a vulnerability privately](https://github.com/ahesh001/Marven/security/advisories/new)**

Private vulnerability reporting is enabled for this repository. The report and any draft advisory are visible only to the reporter, invited collaborators, and repository maintainers until coordinated disclosure.

Please include as much of the following as is safe and relevant:

- a concise description of the vulnerability and its impact;
- the affected commit, branch, component, endpoint, or dependency;
- operating system, Python and Node.js versions, model backend, and relevant configuration;
- required privileges, user interaction, and network position;
- clear reproduction steps or a minimal proof of concept;
- sanitized logs, requests, responses, or screenshots;
- whether the issue exposes files, memory, conversations, secrets, devices, or internal network services; and
- any suggested mitigation or patch.

Do not include real credentials, private conversations, personal memory archives, model secrets, or another person's data. Use synthetic test data and redact tokens, paths, hostnames, and identifiers where possible. If a secret may already be exposed, revoke or rotate it immediately and report only the location and type of secret.

## What to report

Reports are especially useful when they demonstrate a security-boundary failure, including:

- unauthorized file access or writes outside policy-approved paths;
- path traversal, symlink escape, or unsafe archive/file handling;
- server-side request forgery or a bypass of public-network and redirect validation;
- remote code execution, command injection, or unsafe deserialization;
- bypasses of capability policy, approval-gated self-update, or audit controls;
- disclosure of credentials, private conversations, persistent memory, or sensitive logs;
- cross-site scripting, request forgery, or injection that crosses a trust boundary;
- an exposed development/debug interface or unsafe deployment default;
- a dependency vulnerability that is reachable in Marven's supported configuration; or
- a GitHub Actions or release-process weakness that could alter distributed code or artifacts.

The following generally belong in a normal bug report or upstream project instead:

- hallucinations, poor model output, bias, or prompt-quality concerns without a security-boundary impact;
- prompt injection that cannot access additional data, permissions, tools, or execution capability;
- vulnerabilities that exist only in a third-party model, service, operating system, or dependency and are not made exploitable by Marven;
- findings that require an already-compromised host or intentionally unsafe local modifications;
- unsupported forks or historical commits;
- automated scanner output without a reachable impact or reproducible path; and
- denial-of-service stress testing, social engineering, phishing, or physical attacks.

Use the repository's issue forms for ordinary bugs, feature requests, and research or architecture proposals.

## Response process

Marven is currently maintained as an early-stage project. These are best-effort targets, not a service-level agreement:

- acknowledge a complete report within 3 business days;
- provide an initial assessment within 7 business days;
- send progress updates at least every 14 days while remediation is active; and
- coordinate publication after a fix or practical mitigation is available.

Maintainers may ask for clarification, reproduce the issue, assess severity, prepare a private fix, and request validation from the reporter. Confirmed vulnerabilities may receive a GitHub Security Advisory, CVE request when appropriate, release or upgrade guidance, and reporter credit unless anonymity is requested.

Please allow a reasonable remediation period before public disclosure. If active exploitation or immediate user risk changes the timeline, state that clearly in the private report.

## Marven security boundaries

Marven's default direction is local-first, but local execution is not automatically private or secure:

- `server.py` is intended for local development and binds to localhost by default. Internet-facing or multi-tenant deployment is not currently supported.
- Ollama, vLLM, web access, speech services, proxies, hosted assets, and other configured integrations may transmit data outside the machine.
- File, network, device, plugin, and self-update capabilities are experimental. Policy checks and approval files reduce risk but do not form a complete operating-system sandbox.
- Retrieved content, uploaded files, webpages, tool output, model output, and stored memory must be treated as untrusted input.
- Generated patches and self-update proposals must remain human-reviewed, narrowly scoped, and reversible.
- Runtime history, memory, audit logs, model files, and private assets are intentionally excluded from the public repository, but contributors must still inspect every staged change.
- The planned mobile application and persistent-presence services are not shipped from this repository and are outside this policy until their code is published here.

Before using sensitive data, exposing an API beyond localhost, or enabling powerful capabilities, review the active configuration, apply least privilege, and read [PRIVACY.md](PRIVACY.md).

## Automated security checks

The repository uses:

- CodeQL analysis for Python and JavaScript/TypeScript on relevant pushes and pull requests, plus a weekly scheduled scan;
- Dependabot updates for GitHub Actions, the core Python package, the voice prototype, and the React client; and
- GitHub secret scanning and Dependabot vulnerability alerts.

Automated results support review; they do not replace manual threat modeling, testing, or responsible disclosure.

## Research guidelines

Good-faith testing should use accounts, machines, data, and services you own or are explicitly authorized to test. Keep activity proportionate to demonstrating the issue, avoid persistence and disruption, stop if you encounter another person's data, and report the finding privately. Do not exfiltrate data, degrade shared services, or expand access beyond what is necessary to prove impact.

Thank you for helping improve Marven's security and user control.
