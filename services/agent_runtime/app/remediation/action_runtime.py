"""Action runtime orchestrates approved remediation execution."""


class ActionRuntime:
    def __init__(self, registry, policy, event_bus=None):
        self.registry = registry
        self.policy = policy
        self.event_bus = event_bus

    async def execute(self, action: str, params: dict, approved: bool = False):
        if not self.policy.check(action, approved):
            raise PermissionError(f"action blocked: {action}")

        handler = self.registry.get(action)
        if not handler:
            raise ValueError(f"unknown action: {action}")

        if self.event_bus:
            await self.event_bus.publish({
                "type": "action_started",
                "action": action,
            })

        result = await handler(**params)

        if self.event_bus:
            await self.event_bus.publish({
                "type": "action_completed",
                "action": action,
                "result": result,
            })

        return result
