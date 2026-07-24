from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """
    Supported healing actions.
    """

    RESTART_POD = "restart_pod"

    SCALE_WORKLOAD = "scale_workload"

    NONE = "none"



class ActionRisk(str, Enum):
    """
    Action risk level.
    """

    LOW = "low"

    MEDIUM = "medium"

    HIGH = "high"



class ActionPlan(BaseModel):
    """
    Generated action plan.
    """

    type: ActionType

    target: str

    risk: ActionRisk = ActionRisk.MEDIUM

    approved: bool = False

    metadata: dict = Field(
        default_factory=dict
    )