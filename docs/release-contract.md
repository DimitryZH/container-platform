# Release contract v1

This document is the canonical format and policy specification. The [JSON Schema](../schemas/release-v1.schema.json) uses JSON Schema Draft 2020-12. The [illustrative manifest](../examples/release-v1.example.json) is synthetic, incomplete and **non-deployable**. The [offline CLI](offline-validation.md) implements strict parsing, schema/inventory checks and traceability eligibility. No approved production release is supplied.

## Encoding and identity

V1 accepts UTF-8 JSON only; YAML is not accepted. The offline parser rejects duplicate object keys before schema validation, rejects non-JSON numeric values and fails closed on parse errors. Ordinary JSON parsers may silently discard duplicate keys, so schema validation alone cannot enforce this requirement.

`notice` is a required nonempty human-readable description; examples must use exactly `NON-DEPLOYABLE SYNTHETIC EXAMPLE`.

`schemaVersion` is exactly `1`. Incompatible contract changes require a new schema version. `releaseId` identifies a Container Platform bundle independently of `application.upstreamVersion`, which records an upstream application version or null when unknown. `application.name` is `online-shop`. `purpose` is `candidate` or `example`; it is not an approval state. Example purpose must always be ineligible for approval, rendering, promotion and deployment, even if structural validation succeeds.

A release is a full snapshot, never a delta. Updating one component retains explicit digests for every unchanged component. Components may have independent source repositories, commits and build runs; there is no release-wide source commit or build run. After approval, do not edit or reuse a release ID for different contents: create another bundle. Immutable consumption identity is the repository plus full Git commit SHA plus manifest path, not the release label alone.

## Inventory and allowlists

Container Platform maintainers own and review the versioned service inventory and image allowlists, with application-owner confirmation of required services/dependencies and CI-owner confirmation of publication mappings. `inventory.revision` is a full Git commit SHA in this repository and `inventory.path` identifies the JSON inventory at that revision. The inventory must enumerate exact component IDs, artifact classes, required membership, allowed repositories and target platforms. It must separate first-party repositories from third-party dependency repositories. Changes to membership require explicit inventory review before release approval.

The [baseline discovery](baseline.md) records a proposed, committed inventory for the pinned `sre-dev-online-shop-v1` profile, reconciled from application manifests, SRE configuration and published artifacts. Its adoption still requires maintainer/owner review; it does not establish a universal service count or release approval. The synthetic example's inventory path and revision deliberately resolve to no usable baseline.

The offline validator requires exactly the inventory's required component set for the chosen application profile: it rejects unknown IDs, missing required IDs, duplicate IDs (including entries with differing digests), repositories/classes/platforms outside the selected inventory and unresolved local inventory references. Candidate and inventory target platforms are compared as equal sets, independent of ordering; top-level inventory platforms, when present, must match each component. V1 has no implicit optional services; distinct profiles require a separately reviewed inventory. JSON Schema cannot compare membership against an external inventory or enforce uniqueness by component ID; `uniqueItems` only rejects identical entries.

Artifact class follows origin and ownership, not the publication namespace. The discovered baseline contains third-party upstream copies and Redis; future owned application builds require a separate first-party mapping. First-party repositories must be exact reviewed entries within `docker.io/dmitryzhuravlev/online-shop-*`; the prefix alone is not authorization. Third-party dependencies such as Redis use independently reviewed Docker Hub repositories, versions/licenses and update policy; they need not use the first-party naming family. Legacy status describes traceability, not ownership, and can apply to either class.

## Component fields and traceability

| Field | Meaning and requirement |
|---|---|
| `id` | Stable inventory component ID; lowercase letters, digits and hyphens |
| `class` | `first-party` or `third-party` |
| `image.repository` | Explicit canonical `docker.io/namespace/repository`, without tag or digest |
| `image.digest` | Lowercase `sha256:` plus 64 hexadecimal digits |
| `image.kind` | `index` or `manifest` |
| `image.platforms` | Explicit unique `os/architecture[/variant]` targets |
| `traceability.status` | `recorded`, `upstream` or `legacy`; never `verified` |
| `traceability.source` | Source repository HTTPS URL plus full 40- or 64-character Git commit, or null |
| `traceability.build` | HTTPS CI run URL, or null |
| `traceability.upstream` | HTTPS publisher release/reference URL, or null |
| `traceability.reason` | Nonempty explanation for legacy gaps; otherwise null |

First-party non-legacy components require `recorded` status and non-null source/build. Third-party non-legacy components require `upstream` status and a publisher reference; source/build may be null if unavailable, or recorded if actually known. Legacy components require `legacy` status, an explicit reason, and explicit nulls for unavailable references. Never invent a commit, run or provenance. Available authentic references may be retained. Legacy candidates may be recorded for baseline investigation but are not approval-eligible by default; a future policy must define a narrowly scoped, reviewed exception with evidence and expiry, or require replacement with traceable artifacts. No such exception is granted here.

A CI URL is metadata, not cryptographically verified provenance. Signing/attestation verification is future policy work. A digest pins content; it does not prove publisher trust, compatibility, registry retention or future availability.

## Digest semantics

The pull reference is `repository@digest`. Tags are not accepted selectors. For `index`, the digest identifies a multi-platform OCI index / Docker manifest list; declared platforms are the required subset to check, not a claim to enumerate all index children. Future registry checks must confirm every selected child and platform. For `manifest`, the digest identifies a platform-specific image manifest and exactly one platform is required. V1 permits both explicit kinds and never silently converts an index digest to a child digest. Future registry validation must check media type, platform metadata, pullability and target support; registry resolution is not implemented here.

## Candidate, approval and deployment decisions

1. **Structure:** parse strictly and validate against the pinned v1 schema. This establishes field shape only.
2. **Offline release policy (implemented), registry (planned):** resolve the pinned local inventory, validate its structure and exact membership/allowlists, identity uniqueness, declared platforms and traceability eligibility. Reject examples and unresolved references. The CLI does not establish inventory approval. Registry digest kind/platform metadata and availability checks remain planned.
3. **Integration (planned):** bounded Compose checks consume the selected published images. Record evidence tied to the exact candidate revision and image set. Structure and registry success do not establish service compatibility.
4. **Container-release review:** maintainers review composition, policy results and required integration evidence. Approval is external review evidence tied to the exact manifest/revision, not a self-asserted manifest flag. Only a release PR reviewed by authorized maintainers with all documented mandatory checks passing may use merge as the approval event. Git merge itself performs no validation. Until those checks and review gates are implemented, merging this contract or an example approves no deployable release.
5. **SRE promotion (planned):** SRE separately authorizes suitability for a specific environment through a reviewed GitOps change. It must record the full immutable Container Platform commit SHA and manifest path, and consume exactly that image set. A moving branch/tag is insufficient. Checking rendered SRE values against the manifest, making that check mandatory and protecting merges remain future implementation; bypasses are not blocked today.
6. **Deployment and runtime:** Argo CD applies environment configuration; Kubernetes pulls from Docker Hub. SRE evaluates rollout health, observability, data/state constraints and operational acceptance.

Evidence must be rerun or explicitly re-evaluated after candidate changes; results for another revision do not automatically apply. The offline rules are executable locally; mandatory PR gates and authorization policy remain future implementation. This contract does not claim protected merges or deployment enforcement.

## Rollback

Rollback selects a previous approved complete image set at its immutable Container Platform revision and manifest path, without rebuilding or retagging. SRE separately verifies artifact availability, environment compatibility, migrations/data/state constraints and authorization before reviewing/applying the GitOps change. A prior approved release is not a guarantee of a safe or available rollback.

## Schema limits and local verification

The schema enforces required fields, enums, strict properties, reference shapes, digest syntax and conditional traceability/platform rules. It does not resolve URLs/commits, prove inventory completeness or authenticity, compare IDs across entries, determine approval, verify provenance, contact a registry, test compatibility or authorize deployment. HTTPS URL syntax does not prove trust; reference owners and evidence must be reviewed.

For a temporary Python environment with `jsonschema` installed, run from the repository root:

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator
schema = json.loads(Path("schemas/release-v1.schema.json").read_text())
example = json.loads(Path("examples/release-v1.example.json").read_text())
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema).validate(example)
```

The snippet above checks structure only. Use the [offline CLI](offline-validation.md) for immutable-inventory and traceability evaluation; CI gates and registry verification remain planned.
