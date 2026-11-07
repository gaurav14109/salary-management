from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration, read from the environment. No secret is ever hard-coded."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://salary:salary@localhost:5432/salary"

    # The shared secret that proves a request came from the BFF, which is the only
    # thing permitted to call this service. No default: an unset key makes every
    # request fail closed, because the alternative - a default that ships - means
    # every deployment that forgets to set one shares a key with every other.
    #
    # There is no OIDC configuration here on purpose. This service never sees a
    # token: the BFF verifies one on every request against Keycloak's published
    # keys and forwards the decoded claims as headers. One process parses tokens,
    # so there is one place to get token handling wrong.
    gateway_key: str = ""

    # JSON by default: these logs are read by queries, not by eyes - every 403 for
    # one subject, everything carrying one request id across two services. Set
    # LOG_JSON=false for a readable terminal while developing.
    log_level: str = "INFO"
    log_json: bool = True

    # No ANTHROPIC_API_KEY here, deliberately. The chat service is a separate
    # deployable and is the only process that talks to a third party; it carries
    # its own credential. A key held by a process that never calls the API is an
    # unnecessary credential that can still leak from it.


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process, not per request."""
    return Settings()
