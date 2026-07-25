from services.gateway.app.publisher.runtime import (
    RuntimePublisher,
)


from services.gateway.app.publisher.memory import (
    MemoryPublisher,
)



def create_publisher():
    """
    Create event publisher.

    Default:

    Gateway
        |
        v
    Agent Runtime
    """


    return RuntimePublisher()