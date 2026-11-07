"""The session cookie: encrypted, httpOnly, and the reason no token reaches the browser.

An SPA holding an access token loses it to any successful XSS. Here the browser holds
an opaque blob it cannot read and JavaScript cannot reach, and the tokens live inside
it, decrypted only by this process.
"""

import json
import time
from dataclasses import asdict, dataclass, field

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@dataclass
class Session:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds, access token
    sub: str
    username: str
    email: str | None
    roles: list[str]
    csrf_token: str
    created_at: float = field(default_factory=time.time)

    def access_token_expired(self, *, leeway: int = 30) -> bool:
        """Expired slightly early, so a token cannot lapse between here and upstream."""
        return time.time() >= self.expires_at - leeway

    def past_absolute_lifetime(self) -> bool:
        """A ceiling that refreshing cannot extend.

        Without it, a session that keeps refreshing never ends and a stolen cookie is
        good forever. Re-authentication is the only thing that resets this.
        """
        return time.time() - self.created_at > get_settings().session_max_lifetime_seconds


def _fernet() -> Fernet:
    return Fernet(get_settings().session_secret.encode())


def encode(session: Session) -> str:
    return _fernet().encrypt(json.dumps(asdict(session)).encode()).decode()


def decode(raw: str | None) -> Session | None:
    """None rather than an exception: a tampered, stale or absent cookie all mean the
    same thing to a caller - not logged in. Fernet authenticates as well as encrypts,
    so an edited cookie fails here rather than decoding into something chosen."""
    if not raw:
        return None
    try:
        session = Session(**json.loads(_fernet().decrypt(raw.encode()).decode()))
    except (InvalidToken, ValueError, TypeError):
        return None
    if session.past_absolute_lifetime():
        return None
    return session


def encrypt_text(value: str) -> str:
    """For the short-lived login transaction cookie, which is not a session."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None
