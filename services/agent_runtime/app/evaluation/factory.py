from services.agent_runtime.app.evaluation.registry import (
    EvaluationRegistry,
)

from services.agent_runtime.app.evaluation.evaluator import (
    DefaultEvaluator,
)



def create_evaluation_registry() -> EvaluationRegistry:
    """
    Create default evaluation registry.
    """


    registry = EvaluationRegistry()


    registry.register(
        DefaultEvaluator()
    )


    return registry