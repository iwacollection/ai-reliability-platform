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


        namespace = (
            request.parameters.get(
                "namespace",
                "default",
            )
        )


        return ObservationResult(

            source=self.name,

            success=True,

            data={

                "namespace": namespace,

                "pod": request.resource,

                "status": "CrashLoopBackOff",

                "containers": [

                    {

                        "name": request.resource,

                        "restart_count": 15,

                        "reason": "OOMKilled",

                    }

                ],

            },

            message="mock kubernetes response"

        )