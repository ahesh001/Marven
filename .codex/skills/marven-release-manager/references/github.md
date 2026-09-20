# GitHub Release Mode

Use this reference when the requested destination is the Marven GitHub repository.

## Prepare

1. Identify the repository, default branch, candidate branch, full commit SHA, and latest relevant release tag.
2. Review commits and changed files from the selected baseline through the candidate SHA.
3. Confirm repository instructions, license, README, privacy documentation, dependency metadata, installation commands, and public-file boundary still match the candidate.
4. Run applicable tests and builds. Record exact commands and results.
5. Choose a SemVer version supported by the change scope. Treat pre-1.0 compatibility deliberately rather than assuming breaking changes are harmless.
6. Draft release notes with highlights, fixes, security or privacy changes, migration steps, known limitations, and checksums for attached artifacts.
7. Inspect the tag and archive contents before any push or publication.

## Publish boundary

Creating or pushing a tag and publishing or un-drafting a GitHub release are separate external actions. Obtain action-time approval that identifies the repository, version, commit SHA, and whether the release is a prerelease, draft, or public stable release.

Never:

- tag a commit different from the verified candidate;
- force-move an existing release tag;
- publish from an unreviewed dirty working tree;
- attach unverified local state, memory, secrets, or model weights;
- silently regenerate an artifact after its checksum was approved.

If repository tooling supports a draft release, prefer preparing a draft before the final public publication. A draft still requires authorization when creating it would mutate GitHub.

## Verify

After publication, confirm:

- tag and release point to the approved full commit SHA;
- version, title, notes, prerelease status, and target branch are correct;
- every artifact is present and its SHA-256 matches the dossier;
- source archives do not cross the publication boundary;
- the release URL and any CI results are recorded.

If verification fails, stop. Do not delete, replace, or re-tag without a new explicit recovery decision.
