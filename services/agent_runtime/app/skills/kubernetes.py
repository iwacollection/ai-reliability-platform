from services.agent_runtime.app.skills.base import (
    BaseSkill,
)



class KubernetesDiagnosisSkill(
    BaseSkill
):
    """
    Kubernetes diagnosis skill.

    Use:
    - MCP
    - Runtime tools

    to collect kubernetes evidence.
    """



    @property
    def name(
        self,
    ) -> str:

        return "kubernetes_diagnosis"



    async def execute(
        self,
        context,
        input_data: dict,
    ) -> dict:


        resource = input_data.get(
            "resource",
            "unknown",
        )


        result = {

            "resource": resource,

        }



        #
        # MCP First
        #
        # Future:
        # MCP Server
        # Kubernetes API
        #

        if context.mcp:


            mcp_result = await context.mcp.get(
                "mock_mcp"
            ).call(

                "kubernetes_diagnosis",

                context=context,

                resource=resource,

            )


            result["mcp"] = (
                mcp_result
            )



        #
        # Kubernetes Tool
        #

        if context.tools:


            kubernetes_result = await context.tools.call(

                "kubernetes",

                context=context,

                action="describe",

                resource="pod",

                target=resource,

            )


            result["kubernetes"] = (
                kubernetes_result
            )



        #
        # Prometheus Tool
        #

        if context.tools:


            prometheus_result = await context.tools.call(

                "prometheus",

                context=context,

                query=(

                    f'pod_cpu_usage'

                    f'{{pod="{resource}"}}'

                ),

            )


            result["metrics"] = (
                prometheus_result
            )



        return result