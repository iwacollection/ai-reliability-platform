from enum import Enum

from pydantic import (
    BaseModel,
    Field,
)


class ActionType(str, Enum):
    """
    Supported remediation action types.
    """

    NONE = "none"

    RESTART_POD = "restart_pod"

    INCREASE_MEMORY_LIMIT = (
        "increase_memory_limit"
    )

    ROLLBACK_APPLICATION = (
        "rollback_application"
    )

    SCALE_WORKLOAD = (
        "scale_workload"
    )

    UPDATE_CONFIG = (
        "update_config"
    )


class ActionRisk(str, Enum):
    """
    Remediation action risk level.
    """

    LOW = "low"

    MEDIUM = "medium"

    HIGH = "high"


class ActionPlan(BaseModel):
    """
    Executable remediation action plan.

    namespace and cluster are first-class persisted fields instead of
    free-form metadata. ApprovalStore serializes the complete ActionPlan, so
    an approved action can recover its original resource scope after a
    process restart.

    Both fields remain optional during the staged migration so legacy
    approval records and actions outside Kubernetes can still be loaded.
    Kubernetes execution and verification must validate the required scope
    before using the plan; a missing namespace must not silently select the
    default namespace.

    Enum fields remain Enum objects inside Python so callers can safely use
    comparisons such as:

        action.type == ActionType.RESTART_POD

    and access serialized values through:

        action.type.value
    """

    type: ActionType

    target: str

    namespace: str | None = None

    cluster: str | None = None

    risk: ActionRisk = (
        ActionRisk.MEDIUM
    )

    approved: bool = False

    metadata: dict = Field(
        default_factory=dict
    )
