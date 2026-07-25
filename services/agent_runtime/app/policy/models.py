from pydantic import BaseModel


class PolicyDecision(BaseModel):
    """
    Result of policy evaluation.

    Decide whether an action
    can be executed.
    """


    allowed: bool


    approved: bool


    require_human: bool


    reason: str


    policy: str