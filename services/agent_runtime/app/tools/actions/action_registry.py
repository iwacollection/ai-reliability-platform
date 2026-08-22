"""Registry for approved remediation actions."""

from typing import Callable


class ActionRegistry:
    def __init__(self):
        self._actions: dict[str, Callable] = {}

    def register(self, name: str, handler: Callable):
        self._actions[name] = handler

    def get(self, name: str):
        return self._actions.get(name)

    def available_actions(self):
        return list(self._actions.keys())
