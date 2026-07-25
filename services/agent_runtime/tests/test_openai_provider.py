import pytest

import httpx

from services.agent_runtime.app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
)



@pytest.mark.asyncio
async def test_openai_provider_name():

    provider = OpenAICompatibleProvider()

    assert provider.name == "openai"




@pytest.mark.asyncio
async def test_openai_provider_chat(monkeypatch):

    provider = OpenAICompatibleProvider()


    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:


        return httpx.Response(

            status_code=200,

            json={

                "id": "test",

                "model": "test-model",

                "choices": [

                    {

                        "message": {

                            "content":
                            "hello from llm",

                        }

                    }

                ],

                "usage": {

                    "prompt_tokens": 10,

                    "completion_tokens": 20,

                    "total_tokens": 30,

                },

            },

        )



    transport = httpx.MockTransport(
        handler
    )



    original_client = httpx.AsyncClient



    class MockAsyncClient(
        original_client
    ):


        def __init__(
            self,
            *args,
            **kwargs,
        ):

            kwargs["transport"] = transport

            super().__init__(
                *args,
                **kwargs,
            )



    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        MockAsyncClient,
    )



    response = await provider.chat(

        ChatRequest(

            system_prompt="You are SRE.",

            user_prompt="Analyze alert.",

        )

    )



    assert response.content == (
        "hello from llm"
    )


    assert response.model == (
        "test-model"
    )


    assert response.total_tokens == 30