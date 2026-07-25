from abc import ABC, abstractmethod

from services.agent_runtime.app.action.models import (
    ActionPlan,
)

from services.agent_runtime.app.policy.models import (
    PolicyDecision,
)



class BasePolicy(ABC):
    """
    Base policy interface.

    Evaluate whether an action
    can be executed.
    """


    @property
    @abstractmethod
    def name(
        self,
    ) -> str:
        ...


    @abstractmethod
    def evaluate(
        self,
        action: ActionPlan,
    ) -> PolicyDecision:
        ...