from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class AppConfig(BaseModel):
    """
    Application configuration.
    """

    name: str

    version: str


class LLMConfig(BaseModel):
    """
    LLM configuration.
    """

    provider: str

    temperature: float

    timeout: int


class RuntimeConfig(BaseModel):
    """
    Agent runtime configuration.
    """

    pipeline: str

    max_workers: int


class Settings(BaseModel):
    """
    Root application settings.
    """

    app: AppConfig

    llm: LLMConfig

    runtime: RuntimeConfig


@lru_cache
def get_settings() -> Settings:
    """
    Load settings from configs/app.yaml.
    """

    root = Path(__file__).resolve().parents[5]

    config_path = (
        root
        / "configs"
        / "app.yaml"
    )

    data = yaml.safe_load(
        config_path.read_text(
            encoding="utf-8",
        )
    )

    return Settings.model_validate(data)