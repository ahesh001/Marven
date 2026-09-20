# Privacy Policy

**Effective date: September 20, 2026**

Marven is a local-first software project. This policy explains how the software and this source repository are intended to handle information. It is not a promise that every optional dependency or model service has identical practices; check those services' documentation before using them.

## Information Marven may process

Depending on the features you enable, Marven may process:

- prompts, responses, and conversation history;
- locally stored memory, summaries, uploaded files, and application logs;
- configuration values and local file paths needed to run the application;
- audio captured by the optional voice prototype;
- requests sent to a model server that you configure, such as Ollama or a compatible local endpoint.

## Where information goes

The default design is local execution. The source repository does not include a hosted Marven telemetry service. However, information can leave your machine if you configure a remote model endpoint, web connector, cloud speech or text service, CDN, proxy, or other third-party integration. Those services have their own policies and terms.

## Storage and deletion

Runtime data may be written to local history, memory, cache, audio, or log paths created by the application or its dependencies. Inspect the active configuration and delete those files from your machine when they are no longer needed. The public repository excludes known runtime state and private development records; contributors must still inspect staged changes before publishing.

## Sensitive information

Do not provide secrets, passwords, API keys, private keys, regulated data, or another person's confidential information unless you understand and accept the storage and processing risks. Do not commit such information to Git. If a secret is exposed, revoke or rotate it immediately and remove it from repository history.

## Children's information

Marven is not directed at children and is not designed to process children's personal information.

## Changes and contact

This policy may change as the project changes. Questions or privacy reports can be opened as an issue in the repository, but do not include personal or confidential information in a public issue.
