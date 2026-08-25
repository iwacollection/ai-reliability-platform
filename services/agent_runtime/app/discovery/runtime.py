import inspect
from collections.abc import Awaitable, Callable

from services.agent_runtime.app.discovery.detector import DiscoveryDetector
from services.agent_runtime.app.discovery.models import DiscoveryBatch, DiscoveryFinding
from services.agent_runtime.app.discovery.source import DiscoverySource


FindingSink = Callable[[DiscoveryFinding], None | Awaitable[None]]


class ProactiveDiscoveryRuntime:
    """Run a read-only scan and promote sufficiently strong findings."""

    def __init__(
        self,
        source: DiscoverySource,
        *,
        detector: DiscoveryDetector | None = None,
        min_score: float = 0.75,
        sink: FindingSink | None = None,
    ) -> None:
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")

        self.source = source
        self.detector = detector or DiscoveryDetector()
        self.min_score = min_score
        self.sink = sink

    async def scan(self) -> DiscoveryBatch:
        observations = await self.source.collect()
        findings: list[DiscoveryFinding] = []
        promoted: list[DiscoveryFinding] = []

        for observation in observations:
            for finding in self.detector.evaluate(observation):
                findings.append(finding)

                if finding.score < self.min_score:
                    continue

                finding.should_investigate = True
                promoted.append(finding)

                if self.sink is not None:
                    result = self.sink(finding)
                    if inspect.isawaitable(result):
                        await result

        return DiscoveryBatch(
            scanned=len(observations),
            findings=findings,
            promoted=promoted,
        )
