from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_terraform_apply_is_manual_and_production_protected() -> None:
    content = _read("terraform-apply.yml")

    assert "workflow_dispatch:" in content
    assert "operation: apply" in content
    assert "environment: production" in content
    assert "confirm" in content
    assert "APPLY" in content


def test_terraform_reusable_workflow_uses_azure_oidc() -> None:
    content = _read("reusable-terraform.yml")

    assert "id-token: write" in content
    assert "azure/login@v2" in content
    assert "AZURE_CLIENT_ID" in content
    assert "AZURE_TENANT_ID" in content
    assert "AZURE_SUBSCRIPTION_ID" in content
    assert "terraform plan" in content
    assert "terraform apply" in content


def test_terraform_plan_is_pull_request_gated() -> None:
    content = _read("terraform-plan.yml")

    assert "pull_request:" in content
    assert "operation: plan" in content
    assert "infrastructure/terraform/**" in content


def test_reusable_docker_build_has_supply_chain_metadata() -> None:
    content = _read("reusable-docker-build.yml")

    assert "workflow_call:" in content
    assert "docker/build-push-action@v6" in content
    assert "provenance: true" in content
    assert "sbom: true" in content
    assert "cache-from: type=gha" in content
    assert "ghcr.io" in content
