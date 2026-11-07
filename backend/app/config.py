from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration, read from the environment. No secret is ever hard-coded."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://salary:salary@localhost:5432/salary"

    # Authentication is delegated to Keycloak over OIDC. This application validates
    # tokens; it never issues one and never sees a password, so there is no signing
    # secret here. Tokens are checked against Keycloak's published public keys,
    # fetched once and cached - not by calling Keycloak on every request.
    oidc_issuer: str = "http://localhost:8080/realms/salary"
    oidc_audience: str = "salary-api"
    oidc_jwks_ttl_seconds: int = 3600

    # No ANTHROPIC_API_KEY here, deliberately. The chat service is a separate
    # deployable and is the only process that talks to a third party; it carries
    # its own credential. A key held by a process that never calls the API is an
    # unnecessary credential that can still leak from it.


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process, not per request."""
    return Settings()
