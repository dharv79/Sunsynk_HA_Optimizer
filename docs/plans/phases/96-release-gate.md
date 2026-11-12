# Phase 96 — Release gate

**Status:** Done, merged (ea89659, PR #196). **Size:** XS, ~15-20k est. **Origin:** code review 11/11/2026.

## Findings

- `release.yml` runs on any push to `release/**` with no test step; tests run only on `main` pushes and pull requests.
- The tag is regex-checked but never compared with `manifest.json`. A mismatch ships the wrong version stamp and an unchanged card URL (`?v=<version>`).
- The bundled `www/*.js` is never syntax-checked.

## Design

- Make the test job reusable (`workflow_call`) and required by the release job.
- Fail the release unless the manifest version equals the tag.
- Add `node --check www/*.js` to the test job.

## Saving

None. Stops a broken or mislabelled release.
