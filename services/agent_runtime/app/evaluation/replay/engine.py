from datetime import UTC, datetime


from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)


from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)


from services.agent_runtime.app.model.context import (
    AgentContext,
)


from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)



class ScenarioReplayEngine:
    """
    Replay scenario through Agent Runtime.

    Used for:
    - Harness engineering
    - Regression testing
    - Agent evaluation
    """



    def __init__(
        self,
        runtime: AgentRuntime,
    ):

        self.runtime = runtime



    def build_event(
        self,
        scenario: ScenarioDefinition,
    ) -> StandardEvent:
        """
        Convert scenario definition
        into runtime event.
        """

        return StandardEvent(

            header=Header(

                source=EventSource.ALERTMANAGER,

                occurred_at=datetime.now(
                    UTC
                ),

            ),


            signal=Signal(

                type=SignalType.ALERT,

                name=scenario.event.get(
                    "alertname",
                    "Unknown",
                ),


                severity=Severity(
                    scenario.event.get(
                        "severity",
                        "info",
                    )
                ),


                message=scenario.description,

            ),


            resources=[

                Resource(

                    kind=ResourceKind.POD,

                    name=scenario.event.get(
                        "resource",
                        "unknown",
                    ),

                )

            ],

        )



    async def replay(
        self,
        scenario: ScenarioDefinition,
    ):

        event = self.build_event(
            scenario
        )


        context = AgentContext(

            event=event,

            memory=self.runtime.memory,

            tools=self.runtime.tools,

            skills=self.runtime.skills,

        )


        results = await self.runtime.pipeline.execute(
            context
        )


        return {

            "scenario":
            scenario.name,


            "results":
            results,


            "context":
            context,

        }