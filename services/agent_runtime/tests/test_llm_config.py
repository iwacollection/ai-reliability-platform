from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)


def test_create_provider_from_config():

    provider = create_llm_provider()

    print()

    print(
        provider.name
    )

    assert (
        provider.name
        == "mock"
    )