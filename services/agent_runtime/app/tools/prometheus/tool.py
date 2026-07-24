from services.agent_runtime.app.tools.base import (
    BaseTool,
)


class PrometheusTool(BaseTool):
    """
    Prometheus query tool.

    First version is mock.
    """


    @property
    def name(self) -> str:

        return "prometheus"


    async def execute(
        self,
        query: str,
        **kwargs,
    ) -> dict:


        # mock data

        return {
            "query": query,
            "metrics": {
                "cpu_usage": 92,
                "memory_usage": 65,
                "requests_per_second": 1200,
            },
            "source": "mock_prometheus",
        }