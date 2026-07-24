from enum import Enum

from pydantic import BaseModel


class ActionType(str, Enum):

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

    LOW = "low"

    MEDIUM = "medium"

    HIGH = "high"



class ActionPlan(BaseModel):

    type: ActionType

    target: str

    risk: ActionRisk

    approved: bool = False

    metadata: dict = {}