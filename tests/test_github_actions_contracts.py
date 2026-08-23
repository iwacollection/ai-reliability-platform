from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_container_release_uses_reusable_build_and_real_dockerfiles() -> None:
    workflow = _read("container-release.yml")
    assert "reusable-docker-build.yml" in workflow
    assert (ROOT / "services" / "agent_runtime" / "Dockerfile").is_file()
    assert (ROOT / "services" / "gateway" / "Dockerfile").is_file()


def test_reusable_docker_build_has_supply_chain_evidence() -> None:
    workflow = _read("reusable-docker-build.yml")
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "actions/attest-build-provenance@v2" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow


def test_terraform_uses_oidc_and_serializes_environment_changes() -> None:
    workflow = _read("reusable-terraform.yml")
    assert "id-token: write" in workflow
    assert "azure/login@v2" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "terraform plan" in workflow
    assert "terraform apply" in workflow


def test_production_apply_requires_manual_confirmation_and_environment() -> None:
    workflow = _read("terraform-apply.yml")
    assert "workflow_dispatch:" in workflow
    assert 'confirm }}\" != \"APPLY' in workflow
    assert "environment: production" in workflow
    assert "commit-sha" in workflow
    assert "origin/main" in workflow
