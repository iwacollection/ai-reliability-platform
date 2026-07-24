from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
    ActionRisk,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)


class ActionPlanner:
    """
    Convert AgentResult to ActionPlan.
    """


    def create_plan(
        self,
        result: AgentResult,
    ) -> ActionPlan:


        action = result.data.get(
            "action",
            "none",
        )


        target = result.data.get(
            "target",
            "unknown",
        )


        try:

            action_type = ActionType(
                action
            )

        except ValueError:

            action_type = ActionType.NONE



        return ActionPlan(
            type=action_type,
            target=target,
            risk=ActionRisk.MEDIUM,
        )