import os
from typing import Any

import httpx

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)



class OpenAICompatibleProvider(
    BaseLLMProvider
):
    """
    OpenAI compatible LLM provider.

    Compatible with:

    - OpenAI
    - vLLM
    - Ollama
    - other OpenAI API compatible servers
    """



    @property
    def name(
        self,
    ) -> str:

        return "openai"



    def __init__(
        self,
    ) -> None:

        self.base_url = os.getenv(
            "OPENAI_BASE_URL",
            "https://api.openai.com/v1",
        )


        self.api_key = os.getenv(
            "OPENAI_API_KEY",
            "",
        )


        self.model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5",
        )



    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:


        messages: list[dict[str, Any]] = []


        if request.system_prompt:


            messages.append(

                {
                    "role": "system",

                    "content":
                    request.system_prompt,

                }

            )


        messages.append(

            {
                "role": "user",

                "content":
                request.user_prompt,

            }

        )



        payload = {

            "model":
            self.model,


            "messages":
            messages,


            "temperature":
            request.temperature,

        }



        headers = {

            "Content-Type":
            "application/json",

        }



        if self.api_key:


            headers["Authorization"] = (
                f"Bearer {self.api_key}"
            )



        async with httpx.AsyncClient(
            timeout=30,
        ) as client:


            response = await client.post(

                f"{self.base_url}/chat/completions",

                json=payload,

                headers=headers,

            )


            response.raise_for_status()


            data = response.json()



        message = (

            data["choices"][0]["message"]["content"]

        )



        usage = data.get(
            "usage",
            {},
        )



        return ChatResponse(

            content=message,


            model=data.get(
                "model",
                self.model,
            ),


            prompt_tokens=usage.get(
                "prompt_tokens",
                0,
            ),


            completion_tokens=usage.get(
                "completion_tokens",
                0,
            ),


            total_tokens=usage.get(
                "total_tokens",
                0,
            ),

        )