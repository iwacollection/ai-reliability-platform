from services.agent_runtime.app.aggregator.base import (
    BaseAggregator,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)


class SimpleAggregator(BaseAggregator):
    """
    Simple result aggregator.
    """


    def aggregate(
        self,
        results: list[AgentResult],
    ) -> dict:

        if not results:

            return {
                "incident": False,
                "confidence": 0,
            }


        best = max(
            results,
            key=lambda x: x.score,
        )


        return {
            "incident": (
                best.message
                == "Real Alert"
            ),
            "confidence": best.score,
            "source_agent": best.agent,
        }