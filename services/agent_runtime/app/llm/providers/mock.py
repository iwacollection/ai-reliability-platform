import json

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class MockProvider(BaseLLMProvider):
    """
    Fake LLM provider used for local development.
    """


    @property
    def name(self) -> str:

        return "mock"


    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:


        system_prompt = request.system_prompt.lower()


        if "noise" in system_prompt:

            result = {
                "noise": False,
                "confidence": 0.95,
                "reason": (
                    "Critical production alert."
                ),
            }


        elif "rca" in system_prompt or "root cause" in system_prompt:

            result = {
                "root_cause": (
                    "High CPU caused by "
                    "traffic spike."
                ),
                "confidence": 0.90,
            }


        elif "healing" in system_prompt:

            result = {
                "action": "restart_pod",
                "target": "payment-api",
                "risk": "medium",
            }


        else:

            result = {
                "message": "unknown",
            }


        return ChatResponse(
            content=json.dumps(
                result
            ),
            model="mock-model",
            prompt_tokens=20,
            completion_tokens=15,
            total_tokens=35,
        )