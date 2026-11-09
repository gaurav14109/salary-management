"""What a request needs, built once and injected.

Both of these are overridden in tests, which is the reason they are dependencies
rather than module-level singletons: a test that had to reach a real Groq key or a
running salary API would not be a unit test.
"""

from functools import lru_cache

from langchain_groq import ChatGroq

from app.clients.salary import SalaryClient
from app.config import get_settings
from app.errors import UpstreamUnavailable


@lru_cache
def _salary() -> SalaryClient:
    return SalaryClient()


@lru_cache
def _model() -> ChatGroq:
    settings = get_settings()
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        # Low but not zero. This is a reporting assistant: the same question should
        # give the same answer, and creative phrasing of a payroll figure is not a
        # feature.
        temperature=0.1,
        # One retry on a transient failure. More would mean a user waiting through
        # several backoffs on a free tier that is rate limited by the minute.
        max_retries=1,
    )


def get_salary() -> SalaryClient:
    return _salary()


def get_model() -> ChatGroq:
    settings = get_settings()
    if not settings.model_configured:
        # Fail with the one sentence an operator can act on. Calling Groq with an
        # empty key returns a 401 that surfaces as "the assistant is unavailable",
        # which sends somebody looking in the wrong place.
        raise UpstreamUnavailable("The assistant is not configured: GROQ_API_KEY is unset.")
    return _model()
