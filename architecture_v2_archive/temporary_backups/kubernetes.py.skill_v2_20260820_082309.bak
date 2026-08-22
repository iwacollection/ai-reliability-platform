from services.agent_runtime.app.skills.base import (
    BaseSkill,
)


class KubernetesDiagnosisSkill(BaseSkill):
    """
    Kubernetes diagnosis skill.

    Use MCP and runtime tools to collect Kubernetes evidence.
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
        resource = str(
            input_data.get(
                "resource",
                "unknown",
            )
        ).strip() or "unknown"

        namespace = str(
            input_data.get(
                "namespace",
                "default",
            )
            or "default"
        ).strip() or "default"

        cluster_value = input_data.get(
            "cluster"
        )
        cluster = (
            str(cluster_value).strip()
            if cluster_value
            else None
        )

        result = {
            "resource": resource,
            "namespace": namespace,
            "cluster": cluster,
        }

        #
        # MCP First
        #
        # Keep the existing MCP parameter contract in this stage.
        # Namespace and cluster routing will be added only after
        # the MCP server interface has been inspected.
        #
        if context.mcp:
            mcp_result = await context.mcp.get(
                "mock_mcp"
            ).call(
                "kubernetes_diagnosis",
                context=context,
                resource=resource,
            )
            result["mcp"] = mcp_result

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
                namespace=namespace,
            )
            result["kubernetes"] = (
                kubernetes_result
            )

        #
        # Prometheus Tool
        #
        if context.tools:
            prometheus_query = (
                self._build_cpu_query(
                    resource=resource,
                    namespace=namespace,
                    cluster=cluster,
                )
            )

            prometheus_result = await context.tools.call(
                "prometheus",
                context=context,
                query=prometheus_query,
            )
            result["metrics"] = (
                prometheus_result
            )

        return result

    @classmethod
    def _build_cpu_query(
        cls,
        resource: str,
        namespace: str,
        cluster: str | None = None,
    ) -> str:
        labels = [
            (
                'pod="'
                f'{cls._escape_label_value(resource)}'
                '"'
            ),
            (
                'namespace="'
                f'{cls._escape_label_value(namespace)}'
                '"'
            ),
            'container!="POD"',
            'container!=""',
            'image!=""',
        ]

        if cluster:
            labels.append(
                'cluster="'
                f'{cls._escape_label_value(cluster)}'
                '"'
            )

        selector = ",".join(
            labels
        )

        return (
            "sum(rate("
            "container_cpu_usage_seconds_total"
            f"{{{selector}}}"
            "[5m]))"
        )

    @staticmethod
    def _escape_label_value(
        value: str,
    ) -> str:
        return (
            value
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace('"', '\\"')
        )