# Production Release Governance

## Scope

This document defines the repository-level release controls for application containers and Terraform-managed Azure infrastructure.

## Application containers

Pull requests build both `agent-runtime` and `gateway` images without publishing them. Pushes to `main`, version tags, and explicit manual runs may publish images to GHCR.

The reusable Docker workflow must preserve these controls:

- GHCR authentication uses `GITHUB_TOKEN`; no long-lived registry password is stored.
- Docker Buildx and the GitHub Actions cache are used for repeatable CI performance.
- SBOM generation is enabled.
- Max-mode build provenance is enabled.
- Published images receive GitHub build provenance attestations bound to the image digest.
- Published image identity includes a commit-SHA-derived tag and immutable digest.
- Runtime containers execute as a non-root user.
- Dependency installation uses the locked project state with `uv sync --frozen --no-dev`.

Production deployment workflows should consume an immutable image digest instead of a mutable branch tag.

## Azure authentication

Terraform uses GitHub Actions OIDC federation through `azure/login`.

The GitHub Environments used by Terraform are expected to define:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

No Azure client secret should be required by the workflow.

## Terraform plan gate

Terraform changes targeting production must first pass the pull-request plan workflow.

The plan workflow performs:

1. `terraform fmt -check -recursive`
2. `terraform init -input=false`
3. `terraform validate -no-color`
4. `terraform plan -input=false -no-color -out=tfplan`
5. `terraform show -no-color tfplan > tfplan.txt`
6. upload of both the binary plan and human-readable plan as review evidence

Plan evidence is retained for 14 days.

## Terraform apply gate

Production apply is intentionally separated from pull-request plan execution and fails closed.

Apply requires all of the following:

1. explicit `workflow_dispatch`
2. the literal confirmation value `APPLY`
3. a full 40-character commit SHA
4. that SHA must equal current `origin/main`
5. the reusable Terraform job must target the `production` GitHub Environment
6. production Environment protection rules should require a human reviewer
7. the exact approved revision is checked out before Terraform runs

The apply job re-plans the exact reviewed `main` revision after approval and immediately applies that generated plan. This avoids applying a stale artifact whose provider state or remote infrastructure may have changed since PR review.

## Concurrency

Terraform operations targeting the same GitHub Environment are serialized with a shared concurrency group and `cancel-in-progress: false`.

This prevents overlapping Plan/Apply operations from racing against the same remote state. Production changes must queue rather than cancel an in-flight infrastructure operation.

## Required repository settings

GitHub repository administrators should configure:

- branch protection on `main`
- pull requests for changes to protected branches
- required CI status checks
- CODEOWNERS review for `.github/workflows/**` and `infrastructure/terraform/**`
- a `production` Environment with required reviewers
- a lower-privilege `production-plan` Environment for plan access
- Azure federated credentials restricted to this repository and the intended GitHub Environment subjects

## Safety invariants

- No production Terraform apply runs automatically on merge.
- Production apply cannot target an arbitrary branch or stale SHA.
- Terraform cloud access uses short-lived OIDC credentials.
- Application pull requests never push container images.
- Release images are traceable to Git commit SHA and digest.
- Missing OIDC variables, invalid revision input, failed Terraform validation, failed provenance attestation, or missing production approval stops the workflow.
