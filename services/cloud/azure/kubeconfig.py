from dataclasses import dataclass


@dataclass
class AKSKubeConfig:
    endpoint: str
    token: str
    cluster_name: str


class AKSKubeConfigProvider:
    """Build Kubernetes runtime configuration from Azure identity output."""

    def create_config(self, cluster_name: str, endpoint: str, token: str):
        return AKSKubeConfig(
            endpoint=endpoint,
            token=token,
            cluster_name=cluster_name,
        )
