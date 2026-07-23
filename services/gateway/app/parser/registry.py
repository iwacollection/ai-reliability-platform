from typing import Dict

from services.gateway.app.parser.base import BaseParser


class ParserRegistry:
    """Parser registry."""

    def __init__(self) -> None:
        self._parsers: Dict[str, BaseParser] = {}

    def register(self, name: str, parser: BaseParser) -> None:
        self._parsers[name] = parser

    def get(self, name: str) -> BaseParser:
        if name not in self._parsers:
            raise KeyError(f"Parser '{name}' not found.")

        return self._parsers[name]