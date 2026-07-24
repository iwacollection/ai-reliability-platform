from services.agent_runtime.app.skills.base import (
    BaseSkill,
)


class KubernetesDiagnosisSkill(
    BaseSkill
):
    """
    Kubernetes diagnosis skill.

    Provide kubernetes incident evidence.
    """


    @property
    def name(
        self,
    ) -> str:

        return "kubernetes_diagnosis"



    async def execute(
        self,
        input_data: dict,
    ) -> dict:


        resource = input_data.get(
            "resource",
            "unknown",
        )


        return {

            "source":
                "kubernetes",


            "resource":
                resource,


            "facts":[

                "pod_status=CrashLoopBackOff",

                "restart_count=15",

                "reason=OOMKilled",

            ],

        }