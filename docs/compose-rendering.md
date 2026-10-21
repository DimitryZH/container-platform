# Offline Compose rendering

The canonical Compose path is generated JSON, not the root `docker-compose.yaml` legacy scaffold. Install the wheel or use `python -m pip install .` with Python 3.11+ and local Git history containing the candidate's inventory objects.

## Profile and inputs

The packaged `aml-online-shop-compose-v1` profile derives topology, ports and environment settings from [canonical AML Online Shop](https://github.com/DimitryZH/application-modernization-lab/tree/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop), pinned at `9960484b5c7f1568688fa6fc8eb256976d6c18a4`. The [profile resource](../src/container_platform/profiles/aml-online-shop-compose-v1.json) records immutable README, Kubernetes manifest, image mapping and upstream-record references. Render performs no source fetch, registry request or Docker call.

This Compose profile is distinct from inventory profile `sre-dev-online-shop-v1`. Inventory supplies required membership, repository allowlists and platforms; the candidate supplies every exact repository and digest. Matching images do not imply runtime configuration equivalence with SRE or Kubernetes.

All 12 components are required, including loadgenerator (10 users, rate 1). Compose DNS uses component IDs. Container ports replace Kubernetes Service translation: frontend `80 -> 8080` becomes `frontend:8080`, and email `5000 -> 8080` becomes `emailservice:8080`. Only frontend publishes `127.0.0.1:8080:8080`. Redis `/data` is ephemeral tmpfs, with no persistence or host bind. Assistant is disabled with the AML loopback sentinel; profiler settings follow AML. No credentials, assistant/cloud services, extra images or startup helper images are added.

`depends_on` expresses startup order only. No readiness guarantees or healthcheck tools are assumed. Loadgenerator can encounter initial connection failures and would send synthetic checkout traffic if run. Static configuration is not runtime compatibility evidence.

## Commands and results

```text
python -m container_platform.compose candidate.json --repository . --profile aml-online-shop-compose-v1 --output compose.generated.json
container-compose candidate.json --repository . --profile aml-online-shop-compose-v1 --output compose.generated.json
```

Ordinary render requires all offline validation stages, including traceability. Examples are always rejected. Unsupported profiles, incompatible membership/platforms and malformed configuration fail before output is created. Candidate fields cannot supply commands, environment, mounts or includes.

For explicit inspection of the committed legacy baseline:

```text
python -m container_platform.compose releases/baseline-2026-10-18.json --repository . --profile aml-online-shop-compose-v1 --output compose.generated.json --inspection-only
docker compose -f compose.generated.json config --format json
```

Inspection-only tolerates exclusively `candidate.traceability` failures with `legacy_ineligible`; every preceding stage must pass. Successful artifact creation returns renderer exit 0 while its separate stdout JSON report retains the complete negative validation result (baseline exit 1 and 12 legacy diagnostics). The artifact records release ID, inventory revision, profile version and inspection mode without timestamps or local paths. It grants no eligibility, exception, approval or deployment authorization. Existing validator and PR release-check exit semantics are unchanged.

Compose bytes go only to `--output`; diagnostics/report go to stdout. Invalid input leaves output untouched. Existing output requires `--overwrite`; candidate, inventory and packaged resource paths cannot be overwritten. Complete output is published atomically. Use an existing writable output directory. No `.env`, interpolation, external includes or runtime files are required.

## Verification and limits

Offline tests cover deterministic bytes, exact mapping and candidate digests, service addresses, loopback exposure, safe model fields, invalid inputs, inspection restrictions and output protection. Package checks exercise both rendering interfaces and profile/schema resources from a clean installed wheel outside checkout.

The actual baseline static Compose check result is recorded below. No image pull, container execution, build or publication is part of these checks. Registry availability remains a separate opt-in validator operation. The [separate runtime runner](runtime-smoke.md) requires explicit execution opt-in and records its own result; static evidence does not establish application compatibility or deployment authorization.

### Static observation: 2026-10-20

Docker Compose `v2.40.3-desktop.1` successfully normalized the inspection-only artifact generated from `releases/baseline-2026-10-18.json` at repository revision `d3cb1e755fabfabc82718106a86582074227c35f` with profile `aml-online-shop-compose-v1`. Normalized output contained exactly 12 services and the exact 12 candidate image references, frontend-only loopback publishing, no build directives and no additional images. Rendering returned 0; the embedded offline validation remained exit 1 with 12 legacy-ineligibility diagnostics. This is a configuration check, not integration/runtime validation or baseline approval.
