from abc import ABC, abstractmethod

from services.agent_runtime.app.action.models import (
    ActionPlan,
)



class BaseExecutor(ABC):
    """
    Action executor interface.
    """


    @abstractmethod
    async def execute(
        self,
        action: ActionPlan,
    ) -> dict:
        ...