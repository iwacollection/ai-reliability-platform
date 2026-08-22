# IaC Release Governance

## Scope

This document defines the release contract for Terraform changes under `infrastructure/terraform` and reusable container build workflows.

## Terraform lifecycle

1. A pull request changing `infrastructure/terraform/**` triggers `terraform-plan.yml`.
2. The reusable Terraform workflow performs `fmt`, `init`, `validate`, and `plan`.
3. The generated plan is uploaded as a short-lived workflow artifact for review evidence.
4. Production apply is never triggered by a normal push or merge.
5. `terraform-apply.yml` is manual (`workflow_dispatch`) and requires the literal confirmation value `APPLY`.
6. The apply job targets the GitHub `production` Environment. Required reviewers must be configured on that Environment before production use.

## Azure OIDC contract

Terraform workflows use GitHub OIDC instead of a stored Azure client secret.

Repository or Environment variables required:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Azure must have a federated identity credential that trusts this repository and the intended GitHub Environment/ref. Do not store a long-lived `AZURE_CLIENT_SECRET` for this workflow.

## Environment protection

Recommended GitHub Environment configuration:

### `production-plan`

- No deployment approval required.
- Read-only or planning-capable Azure role assignment.
- OIDC federation restricted to this repository.

### `production`

- Required reviewers enabled.
- Prevent self-review where supported by the account plan.
- Azure role assignment limited to the resources managed by this Terraform root.
- Deployment branch policy restricted to `main`.

The workflow wiring alone does not create GitHub Environment protection rules; repository administrators must configure those settings in GitHub.

## Container build contract

`reusable-docker-build.yml` is a reusable build primitive. It does not assume a Dockerfile exists and is only invoked by service-specific workflows once a service image contract is defined.

The workflow provides:

- Buildx
- GitHub Actions cache
- GHCR authentication using `GITHUB_TOKEN`
- OCI metadata tags
- provenance attestation
- SBOM generation

Image publishing should be enabled only from trusted branches or release workflows.

## Release safety properties

- Pull requests can plan but cannot apply production Terraform.
- Production apply requires an explicit manual trigger and GitHub Environment approval.
- Azure authentication uses short-lived OIDC credentials.
- Long-lived cloud credentials are not required by the workflow.
- Container publishing and Terraform execution are reusable building blocks rather than duplicated service workflows.

## Validation

Repository tests in `services/agent_runtime/tests/test_ci_workflow_contracts.py` assert the critical governance invariants so accidental workflow weakening is visible in CI.
