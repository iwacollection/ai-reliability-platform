from common.config import get_settings


def test_load_settings():

    settings = get_settings()

    print(
        settings.model_dump()
    )

    assert (
        settings.llm.provider
        == "mock"
    )

    assert (
        settings.runtime.pipeline
        == "sequential"
    )