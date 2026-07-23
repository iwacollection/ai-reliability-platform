from common.domain.event import StandardEvent

from services.gateway.app.publisher.base import EventPublisher


class MemoryPublisher(EventPublisher):
    """
    In-memory publisher.

    Only used during local development.
    """

    async def publish(self, event: StandardEvent) -> None:
        print("=" * 80)
        print("EVENT PUBLISHED")
        print(event.model_dump_json(indent=2))
        print("=" * 80)