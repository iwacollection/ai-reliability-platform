from services.agent_runtime.app.observation.registry import (
    ObservationRegistry,
)

from services.agent_runtime.app.observation.manager import (
    ObservationManager,
)

from services.agent_runtime.app.observation.providers.kubernetes import (
    KubernetesProvider,
)



def create_observation_manager():

    registry = ObservationRegistry()


    registry.register(
        KubernetesProvider()
    )


    return ObservationManager(
        registry
    )