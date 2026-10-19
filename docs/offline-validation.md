# Offline release validation

The Python CLI checks release contract v1 against its immutable inventory using local files and Git objects only. Python **3.11 or newer** and Git are required. Windows PowerShell and Linux are supported; Bash is not required. Installation may download Python dependencies; offline evaluation itself performs no network retrieval. [Docker Hub metadata verification](registry-verification.md) is available only with explicit `--registry` opt-in.

## Install and run

From a checkout, using the Python interpreter of an activated virtual environment:

```text
python -m pip install .
python -m container_platform releases/baseline-2026-10-18.json --repository .
python -m container_platform releases/baseline-2026-10-18.json --repository . --format json
```

The equivalent installed command is `container-release`. `candidate` is a file path; `--repository` is a required path to the local Container Platform Git repository. `--format` accepts `text` (default) or `json`. Paths are relative to the caller's current directory. From another directory, supply paths to the candidate and repository; schema lookup uses installed package resources, independent of the current directory. A normal wheel installation includes the unchanged canonical `schemas/release-v1.schema.json`, mapped into the `container_platform.schemas` resource package without a second JSON copy in source.

Do not require a successful exit for the committed baseline: it intentionally fails traceability eligibility. `--help` prints usage. Run automated tests from the checkout after installation:

```text
python -m unittest discover -s tests -v
```

Tests use temporary repositories with local fixture identities and disabled signing/hooks. They do not change developer Git configuration, use Docker/Kubernetes, or contact registries. Python sockets are denied in the fixtures; schema retrieval is explicitly tested at its deny boundary. Shallow-clone tests use local file transport, not a network service.

## Evaluation stages

| Stable check ID | Evaluation |
|---|---|
| `candidate.parse` | Strict UTF-8 JSON; duplicate keys at any level, NaN/Infinity, overflowing nonfinite floats and malformed input rejected |
| `candidate.schema` | Packaged Draft 2020-12 schema; strict string patterns, required fields and conditional metadata rules |
| `candidate.purpose` | Require `purpose=candidate`; reject synthetic examples |
| `inventory.resolve` | Require an existing commit and regular file blob at the exact revision/path; no working-copy substitution |
| `inventory.parse` | Apply the same strict JSON parsing to the committed blob |
| `inventory.policy` | Validate inventory v1 identity, fields, classes, required membership, exact repositories and unique platforms before evaluation |
| `candidate.inventory` | Application identity, duplicate/unknown/missing IDs, class, exact repository and platform agreement |
| `candidate.traceability` | Report every legacy component as ineligible; non-legacy source/build/publisher requirements have already passed schema validation |

Stages run in this order. A failed stage prevents dependent stages from being evaluated; these have `status=skipped` and `prerequisite_failed`, never `pass`. Independent diagnostics within inventory policy, candidate consistency or legacy evaluation are accumulated in input order. Schema diagnostics have a deterministic field/schema-path ordering. Invalid values are not stripped, lowercased or echoed back, and schema error messages do not include input URLs or arbitrary values.

Inventory application must be `online-shop`; `profile` must be a nonempty release-ID-shaped identifier. The candidate chooses a profile through its pinned inventory, since contract v1 has no separate candidate profile field. Each component must explicitly declare `required: true`: v1 has no implicit optional members. Unknown descriptive inventory fields are permitted for historical compatibility but cannot execute code or add policy behavior. Component IDs, repositories and platforms use the release schema's syntax. First-party inventory entries must use the contract's application namespace, but a matching prefix never automatically adds a repository to the allowlist.

Candidate and inventory component platforms are compared as **equal sets**, ignoring order. If top-level inventory platforms are present, each component must declare the same set. Missing/extra platforms fail; subset matching is not supported. This compares the declared target requirements only. For an index digest, it does not enumerate or verify registry child manifests; for a manifest, schema permits exactly one declared platform.

Inventory commit and blob are read with Git object commands without checkout, hooks, filters, fetch or mutation. Git replacements and ambient `GIT_*` overrides are disabled; lazy object retrieval and all Git transport protocols are denied. Missing local objects, including shallow/partial-clone gaps, fail explicitly. Supply a repository containing the required objects yourself; the CLI never repairs/fetches it. Symlinks, trees and gitlinks are not inventory files. Regular modes `100644` and `100755` are accepted.

Schema `$schema` and `$id` are identifiers, not download instructions. Only in-document `$ref`/`$dynamicRef` fragments are supported; external references, including file references, fail explicitly. An explicit deny-only `referencing.Registry` replaces default retrieval behavior. No candidate or inventory field is interpreted as a schema, command, hook or remote retrieval instruction.

## Output and exit codes

| Exit code | Overall `result` | Meaning |
|---|---|---|
| `0` | `offline_rules_passed` | All implemented offline rules passed; no verified provenance, approval or deployment authorization implied |
| `1` | `validation_failed` | Input/policy violation, unavailable recorded inventory objects, example rejection or legacy ineligibility |
| `2` | `execution_error` | Usage error, unreadable candidate, unavailable repository/Git, Git execution failure, or invalid/unavailable packaged schema |

Output version 1 JSON has `outputSchemaVersion`, `result`, `exitCode`, `scope`, top-level `diagnostics`, and an ordered `checks` array. Each check contains `id`, `status` (`pass`, `fail`, `skipped`) and `diagnostics`. Every diagnostic has a stable `code`, field `path`, and concise `message`. Diagnostic paths normally address candidate fields; `inventory.*` identifies missing required inventory entries, and inventory-stage paths address the inventory. Consumers should use check IDs/codes, not parse prose. Normal results contain no timestamps, random identifiers, absolute local paths or raw input values. The same input bytes and Git objects produce the same result/order. JSON mode emits exactly one JSON document to stdout, including ordinary validation and usage failures; no traceback is printed for these failures. `--help` is human-readable usage, not a result document.

The committed baseline has this stage summary (the actual JSON contains all diagnostics):

```text
candidate.parse: pass
candidate.schema: pass
candidate.purpose: pass
inventory.resolve: pass
inventory.parse: pass
inventory.policy: pass
candidate.inventory: pass
candidate.traceability: fail
12 x legacy_ineligible
result: validation_failed
exitCode: 1
```

Stable reason codes are:

| Area | Codes |
|---|---|
| Parsing | `duplicate_json_key`, `invalid_utf8`, `malformed_json`, `non_json_number`, `validation_limits_exceeded` |
| Schema/purpose | `schema_violation`, `example_not_candidate`, `schema_unavailable`, `schema_invalid`, `schema_reference_unsupported`, `schema_reference_unavailable` |
| Git inventory | `inventory_reference_invalid`, `inventory_commit_missing`, `inventory_revision_not_commit`, `inventory_path_missing`, `inventory_not_regular_file`, `inventory_object_missing` |
| Inventory policy | `inventory_type_invalid`, `inventory_version_unsupported`, `inventory_application_invalid`, `inventory_profile_invalid`, `inventory_components_invalid`, `inventory_component_invalid`, `inventory_id_invalid`, `inventory_id_duplicate`, `inventory_class_invalid`, `inventory_required_invalid`, `inventory_repositories_invalid`, `inventory_first_party_namespace`, `inventory_platforms_invalid`, `inventory_platforms_inconsistent` |
| Candidate policy | `application_mismatch`, `component_duplicate`, `component_unknown`, `component_missing`, `component_class_mismatch`, `repository_disallowed`, `platforms_mismatch`, `legacy_ineligible` |
| Execution/dependency | `usage_error`, `candidate_unreadable`, `repository_unavailable`, `git_unavailable`, `git_execution_error`, `prerequisite_failed` |

Legacy exceptions are not supported. Recorded source/build URLs and publisher references are metadata, not cryptographically verified provenance. A committed inventory is the explicitly selected policy input; the CLI does not establish that maintainers approved that commit. Offline mode does not check registry availability. Integration compatibility, mandatory PR gates, release approval, SRE handoff and deployment authorization remain separate future checks/decisions. This issue implements only the offline portion of Executable Release Validation; Optional [Docker Hub metadata verification](registry-verification.md) is now implemented separately; registry mode uses output version 2 and preserves independent traceability failures.
