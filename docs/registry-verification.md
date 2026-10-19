# Docker Hub metadata verification

Opt in explicitly after [installation](offline-validation.md):

```text
python -m container_platform releases/baseline-2026-10-18.json --repository . --registry --format json
```

The installed `container-release` command supports the same flag. Without `--registry`, invocation, output version 1 and network-free behavior remain unchanged. No Docker daemon, login, credential store, image layers or containers are used. Candidate parsing, local schema, purpose, immutable inventory parsing/policy and exact component consistency must all pass before constructing the registry client. Failed prerequisites leave `registry.artifacts` skipped with no requests.

Traceability and registry inspection are independent after those prerequisites. Legacy components are inspected, every legacy diagnostic remains visible, and registry errors do not stop subsequent components within the shared budgets. Registry success never makes a legacy candidate eligible.

## Transport boundary

Only public `https://registry-1.docker.io` is used for registry requests. Repository pull scopes are constructed from schema-validated, inventory-allowlisted canonical repositories. An unauthenticated request may receive a bearer challenge; its parameters must exactly identify:

- realm `https://auth.docker.io/token`;
- service `registry.docker.io`;
- scope `repository:<selected namespace/repository>:pull`.

Duplicate/unknown challenge parameters, other realms, services, repositories or actions fail. The token request is built locally, never by following an untrusted realm URL. A resource may be resubmitted once after authentication; a rejected token is not refreshed indefinitely. Tokens stay in memory and are never written to results. Environment proxies are disabled to avoid implicit proxy credentials/destination routing. TLS certificate/hostname verification remains enabled using Python's default trust context. Custom registry/auth endpoints, custom CA flags, insecure TLS and interactive login are not supported.

Default redirect handling is disabled. Token and manifest redirects are rejected. Config blobs may follow at most two HTTPS redirects to the same canonical registry blob path, or to the **exact** `production.cloudfront.docker.com` host under `/registry-v2/docker/registry/v2/blobs/sha256/`. That pull CDN is listed in [Docker's endpoint allowlist](https://docs.docker.com/desktop/enterprise/allow-list/); it is the only supported CDN destination. There is no wildcard CDN/object-storage allowance. Authorization is removed on origin changes and is not reintroduced by the redirect loop. Userinfo, non-default ports, fragments, control/whitespace characters, arbitrary hosts/paths, IP literals and localhost destinations are rejected. Signed redirect URLs exist only in memory and are never logged. A new Docker Hub endpoint requires a separate reviewed transport change; live checks do not relax the boundary automatically.

External `urls` fields in manifest, child/config or layer descriptors are never fetched. All object requests are GETs constructed from the allowed repository and SHA-256 digest.

## Verified metadata and limits

The client hashes raw manifest/config bytes, without JSON reserialization, and compares SHA-256 with the selected reference/descriptor. `Docker-Content-Digest`, when present, must match. Descriptor byte sizes are checked. JSON uses the same duplicate-key, UTF-8 and finite-number restrictions as the offline parser. Manifest/index `schemaVersion` must be integer 2. Required descriptor fields and manifest/index structure are checked. Content-Type parameters are accepted; response and body media types must agree when present.

Supported top-level types are OCI image manifest/index and Docker distribution manifest/list v2. The actual type must match candidate `kind`. Image config descriptors must use OCI image config or Docker container image config v1 and SHA-256. Config responses may use their descriptor media type or `application/octet-stream`; config `os/architecture[/variant]` must exactly match the selected platform, without implicit variant aliases/defaults.

For an index, each required platform selects exactly one matching descriptor. Selected child manifests are fetched and checked for hash, size, type and config-platform agreement. Missing or ambiguous selections fail. The declared platform set need not enumerate every index child: unselected attestations, other platforms and unknown/absent platform metadata are not fetched. Descriptor envelopes still require valid SHA-256 digests, nonnegative integer sizes and nonempty media types. Nested indexes or unsupported artifact types selected as required images are rejected; there is no recursive index traversal.

Layer descriptor structure is checked, but layers are **never downloaded**. Metadata success does not establish layer availability, future retention, publisher trust/provenance, application compatibility, inventory/release approval or deployment authorization.

| Bound | Value |
|---|---|
| Connection/socket I/O timeout | 10 seconds, reduced to remaining evaluation budget |
| Response read deadline | 10 seconds, checked between bounded reads; OS DNS/connection scheduling is subject to the platform |
| Per-response bytes | Token 32 KiB; manifest/index/child 2 MiB; config 1 MiB |
| Redirect hops | 2, config only |
| Attempts per hop | At most 2 for transient network/socket timeouts, HTTP 429, 500, 502, 503, 504 |
| Retry-After | Numeric 0–1 seconds; longer values, HTTP dates and invalid values return non-success immediately |
| HTTP exchanges per component/run | 24 / 256, including auth, redirects and retries |
| Component/run elapsed budget | 45 / 180 seconds, enforced before requests and between response reads |

Retries without Retry-After are immediate. TLS failures, invalid content, hash/size/type/platform errors and permanent HTTP failures are not retried. There is no background worker or unbounded sleep. Once a shared budget is exhausted, remaining components receive limit failures without requests. These are bounded read-only observations, not retention guarantees.

## Output version 2 and exit codes

Registry mode emits `outputSchemaVersion: 2`, `mode: registry`, an explicit metadata-only `scope`, the existing stage results, and `registry.artifacts`. That stage includes `components`, an input-ordered array of `{componentIndex, status}`; its diagnostics address `$.components[index].image`. Safe positional indices identify components without echoing arbitrary IDs, URLs or signed query strings. Results contain no timestamps; a separately documented live observation can record UTC time. Mocked responses produce deterministic results/order. Text output shows the same stage/component outcomes and reason codes as JSON.

| Exit | Meaning |
|---|---|
| `0`, `selected_rules_passed` | All selected offline and registry checks passed; no approval or deployment authorization implied |
| `1`, `validation_failed` | Input/policy failure, legacy ineligibility, missing artifact/config, unsafe challenge/redirect, malformed metadata or integrity/type/platform failure |
| `2`, `execution_error` | Existing usage/local execution failures, or registry auth, rate-limit, network/TLS/timeout, server/HTTP failure or exhausted budgets |

Execution errors take precedence over exit 1, but all independent traceability and registry diagnostics remain visible. Thus a legacy baseline with registry success returns 1; with a transport blocker it returns 2, never 0. The existing offline mode retains its original exit-code semantics and success wording.

Registry reason codes:

- `registry_artifact_missing`, `registry_config_missing`, `registry_auth_failed`, `registry_rate_limited`;
- `registry_network_error`, `registry_tls_error`, `registry_timeout`, `registry_server_error`, `registry_http_error`;
- `registry_response_too_large`, `registry_malformed_response`, `registry_digest_mismatch`, `registry_size_mismatch`;
- `registry_unsupported_media_type`, `registry_kind_mismatch`, `registry_platform_mismatch`, `registry_platform_missing`, `registry_platform_ambiguous`;
- `registry_unsafe_challenge`, `registry_unsafe_redirect`, `registry_limits_exhausted`.

Tests mock HTTP exchanges/streams and forbid Python sockets. They cover public auth, destination/credential boundaries, byte and request budgets, retries, manifests/indexes/configs, independent legacy failures, output agreement and the unchanged offline suite. A separate live record reports actual registry observations and blockers; historical baseline observations remain unchanged. GitHub Actions gates, Compose integration, promotion, legacy exceptions and provenance verification are outside this implementation.

See the [2026-10-19 live validation record](registry-validation-2026-10-19.md): all 12 inspections were attempted, but public token-endpoint TLS validation blocked completion. No successful live artifact verification is claimed.

The [TLS follow-up](registry-tls-diagnostics-2026-10-19.md) identifies an expired locally loaded public CA cross-certificate selected by Python, compares Schannel results, and provides the owner rerun command. The original full-run record is preserved.
