from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.safety_models import (
    ActionSafetyContractError,
    KubernetesMutationPrecondition,
    KubernetesServerDryRunProof,
    KubernetesWorkloadKind,
    KubernetesWorkloadScope,
    MemoryLimitChange,
    ProductionActionSafetyContract,
    memory_quantity_bytes,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
INCIDENT_ID = UUID("10000000-0000-4000-8000-000000000001")
WORKLOAD_UID = UUID("20000000-0000-4000-8000-000000000002")
PATCH_SHA256 = "a" * 64


def scope(**overrides) -> KubernetesWorkloadScope:
    values = {
        "cluster": "production-a",
        "namespace": "payment",
        "kind": KubernetesWorkloadKind.DEPLOYMENT,
        "name": "payment-api",
        "container": "payment-api",
    }
    values.update(overrides)
    return KubernetesWorkloadScope(**values)


def precondition(**overrides) -> KubernetesMutationPrecondition:
    values = {
        "workload_uid": WORKLOAD_UID,
        "resource_version": "1002003",
        "generation": 17,
    }
    values.update(overrides)
    return KubernetesMutationPrecondition(**values)


def dry_run(**overrides) -> KubernetesServerDryRunProof:
    values = {
        "server_dry_run": True,
        "validated_at": NOW,
        "workload_uid": WORKLOAD_UID,
        "resource_version": "1002003",
        "generation": 17,
        "patch_sha256": PATCH_SHA256,
        "field_manager": "ai-reliability-platform",
        "warnings": (),
    }
    values.update(overrides)
    return KubernetesServerDryRunProof(**values)


def memory(**overrides) -> MemoryLimitChange:
    values = {
        "current_limit": "512Mi",
        "desired_limit": "640Mi",
        "max_increase_percent": 25,
    }
    values.update(overrides)
    return MemoryLimitChange(**values)


def contract(**overrides) -> ProductionActionSafetyContract:
    values = {
        "incident_id": INCIDENT_ID,
        "scope": scope(),
        "precondition": precondition(),
        "memory": memory(),
        "dry_run": dry_run(),
        "prepared_at": NOW + timedelta(seconds=1),
        "expires_at": NOW + timedelta(minutes=10),
    }
    values.update(overrides)
    return ProductionActionSafetyContract(**values)


def plan(**overrides) -> ActionPlan:
    values = {
        "type": ActionType.INCREASE_MEMORY_LIMIT,
        "target": "payment-api",
        "namespace": "payment",
        "cluster": "production-a",
        "risk": ActionRisk.MEDIUM,
        "approved": False,
        "metadata": {
            "reason": "Container was OOMKilled",
        },
    }
    values.update(overrides)
    return ActionPlan(**values)


def test_valid_contract_binds_and_authorizes_exact_approved_plan():
    safety = contract()
    bound = safety.bind_plan(plan())

    assert bound is not plan
    assert bound.approved is False
    assert bound.metadata["safety_contract_id"] == str(safety.contract_id)
    assert bound.metadata["safety_patch_sha256"] == PATCH_SHA256
    assert bound.metadata["current_memory_limit"] == "512Mi"
    assert bound.metadata["desired_memory_limit"] == "640Mi"
    assert bound.metadata["rollback_memory_limit"] == "512Mi"

    bound.approved = True

    safety.require_executable_plan(
        bound,
        at=NOW + timedelta(minutes=2),
    )


def test_contract_is_immutable_and_forbids_unknown_fields():
    safety = contract()

    with pytest.raises(ValidationError):
        safety.scope.namespace = "other"

    with pytest.raises(ValidationError):
        ProductionActionSafetyContract(
            **{
                **safety.model_dump(),
                "credential": "must-not-be-stored",
            }
        )


def test_contract_round_trips_through_persistent_json():
    safety = contract()
    recovered = ProductionActionSafetyContract.model_validate_json(
        safety.model_dump_json()
    )

    assert recovered == safety
    assert recovered.prepared_at.tzinfo is not None
    assert recovered.dry_run.validated_at.tzinfo is not None


@pytest.mark.parametrize(
    "quantity, expected",
    [
        ("512Mi", 512 * 2**20),
        ("1Gi", 2**30),
        ("2Gi", 2 * 2**30),
    ],
)
def test_pilot_memory_quantity_parser_is_exact(quantity, expected):
    assert memory_quantity_bytes(quantity) == expected


@pytest.mark.parametrize(
    "quantity",
    [
        "0Mi",
        "512M",
        "1.5Gi",
        "1024",
        " 512Mi",
        "512Mi ",
        "-1Gi",
    ],
)
def test_pilot_rejects_ambiguous_memory_quantities(quantity):
    with pytest.raises(ValueError):
        memory(current_limit=quantity)


def test_memory_change_requires_positive_increase_with_25_percent_cap():
    assert memory().increase_percent == 25.0
    assert memory().rollback_limit == "512Mi"

    with pytest.raises(ValidationError):
        memory(desired_limit="512Mi")

    with pytest.raises(ValidationError):
        memory(desired_limit="641Mi")

    with pytest.raises(ValidationError):
        memory(max_increase_percent=26)


@pytest.mark.parametrize(
    "field, value",
    [
        ("namespace", ""),
        ("namespace", "Payment"),
        ("name", "payment_api"),
        ("container", "payment.api"),
        ("cluster", " production-a"),
    ],
)
def test_scope_is_explicit_and_kubernetes_safe(field, value):
    with pytest.raises(ValidationError):
        scope(**{field: value})


@pytest.mark.parametrize(
    "proof_override, error_text",
    [
        (
            {"workload_uid": UUID("30000000-0000-4000-8000-000000000003")},
            "UID",
        ),
        ({"resource_version": "different"}, "resourceVersion"),
        ({"generation": 18}, "generation"),
    ],
)
def test_dry_run_proof_must_match_object_precondition(
    proof_override,
    error_text,
):
    with pytest.raises(ValidationError, match=error_text):
        contract(dry_run=dry_run(**proof_override))


def test_contract_rejects_non_server_dry_run_and_bad_digest():
    with pytest.raises(ValidationError):
        dry_run(server_dry_run=False)

    with pytest.raises(ValidationError):
        dry_run(patch_sha256="not-a-sha256")


def test_contract_rejects_stale_future_and_naive_evidence():
    with pytest.raises(ValidationError, match="stale"):
        contract(
            dry_run=dry_run(validated_at=NOW - timedelta(minutes=6))
        )

    with pytest.raises(ValidationError, match="future"):
        contract(
            dry_run=dry_run(validated_at=NOW + timedelta(minutes=2))
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        dry_run(validated_at=NOW.replace(tzinfo=None))


def test_contract_has_short_bounded_lifetime():
    with pytest.raises(ValidationError, match="expire after"):
        contract(expires_at=NOW)

    with pytest.raises(ValidationError, match="15 minutes"):
        contract(expires_at=NOW + timedelta(minutes=16))

    safety = contract()
    assert safety.is_expired(NOW + timedelta(minutes=9)) is False
    assert safety.is_expired(NOW + timedelta(minutes=10)) is True


@pytest.mark.parametrize(
    "plan_override, mismatch",
    [
        ({"type": ActionType.RESTART_POD}, "action_type"),
        ({"risk": ActionRisk.LOW}, "risk"),
        ({"target": "other-api"}, "workload_name"),
        ({"namespace": "other"}, "namespace"),
        ({"cluster": "production-b"}, "cluster"),
    ],
)
def test_contract_rejects_untrusted_or_mismatched_plan_scope(
    plan_override,
    mismatch,
):
    with pytest.raises(ActionSafetyContractError, match=mismatch):
        contract().bind_plan(plan(**plan_override))


def test_execution_requires_approval_unexpired_contract_and_exact_binding():
    safety = contract()
    bound = safety.bind_plan(plan())

    with pytest.raises(ActionSafetyContractError, match="not approved"):
        safety.require_executable_plan(bound, at=NOW + timedelta(minutes=2))

    bound.approved = True

    with pytest.raises(ActionSafetyContractError, match="expired"):
        safety.require_executable_plan(bound, at=NOW + timedelta(minutes=10))

    tampered = bound.model_copy(deep=True)
    tampered.metadata["desired_memory_limit"] = "768Mi"

    with pytest.raises(
        ActionSafetyContractError,
        match="desired_memory_limit",
    ):
        safety.require_executable_plan(
            tampered,
            at=NOW + timedelta(minutes=2),
        )


def test_contract_cannot_be_bound_after_approval():
    with pytest.raises(ActionSafetyContractError, match="before approval"):
        contract().bind_plan(plan(approved=True))
