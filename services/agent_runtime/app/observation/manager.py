from services.agent_runtime.app.observation.models import (
    ObservationQuery,
    ObservationResult,
)

from services.agent_runtime.app.observation.registry import (
    ObservationRegistry,
)



class ObservationManager:
    """
    Unified observation access layer.
    """


    def __init__(
        self,
        registry: ObservationRegistry,
    ):

        self.registry = registry



    async def query(
        self,
        request: ObservationQuery,
    ) -> ObservationResult:


        provider = self.registry.get(
            request.source
        )


        return await provider.query(
            request
        )