from abc import ABC, abstractmethod

from common.domain.event import StandardEvent


class EventPublisher(ABC):
    """Event Publisher Interface."""

    @abstractmethod
    async def publish(self, event: StandardEvent) -> None:
        """Publish a StandardEvent."""
        raise NotImplementedError