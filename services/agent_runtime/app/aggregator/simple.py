from services.agent_runtime.app.model.result import (
    AgentResult,
)


class SimpleAggregator:
    """
    Aggregate agent results.
    """


    def aggregate(
        self,
        results: list[AgentResult],
    ):

        priority = [
            "healing",
            "rca",
            "diagnosis",
            "noise",
        ]


        result_map = {
            r.agent: r
            for r in results
        }


        for name in priority:

            if name in result_map:

                result = result_map[name]

                return {
                    "incident": (
                        result.success
                    ),

                    "confidence": (
                        result.score
                    ),

                    "source_agent": (
                        result.agent
                    )
                }


        return {
            "incident": False,
            "confidence": 0,
            "source_agent": "none",
        }