"""Settings, read once from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The shared secret the BFF presents. No default: an unset key must fail at
    # startup rather than read as "no check configured".
    gateway_key: str = ""

    # Where the tools read from. Service name on the compose network - the chat
    # service has no database credentials for salary data and cannot read a salary
    # row except through this API, which is what keeps the boundary real.
    salary_api_url: str = "http://localhost:8000"

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    log_level: str = "INFO"

    @property
    def model_configured(self) -> bool:
        return bool(self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
