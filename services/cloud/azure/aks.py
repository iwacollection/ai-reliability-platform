from typing import Any


class AKSRuntimeClient:
    """AKS metadata and Kubernetes credential runtime boundary."""

    def __init__(self, azure_client: Any = None):
        self.azure_client = azure_client

    def get_cluster(self, subscription_id: str, resource_group: str, name: str):
        if self.azure_client is None:
            return {
                "status": "mock",
                "subscription": subscription_id,
                "resource_group": resource_group,
                "name": name,
            }

        return self.azure_client.get_cluster(
            subscription_id,
            resource_group,
            name,
        )

    def get_kubernetes_endpoint(self, cluster):
        return cluster.get("endpoint")
