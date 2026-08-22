"""Azure production integration foundation.

Supports future wiring with Azure SDK, Managed Identity and Resource Graph.
"""

from dataclasses import dataclass


@dataclass
class AzureContext:
    subscription_id: str
    tenant_id: str | None = None
    resource_group: str | None = None


class AzureRuntimeConnector:
    """Read-only Azure investigation connector boundary."""

    def __init__(self, context: AzureContext):
        self.context = context

    def discover_resources(self) -> list[dict]:
        """Placeholder for Azure Resource Graph discovery."""
        return []

    def query_monitor_metrics(self, resource_id: str, metric: str) -> dict:
        return {
            "resource_id": resource_id,
            "metric": metric,
            "status": "adapter_ready",
        }
