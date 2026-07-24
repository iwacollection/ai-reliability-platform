from services.agent_runtime.app.observation.base import (
    BaseObservationProvider,
)

from services.agent_runtime.app.observation.models import (
    ObservationQuery,
    ObservationResult,
)



class KubernetesProvider(
    BaseObservationProvider
):


    @property
    def name(self):

        return "kubernetes"



    async def query(
        self,
        request: ObservationQuery,
    ) -> ObservationResult:


        return ObservationResult(

            source=self.name,

            success=True,

            data={

                "pod":
                request.resource,

                "status":
                "CrashLoopBackOff",

                "restart_count":
                15,

                "last_reason":
                "OOMKilled",

            },

            message="mock kubernetes response"
        )