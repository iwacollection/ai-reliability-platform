from services.agent_runtime.app.action.models import (
    ActionPlan,
)


from services.agent_runtime.app.approval.manager import (
    ApprovalManager,
)


from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
)



class ApprovalService:
    """
    Approval workflow service.

    Connect:

    Policy Decision

          |

          v

    Approval Manager

          |

          v

    Approval Request

    """


    def __init__(
        self,
        manager: ApprovalManager | None = None,
    ):


        self.manager = (
            manager
            or ApprovalManager()
        )



    async def create_approval(
        self,
        action: ActionPlan,
        reason: str = "",
    ) -> ApprovalRequest:
        """
        Create human approval request.
        """


        return await (
            self.manager.create_request(
                action,
                reason,
            )
        )



    async def approve(
        self,
        request_id: str,
    ) -> ApprovalRequest:


        return await (
            self.manager.approve(
                request_id
            )
        )



    async def reject(
        self,
        request_id: str,
    ) -> ApprovalRequest:


        return await (
            self.manager.reject(
                request_id
            )
        )



    async def get(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:


        return await (
            self.manager.get_request(
                request_id
            )
        )