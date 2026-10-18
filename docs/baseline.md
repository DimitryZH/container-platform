# Online Shop baseline candidate

[Candidate](../releases/baseline-2026-10-18.json) `baseline-2026-10-18-001` captures the complete required set for profile `sre-dev-online-shop-v1`: **10 application services, one runtime dependency (Redis), and one auxiliary load generator**. It is not approved, approval-eligible by default, deployment-authorized or compatibility-tested. All artifacts retain legacy traceability gaps. This is repository configuration discovery, not an observation of a running cluster.

## Reproducible discovery and selected profile

| Repository | Inspected full commit | Pinned discovery files |
|---|---|---|
| SRE Platform | `d21d2c2bc9ae01504f6973a01168bb747cb0e5f9` | [Chart](https://github.com/DimitryZH/sre-platform/blob/d21d2c2bc9ae01504f6973a01168bb747cb0e5f9/charts/platform/Chart.yaml), [defaults](https://github.com/DimitryZH/sre-platform/blob/d21d2c2bc9ae01504f6973a01168bb747cb0e5f9/charts/platform/values.yaml), [dev override](https://github.com/DimitryZH/sre-platform/blob/d21d2c2bc9ae01504f6973a01168bb747cb0e5f9/environments/dev/values/platform.yaml), [Argo application](https://github.com/DimitryZH/sre-platform/blob/d21d2c2bc9ae01504f6973a01168bb747cb0e5f9/environments/dev/argocd/apps/online-shop-dev.yaml), [packaged dependency](https://github.com/DimitryZH/sre-platform/blob/d21d2c2bc9ae01504f6973a01168bb747cb0e5f9/charts/platform/charts/online-shop-service-0.1.0.tgz) |
| Application Modernization Lab | `9960484b5c7f1568688fa6fc8eb256976d6c18a4` | [Canonical Online Shop README](https://github.com/DimitryZH/application-modernization-lab/blob/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/README.md), [application manifests](https://github.com/DimitryZH/application-modernization-lab/blob/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/kubernetes/online-shop.yaml), [image mapping](https://github.com/DimitryZH/application-modernization-lab/blob/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/images.lock.json), [upstream record](https://github.com/DimitryZH/application-modernization-lab/blob/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/UPSTREAM.md) |
| CI Build Platform | `5c9ab4626f7b6e4f3f7104299eb8a0fecfe8f185` | [Publication workflow](https://github.com/DimitryZH/ci-build-platform/blob/5c9ab4626f7b6e4f3f7104299eb8a0fecfe8f185/.github/workflows/build-and-push.yml) |
| Upstream microservices-demo v0.10.4 | `474d0f2eb73d97525234995a40d7781b7923a07d` | [Release manifests](https://github.com/GoogleCloudPlatform/microservices-demo/blob/474d0f2eb73d97525234995a40d7781b7923a07d/release/kubernetes-manifests.yaml), [optional assistant behavior](https://github.com/GoogleCloudPlatform/microservices-demo/blob/474d0f2eb73d97525234995a40d7781b7923a07d/src/frontend/handlers.go) |

Selected inputs are the committed `charts/platform` chart and packaged `online-shop-service-0.1.0.tgz`, with umbrella defaults followed by `environments/dev/values/platform.yaml`, release name `online-shop-dev`. Helm recursively merges maps; later scalar/list values replace earlier ones. No CLI image overrides or dependency update were applied. All tags are explicit, so the subchart's appVersion fallback is unused. The dev overlay changes four core tags to `v0.10.4`; the remaining application/helper tags inherit `v0.10.4`, and Redis inherits `7-alpine`. The frontend is a Rollout; the remaining selected components are Deployments.

Local `helm template` (Helm v3.17.3) confirmed the effective image set. The packaged and sibling subchart values/deployment templates match after line-ending normalization; the committed package was used. Reproduction requires downloading the pinned chart tree and dev values to an isolated local directory, then running:

```sh
helm template online-shop-dev charts/platform -f environments/dev/values/platform.yaml
```

This renders repository configuration only. No Helm install, Kubernetes API, gcloud or deployment was used.

## Inventory format and immutable binding

The [inventory](../inventories/online-shop-sre-dev-v1.json) is a small JSON format (`inventoryVersion: 1`), not a production validator framework. Top-level `application`, `profile`, owner/status and `selection` identify its scope. `sources` maps discovery IDs to full-revision file URLs. Each component declares:

- stable `id`, `role` (`application-service`, `runtime-dependency`, `auxiliary`) and artifact `class`;
- `required: true`, exact `allowedRepositories` and `targetPlatforms`;
- `sreAlias` and `sreWorkload` to reconcile chart aliases with component identity;
- `publication` evidence category and `discoverySources` keys into `sources`.

All entries are required for this profile; no implicit optional members are permitted. `excludedComponents` records deliberate boundaries. Container Platform maintainers own inventory/allowlist review with application and producer owners under the [canonical contract](release-contract.md). This proposed baseline is reviewable evidence, not a claim that external owners have already approved it.

The updated inventory with canonical AML discovery links was committed separately at **`480bd0a5769ae61313c4bd0da846ba3a6fc0ce51`**. The candidate records that full SHA and `inventories/online-shop-sre-dev-v1.json`; it does not refer to its own commit. The referenced inventory commit is retained as an ancestor of the candidate update. Its committed blob exactly matches the selected inventory content. Consumption must use the candidate's immutable repository revision and path as well as this inventory reference.

## Selected images and registry observations

All repositories below have prefix `docker.io/dmitryzhuravlev/`. All 12 selected references resolved separately by immutable digest with HTTP 200; SHA-256 of returned manifest bytes matched the reported digest. Every selected object is `application/vnd.docker.distribution.manifest.v2+json`, contract kind `manifest`, with `linux/amd64` confirmed through its referenced config blob. No selected object is an index, so no child-index selection or digest substitution occurred. Other platforms are not claimed.

| Component | Role | Repository suffix | Observed discovery tag |
|---|---|---|---|
| `frontend` | application-service | `online-shop-frontend` | `v0.10.4` |
| `cartservice` | application-service | `online-shop-cartservice` | `v0.10.4` |
| `checkoutservice` | application-service | `online-shop-checkoutservice` | `v0.10.4` |
| `paymentservice` | application-service | `online-shop-paymentservice` | `v0.10.4` |
| `adservice` | application-service | `online-shop-adservice` | `v0.10.4` |
| `currencyservice` | application-service | `online-shop-currencyservice` | `v0.10.4` |
| `emailservice` | application-service | `online-shop-emailservice` | `v0.10.4` |
| `loadgenerator` | auxiliary | `online-shop-loadgenerator` | `v0.10.4` |
| `productcatalogservice` | application-service | `online-shop-productcatalogservice` | `v0.10.4` |
| `recommendationservice` | application-service | `online-shop-recommendationservice` | `v0.10.4` |
| `redis-cart` | runtime-dependency | `online-shop-redis-cart` | `7-alpine` |
| `shippingservice` | application-service | `online-shop-shippingservice` | `v0.10.4` |

The [registry observation record](../releases/baseline-2026-10-18.observations.json) contains each full digest, config digest, UTC timestamp, media type and lookup result; the candidate contains the selected immutable references. Primary observations were made **2026-10-18T14:12:52Z–2026-10-18T14:13:01Z**. Tags are discovery hints only and may move. Availability is a point-in-time observation, not a retention guarantee. Registry tokens and authentication/redirect headers are excluded from all artifacts.

## Ownership, source/build records and gaps

All 11 application/helper manifest digests exactly match the corresponding public upstream v0.10.4 manifests at observation time. They are third-party upstream artifacts republished under the selected namespace, not demonstrated first-party builds. Publication namespace, application role, and traceability are independent. The first-party allowlist has no entries in this baseline; future genuinely owned builds must be reviewed and mapped separately. Matching content establishes artifact equivalence at observation, not an authenticated source commit or producing run.

Redis is a third-party runtime dependency even though its selected repository is also named `online-shop-*`. Config/history metadata identifies Redis 7.4.8 and a Redis source-download build recipe. Looking up the selected digest under `docker.io/library/redis` returned HTTP 404; an exact official-image identity and publication chain remain unresolved. The namespace copy itself is accessible and its platform is known. No substitution with today's official Redis tag was made.

All selected config labels were absent; no source/build label mapping was available. The inspected CI workflow builds/publishes `ci-artifact:latest`, not these Online Shop images. Available successful workflow records at source revision `72771679394ada5a57a9db89702d4a0368f6da2e` use the same unrelated publication mapping; no producing Online Shop run was identified. The [historical workflow](https://github.com/DimitryZH/ci-build-platform/blob/72771679394ada5a57a9db89702d4a0368f6da2e/.github/workflows/build-and-push.yml) is context only and is not assigned to any component.

AML now contains canonical Online Shop application/helper source, Dockerfiles and build inputs under `workloads/online-shop/`, merged through [PR #20](https://github.com/DimitryZH/application-modernization-lab/pull/20) at `9960484b5c7f1568688fa6fc8eb256976d6c18a4`. Its pinned image mapping and Kubernetes manifest confirm the same 12 component repositories and digests as this candidate. Redis remains an external runtime dependency. AML is the canonical source location and additional mapping evidence; the pinned SRE chart/dev configuration remains the selection authority for `sre-dev-online-shop-v1`. The AML mapping retains its original Container Platform baseline revision as historical context, not a moving reference to this update.

The existing images were not built from the canonical AML path and do not contain its Online Shop branding or other local source adaptations, including the optional Spanner default. Future CI integration must build the adapted sources and record actual producing revisions/runs and new digests. Creating a canonical source location does not establish historical source/build associations or resolve Redis lineage.

Accordingly all 12 components use `traceability.status: legacy`, explicit null source/build and a reason. The 11 matched upstream components retain a pinned publisher manifest reference as context; Redis's exact publisher artifact reference remains null. No CI run or source SHA is invented. Release-wide `upstreamVersion` remains null because the mixed bundle includes Redis and the tag/content match is not a verified common producing source version. Metadata and content comparisons are not cryptographic provenance. No legacy exception or release approval is granted.

## Discrepancies and boundaries

- Upstream and AML use `cartservice`, `checkoutservice`, `paymentservice` and `redis-cart`; SRE workloads are `cart`, `checkout`, `payment` and `redis`. Inventory mappings make these differences explicit; SRE addresses connect the corresponding selected workloads.
- The load generator is included because the selected SRE chart instantiates it without a disabling condition. It is auxiliary, not an eleventh application service. An application-only profile would need a separately reviewed inventory.
- `SHOPPING_ASSISTANT_SERVICE_ADDR` alone does not enable the optional assistant; `ENABLE_ASSISTANT` is absent, and no assistant workload is selected. Upstream source enables the feature only when that flag equals true. This conclusion is limited to repository configuration.
- The SRE render also includes `break-500` (`go-httpbin`), an SRE failure-injection tool. It is deliberately excluded along with ingress, monitoring, rollout controllers and other SRE infrastructure. Application release membership is not every image in the umbrella render.
- The upstream load-generator BusyBox init container is absent from the selected SRE template/render, so BusyBox is not a required artifact for this profile.
- The canonical AML profile has the same image membership/digests but different configuration: internal service/workload names, a frontend Deployment instead of the SRE Rollout, internal-only Services, disabled API token mounts, explicit linux/amd64 scheduling, a local frontend platform and a disabled-assistant loopback sentinel. Its upstream record documents source adaptations. These profiles are not configuration-equivalent or runtime-compatibility-tested. The legacy SRE Argo application and stage/prod overrides are also outside the selected input set.
- Container Platform's empty Dockerfiles/scripts and placeholder Compose remain unchanged and supply no discovery evidence.

No required member, selected digest or target platform is unresolved for this profile. Remaining lineage, owner review, service compatibility, feature/configuration correctness and deployment readiness are blockers to future approval/promotion, not silently satisfied by structural or registry success. Other profiles require their own reconciliation.

## Validation and limits

Local checks: Draft 2020-12 schema self-check; candidate and synthetic example validation; unique IDs; exact required membership; exact repository/class/platform allowlists; inventory bytes retrieved at the recorded commit/path and ancestry; digest syntax/kind and observation consistency; effective SRE image-set comparison; relative documentation links; diff review and `git diff --check`.

Canonical-source follow-up checks: merged AML revision/main availability; all four pinned files retrieved; exact comparison of all 12 image mappings and legacy fields; schema self-check and candidate/example validation; renewed allowlist/membership checks; inventory blob bytes and ancestry at the new recorded revision; current naming/path scan and relative links. No new registry requests were needed or made for this discovery-link update.

Registry checks are separately recorded point-in-time read-only observations, not an implemented production policy gate. No image layers were pulled, containers run, images changed, cloud resources created or related repositories modified. No integration, Compose, Kubernetes or runtime validation was performed. Baseline completeness does not establish service compatibility or deployment readiness.
