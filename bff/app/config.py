from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the backend-for-frontend.

    This process holds the two things the browser must never see: the Keycloak client
    secret, and the key that encrypts the session cookie. A server can keep a secret;
    a single-page application cannot. That asymmetry is the whole reason this layer
    exists.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── identity provider ───────────────────────────────────────────────────────
    oidc_issuer: str = "http://localhost:8080/realms/salary"
    oidc_client_id: str = "salary-bff"
    oidc_client_secret: str = "local-dev-bff-secret-not-for-production"

    # The audience every accepted token must carry. Without this check a token minted
    # for any other client in the realm would be accepted here.
    oidc_audience: str = "salary-api"
    oidc_jwks_ttl_seconds: int = 3600

    # The provider has two addresses whenever this runs in a container network: the
    # one the browser can resolve, and the one this process can. Keycloak advertises
    # only the first, because that is the value it signs into every token as `iss`.
    # This setting names the second. Empty - the normal case outside Docker - means
    # there is only one address and no rewriting happens.
    oidc_internal_issuer: str = ""

    @property
    def backchannel_issuer(self) -> str:
        """Where this process reaches the provider, as opposed to where the browser does."""
        return self.oidc_internal_issuer or self.oidc_issuer

    # ── this service, and where the browser lives ───────────────────────────────
    bff_base_url: str = "http://localhost:8001"
    spa_base_url: str = "http://localhost:5173"

    # ── upstreams; the browser can reach neither directly ───────────────────────
    salary_api_url: str = "http://localhost:8000"

    # Proves to the services behind that a request came from this process. They are
    # told who the caller is by headers rather than by a token, so this - plus the
    # fact that they publish no host port - is what stops anyone else telling them.
    gateway_key: str = ""
    chat_service_url: str = "http://localhost:8002"

    # ── session ─────────────────────────────────────────────────────────────────
    # A Fernet key: 32 bytes, url-safe base64. Generate with
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Rotating it logs everyone out, which is correct: old cookies become
    # undecodable rather than merely distrusted.
    session_secret: str = "6gXrC0gEwIJL8asC9ifwYE-XougVQpp8h_GBe2Z2HxI="
    session_cookie_name: str = "salary_session"

    # An absolute ceiling, independent of token refresh. Without it a session that
    # keeps refreshing never ends, and a stolen cookie is valid indefinitely.
    session_max_lifetime_seconds: int = 36_000  # 10 hours

    cookie_secure: bool = False  # true wherever the site is served over HTTPS

    # ── limits ──────────────────────────────────────────────────────────────────
    # An unbounded proxy body is a memory-exhaustion primitive. 25 MB covers a
    # 10,000-row spreadsheet with room to spare.
    max_body_bytes: int = 25 * 1024 * 1024

    upstream_connect_timeout_seconds: float = 5.0
    upstream_read_timeout_seconds: float = 60.0  # bulk import is slow by nature

    # Blunts credential stuffing against the login endpoint. Per-instance and
    # in-memory: a real deployment puts this at the edge, where it can see every
    # instance. Defence in depth, not the only defence.
    login_rate_limit_per_minute: int = 20

    # ── observability ───────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = True

    expose_docs: bool = Field(
        default=True,
        description="OpenAPI docs. Off in any deployed environment: a proxy's route "
        "list is a map of the services behind it.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
