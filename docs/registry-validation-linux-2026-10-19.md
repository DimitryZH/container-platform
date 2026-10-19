# Registry live verification in Linux — 2026-10-19

A single bounded live run in the already available Ubuntu WSL environment successfully verified **all 12 baseline components' registry metadata** with the unchanged implementation. The [sanitized observation JSON](registry-validation-linux-2026-10-19.json) records the actual normal CLI report separately from observation timestamps.

- UTC run window: `2026-10-19T14:43:38+00:00` to `2026-10-19T14:43:47+00:00`.
- Implementation and selected baseline revision: **`235291f386a0d4a7c4229248580fa1bf2ecde3e0`**.
- Baseline path: `releases/baseline-2026-10-18.json`.
- Inventory: `480bd0a5769ae61313c4bd0da846ba3a6fc0ce51`, `inventories/online-shop-sre-dev-v1.json`.
- Environment: existing Ubuntu WSL, Python **3.12.3**, OpenSSL **3.0.13 (30 Jan 2024)**, clean temporary Linux venv outside the repository.

## Environment selection and installation

One clean Windows venv was tested first at **2026-10-19T14:38:53Z**, using available Python 3.13.3/OpenSSL 3.0.16. Its default context still rejected `auth.docker.io` with code 10 (expired certificate); `registry-1.docker.io` passed. No duplicate Windows venvs were created. A venv does not isolate the Windows certificate store.

Already installed Ubuntu was then checked at **2026-10-19T14:39:33Z**: both fixed endpoints passed `ssl.create_default_context()` certificate/hostname verification. The Linux venv preflight also passed before the full run. No WSL distribution, VM, container infrastructure or system Python was installed. No Windows certificate store, verification flags, custom CA bundle, endpoint or proxy/VPN settings were changed.

The existing Linux Python lacked ensurepip. Its temporary venv was populated with pip 25.0.1 from an already available universal wheel shipped with the existing Python installation, without changing system packages. The package was installed from an isolated local Git checkout at the exact implementation commit above. Installed package modules and schema were compared with that commit before execution; baseline content and referenced inventory Git objects were confirmed present and unchanged. Source Git history was copied locally; the CLI performed no fetch or checkout.

**All 48 automated tests passed in Linux** (9.293 seconds), including the existing offline suite, mocked transport/limits, immutable inventory, no-network boundaries, credential-safe redirects, platform selection and output/exit semantics. No real network was used by those tests. The live run below was separate.

## Actual bounded live result

Equivalent invocation from the pinned checkout, using its installed environment:

```text
python -m container_platform releases/baseline-2026-10-18.json --repository . --registry --format json
```

| Check | Actual outcome |
|---|---|
| Parsing/schema/purpose | pass |
| Immutable inventory resolution/parsing/policy | pass |
| Component membership/classes/repositories/platforms | pass |
| Registry artifacts | **pass, 12/12 components**, no registry diagnostics |
| Traceability | **fail, 12/12 legacy-ineligible components** |
| Overall | `validation_failed`, **exit 1**, as expected |

The CLI verified manifest/config response bytes, descriptor sizes/types and declared target-platform metadata for all selected immutable references. It retained its documented request/response/time/retry bounds, TLS verification and fixed endpoint/CDN restrictions. No implementation changes or relaxed boundaries were needed. Neither image layers nor containers were downloaded/run.

The [original Windows failed observation](registry-validation-2026-10-19.md) and [TLS diagnosis](registry-tls-diagnostics-2026-10-19.md) remain unchanged. The diagnosed stale Windows intermediate remains a local Windows issue: a future Windows run requires owner/administrator review of the expired ISRG Root X2 cross-certificate through the approved certificate-maintenance process. It was not repaired automatically.

This successful registry observation does not approve the baseline. All 12 artifacts remain legacy and traceability-ineligible. Layer availability, future retention, publisher trust/provenance, application compatibility, release approval and deployment authorization are not established. Existing baseline digests, inventory and historical observations remain unchanged.
