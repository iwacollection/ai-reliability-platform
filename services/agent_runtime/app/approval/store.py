from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
)



class ApprovalStore:
    """
    Store approval requests.

    First version:
    in-memory storage.

    Future:
    Redis / PostgreSQL.
    """


    def __init__(self):

        self._requests: dict[
            str,
            ApprovalRequest
        ] = {}



    async def save(
        self,
        request: ApprovalRequest,
    ) -> ApprovalRequest:


        self._requests[
            request.id
        ] = request


        return request



    async def get(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:


        return self._requests.get(
            request_id
        )



    async def update(
        self,
        request: ApprovalRequest,
    ) -> ApprovalRequest:


        self._requests[
            request.id
        ] = request


        return request



    async def list_all(
        self,
    ) -> list[ApprovalRequest]:


        return list(
            self._requests.values()
        )