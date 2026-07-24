from abc import ABC, abstractmethod

from services.agent_runtime.app.observation.models import (
    ObservationQuery,
    ObservationResult,
)


class BaseObservationProvider(ABC):
    """
    Base class of observation providers.
    """


    @property
    @abstractmethod
    def name(self) -> str:
        ...


    @abstractmethod
    async def query(
        self,
        request: ObservationQuery,
    ) -> ObservationResult:
        ...