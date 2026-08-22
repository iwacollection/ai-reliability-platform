"""Azure Resource Graph integration foundation.

Provides read-only resource discovery primitives for investigation workflows.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AzureResourceQueryResult:
    resources: list[dict[str, Any]]
    query: str


class AzureResourceGraphClient:
    def query(self, kql: str) -> AzureResourceQueryResult:
        # Runtime wiring will use Azure SDK credentials.
        return AzureResourceQueryResult(resources=[], query=kql)
