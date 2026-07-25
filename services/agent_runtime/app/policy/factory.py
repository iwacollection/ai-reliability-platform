from services.agent_runtime.app.policy.engine import (
    PolicyEngine,
)



def create_policy_engine() -> PolicyEngine:
    """
    Create policy engine.
    """

    return PolicyEngine()