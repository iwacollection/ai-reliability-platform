from services.gateway.app.parser.alertmanager import AlertManagerParser
from services.gateway.app.parser.registry import ParserRegistry


def create_parser_registry() -> ParserRegistry:
    registry = ParserRegistry()

    registry.register(
        "alertmanager",
        AlertManagerParser(),
    )

    return registry