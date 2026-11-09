"""The assistant service.

Separate from the salary API on purpose. It holds no salary data and has no database
credentials for any: it reads through the salary API with the asking user's claims,
so the role checks that protect the UI protect the assistant too, and there is one
data-access path to audit rather than two.
"""

import logging

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.errors import register_error_handlers
from app.gateway import install_gateway_guard
from app.logging_setup import configure_logging

log = logging.getLogger("chat.main")


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="ACME salary assistant",
        description="Answers questions about payroll from whitelisted, read-only tools.",
        version="0.1.0",
    )
    register_error_handlers(app)
    install_gateway_guard(app)
    app.include_router(router)

    if not settings.model_configured:
        # Logged at startup rather than discovered on the first question, so the
        # gap is visible in the deployment log.
        log.warning("model_not_configured", extra={"detail": "GROQ_API_KEY is unset"})

    return app


app = create_app()
