"""A chat model that answers from a script.

Speaks the three methods the loop uses - bind_tools, invoke, stream - and nothing
else. A real LangChain FakeMessagesListChatModel exists and does not support
bind_tools with tool calls the way this needs, so the fake is explicit about the
contract rather than borrowing one.
"""

from langchain_core.messages import AIMessage, AIMessageChunk


def wants(*calls) -> AIMessage:
    """A turn that asks for tools. `calls` are (name, args) pairs."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ],
    )


def says(text: str) -> AIMessage:
    return AIMessage(content=text)


class FakeChatModel:
    """Replays scripted turns, recording what it was asked and what it was offered."""

    def __init__(self, *turns) -> None:
        self.turns = list(turns)
        self.seen: list[list] = []
        self.offered: list | None = None
        self.bound = False

    def bind_tools(self, tools):
        bound = FakeChatModel(*self.turns)
        bound.turns = self.turns  # shared, so a scripted turn is consumed once
        bound.seen = self.seen
        bound.offered = list(tools)
        bound.bound = True
        # Recorded on both: the loop uses the bound model, the test holds the one
        # it constructed.
        self.offered = bound.offered
        return bound

    def invoke(self, messages):
        self.seen.append(list(messages))
        return self.turns.pop(0) if self.turns else says("")

    def stream(self, messages):
        """Yield the next turn as chunks, the way a provider would.

        Prose arrives in pieces; a tool call arrives whole, because LangChain has
        already reassembled it by the time the loop sees it.
        """
        self.seen.append(list(messages))
        turn = self.turns.pop(0) if self.turns else says("")
        if turn.tool_calls:
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": __import__("json").dumps(call["args"]),
                        "id": call["id"],
                        "index": i,
                        "type": "tool_call_chunk",
                    }
                    for i, call in enumerate(turn.tool_calls)
                ],
            )
            return
        text = turn.text() if callable(getattr(turn, "text", None)) else str(turn.content)
        for i in range(0, len(text), 5) or [0]:
            yield AIMessageChunk(content=text[i : i + 5])
        if not text:
            yield AIMessageChunk(content="")


def tool_names(offered) -> set:
    return {tool.name for tool in offered or []}
