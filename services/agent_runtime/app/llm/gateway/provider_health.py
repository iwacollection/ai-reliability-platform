from enum import Enum



class ProviderHealthStatus(str, Enum):
    """
    Provider health state.
    """


    HEALTHY = "healthy"


    UNHEALTHY = "unhealthy"




class ProviderHealthManager:
    """
    Manage LLM provider health status.

    Responsibilities:

    - provider health state storage
    - health state query
    - health state update

    """



    def __init__(
        self,
        providers: list[str],
    ) -> None:


        self._status: dict[
            str,
            ProviderHealthStatus,
        ] = {}


        for provider in providers:

            self._status[
                provider
            ] = ProviderHealthStatus.HEALTHY




    def is_healthy(
        self,
        provider: str,
    ) -> bool:
        """
        Check provider health.
        """


        return (

            self._status.get(

                provider,

                ProviderHealthStatus.UNHEALTHY,

            )

            ==
            ProviderHealthStatus.HEALTHY

        )




    def mark_unhealthy(
        self,
        provider: str,
    ) -> None:
        """
        Mark provider unavailable.
        """


        self._status[
            provider
        ] = ProviderHealthStatus.UNHEALTHY




    def mark_healthy(
        self,
        provider: str,
    ) -> None:
        """
        Mark provider recovered.
        """


        self._status[
            provider
        ] = ProviderHealthStatus.HEALTHY




    def get_status(
        self,
        provider: str,
    ) -> ProviderHealthStatus:
        """
        Get provider health status.
        """


        return self._status.get(

            provider,

            ProviderHealthStatus.UNHEALTHY,

        )




    def list_status(
        self,
    ) -> dict[str, ProviderHealthStatus]:
        """
        Return all provider status.
        """


        return self._status.copy()