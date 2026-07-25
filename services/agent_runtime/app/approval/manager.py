from uuid import uuid4


from services.agent_runtime.app.action.models import (
    ActionPlan,
)


from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)


from services.agent_runtime.app.approval.store import (
    ApprovalStore,
)



class ApprovalManager:
    """
    Manage human approval workflow.

    Flow:

    Policy Decision

          |

          v

    ApprovalRequest

          |

          v

    Human Decision

    """


    def __init__(
        self,
        store: ApprovalStore | None = None,
    ):


        self.store = (
            store
            or ApprovalStore()
        )



    async def create_request(
        self,
        action: ActionPlan,
        reason: str = "",
    ) -> ApprovalRequest:
        """
        Create pending approval request.
        """


        request = ApprovalRequest(

            id=str(
                uuid4()
            ),


            action=action,


            reason=reason,

        )


        return await self.store.save(
            request
        )



    async def get_request(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:


        return await self.store.get(
            request_id
        )



    async def approve(
        self,
        request_id: str,
    ) -> ApprovalRequest:


        request = await self.store.get(
            request_id
        )


        if request is None:

            raise ValueError(
                "Approval request not found"
            )


        request.status = (
            ApprovalStatus.APPROVED
        )


        return await self.store.update(
            request
        )



    async def reject(
        self,
        request_id: str,
    ) -> ApprovalRequest:


        request = await self.store.get(
            request_id
        )


        if request is None:

            raise ValueError(
                "Approval request not found"
            )


        request.status = (
            ApprovalStatus.REJECTED
        )


        return await self.store.update(
            request
        )



    async def list_requests(
        self,
    ) -> list[ApprovalRequest]:


        return await self.store.list_all()