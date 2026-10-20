# Container Platform

Container Platform defines the contract for an **Online Shop** release composed of independently built container images. Offline release validation and opt-in Docker Hub metadata verification are implemented.

The project does not build application code or operate a Kubernetes cluster. Its target workflow turns images published to Docker Hub into a versioned release bundle pinned by digests for review, validation and promotion to the SRE Platform.

> **Status:** Release contract v1 is defined; a digest-pinned baseline candidate and profile inventory are available for review. Offline and opt-in registry metadata checks, PR checks and offline Compose rendering are available; required-check rules are owner-configured. Runtime integration and SRE handoff remain planned.

## Why this platform exists

The Online Shop consists of multiple independently built services. A successful build of each image does not by itself prove that the images form one compatible application release.

The target platform will provide the release boundary below; architecture, the v1 schema, a synthetic example and a discovered baseline candidate are available:

- defines which images and immutable digests make up a release;
- validates that the release is complete and follows container-release rules;
- provides a local Compose-based integration harness for the published images;
- hands an approved release to the SRE Platform for GitOps deployment.

## Platform ecosystem

```mermaid
flowchart LR
    AML["Application Modernization Lab\nsource code and Dockerfiles"]
    CI["CI Build Platform\nbuild, test, scan, publish"]
    DH["Docker Hub\nindividual online-shop images"]
    CP["Container Platform\nrelease contract and validation"]
    SRE["SRE Platform\nGitOps, Argo CD, GKE"]

    AML --> CI
    CI --> DH
    CI -.->|build metadata| CP
    CP -.->|planned release control| SRE
    DH -->|direct image distribution| SRE
```

The two paths into the SRE Platform have different roles:

- Docker Hub is the data path: Kubernetes nodes pull the approved images from `dmitryzhuravlev/online-shop-*`.
- Container Platform is the release-control path: it identifies the exact image digests that are approved together as one Online Shop release.

The diagram describes target relationships. Offline, opt-in Docker Hub metadata, PR checks and Compose rendering are implemented; required-check configuration remains an owner action. Runtime integration and SRE handoff remain planned.

## Responsibilities

| Component | Owns | Does not own |
|---|---|---|
| Application Modernization Lab | Online Shop source code, Dockerfiles, instrumentation | image distribution or runtime deployment |
| CI Build Platform | per-service build, tests, scans, source/build metadata, Docker Hub publication | release approval or Kubernetes deployment |
| Container Platform | release manifest, release-policy validation, published-image integration checks | application source, image builds, a long-lived environment, or Kubernetes deployment |
| SRE Platform | environment-specific GitOps configuration, rollout, rollback, observability, and runtime operation | rebuilding or retagging the approved release |

## Planned release lifecycle

1. CI Build Platform builds and publishes individual Online Shop images to Docker Hub.
2. A full release manifest records immutable digests and per-component source/build references under the contract, with explicit legacy gaps and separate third-party dependency rules.
3. Container Platform validates the manifest and the published image references through pull-request checks.
4. A release PR may represent approval only after authorized review and all documented mandatory checks pass; merge alone is not validation. These gates are not implemented yet.
5. A promotion change updates the SRE Platform's GitOps configuration to the exact digests from that release.
6. Argo CD applies the GitOps change, and GKE pulls the same images from Docker Hub.

Container Platform does not sit in the network path between GKE and Docker Hub. It improves the decision and audit path **before** deployment.

## MVP scope

The first MVP will provide:

- a machine-readable Online Shop release manifest;
- immutable Docker Hub digest references for all required services;
- deterministic release-policy checks;
- Docker Hub availability verification;
- a Compose-based validation path that uses published images rather than local builds;
- a documented, reviewable handoff to the SRE Platform;
- a rollback model based on selecting a previously approved release.

The MVP intentionally excludes a new registry, a Kubernetes cluster, a long-lived development or staging environment, and automatic deployment.

## Related projects

- [Application Modernization Lab](https://github.com/DimitryZH/application-modernization-lab)
- [CI Build Platform](https://github.com/DimitryZH/ci-build-platform)
- [SRE Platform](https://github.com/DimitryZH/sre-platform)
- [Docker Hub profile](https://hub.docker.com/u/dmitryzhuravlev)

See the [roadmap](docs/roadmap.md) for the planned implementation sequence.

## Release Contract artifacts

- [Architecture](docs/architecture.md)
- [Canonical release contract](docs/release-contract.md)
- [Version 1 JSON Schema](schemas/release-v1.schema.json)
- [Non-deployable synthetic example](examples/release-v1.example.json)

The existing Dockerfiles and build/push scripts are empty; root Compose contains legacy local-build placeholders. Use the [generated Compose workflow](docs/compose-rendering.md) for the supported profile. The scaffold is not a working release pipeline.

## Baseline candidate

See the [baseline discovery](docs/baseline.md), [profile inventory](inventories/online-shop-sre-dev-v1.json), [candidate](releases/baseline-2026-10-18.json), and [registry observations](releases/baseline-2026-10-18.observations.json). The selected dev profile includes 10 application services, Redis and a load generator. These namespace-published artifacts are third-party upstream copies/dependencies with legacy traceability gaps; this candidate is not approved or deployment-authorized.

Canonical Online Shop source and Dockerfiles are available in [Application Modernization Lab](https://github.com/DimitryZH/application-modernization-lab/blob/9960484b5c7f1568688fa6fc8eb256976d6c18a4/workloads/online-shop/README.md). Its adapted profile confirms the selected image mappings; the pinned SRE configuration remains authoritative for this baseline. Existing images were not built from that canonical path and retain legacy source/build gaps.

## Offline validation

Install with `python -m pip install .` (Python 3.11+ and Git), then run:

```text
python -m container_platform releases/baseline-2026-10-18.json --repository . --format json
```

The committed baseline passes structure and inventory checks but returns exit code **1** with 12 legacy-ineligibility diagnostics. Passing offline rules does not establish approval or deployment authorization. See [installation, checks and output/exit-code semantics](docs/offline-validation.md).

## Optional Docker Hub verification

Add `--registry` to inspect immutable manifest/config bytes and declared platforms after local prerequisites pass. Legacy failures remain visible and keep the baseline unsuccessful. No layers are downloaded, and metadata success grants no approval or deployment authorization. See [transport restrictions, output v2 and limits](docs/registry-verification.md).

## Pull request checks

[GitHub Actions validation](docs/pull-request-validation.md) runs tests/package checks on Python 3.11 and 3.13 and a stable release check. New candidates receive opt-in registry validation; merged release files are immutable. No candidate changes produces an explicit successful result. The historical legacy baseline remains a negative regression fixture. Workflow availability does not configure required merge checks or grant release approval.

## Compose configuration

[Offline Compose rendering](docs/compose-rendering.md) generates all 12 digest-pinned services from a candidate and the packaged AML profile. Legacy baseline rendering requires explicit `--inspection-only`; its validation remains negative. Static `docker compose config` checks do not establish runtime compatibility or release approval.
