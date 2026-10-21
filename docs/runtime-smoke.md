# Opt-in Online Shop runtime smoke

`container-smoke` / `python -m container_platform.smoke` runs one temporary Compose project. This is integration evidence for a selected image set, not a long-lived environment or SRE deployment. The existing validator and renderer never start Docker.

## Prerequisites and execution

Install the package with Python 3.11+ and Git. Supply local inventory Git objects, a local Docker Engine with native Linux/amd64 runtime, and Docker Compose v2 supporting JSON config/ps and pull policy options. Only local Unix socket/named pipe transports are supported; SSH/TCP remote contexts are rejected before mutations. The runner does not install, start or reconfigure system tools. Loopback port 8080 must be free; a conflict never stops another project. A race after the port check is reported as startup failure.

```text
python -m container_platform.smoke candidate.json --repository . --profile aml-online-shop-compose-v1 --execute
container-smoke releases/baseline-2026-10-18.json --repository . --profile aml-online-shop-compose-v1 --execute --inspection-only
```

Without `--execute`, no candidate read or Docker call occurs (exit 2). Ordinary execution requires successful offline validation. Inspection-only permits exclusively the renderer's existing legacy traceability failures; all other prerequisites must pass. It preserves the full negative offline result and grants no eligibility exception. Baseline eligibility remains negative even if runtime succeeds.

The input is capped at 1 MiB and read once into a private snapshot. The existing renderer validates that snapshot and generates the only accepted Compose file. Report SHA-256 identifies its exact bytes. No external Compose file is accepted. All 12 required services use candidate `repository@sha256:...` references and `linux/amd64`. Pull downloads these images; up prohibits builds and further pulls. Images remain cached after the test.

Each execution generates a unique `cp-smoke-<random hex>` project and checks its resource labels before creating resources. Commands use an explicit Compose file, project directory, empty env file and project name. Inherited `COMPOSE_*` selectors and `DOCKER_DEFAULT_PLATFORM` are removed. Docker client context/transport configuration remains the operator's responsibility; no credentials or environment are emitted in reports. Frontend-only loopback exposure, ephemeral Redis storage and the AML-derived [profile](compose-rendering.md) are preserved. No foreign networks, named volumes, host binds, Docker socket, privileged containers or auxiliary images are requested.

## Budgets and checks

All retries share monotonic deadlines; they never restart a stage budget.

| Operation | Limit |
|---|---:|
| Overall coordination | 1,200 seconds |
| Prerequisites | 30 seconds |
| Pull | 600 seconds |
| Startup | 120 seconds |
| Readiness | 180 seconds |
| Entire semantic smoke sequence | 60 seconds |
| Each HTTP request, including redirects/body | 10 seconds |
| Diagnostics | 20 seconds |
| Cleanup, including resource verification | 120 seconds |

Work stops before the overall deadline to reserve diagnostics and cleanup time. Child process termination/reaping can add at most ten seconds per timed-out process; one HTTP socket read can add up to one second. These are bounded termination grace, not retry budgets. Process output is capped at 1 MiB per command; HTTP responses at 2 MiB. Raw process output, response bodies and cookies are not included in the report. Diagnostics collect only sanitized service states; no application logs or payment/session payloads are published.

Readiness requires exactly 12 expected service records, the exact image references, every service running and a frontend catalog response. This establishes process state plus frontend availability, not independent health evidence for every backend. Host-side semantic assertions then exercise catalog browsing, a selected product, empty/add/remove/re-add cart operations and synthetic checkout. A final service-state check detects an exit during the sequence. Loadgenerator traffic is independent and cannot satisfy these assertions.

Routes/forms and assertions derive from immutable AML revision [`9960484b5c7f1568688fa6fc8eb256976d6c18a4`](https://github.com/DimitryZH/application-modernization-lab/tree/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/src): frontend routes/handlers, product/cart/order templates and loadgenerator. Pinned AML source implements a pretend charge and dummy email default; legacy image runtime behavior remains unverified until live checks pass. The runner uses a standard test card number, reserved `.invalid` email and synthetic address, without payment/mail credentials. It checks selected product ID, cart quantity, order confirmation/tracking and post-checkout cart clearance. It does not fetch browser assets, execute scripts or follow redirects outside `http://127.0.0.1:8080`. This is a narrow demo flow, not verification of real payment/email systems or image provenance.

## Report and exit codes

Stdout contains a separate versioned JSON report: release ID and exact candidate-byte SHA-256, inventory reference, Compose profile, exact image set, inspection mode, original offline validation, check/reason codes, sanitized service states, smoke assertions, runtime outcome, cleanup outcome and overall exit code. Input or resource failures may leave unavailable identity fields null. No output paths, environment dump or raw Docker errors are included.

| Exit | Meaning for this command |
|---|---|
| 0 | Runtime flow and verified cleanup succeeded |
| 1 | Release validation or application/semantic failure |
| 2 | Missing opt-in, prerequisite/execution error, or cleanup failure after runtime success |
| 3 | Deadline/request timeout |
| 130 | Handled interruption |

These codes do not change validator/render codes. A report can have runtime success and exit 2 when cleanup fails. A cleanup failure never overwrites the original runtime failure; inspect both fields. No outcome grants release approval or deployment authorization.

## Cleanup and interruptions

After an attempted startup, a finally block runs project-scoped `down --volumes --remove-orphans --timeout 10` and checks containers, networks and volumes by the unique project label. No global prune or image deletion is performed. Before startup, cleanup is `not_required`: only read operations and possibly an image pull have occurred. Failed cleanup is explicit and cannot produce overall success. Ctrl+C and SIGTERM are handled; a second interruption can prevent completion and is reported where possible.

Power loss, forced process termination or an unavailable Engine can prevent cleanup. Each report includes `project` and an exact `manualCleanupPowerShell` command using only that project's label, independent of temporary files. Preserve the project identifier if terminating externally. If no final report was emitted, locate only labels starting with `cp-smoke-`, identify the interrupted run, and substitute its exact identifier below; never substitute another project or omit the filter:

```powershell
$smokeProject = 'cp-smoke-<exact-run-hex>'
$smokeLabel = "label=com.docker.compose.project=$smokeProject"
docker ps -aq --filter $smokeLabel | ForEach-Object { docker rm -f -v $_ }
docker network ls -q --filter $smokeLabel | ForEach-Object { docker network rm $_ }
docker volume ls -q --filter $smokeLabel | ForEach-Object { docker volume rm $_ }
```

## Validation evidence

Automated tests use mock Docker/HTTP adapters and isolated local Git fixtures; the ordinary suite requires no Docker or registry requests. Installed-wheel checks exercise both smoke interfaces outside checkout without runtime opt-in. No mandatory runtime PR job is added. Live results are recorded separately below; runtime integration cannot be claimed from mocks or static configuration alone.

### Live observation: 2026-10-21

- Implementation: `f5ea3b8e2b2f48e014cd985c3a20412bbdce0956`, installed-wheel code/profile/schema matched before execution.
- Input: `releases/baseline-2026-10-18.json`, byte SHA-256 `005293954afebcf1581116b2e7e6bc5ec6313a658496c88df3186aa84a46a99f`; profile `aml-online-shop-compose-v1`, explicit inspection-only.
- Render passed; prerequisites failed with `docker_engine_unavailable`. Runtime outcome: `execution_error`, exit 2. Pull/startup and HTTP smoke were not attempted.
- Cleanup: `not_required`; no project resources were created.
- Offline validation remained exit 1 with 12 `legacy_ineligible` failures. No approval or eligibility was granted.

One bounded live attempt was made. Provide a running local Linux/amd64 Docker Engine and repeat the documented baseline command to verify readiness, semantic checkout and cleanup after actual startup. Those live behaviors remain unverified; mock tests do not replace them.

### Live observation: 2026-10-21 — successful follow-up

- Implementation: `9f28d77df64106ddefa52c2934c132d9cb022bb0`; installed code, profile and schema matched the commit before execution.
- Input: `baseline-2026-10-18-001`, `releases/baseline-2026-10-18.json`, byte SHA-256 `005293954afebcf1581116b2e7e6bc5ec6313a658496c88df3186aa84a46a99f`; inventory `inventories/online-shop-sre-dev-v1.json` at `480bd0a5769ae61313c4bd0da846ba3a6fc0ce51`; profile `aml-online-shop-compose-v1`, inspection-only.
- One bounded run passed in 45.8 seconds: prerequisites, pull, startup, all 12 services running with exact candidate digest references, readiness and semantic catalog/product/cart/synthetic-checkout assertions. Runtime exit 0.
- Cleanup passed; an independent label-scoped query confirmed zero remaining project containers, networks and volumes. Cached images were retained.
- Offline validation remained exit 1 with 12 `legacy_ineligible` failures. The earlier negative observation is retained above.

This confirms the selected demo flow for these exact inputs in one run. It does not establish production readiness, performance, full backend coverage, provenance, release approval or deployment authorization. No mandatory runtime CI job was added.
