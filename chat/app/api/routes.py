"""POST /ask - one question, one answer.

Stateless by design for now: each question is answered on its own. Conversation
history would mean storing what people asked about payroll, which is a data-retention
decision rather than a technical one, and it is not needed to answer the questions
this assistant exists for.
"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.access import Principal, Role, require_role
from app.deps import get_model, get_salary
from app.errors import ChatError
from app.knowledge import as_context, search
from app.schemas.chat import AskRequest, AskResponse
from app.services import conversation

log = logging.getLogger("chat.api")

router = APIRouter(tags=["chat"])


@router.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    principal: Principal = Depends(require_role(Role.ADMIN, Role.HR_MANAGER)),
    salary=Depends(get_salary),
    model=Depends(get_model),
) -> AskResponse:
    notes = search(request.question)
    result = conversation.answer(
        request.question,
        model=model,
        salary=salary,
        caller=principal,
        context=as_context(notes) if notes else None,
    )

    log.info(
        "answered",
        extra={
            "sub": principal.sub,
            "tools": result.used_tools,
            "notes": [note.title for note in notes],
            # Deliberately not the question or the answer: both are free text about
            # salaries, and a log is the wrong place for either.
            "question_chars": len(request.question),
        },
    )
    return AskResponse(answer=result.text, used_tools=result.used_tools, figures=result.figures)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/ask/stream")
def ask_stream(
    request: AskRequest,
    principal: Principal = Depends(require_role(Role.ADMIN, Role.HR_MANAGER)),
    salary=Depends(get_salary),
    model=Depends(get_model),
) -> StreamingResponse:
    """The same answer, delivered as it is written.

    Server-sent events rather than a websocket: this is one-directional and
    short-lived, and SSE survives proxies that a websocket upgrade does not.

    Three event types. `status` while a tool runs - a tool round is the slow part,
    and silence for three seconds reads as broken. `token` for each piece of prose.
    `done` last, carrying the tools used and the figures they returned, which is the
    provenance the non-streaming endpoint returns in its body.

    Errors inside the generator cannot become a status code: by then the response
    has begun. They arrive as a final `error` event, which the client renders in
    place of the answer.
    """
    notes = search(request.question)

    def events():
        try:
            for event in conversation.stream_answer(
                request.question,
                model=model,
                salary=salary,
                caller=principal,
                context=as_context(notes) if notes else None,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except ChatError as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': exc.detail})}\n\n"
        except Exception:
            log.exception("stream_failed", extra={"sub": principal.sub})
            yield (
                "data: " + json.dumps({"type": "error", "detail": "Something went wrong."}) + "\n\n"
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # Without this nginx buffers the whole stream and delivers it at once,
            # which is the thing streaming exists to avoid.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
