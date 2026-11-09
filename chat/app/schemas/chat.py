"""The chat contract."""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(description="What the user typed.", max_length=2_000)


class AskResponse(BaseModel):
    """The prose, and what it was computed from.

    `figures` is the raw tool output. A number in a sentence with no provenance is a
    number nobody can check, and this is a product where being quietly wrong is
    worse than being unavailable - so the UI can always show its working.
    """

    answer: str
    used_tools: list[str]
    figures: list[dict]
