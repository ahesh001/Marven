# Apple Release Mode

Use this reference only when a Marven iOS or iPadOS project or a traceable signed Apple artifact is part of the release batch. Verify current requirements against official Apple Developer and App Store Connect documentation because submission rules change.

## Required evidence

- Actual mobile source project and scheme, or a traceable signed archive tied to the approved commit.
- Stable bundle identifier, marketing version, and monotonically increasing build number.
- Valid distribution signing and provisioning configured outside the repository.
- Successful release archive and validation for the intended devices and minimum OS.
- Representative device testing, launch testing, authentication testing, and upgrade testing.
- Current privacy manifest and required-purpose API declarations where applicable.
- Accurate App Privacy responses, privacy-policy URL, support URL, age rating, export-compliance response, screenshots, description, and review notes.
- Complete permission purpose strings and in-app consent behavior for every used sensitive capability, including microphone, camera, photos, files, location, contacts, or tracking.
- Demo or review access prepared securely when Apple needs authenticated functionality; never place credentials in release notes or the repository.

## Channel gates

Treat these as distinct actions:

1. Upload a build to App Store Connect.
2. Make a build available to internal or external TestFlight testers.
3. Submit a version for App Review.
4. Release an approved version manually, automatically, or through a phased release.

Authorization for one step does not authorize the next. Identify app, version, build, tester group or storefront scope, and release mode in each action-time approval.

Start with TestFlight unless the user explicitly requests another valid channel and its gates pass. Store-review readiness requires metadata and disclosure review even when the binary already passed TestFlight testing.

## Marven checks

- Confirm the mobile client does not expose model, Firebase, or service credentials.
- Verify account deletion and data-control paths if accounts are offered.
- Make voice, camera, persistent memory, cloud inference, and human-state features opt-in where required and consistent with the privacy disclosures.
- Keep therapeutic, diagnostic, emergency, consciousness, and accuracy claims within the product's demonstrated and reviewed capabilities.
- Confirm local-only claims remain true for the selected configuration; disclose any configured cloud processing.

## Verify and recover

Record the App Store Connect app identifier, version, build, processing state, TestFlight group or review state, and release setting. If a rollout must be stopped, use the platform-supported pause, removal, or forward-fix path; do not assume an installed App Store binary can be remotely rolled back.
