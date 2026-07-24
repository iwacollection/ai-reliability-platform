from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    Chat request sent to an LLM.
    """

    system_prompt: str = Field(
        default=""
    )

    user_prompt: str

    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )


class ChatResponse(BaseModel):
    """
    Standard chat response returned by an LLM.
    """

    content: str

    model: str

    prompt_tokens: int = 0

    completion_tokens: int = 0

    total_tokens: int = 0