from abc import ABC, abstractmethod

from services.agent_runtime.app.discovery.models import DiscoveryObservation


class DiscoverySource(ABC):
    """Read-only source used by proactive discovery scans."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def collect(self) -> list[DiscoveryObservation]:
        raise NotImplementedError


class StaticDiscoverySource(DiscoverySource):
    """Deterministic source for tests, replay, and simulator integration."""

    def __init__(self, observations: list[DiscoveryObservation], name: str = "static"):
        self._observations = observations
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def collect(self) -> list[DiscoveryObservation]:
        return list(self._observations)
