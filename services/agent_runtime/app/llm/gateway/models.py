from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)



class LLMTaskType(str, Enum):
    """
    LLM task category.

    Used for routing and policy decisions.
    """


    GENERAL = "general"


    CLASSIFICATION = "classification"


    ANALYSIS = "analysis"


    RCA = "root_cause_analysis"


    REMEDIATION = "remediation"



class LLMPriority(str, Enum):
    """
    LLM priority.

    Used for scheduling and fallback.
    """


    LOW = "low"


    NORMAL = "normal"


    HIGH = "high"




class LLMInvocationContext(BaseModel):
    """
    Metadata used by LLM Gateway.

    Describe who is calling LLM
    and what capability is required.
    """


    model_config = ConfigDict(
        use_enum_values=True,
        validate_default=True,
    )


    #
    # Caller identity
    #

    agent: str


    #
    # Business task
    #

    task: LLMTaskType = (
        LLMTaskType.GENERAL
    )


    #
    # Request priority
    #

    priority: LLMPriority = (
        LLMPriority.NORMAL
    )


    #
    # Output requirements
    #

    require_json: bool = False



    #
    # Model preference override
    #

    preferred_provider: str | None = None


    preferred_model: str | None = None



    #
    # Fallback behavior
    #

    enable_fallback: bool = True





class LLMGatewayRequest(BaseModel):
    """
    Request accepted by LLM Gateway.
    """


    prompt: str


    system_prompt: str = ""


    context: LLMInvocationContext


    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )





class LLMGatewayResponse(BaseModel):
    """
    Standard response returned by Gateway.
    """


    content: str


    provider: str


    model: str


    fallback_used: bool = False