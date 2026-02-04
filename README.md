# Container Platform

This project provides a comprehensive collection of container images for microservices workloads of the Online Shop application.  

Images are built using best practices:
- multi-stage builds
- pinned base images
- small footprint
- tagged with Git commit SHA for immutability

## Local Development

```bash
# Build all images locally
./scripts/build-all.sh

# Run local stack
docker-compose up -d
```

## Docker Hub Publishing

Images are pushed to Docker Hub via CI Build Platform and consumed by SRE Platform as immutable artifacts:

[CI Build Platform](https://github.com/DimitryZH/ci-build-platform)

[SRE Platform](https://github.com/DimitryZH/ecommerce-observability-platform)

[Docker Hub Repository](https://hub.docker.com/u/dmitryzhuravlev)

```bash
flowchart LR
    CI[CI Build Platform] -->|Build and Tag Images| DockerHub[Container Platform]
    DockerHub -->|Provide Images to Deploy| SRE[SRE Platform on GKE]

    subgraph Platforms Ecosystem
        CI
        DockerHub
        SRE
    end

    style CI fill:#E5F2FF,stroke:#1E70BF,stroke-width:2px
    style DockerHub fill:#FFF2E5,stroke:#BF5E1E,stroke-width:2px
    style SRE fill:#E5FFE5,stroke:#1EBF2F,stroke-width:2px
```
