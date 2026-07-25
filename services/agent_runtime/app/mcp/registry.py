from services.agent_runtime.app.mcp.base import (
    BaseMCPClient,
)



class MCPRegistry:
    """
    Registry for MCP clients.
    """


    def __init__(
        self,
    ) -> None:

        self._clients: dict[str, BaseMCPClient] = {}



    def register(
        self,
        client: BaseMCPClient,
    ) -> None:

        self._clients[
            client.name
        ] = client



    def get(
        self,
        name: str,
    ) -> BaseMCPClient:


        if name not in self._clients:

            raise KeyError(
                f"MCP client '{name}' not found"
            )


        return self._clients[name]



    def list_clients(
        self,
    ) -> list[BaseMCPClient]:

        return list(
            self._clients.values()
        )



    def names(
        self,
    ) -> list[str]:

        return list(
            self._clients.keys()
        )