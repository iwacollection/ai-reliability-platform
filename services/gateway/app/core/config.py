from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway configuration."""

    app_name: str = "AI Reliability Gateway"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GATEWAY_",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()