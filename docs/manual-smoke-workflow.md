# Manual hosted Compose smoke

`.github/workflows/manual-smoke.yml` supplies opt-in runtime evidence on an ephemeral `ubuntu-24.04` Linux/amd64 hosted runner. It has only `workflow_dispatch`, read-only repository permissions, pinned Actions, a 45-minute timeout and no cancellation concurrency policy. Existing PR checks are unchanged. No registry login, secrets, application image builds, publication or deployment are requested.

## Inputs and tested revision

- `candidate`: required repository-relative tracked regular file, for example `releases/baseline-2026-10-18.json`. Absolute/traversal paths, symlink files/ancestors, untracked files and content differing from the selected commit are rejected.
- `profile`: `aml-online-shop-compose-v1`, the only supported choice.
- `inspection_only`: boolean, default `false`. Helper parsing accepts only the exact strings `true` and `false` supplied through environment values.

The workflow's native branch/tag selection chooses the workflow revision. There is no separate code-ref input: checkout pins `github.sha`, includes full Git history and does not persist credentials. The wheel, candidate snapshot, profile and schema come from this commit. The helper checks installed module/resource bytes against Git objects, verifies local inventory objects, then calls the installed [bounded runner](runtime-smoke.md) with explicit execution opt-in. Runtime orchestration and its budgets are not duplicated or weakened. Inputs are never interpolated into shell code.

Ordinary mode requires successful offline validation. For the legacy baseline, explicit inspection-only is required. A successful runtime result then coexists with validation exit 1 and 12 `legacy_ineligible` failures; it does not grant approval or deployment authorization.

## Evidence and failure behavior

The sole uploaded artifact is `compose-smoke-evidence`, containing `report.json` at the predictable `container-platform-smoke-evidence/report.json` location under the runner temporary root. Retention is seven days. It includes tested full commit SHA, candidate path/byte SHA-256, profile/inspection mode, the sanitized runner result, original primary exit code and workflow cleanup verification. The job summary adds release ID, runtime/cleanup outcomes and offline eligibility. Candidate-controlled values are JSON-quoted and HTML-escaped; no raw values are printed as workflow commands.

Only this report is uploaded, never the workspace, cleanup state, raw Docker logs, cookies or environment. Missing/malformed reports create explicit execution/report failure evidence and cannot yield success. Preparation failures report stable diagnostics; identity fields that could not be established remain unavailable. Runtime nonzero exit codes remain primary even when report processing or cleanup also fails. Upload failures fail the job; the report's primary code describes runtime, not artifact-service availability. Consult the job conclusion as well as the evidence.

## Cancellation and cleanup

The runner retains its normal interruption cleanup. A minimal pre-mutation hook saves the exact generated project identity and Compose bytes after successful prerequisites/name-collision checks. An `always()` workflow step checks only that identity and performs bounded project-scoped cleanup if resources remain, then verifies absence of its containers, networks and volumes. Identity, commit and Compose-byte hash must agree before cleanup. It does not search projects by prefix, prune globally or delete cached images.

Best-effort recovery and upload cannot be guaranteed after forced cancellation, repeated interruption, job timeout, Engine failure or runner loss. Cancellation recovery has offline adapter coverage but has not been verified by a live cancellation experiment. Ephemeral-host disposal is separate from verified application cleanup. A successful ordinary run must contain successful runner cleanup plus verified workflow resource absence.

## Hosted acceptance

The workflow is registered on the default branch. One actual [workflow_dispatch run](https://github.com/DimitryZH/container-platform/actions/runs/40046455406) completed successfully on `main`.

| Evidence | Observed result |
|---|---|
| Tested commit | `58241e3db4c8a2e15ec9a675be425e42d89759f9` |
| Candidate | `releases/baseline-2026-10-18.json`, release ID `baseline-2026-10-18-001` |
| Candidate byte SHA-256 | `4c4dcb50b8cbe6f8bd9955766d6149b12cb3f31c693705bb0f6aa1390227e9d9` |
| Profile / mode | `aml-online-shop-compose-v1`, explicit inspection-only |
| Services | All 12 running with the candidate's exact repository/digest references |
| Readiness / semantics | Readiness and all five catalog/product/cart/add-remove/synthetic-checkout assertions passed |
| Runtime | Exit 0, 47.627 seconds |
| Runner cleanup | `success` |
| Workflow cleanup | `verified_absent` for the exact project's containers, networks and volumes |
| Offline validation | Exit 1 with 12 `legacy_ineligible` failures |
| Evidence | Valid sanitized `compose-smoke-evidence/report.json`; published job summary verified during the original review |

On 2026-10-24, the run metadata and successful job steps were rechecked. The artifact was still available (expiry `2026-10-31T00:45:23Z`), downloaded again and verified against the tested commit's Git blob, exact image set, assertions, cleanup and retained legacy failures. Summary verification above refers to the original review, not a newly claimed summary download. After retention expiry, distinguish this recorded verification from whatever primary evidence remains accessible; do not claim a fresh artifact inspection without actually downloading it.

This is evidence for one selected demo flow, not production readiness, performance, provenance, release approval or deployment authorization. Historical negative/local observations remain in [runtime smoke evidence](runtime-smoke.md). Live cancellation recovery remains unverified. [Main protection](main-protection.md) and milestone closure are separate owner decisions; runtime smoke is not a required PR check.

## Future manual dispatch and evidence review

For a deliberate future run (not required merely to refresh this documentation):

```text
gh workflow run manual-smoke.yml --repo DimitryZH/container-platform --ref main -f candidate=releases/baseline-2026-10-18.json -f profile=aml-online-shop-compose-v1 -f inspection_only=true
gh run list --repo DimitryZH/container-platform --workflow manual-smoke.yml --event workflow_dispatch --limit 5
gh run view RUN_ID --repo DimitryZH/container-platform
gh run download RUN_ID --repo DimitryZH/container-platform --name compose-smoke-evidence
```

Confirm the intended tested SHA, candidate hash and exact images, all 12 service records, semantic assertions, runtime exit 0, successful runner cleanup, verified workflow resource absence and retained validation exit 1 with 12 legacy failures. Confirm artifact and summary availability, and record the specific run URL. Never infer release eligibility from runtime success.
