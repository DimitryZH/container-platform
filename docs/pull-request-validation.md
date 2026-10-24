# Pull request validation

The `Pull request validation` workflow runs on every `pull_request` opened, synchronized, reopened, edited or marked ready for review, and on pushes to `main`. There are no path filters: documentation-only changes still produce explicit check results.

## Stable checks and scope

| Check name | Scope |
|---|---|
| `Tests and package (Python 3.11)` | Minimum supported Python; offline/mocked tests and installed-wheel smoke checks |
| `Tests and package (Python 3.13)` | Newer supported Python; same checks |
| `Release check` | Git discovery/immutability and opt-in registry validation of new candidates |

Both matrix entries use GitHub-hosted Ubuntu 24.04 with a 15-minute job timeout. The release job has a 20-minute timeout. This small Python matrix exercises the supported minimum and a newer interpreter without duplicating operating-system jobs. All checkout/setup Actions are pinned to full official commit SHAs with major-version comments. Checkout fetches full history (`fetch-depth: 0`) and does not persist credentials.

The wheel is built, installed into a new temporary venv, and tested there. The helper executes the module and console commands outside the checkout, compares the packaged schema with the canonical JSON, and asserts the committed baseline's **expected negative** offline result: seven passing prerequisite stages, 12 `legacy_ineligible` diagnostics, exit 1. Automated tests use mocked registry exchanges; this suite makes no real registry requests. The baseline remains ineligible and is never granted an exception to make CI pass.

## Candidate paths and cumulative comparison

Candidate locations are direct-child `releases/*.json` files except `*.observations.json`. `examples/`, nested release directories and observation records are excluded. Selection is based on Git filenames, not the ability to parse JSON or read `purpose`: malformed new manifests remain selected for the existing CLI to reject. A synthetic example moved into the candidate location is selected and rejected by its candidate-purpose check.

For pull requests, event `pull_request.base.sha` and `head.sha` are validated as full commit objects; their merge-base defines the cumulative change set. This includes all commits in the PR, rather than only the most recent commit. Releases added to the current base after divergence are also considered when checking path/ID collisions; they are not treated as deletions by an older feature branch. Local result-tree identity checking combines the immutable base release catalog with head additions. It does not attempt to resolve unrelated merge conflicts; GitHub mergeability/review remains separate.

Push events use exact `before` and `after` trees, including multi-commit updates. Missing commits, unavailable merge-bases, all-zero initial/deleted revisions and unsupported events fail explicitly. There is no checkout/fetch or `HEAD~1` fallback inside orchestration. Required inventory objects must already be present locally; the release CLI reports missing objects rather than substituting working-tree content. Full checkout history supplies normal reachable baseline/inventory history, but cannot guarantee arbitrary unpublished references exist.

Diff names and trees use NUL-delimited Git output. Filenames remain data, never shell fragments. Non-UTF-8 Git filenames fail explicitly. Candidate content is read from regular blobs in the selected head revision and passed to the CLI through a temporary fixed-name file. Symlinks/gitlinks are not new candidate files. Summaries identify only counts/results; detailed CLI diagnostics in step logs use safe candidate/component indices, without copying arbitrary release IDs or paths into shell commands or summaries.

## Immutability and identity rules

- Added candidates are validated with the installed `python -m container_platform ... --registry --format json` interface.
- Modification, deletion or rename of a candidate already present in the comparison base fails discovery/immutability before registry access. Moving it into observations, examples or another directory is also forbidden.
- Moving an excluded file into a candidate location is a new candidate and receives normal validation. Changing an observation record alone is not candidate discovery.
- Every new candidate must have a new `releaseId` relative to other candidates in the resulting release catalog. Historical candidates are read for identity only, not rechecked for registry eligibility. If an existing identity cannot be read, uniqueness cannot be established and an addition fails. Malformed new identities are left for CLI diagnostics, not silently excluded.
- With no candidate changes, `Release check` returns **exit 0 / `no_candidate_changes`**, with an explicit small summary. The historical baseline is not inspected against the live registry on unrelated PRs.

These storage rules apply to every previously merged candidate manifest, including historical and not-yet-approved candidates. Immutability does not establish release approval. Corrections require a new file and a new release ID. A candidate newly introduced in the current PR can be edited or renamed repeatedly before merge: only the cumulative final change relative to base is evaluated. A rename of a file already in base remains forbidden.

All new candidates must satisfy both offline and registry rules. CLI exits **1 and 2 fail the release check**, including legacy ineligibility, missing inventory objects, registry outages, rate limits, timeouts and execution failures. There is no `continue-on-error` or blanket allow-failure. Independent traceability/registry diagnostics are retained in the JSON step log. The orchestration result preserves candidate exit codes and gives execution errors precedence when multiple candidates fail. A blocked registry must be investigated/retried after recovery, not treated as eligibility success. Normal registry request/retry/response bounds remain as [documented](registry-verification.md).

## Local execution

After package installation, use explicit full commit SHAs (replace the example labels with actual SHAs):

```text
python -m container_platform.ci --repository . --base BASE_SHA --head HEAD_SHA --pull-request
python -m container_platform.ci --repository . --base BEFORE_SHA --head AFTER_SHA
python -m unittest discover -s tests -v
python -m pip install build
python -m build --wheel
python scripts/check_package.py --repository . --wheel-directory dist
```

Use a fresh `dist` directory containing exactly one project wheel. The package helper makes its own clean temporary venv. New valid candidates cause real registry requests in orchestration; unit tests inject responses or use malformed inputs rejected before networking. `--github-event` is the workflow form and reads `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH` directly, without interpolating event strings into shell commands.

Orchestration JSON version 1 includes the overall result/exit, discovery result, new-candidate count, stable diagnostic codes and ordered candidate results containing the existing CLI reports. No candidate change returns 0; immutability/identity or CLI validation failure returns 1; comparison/usage/execution failure returns 2. The summary is limited to tests/package outcome or candidate count, discovery result and validation scope. It contains no large raw reports or local-machine/TLS-investigation details.

## Owner-configured merge protection

The [main protection guide](main-protection.md) records the current read-only settings observation, exact owner configuration and enforcement checklist. Recommended configuration is one active `main` ruleset requiring PRs, **zero approving reviews** for the solo-maintainer process, resolved conversations, all three stable checks above, an up-to-date branch, no force pushes/deletion and no bypass entries. Do not enable ordinary-update restrictions that prevent normal PR merges. Manual runtime smoke is optional and must not be a required check. `merge_group` is not supported.

A maintainer's self-review is not independent approval. Green check runs demonstrate CI execution, not required-check enforcement; verify the saved active rule and the PR merge section separately. Pending-check blocking must remain unverified unless it was actually observed after activation. No repository protection setting is changed by these documentation updates.

Only `pull_request` executes PR code, never `pull_request_target`. Jobs use GitHub-hosted unprivileged runners and `contents: read`; there are no registry/cloud credentials, new secrets, write-token/comment jobs, OIDC permissions or privileged workflow handoffs. The workflow tests code supplied by the PR author, including workflow/validator changes: it is **not independent tamper-proof enforcement**. Maintainer review and configured merge rules remain separate requirements; zero-approval solo review does not establish independent approval. Fork workflow approval or Actions organization policies may require owner action before jobs run.

Checks establish regression behavior and selected candidate eligibility under implemented policy, not container-release approval or SRE deployment authorization. The separate [manual smoke workflow](manual-smoke-workflow.md) records bounded integration evidence; it is not a required PR check. Image publication, promotion and legacy exceptions remain outside these checks.
