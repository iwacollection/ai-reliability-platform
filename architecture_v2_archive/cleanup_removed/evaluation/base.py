from abc import ABC, abstractmethod


from services.agent_runtime.app.model.result import (
    AgentResult,
)


from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)


from services.agent_runtime.app.evaluation.models import (
    EvaluationResult,
)



class BaseEvaluator(ABC):
    """
    Base evaluator.

    Evaluate agent execution result.
    """


    @property
    @abstractmethod
    def name(self) -> str:
        pass



    @abstractmethod
    async def evaluate(
        self,
        result: AgentResult,
        execution: AgentExecutionRecord,
    ) -> EvaluationResult:
        pass