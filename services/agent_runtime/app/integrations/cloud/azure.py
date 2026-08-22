"""Azure connector foundation for cloud evidence collection."""

from dataclasses import dataclass


@dataclass
class AzureConfig:
    subscription_id: str


class AzureConnector:
    def __init__(self, config: AzureConfig):
        self.config = config

    def query_resource(self, resource_id: str) -> dict:
        # Production implementation will use Azure SDK with managed identity/OIDC.
        return {
            "resource_id": resource_id,
            "source": "azure-resource-graph",
        }
