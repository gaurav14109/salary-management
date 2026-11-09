"""The loop: question in, tools run, answer out.

LangChain owns the mechanics - building the provider's tool schemas, parsing tool
calls out of a response, reassembling arguments split across stream chunks, echoing
a tool call back in the shape the API demands. Those were hand-rolled first and each
one shipped a bug: optional arguments that forbade `null`, arguments echoed as a
dict rather than a JSON string, fragments spliced in arrival order. They are not
interesting problems and they are not ours.

What stays here is everything that is *not* generic, and it is all of the safety:

  * the **scope gate**, which refuses a question that is not about payroll before
    the model is called at all;
  * the **tool menu**, filtered by the caller's roles, so a model cannot choose what
    the person may not use;
  * the **turn cap**, so a model that keeps asking for one more tool stops;
  * the **grounding guard**, which discards an answer that no tool and no reference
    note produced - the most important line in the service, because an invented
    payroll figure is indistinguishable from a real one.

A ready-made agent executor would own the loop and leave nowhere to put the last of
those: the guard has to sit between the model's output and the user, which is
exactly the seam an executor hides.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.access import Principal
from app.errors import UnprocessableEntity
from app.services import tools as tool_module
from app.services.scope import REFUSAL, UNAVAILABLE, in_scope

log = logging.getLogger("chat.conversation")

# How many rounds of tool calls before the loop gives up and asks for a final
# answer. Three is enough for the deepest real chain - resolve a department name to
# an id, query it, answer - and small enough that a model stuck in a loop costs
# three calls on a free tier rather than a timeout's worth.
MAX_TURNS = 3

MAX_QUESTION_CHARS = 2_000

SYSTEM_PROMPT = """
You are the assistant inside ACME's salary management tool. You help an HR Manager
understand payroll data.

Rules you must follow:

- Answer only from the results of the tools you are given, or from the reference
  notes provided in the conversation. You have no other knowledge of ACME.
- If the tools cannot answer the question, say so plainly. Never estimate, never
  extrapolate, and never state a figure you were not given.
- Always say what a figure covers - the filter that produced it. When the figure is
  an amount of money, say it is in US dollars. Never say that about a headcount or
  any other count.
- You cannot change any data. If asked to, say that changes are made on the
  employee's own page, not here.
- Do not list or describe individual people unless the question is specifically
  about pay levels, and never more than the tools return.
- Be brief. One short paragraph, or a short list. This is a tool, not a chat.
""".strip()

_WRAP_UP = (
    "Answer now using only what you already have. Do not request any more tools. "
    "If you cannot answer, say so."
)


@dataclass(frozen=True)
class Answer:
    """What the user gets, and what it was built from."""

    text: str
    used_tools: list[str]
    # The raw tool output, so the UI can show the figures the prose describes. A
    # number with no provenance is a number nobody can check.
    figures: list[dict[str, Any]]


def answer(
    question: str,
    *,
    model,
    salary,
    caller: Principal,
    context: str | None = None,
    max_turns: int = MAX_TURNS,
) -> Answer:
    """Answer one question, running whatever permitted tools the model asks for."""
    session = _start(question, model=model, salary=salary, caller=caller, context=context)
    if session is None:
        return Answer(text=REFUSAL, used_tools=[], figures=[])

    for _ in range(max_turns):
        reply = session.ask()
        if not reply.tool_calls:
            return session.finish(_text(reply))
        session.run_tools(reply)

    final = session.ask(wrap_up=True)
    return session.finish(_text(final) or "I could not finish working that out.")


def stream_answer(
    question: str,
    *,
    model,
    salary,
    caller: Principal,
    context: str | None = None,
    max_turns: int = MAX_TURNS,
) -> Iterator[dict]:
    """The same answer, delivered as it is written.

    Tokens cannot be un-sent, which is the whole difficulty: a naive stream shows
    "$4.2 million" word by word and only afterwards discovers that nothing produced
    that figure. So prose is **held until something grounds it**. Once a tool has
    returned data - or a reference note is already in the prompt - everything from
    that point is released the moment it arrives, including what was held. If a turn
    ends still ungrounded, the held text is discarded unseen.

    The hold costs nothing where it matters: the expensive turn is the one *after*
    the tools have run, and by then the answer is grounded and streams live.
    """
    session = _start(question, model=model, salary=salary, caller=caller, context=context)
    if session is None:
        yield {"type": "token", "text": REFUSAL}
        yield {"type": "done", "used_tools": [], "figures": []}
        return

    for _ in range(max_turns):
        grounded = session.grounded
        reply = None

        for piece in session.stream():
            if isinstance(piece, str):
                if grounded:
                    yield {"type": "token", "text": piece}
                # Otherwise held, and discarded unless something grounds it.
            else:
                reply = piece

        if reply is None or not reply.tool_calls:
            yield from session.close(grounded)
            return

        for call in reply.tool_calls:
            yield {"type": "status", "tool": call["name"]}
        session.run_tools(reply)

    # Out of turns. One more pass, which by now is either grounded or refused.
    grounded = session.grounded
    for piece in session.stream(wrap_up=True):
        if isinstance(piece, str) and grounded:
            yield {"type": "token", "text": piece}
    yield from session.close(grounded)


def _start(
    question: str, *, model, salary, caller: Principal, context: str | None
) -> _Session | None:
    """Validate, apply the scope gate, and open a session. None means refused."""
    question = _validated(question)
    if not in_scope(question):
        log.info("out_of_scope", extra={"sub": caller.sub, "chars": len(question)})
        return None

    session = _Session(model=model, salary=salary, caller=caller, context=context)
    session.ask_about(question)
    return session


class _Session:
    """One question's conversation: the messages, the tools, and what they returned.

    Holds the state both entry points need, so the streaming and non-streaming paths
    cannot drift apart on what matters - which tools ran, what grounded the answer,
    where the turn cap bites.
    """

    def __init__(self, *, model, salary, caller: Principal, context: str | None) -> None:
        self._salary = salary
        self._caller = caller
        self._context = context

        system = SYSTEM_PROMPT
        if context:
            # Reference notes go in the system turn, labelled, so the model treats
            # them as background rather than as something the user just said.
            system = f"{system}\n\nReference notes:\n{context}"
        self._messages: list[BaseMessage] = [SystemMessage(system)]

        # Only the tools this caller may use. Filtered here rather than asked for in
        # the prompt, because a prompt is a request: a model cannot be talked into
        # calling something it was never told about.
        bound = tool_module.as_langchain(caller, salary=salary)
        self._with_tools = model.bind_tools(bound) if bound else model
        self._bare = model

        self.used: list[str] = []
        self.attempted: list[str] = []
        self.figures: list[dict[str, Any]] = []

    def ask_about(self, question: str) -> None:
        self._messages.append(HumanMessage(question))

    @property
    def grounded(self) -> bool:
        """A tool returned something, or a note is already in front of the model."""
        return bool(self.used or self._context)

    def ask(self, *, wrap_up: bool = False) -> AIMessage:
        return self._target(wrap_up).invoke(self._turn(wrap_up))

    def stream(self, *, wrap_up: bool = False) -> Iterator[Any]:
        """Yield text fragments as they arrive, then the assembled message.

        Adding the chunks together is what reassembles a tool call whose arguments
        were split across the stream - the bug that cost a whole afternoon when
        this was hand-rolled.
        """
        assembled = None
        for chunk in self._target(wrap_up).stream(self._turn(wrap_up)):
            assembled = chunk if assembled is None else assembled + chunk
            text = _text(chunk)
            if text:
                yield text
        if assembled is not None:
            yield assembled

    def run_tools(self, reply: AIMessage) -> None:
        """Run every tool the model asked for, and record what came back."""
        self._messages.append(reply)
        for call in reply.tool_calls:
            name = call["name"]
            self.attempted.append(name)
            ok, value = self._run_one(name, call.get("args") or {})
            if ok:
                self.used.append(name)
                self.figures.append({"tool": name, "result": value})
            self._messages.append(
                ToolMessage(content=json.dumps(value, default=str), tool_call_id=call["id"])
            )

    def finish(self, text: str) -> Answer:
        """The grounding guard: an answer must have come from somewhere.

        If no tool returned anything and no note was in the prompt, whatever the
        model said came from its own training - and for a figure about ACME that
        means it was invented. The question passed the vocabulary gate, so it looked
        like a payroll question; an invented payroll figure is the single worst
        thing this feature could produce, because it is indistinguishable from a
        real one.

        A tool that *failed* does not count: `used` collects only tools that
        returned something, so "the dashboard is unreachable, here is $4.2m anyway"
        is caught by the same check.
        """
        if self.grounded:
            return Answer(text=text, used_tools=self.used, figures=self.figures)
        if self.attempted:
            log.warning("all_tools_failed", extra={"attempted": self.attempted})
            return Answer(text=UNAVAILABLE, used_tools=[], figures=[])
        log.warning("ungrounded_answer_discarded", extra={"chars": len(text)})
        return Answer(text=REFUSAL, used_tools=[], figures=[])

    def close(self, grounded: bool) -> Iterator[dict]:
        """The streaming equivalent of `finish`, as events."""
        if grounded:
            yield {"type": "done", "used_tools": self.used, "figures": self.figures}
            return
        if self.attempted:
            log.warning("all_tools_failed", extra={"attempted": self.attempted})
            yield {"type": "token", "text": UNAVAILABLE}
        else:
            log.warning("ungrounded_answer_discarded")
            yield {"type": "token", "text": REFUSAL}
        yield {"type": "done", "used_tools": [], "figures": []}

    def _target(self, wrap_up: bool):
        # The wrap-up turn offers no tools at all, so the model has to answer with
        # what it already has rather than asking for one more thing.
        return self._bare if wrap_up else self._with_tools

    def _turn(self, wrap_up: bool) -> list[BaseMessage]:
        return [*self._messages, HumanMessage(_WRAP_UP)] if wrap_up else self._messages

    def _run_one(self, name: str, args: dict) -> tuple[bool, Any]:
        """Run one tool, turning every refusal into text the model can act on.

        Nothing raises. A model that asked for something it should not have gets
        told so and can still answer from what it has, which serves the user better
        than a 500; and the salary API being briefly down should cost one figure,
        not the conversation.
        """
        try:
            return True, tool_module.run(name, args, salary=self._salary, caller=self._caller)
        except tool_module.UnknownTool:
            log.warning("tool_refused", extra={"tool": name, "sub": self._caller.sub})
            return False, f"There is no tool called {name}. Use only the tools provided."
        except tool_module.NotPermitted:
            return False, (
                f"You may not use {name}. Tell the user they do not have access to "
                "that information."
            )
        except Exception:
            log.exception("tool_failed", extra={"tool": name, "sub": self._caller.sub})
            return False, f"The {name} tool could not be reached just now."


def _validated(question: str) -> str:
    """Checked before the model is called: a blank or enormous prompt is a wasted
    call against a rate-limited free tier, and the error is the user's to fix."""
    question = (question or "").strip()
    if not question:
        raise UnprocessableEntity("Ask a question.")
    if len(question) > MAX_QUESTION_CHARS:
        raise UnprocessableEntity(
            f"That question is too long. Keep it under {MAX_QUESTION_CHARS} characters."
        )
    return question


def _text(message) -> str:
    """The prose out of a message or chunk, whatever shape the provider used."""
    if message is None:
        return ""
    if hasattr(message, "text"):
        text = message.text
        return text() if callable(text) else (text or "")
    return str(getattr(message, "content", "") or "")
