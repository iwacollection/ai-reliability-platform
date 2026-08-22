# Production Release Pipeline

## Scope

This document defines the production release contract for the AI Reliability Platform.

## Application image flow

1. Pull requests that change Agent Runtime, shared packages, or dependency metadata build the container image without publishing it.
2. Pushes to `main` build the same Dockerfile and publish immutable `${GITHUB_SHA}` and `latest` tags to GHCR.
3. The Agent Runtime image starts `services.agent_runtime.app.server:app` with Uvicorn and exposes `/health`.

## Terraform production flow

Production infrastructure lives at:

`infrastructure/terraform/environments/azure-production`

The release sequence is intentionally split into three gates:

1. **Static validation** — `terraform fmt`, backend-free `terraform init`, and `terraform validate`.
2. **Authenticated plan** — Azure OIDC authentication, `terraform plan -out=tfplan`, human-readable plan summary, immutable plan artifact, and provenance metadata.
3. **Approved apply** — manual workflow dispatch, GitHub `production` environment approval, explicit confirmation phrase, immutable plan download, SHA/run/repository/root provenance validation, Azure OIDC authentication, then `terraform apply` against the approved plan file.

## Required GitHub repository variables

Configure these repository or environment variables before enabling Azure workflows:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

The Azure application or managed identity must trust GitHub's OIDC subject for this repository and must only receive the permissions required by the production Terraform root.

## Required GitHub environment protection

Create a GitHub Environment named `production` and configure required reviewers before production apply is enabled. The workflow references this environment but repository policy controls who may approve it.

Recommended controls:

- required reviewers;
- deployment branch restricted to `main`;
- no long-lived Azure client secret;
- branch protection requiring CI, Terraform static validation, and Terraform plan checks;
- apply only from a previously generated plan run ID and full commit SHA.

## Rollback and recovery

Terraform rollback is not implemented as an automatic reverse apply. Infrastructure rollback must use a reviewed corrective Terraform change and a new Plan/Apply cycle. This prevents an automated rollback from destroying stateful production resources.

Application rollback should deploy a previously known-good immutable image SHA rather than rebuilding an older source revision.

## Common failure modes

- Azure OIDC variables are missing or the federated credential subject does not match the GitHub workflow context.
- The Plan artifact has expired (14-day retention) and must be regenerated.
- `expected_sha` or `plan_run_id` does not match the provenance metadata; Apply fails closed.
- GitHub `production` environment has no required reviewers; the workflow still runs, but organizational approval policy is weaker than intended.
- Terraform provider/backend configuration changes between Plan and Apply. Generate a new Plan rather than forcing the old artifact.
