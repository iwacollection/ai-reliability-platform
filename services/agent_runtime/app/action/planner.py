from typing import Any

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.model.result import (
    AgentResult,
)


class ActionPlanner:
    """
    Convert a HealingAgent result into an executable ActionPlan.

    Supported HealingAgent result formats:

    Legacy flat format:

        {
            "action": "restart_pod",
            "target": "payment-api",
            "risk": "medium"
        }

    Current nested format:

        {
            "action": {
                "type": "restart_pod",
                "target": "payment-api"
            },
            "risk": "medium"
        }
    """

    def create_plan(
        self,
        result: AgentResult,
    ) -> ActionPlan:
        data = result.data

        raw_action = data.get(
            "action",
            ActionType.NONE,
        )

        raw_type: Any = ActionType.NONE
        raw_target: Any = data.get(
            "target",
            "unknown",
        )
        raw_risk: Any = data.get(
            "risk",
            ActionRisk.MEDIUM,
        )
        raw_approved: Any = False
        action_metadata: dict = {}

        if isinstance(raw_action, ActionPlan):
            raw_type = raw_action.type
            raw_target = raw_action.target
            raw_risk = raw_action.risk
            raw_approved = raw_action.approved
            action_metadata = dict(
                raw_action.metadata
            )

        elif isinstance(raw_action, dict):
            raw_type = raw_action.get(
                "type",
                ActionType.NONE,
            )

            raw_target = raw_action.get(
                "target",
                raw_target,
            )

            raw_risk = raw_action.get(
                "risk",
                raw_risk,
            )

            raw_approved = raw_action.get(
                "approved",
                False,
            )

            nested_metadata = raw_action.get(
                "metadata",
                {},
            )

            if isinstance(nested_metadata, dict):
                action_metadata = dict(
                    nested_metadata
                )

        else:
            raw_type = raw_action

        action_type = self._parse_action_type(
            raw_type
        )

        action_risk = self._parse_action_risk(
            raw_risk
        )

        target = self._parse_target(
            raw_target
        )

        approved = self._parse_bool(
            raw_approved
        )

        action_metadata.update(
            {
                "reason": data.get(
                    "reason",
                    action_metadata.get(
                        "reason",
                        "",
                    ),
                ),
                "rollback": data.get(
                    "rollback",
                    action_metadata.get(
                        "rollback",
                        "",
                    ),
                ),
                "verification": data.get(
                    "verification",
                    action_metadata.get(
                        "verification",
                        "",
                    ),
                ),
                "approval_required": data.get(
                    "approval_required",
                    action_metadata.get(
                        "approval_required",
                        True,
                    ),
                ),
            }
        )

        return ActionPlan(
            type=action_type,
            target=target,
            risk=action_risk,
            approved=approved,
            metadata=action_metadata,
        )

    @staticmethod
    def _parse_action_type(
        value: Any,
    ) -> ActionType:
        if isinstance(value, ActionType):
            return value

        try:
            return ActionType(value)

        except (TypeError, ValueError):
            return ActionType.NONE

    @staticmethod
    def _parse_action_risk(
        value: Any,
    ) -> ActionRisk:
        if isinstance(value, ActionRisk):
            return value

        try:
            return ActionRisk(value)

        except (TypeError, ValueError):
            return ActionRisk.MEDIUM

    @staticmethod
    def _parse_target(
        value: Any,
    ) -> str:
        if value is None:
            return "unknown"

        target = str(value).strip()

        if not target:
            return "unknown"

        return target

    @staticmethod
    def _parse_bool(
        value: Any,
    ) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {
                "1",
                "true",
                "yes",
                "approved",
            }

        return bool(value)