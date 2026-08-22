"""Loki log investigation connector foundation."""

from dataclasses import dataclass


@dataclass
class LokiConfig:
    endpoint: str


class LokiConnector:
    def __init__(self, config: LokiConfig):
        self.config = config

    def query(self, logql: str) -> dict:
        # Production implementation will call Loki query API.
        return {
            "query": logql,
            "source": "loki",
            "logs": [],
        }
