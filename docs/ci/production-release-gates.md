# Production Release Gates

## Scope

This document defines the release contract for container images and Azure Terraform production changes.

## Container release

Container builds use `.github/workflows/reusable-docker-build.yml` and must provide:

- immutable SHA-derived image tags;
- GitHub Actions cache for reproducible build acceleration;
- SBOM generation;
- build provenance attestation for pushed images;
- non-root runtime users in service Dockerfiles.

Pull requests build images without pushing. Main/tag events may push to GHCR.

## Azure authentication

Terraform workflows use GitHub OIDC with `azure/login` and require repository/environment variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Long-lived Azure client secrets must not be stored in the workflow.

## Terraform pull-request gate

`terraform-plan.yml` pins the plan to the pull request head SHA and stores the generated plan as workflow evidence.

Required checks before merge:

1. `terraform fmt -check -recursive`
2. `terraform init -input=false`
3. `terraform validate -no-color`
4. `terraform plan -input=false -out=tfplan`

## Production apply gate

Production apply is deliberately separate from the pull-request plan.

The operator starts `terraform-apply.yml` with:

- the full 40-character SHA of current `origin/main`;
- `confirm=APPLY`.

The workflow then:

1. verifies the requested SHA is exactly current `origin/main`;
2. creates a new plan for that exact immutable revision;
3. uploads the binary plan as an artifact;
4. waits at the GitHub `production` Environment gate;
5. downloads the exact artifact produced by the plan job;
6. verifies the plan is readable;
7. applies that exact `tfplan` without generating a replacement plan.

This prevents approval of one change set followed by application of a different change set.

## GitHub Environment requirement

Create a `production` Environment and configure required reviewers before enabling production apply. Environment approval is an external repository setting and is intentionally not encoded as application source code.

## Concurrency

Terraform jobs use a concurrency group and never cancel an in-progress apply. This prevents two production infrastructure mutations from racing against the same state.

## Failure behavior

The release pipeline fails closed when:

- confirmation is not `APPLY`;
- the requested commit is not current main;
- Azure OIDC authentication fails;
- Terraform validation fails;
- plan evidence is missing;
- production approval is denied;
- the reviewed plan cannot be read.
