from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_terraform_apply_uses_same_reviewed_plan_artifact() -> None:
    reusable = _read("reusable-terraform.yml")
    apply_workflow = _read("terraform-apply.yml")

    assert "Download reviewed Terraform plan" in reusable
    assert "actions/download-artifact@v4" in reusable
    assert "terraform apply -input=false -auto-approve tfplan" in reusable
    assert "if: inputs.operation == 'plan'" in reusable
    assert "if: inputs.operation == 'apply'" in reusable

    assert "Generate production plan evidence" in apply_workflow
    assert "Apply approved production plan" in apply_workflow
    assert "environment: production" in apply_workflow
    assert "- plan" in apply_workflow
    assert "plan-artifact-name:" in apply_workflow


def test_terraform_revision_is_pinned_before_production_apply() -> None:
    plan_workflow = _read("terraform-plan.yml")
    apply_workflow = _read("terraform-apply.yml")

    assert "checkout-ref: ${{ github.event.pull_request.head.sha }}" in plan_workflow
    assert "commit-sha must be a full 40-character Git commit SHA" in apply_workflow
    assert "Requested revision $requested is not current origin/main" in apply_workflow


def test_container_build_has_supply_chain_evidence() -> None:
    docker = _read("reusable-docker-build.yml")

    assert "cache-from: type=gha" in docker
    assert "cache-to: type=gha,mode=max" in docker
    assert "provenance: mode=max" in docker
    assert "sbom: true" in docker
    assert "actions/attest-build-provenance@v2" in docker


def test_service_images_run_as_non_root() -> None:
    agent = (ROOT / "services" / "agent_runtime" / "Dockerfile").read_text(encoding="utf-8")
    gateway = (ROOT / "services" / "gateway" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (agent, gateway):
        assert "USER appuser" in dockerfile
        assert "useradd --create-home --uid 10001 appuser" in dockerfile
