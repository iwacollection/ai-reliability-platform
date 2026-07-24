from services.agent_runtime.app.evaluation.base import (
    BaseEvaluator,
)



class EvaluationRegistry:
    """
    Evaluator registry.
    """


    def __init__(self):

        self._evaluators: dict[str, BaseEvaluator] = {}



    def register(
        self,
        evaluator: BaseEvaluator,
    ):

        self._evaluators[
            evaluator.name
        ] = evaluator



    def get(
        self,
        name: str,
    ) -> BaseEvaluator:

        return self._evaluators[name]



    def list(
        self,
    ) -> list[BaseEvaluator]:

        return list(
            self._evaluators.values()
        )