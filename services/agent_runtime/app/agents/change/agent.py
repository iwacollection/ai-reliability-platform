from services.agent_runtime.app.agent.base import (
    BaseAgent,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)



class ChangeAgent(BaseAgent):

    """
    Detect recent deployment/config changes.
    """


    @property
    def name(self):

        return "change"



    @property
    def agent_type(self):

        return "change_detection"



    @property
    def depends_on(self):

        return []



    @property
    def provides(self):

        return [
            "deployment_change"
        ]



    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        change = {


            "source":
                "deployment",


            "resource":
                context.event.resources[0].name,


            "facts": [

                "deployment_revision_changed",

                "revision abc123 -> def456",

                "configuration changed: increase memory configuration",

            ],


            "changed":
                True,


            "revision_before":
                "abc123",


            "revision_after":
                "def456",


            "author":
                "developer@example.com",


            "message":
                "increase memory configuration",

        }



        context.variables[
            "deployment_change"
        ] = change



        return AgentResult(

            agent=self.name,

            success=True,

            score=0.95,

            message="Deployment change detected",

            data=change,

        )