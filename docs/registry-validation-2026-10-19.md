# Registry live verification — 2026-10-19

This is an actual bounded read-only run of the installed CLI, separate from mocked automated tests and the historical baseline discovery observations. **Registry verification is blocked; no component was successfully verified in this run.**

- UTC observation window: `2026-10-19T14:13:02+00:00` to `2026-10-19T14:13:04+00:00`.
- CLI implementation and selected baseline repository revision: `235291f386a0d4a7c4229248580fa1bf2ecde3e0`.
- Candidate: [releases/baseline-2026-10-18.json](https://github.com/DimitryZH/container-platform/blob/235291f386a0d4a7c4229248580fa1bf2ecde3e0/releases/baseline-2026-10-18.json).
- Immutable inventory: `480bd0a5769ae61313c4bd0da846ba3a6fc0ce51`, `inventories/online-shop-sre-dev-v1.json`.
- Environment: Windows, Python 3.13.3; normal installed package, default certificate validation and documented transport restrictions.

Equivalent invocation from the checkout (private filesystem paths omitted):

```text
python -m container_platform releases/baseline-2026-10-18.json --repository . --registry --format json
```

| Stage | Actual result |
|---|---|
| Candidate parsing/schema/purpose | pass |
| Immutable inventory resolution/parsing/policy | pass |
| Candidate membership/classes/repositories/platforms | pass |
| Traceability | fail: all 12 `legacy_ineligible` diagnostics retained |
| Registry artifacts | fail: all 12 `registry_tls_error` diagnostics |
| Component inspections | 12 attempted; 0 completed successfully; 12 unsuccessful |
| Overall | `execution_error`, exit code **2** |

Registry diagnostics address `$.components[0].image` through `$.components[11].image`, in candidate order. Each reports the fixed message `TLS certificate/connection validation failed.` No response bodies, authentication headers, tokens, signed redirects or query strings are included.

Follow-up bounded TLS-only handshakes to the two fixed public endpoints isolated a certificate-validation blocker: the canonical registry handshake passed, while `auth.docker.io` failed certificate verification with code **10**, `certificate has expired`. No alternative auth endpoint, insecure TLS option or expanded destination allowlist was used. The CLI does not classify the affected artifacts as missing or corrupt merely because the transport could not complete.

Successful live manifest/config verification for this baseline remains **unfulfilled** pending a valid public token-endpoint TLS chain in this environment and a later bounded rerun. Mocked success is not a substitute for this missing live evidence. After registry success, the same legacy baseline must still return exit 1 unless another execution error occurs; no legacy exception is granted.

No image layers were downloaded, containers run, images changed, external repositories modified or cloud/Kubernetes resources accessed. Existing baseline images/digests, inventory and `baseline-2026-10-18.observations.json` remain unchanged. Neither this attempt nor metadata success establishes layer availability, retention, publisher trust/provenance, application compatibility, release approval or deployment authorization.
