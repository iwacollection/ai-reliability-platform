from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)

from services.agent_runtime.app.tools.manager import (
    ToolManager,
)

from services.agent_runtime.app.tools.mock.echo import (
    EchoTool,
)

from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)

from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)


def create_tool_manager() -> ToolManager:


    registry = ToolRegistry()


    registry.register(
        EchoTool()
    )


    registry.register(
        PrometheusTool()
    )


    registry.register(
        KubernetesTool()
    )


    return ToolManager(
        registry
    )