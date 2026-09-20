# Google Play Release Mode

Use this reference only when a Marven Android project or a traceable signed Android App Bundle is part of the release batch. Verify current requirements against official Android Developers and Google Play Console documentation because target API, policy, testing, and submission requirements change.

## Required evidence

- Actual Android source project and variant, or a traceable signed `.aab` tied to the approved commit.
- Stable application ID, user-facing version name, and monotonically increasing version code.
- Play App Signing and release signing configured without committing keystores, passwords, or service-account keys.
- Successful release bundle build, lint or static checks, and representative device testing.
- Compatibility checks for supported Android versions, architectures, screens, permissions, upgrades, and background behavior.
- Compliance with the currently required target API level and applicable Play policies.
- Accurate Data safety form, privacy-policy URL, account-deletion information where required, content rating, ads declaration, app access instructions, screenshots, listing text, and release notes.
- Justification and user-facing consent for sensitive permissions, microphone, camera, files, location, notifications, accessibility, foreground services, or other restricted APIs used by the build.

## Channel gates

Treat these as distinct actions:

1. Upload a bundle to Play Console.
2. Release to internal, closed, or open testing.
3. Promote between tracks.
4. Submit or start a production rollout.
5. Increase, halt, or complete a staged rollout.

Authorization for one step does not authorize another. Identify app, version, version code, track, countries or audience, and rollout percentage in each action-time approval.

Prefer an internal or closed test before production unless the user explicitly requests another valid channel and its gates pass. Confirm any tester-count or testing-duration eligibility requirements in the current Play Console state rather than relying on remembered policy.

## Marven checks

- Confirm the mobile client does not contain model, Firebase, signing, or service credentials.
- Verify account deletion and user-controlled memory deletion if accounts or persistent memory are offered.
- Ensure microphone, camera, file, notification, persistent-memory, cloud-inference, and human-state behavior matches the manifest, runtime prompts, Data safety answers, and privacy policy.
- Keep therapeutic, diagnostic, emergency, consciousness, and accuracy claims within demonstrated behavior.
- Confirm local-first claims remain accurate for the release configuration and disclose optional remote processing.

## Verify and recover

Record the Play application ID, release ID, version name, version code, track, review status, country scope, and rollout percentage. Verify the processed artifact matches the approved bundle. If health signals regress, use the supported halt or staged-rollout controls and prepare a higher-version-code fix; do not reuse or replace an already consumed version code.
