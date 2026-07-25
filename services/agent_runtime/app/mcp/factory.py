from services.agent_runtime.app.mcp.registry import (
    MCPRegistry,
)


from services.agent_runtime.app.mcp.client import (
    MockMCPClient,
)



def create_mcp_registry() -> MCPRegistry:
    """
    Create MCP registry.
    """


    registry = MCPRegistry()


    registry.register(
        MockMCPClient()
    )


    return registry