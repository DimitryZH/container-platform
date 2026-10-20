# Roadmap

## Roadmap principle

Container Platform is a narrow release-management layer for the Online Shop container workload. It turns independently published OCI images into a reviewed, immutable, and reusable release definition without taking ownership of source code, CI execution, registry hosting, or Kubernetes runtime operation.

The implementation sequence follows the release path: define the release contract first, establish a real baseline from the existing Docker Hub images, make the contract executable through validation, prove that the published images work together, and then hand approved releases to the SRE Platform through GitOps.

The platform must remain focused on image-release composition and validation. It must not become a second CI system, a replacement container registry, a generic deployment platform, or a second SRE environment.

---

## Current baseline and boundaries

- Online Shop images already exist in the public Docker Hub namespace `dmitryzhuravlev/online-shop-*`.
- CI Build Platform is the producer of independently built image artifacts.
- SRE Platform currently pulls images from Docker Hub and deploys them through GitOps to Kubernetes.
- Container Platform will add a release-control path: a validated release manifest identifies which immutable image digests may be promoted together.
- Secure Delivery Platform uses a separate workload, Astronomy Shop, and Artifact Registry. It is intentionally outside this MVP.

---

## Milestone 1 — Release Contract

**Goal:** Define the platform's responsibilities and the version-one Online Shop release contract before adding automation.

### Scope

- replace the legacy README with the Container Platform purpose, boundaries, and ecosystem relationship;
- document the producer, distribution, release-control, and runtime responsibilities;
- define the release-manifest schema and required metadata;
- define service inventory and allowlist ownership and rules; establish the verified inventory in Milestone 2;
- define the promotion and rollback semantics at a repository level;
- define MVP non-goals and trust boundaries.

Artifacts: [architecture](architecture.md), [canonical contract](release-contract.md), [v1 schema](../schemas/release-v1.schema.json), and [non-deployable example](../examples/release-v1.example.json). Offline validation and opt-in registry metadata verification are implemented; PR validation workflows are available; owner-required checks and integrations remain separate work.

### Target outcome

A reader can distinguish a successful image build from an approved multi-service release, and can identify which platform owns each decision.

---

## Milestone 2 — Baseline Release Inventory

**Goal:** Capture the existing Online Shop images as the first reproducible release candidate.

### Scope

- inventory the required Online Shop services in Docker Hub;
- resolve and record the immutable digest of each required image;
- record the available source revision and CI build references;
- create the first release candidate from the existing published image set; compatibility requires later integration checks;
- document any image or metadata gaps instead of inventing provenance.

### Target outcome

The repository contains one real, fully enumerated Online Shop release candidate based on published images rather than placeholder tags.

Baseline artifacts proposed for review: [discovery and limitations](baseline.md), [profile inventory](../inventories/online-shop-sre-dev-v1.json), and [digest-pinned candidate](../releases/baseline-2026-10-18.json). Registry availability and structural consistency do not establish compatibility or approval.

---

## Milestone 3 — Executable Release Validation

**Goal:** Turn the release contract into deterministic policy checks.

The [offline CLI](offline-validation.md) implements strict JSON/schema checks, immutable local inventory evaluation and traceability eligibility with automated tests. [Opt-in Docker Hub metadata verification](registry-verification.md) adds bounded manifest/config integrity and platform checks. Milestone closure remains a separate owner decision; PR gates and integration validation remain future work.

### Scope

- implement a local validation CLI and unit tests;
- validate manifest structure, required services, and duplicate or missing entries;
- enforce exact first-party allowlist entries under `dmitryzhuravlev/online-shop-*` and a separate third-party dependency allowlist;
- prohibit mutable image selection such as `latest` and require immutable digest references;
- validate source and CI metadata where it is required by the contract;
- verify that each referenced manifest digest resolves in Docker Hub, checking manifest/config integrity and target-platform metadata without downloading layers;
- expose clear, fail-closed error messages.

### Target outcome

Invalid, incomplete, mutable, or unavailable releases cannot be presented as valid candidates.

---

## Milestone 4 — Pull Request Gates & Integration Validation

**Goal:** Make release validation repeatable and prove that the published images compose into an application.

[Pull request checks](pull-request-validation.md) provide tests/package and new-candidate validation. Required-check settings are owner-configured; Compose integration remains unimplemented, so this milestone is not complete.

### Scope

- add GitHub Actions checks for release-manifest pull requests;
- run static validation and registry-resolution checks in CI;
- add a Compose definition that consumes published images only and never builds from local source;
- render and validate the Compose configuration from a selected release manifest;
- add a bounded integration smoke test when the full baseline can be run reliably in an ephemeral runner;
- document local validation commands for contributors and operators.

### Target outcome

Merge is protected by repeatable release checks, and the approved image set can be validated as a coherent local application without rebuilding it.

---

## Milestone 5 — GitOps Release Handoff

**Goal:** Connect an approved Container Platform release to SRE Platform deployment without bypassing GitOps review.

### Scope

- define a stable reference from SRE GitOps configuration to an immutable Container Platform release;
- implement a release-to-SRE rendering or promotion helper;
- create a reviewable promotion pull request that updates SRE image digests as one release;
- validate that SRE deployment values match the selected release manifest;
- document environment selection, promotion, and rollback to an earlier approved release;
- keep deployment authorization and Argo CD synchronization in the SRE Platform.

### Target outcome

SRE Platform can promote a named, approved release through a reviewed Git change, while GKE continues to pull the same images directly from Docker Hub.

---

## Milestone 6 — MVP Release & Operational Documentation

**Goal:** Package the validated release-control path as a reproducible MVP.

### Scope

- align README, architecture, release contract, and runbook with implemented behavior;
- document normal release creation, validation failure, promotion, and rollback paths;
- verify public links, examples, and Docker Hub references;
- capture validation evidence that is safe to publish;
- prepare release notes and tag criteria for a separate operator decision.

### Target outcome

The repository tells one accurate end-to-end story: independently built images become a validated container release, which is then promoted into the SRE Platform through GitOps.

---

## Completion criteria

The MVP is complete when it can demonstrate that:

1. every required Online Shop service is represented by an immutable Docker Hub digest;
2. a release manifest records the image set and available build/source identity;
3. executable checks reject incomplete, mutable, malformed, or unavailable releases;
4. the published image set can be rendered and validated through Docker Compose without local builds;
5. an approved release can be selected for SRE deployment through a reviewable GitOps change;
6. the SRE deployment image digests can be verified against the selected release manifest;
7. rollback selects a prior approved release without rebuilding or retagging images;
8. CI Build, Container Platform, and SRE Platform retain separate and understandable responsibilities.

## Deferred after MVP

- automatic release creation from CI Build Platform;
- automatic promotion or rollback;
- cross-registry import into Artifact Registry;
- image signing and attestation verification beyond metadata references supplied by CI;
- a dedicated API, database, or long-running control-plane service;
- a permanent Container Platform development, staging, or Kubernetes environment.
