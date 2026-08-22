# Production Release Governance

## Scope

This document defines the release controls for application containers and Azure Terraform changes.

## Application containers

- Pull requests build both `agent-runtime` and `gateway` images without pushing them.
- Merges to `main` and version tags build and publish immutable SHA-tagged images to GHCR.
- Docker builds use `uv.lock` with `uv sync --frozen --no-dev`.
- Runtime containers execute as a non-root user.
- BuildKit cache, provenance and SBOM generation are enabled in the reusable Docker workflow.

## Azure authentication

Terraform workflows use GitHub Actions OIDC through `azure/login`.

Required GitHub Environment or repository variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Long-lived Azure client secrets must not be stored for Terraform workflows.

## Terraform plan

Pull requests that change `infrastructure/terraform/**` run Terraform formatting, initialization, validation and plan for the Azure production root.

The plan artifact is retained for review evidence.

## Terraform apply

Production apply is manual and fail-closed:

1. The operator supplies the full 40-character commit SHA and `confirm=APPLY`.
2. The workflow verifies that the SHA is the current `origin/main` revision.
3. The production job enters the GitHub `production` Environment.
4. Environment protection rules are expected to require human approval.
5. Terraform checks out the exact approved revision.
6. Terraform creates a plan and applies that exact `tfplan`, preventing plan/apply drift inside the job.

## Required repository configuration

GitHub repository administrators should configure:

- `production` Environment with required reviewers.
- `production-plan` Environment for read-only Azure plan credentials if desired.
- Azure federated identity credentials restricted to this repository and intended environment/ref conditions.
- Branch protection on `main` with required CI, container build and Terraform plan checks.

## Safety invariants

- No production Terraform apply runs automatically on merge.
- Production apply cannot target an arbitrary branch or stale SHA.
- Terraform cloud access uses short-lived OIDC credentials.
- Application PRs never push container images.
- Release images are traceable to Git commit SHAs.
