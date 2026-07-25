from services.agent_runtime.app.action.planner import (
    ActionPlanner,
)

from services.agent_runtime.app.action.mock_executor import (
    MockExecutor,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.policy.engine import (
    PolicyEngine,
)

from services.agent_runtime.app.approval.service import (
    ApprovalService,
)

from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)



class ActionRuntime:
    """
    Handle healing result to action execution.

    Flow:

    Healing Result
        |
        v
    Action Planner
        |
        v
    Policy Engine
        |
        +----------------+
        |                |
        v                v

    Executor       Approval Service

    """



    def __init__(
        self,
        approval_service: ApprovalService | None = None,
    ):


        self.planner = ActionPlanner()


        self.policy = PolicyEngine()


        self.approval = (
            approval_service
            or ApprovalService()
        )


        self.executor = MockExecutor()



    async def execute(
        self,
        healing_result: dict,
    ):


        plan = self.planner.create_plan(

            AgentResult(
                **healing_result
            )

        )



        decision = self.policy.evaluate(
            plan
        )



        #
        # Human approval required
        #

        if decision.require_human:


            approval = await self.approval.create_approval(

                action=plan,

                reason=decision.reason,

            )


            return plan, {


                "success": False,


                "status":
                    "pending_approval",


                "approval_id":
                    approval.id,


                "action":
                    plan.type.value,


                "target":
                    plan.target,


                "reason":
                    decision.reason,


                "approval":
                    approval.model_dump(),

            }



        #
        # Policy denied
        #

        if not decision.approved:


            return plan, {


                "success": False,


                "status":
                    "blocked",


                "policy_decision":
                    decision.model_dump(),

            }



        #
        # Execute action
        #

        result = await self.executor.execute(
            plan
        )


        return plan, result




    async def resume(
        self,
        approval_id: str,
    ):
        """
        Resume execution after approval.

        Flow:

        ApprovalRequest

              |

              v

        APPROVED

              |

              v

        Executor

        """


        approval = await self.approval.get(
            approval_id
        )


        if approval is None:


            return {

                "success": False,

                "status":
                    "approval_not_found",

            }



        if approval.status != ApprovalStatus.APPROVED:


            return {

                "success": False,

                "status":
                    "approval_not_approved",

                "current_status":
                    approval.status.value,

            }



        result = await self.executor.execute(

            approval.action

        )


        return result