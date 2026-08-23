"""Automatic failover support for MCP federation."""


class MCPFailoverManager:
    def __init__(self, health_checker, circuit_breaker):
        self.health_checker = health_checker
        self.circuit_breaker = circuit_breaker

    def select_available(self, providers):
        for provider in providers:
            provider_id = provider.provider_id
            if not self.circuit_breaker.allow(provider_id):
                continue
            if self.health_checker.check(provider):
                return provider
        return None
