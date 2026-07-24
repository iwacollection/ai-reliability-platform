import asyncio

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.factory import (
    create_llm_registry,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
)


async def main():

    registry = create_llm_registry()

    provider = registry.get(
        "mock",
    )

    client = LLMClient(
        provider,
    )

    response = await client.chat(
        ChatRequest(
            system_prompt="You are SRE.",
            user_prompt="CPU > 90%",
        )
    )

    print(
        response.model_dump()
    )


if __name__ == "__main__":
    asyncio.run(main())