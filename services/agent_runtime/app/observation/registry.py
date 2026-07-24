from services.agent_runtime.app.observation.base import (
    BaseObservationProvider,
)


class ObservationRegistry:
    """
    Observation provider registry.
    """


    def __init__(self):

        self._providers: dict[
            str,
            BaseObservationProvider
        ] = {}


    def register(
        self,
        provider: BaseObservationProvider,
    ):

        self._providers[
            provider.name
        ] = provider


    def get(
        self,
        name: str,
    ) -> BaseObservationProvider:

        return self._providers[name]


    def names(self):

        return list(
            self._providers.keys()
        )