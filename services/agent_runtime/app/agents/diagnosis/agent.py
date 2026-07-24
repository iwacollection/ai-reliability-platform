from services.agent_runtime.app.agent.base import (
    BaseAgent,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.observation.manager import (
    ObservationManager,
)

from services.agent_runtime.app.observation.models import (
    ObservationQuery,
)

from services.agent_runtime.app.observation.normalizer import (
    EvidenceNormalizer,
)



class DiagnosisAgent(BaseAgent):
    """
    Collect production evidence.
    """



    @property
    def agent_type(self):

        return "observation"



    @property
    def depends_on(self):

        return [
            "alert_classification"
        ]



    @property
    def provides(self):

        return [
            "kubernetes_evidence"
        ]



    def __init__(
        self,
        observation_manager: ObservationManager,
    ):

        self.observation_manager = (
            observation_manager
        )

        self.normalizer = EvidenceNormalizer()



    @property
    def name(self):

        return "diagnosis"



    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        evidence = []


        resource = (

            context
            .event
            .resources[0]
            .name

        )



        #
        # Prefer Skill
        #

        if context.skills:


            skill = context.skills.get(
                "kubernetes_diagnosis"
            )


            skill_result = await skill.execute(

                {
                    "resource": resource
                }

            )


            evidence.append(
                skill_result
            )


            #
            # Record current agent skill execution
            #

            skill_calls = (
                context.metadata
                .setdefault(
                    "current_skill_calls",
                    []
                )
            )


            skill_calls.append(
                "kubernetes_diagnosis"
            )



        #
        # Fallback old Observation system
        #

        else:


            result = await (

                self.observation_manager.query(

                    ObservationQuery(

                        source="kubernetes",

                        resource=resource,

                        parameters={

                            "namespace":
                            "default"

                        },

                    )

                )

            )



            normalized = self.normalizer.normalize(

                result.source,

                result.data,

            )


            evidence.append(
                normalized
            )



        context.variables[

            "evidence"

        ] = evidence



        return AgentResult(

            agent=self.name,

            success=True,

            score=1.0,

            message=(

                "Evidence collected"

            ),

            data={

                "evidence":
                evidence

            },

        )