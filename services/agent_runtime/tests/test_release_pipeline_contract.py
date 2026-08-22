from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_agent_runtime_dockerfile_has_healthcheck_and_server_entrypoint():
    dockerfile = (
        REPOSITORY_ROOT / "services" / "agent_runtime" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "services.agent_runtime.app.server:app" in dockerfile


def test_terraform_plan_uses_azure_oidc_and_real_production_root():
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "terraform-plan.yml"
    ).read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "azure/login@v2" in workflow
    assert "ARM_USE_OIDC: 'true'" in workflow
    assert "infrastructure/terraform/environments/azure-production" in workflow
    assert "terraform-plan-metadata.json" in workflow


def test_terraform_apply_requires_production_environment_and_plan_provenance():
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "terraform-apply.yml"
    ).read_text(encoding="utf-8")

    assert "environment: production" in workflow
    assert "APPLY-AZURE-PRODUCTION" in workflow
    assert "Verify plan provenance" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "azure/login@v2" in workflow


def test_image_workflow_uses_reusable_builder_and_pushes_only_from_main():
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "agent-runtime-image.yml"
    ).read_text(encoding="utf-8")

    assert "reusable-docker-build.yml" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "refs/heads/main" in workflow
