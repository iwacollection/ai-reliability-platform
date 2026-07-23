from services.gateway.app.publisher.memory import MemoryPublisher


def create_publisher():
    return MemoryPublisher()