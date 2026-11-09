"""The HTTP surface: POST /ask.

The BFF proxies /chat/* here with the caller's claims already verified, so these
tests check the two things this service decides for itself - that a caller without a
role is refused, and that an answer carries the figures it was built from.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.deps import get_model, get_salary
from app.errors import register_error_handlers
from tests.fakes import says

GATEWAY_KEY = "test-gateway-key"


class StubModel:
    """Answers in one go, and streams the same answer in pieces.

    One stub for both endpoints, because they are two deliveries of one behaviour -
    a test that passed on one while the other diverged would be worse than none.
    """

    def __init__(self, text="Total payroll is $1,000,000.00."):
        self.text = text

    def bind_tools(self, tools):
        self.offered = list(tools)
        return self

    def invoke(self, messages):
        self.messages = list(messages)
        return says(self.text)

    def stream(self, messages):
        from langchain_core.messages import AIMessageChunk

        self.messages = list(messages)
        for i in range(0, len(self.text), 8):
            yield AIMessageChunk(content=self.text[i : i + 8])


@pytest.fixture
def model() -> StubModel:
    return StubModel()


@pytest.fixture
def api(salary, model) -> TestClient:
    from app.gateway import install_gateway_guard

    app = FastAPI()
    register_error_handlers(app)
    install_gateway_guard(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(router)
    app.dependency_overrides[get_salary] = lambda: salary
    app.dependency_overrides[get_model] = lambda: model
    return TestClient(app)


def headers(roles: str = "hr_manager") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-1",
        "X-User-Name": "priya",
        "X-User-Roles": roles,
    }


def ask(api, question="What is our total payroll?", roles="hr_manager"):
    return api.post("/ask", json={"question": question}, headers=headers(roles))


def test_an_hr_manager_gets_an_answer(api):
    response = ask(api)

    assert response.status_code == 200
    assert response.json()["answer"].startswith("Total payroll")


def test_the_answer_reports_which_tools_it_used(api):
    body = ask(api).json()

    assert body["used_tools"] == []
    assert body["figures"] == []


def test_a_caller_without_a_role_is_refused(api):
    assert ask(api, roles="").status_code == 403


def test_a_request_without_the_gateway_key_is_refused(api):
    """The same trust boundary the salary service has. Claims headers are only
    trustworthy because nothing but the BFF can reach this port."""
    response = api.post("/ask", json={"question": "hi"}, headers={"X-User-Sub": "x"})

    assert response.status_code == 401


def test_an_empty_question_is_a_422_not_a_model_call(api, model):
    response = ask(api, question="   ")

    assert response.status_code == 422
    assert not hasattr(model, "messages")


def test_a_definitional_question_puts_the_reference_notes_in_the_prompt(api, model):
    """The knowledge base reaching the model is what lets it answer "what does the
    median mean here" without inventing a definition."""
    ask(api, question="How is the median salary calculated?")

    system = model.messages[0].content
    assert "Reference notes" in system
    assert "percentile_cont" in system


def test_an_unrelated_question_carries_no_notes(api, model):
    ask(api, question="How many people work in India?")

    assert "Reference notes" not in model.messages[0].content


def test_health_needs_no_gateway_key(api):
    assert api.get("/health").status_code == 200


# --- streaming -------------------------------------------------------------------


def test_the_stream_endpoint_sends_server_sent_events(api, model):
    """text/event-stream, or the browser's EventSource will not read it."""
    response = api.post(
        "/ask/stream",
        json={"question": "How is the median salary calculated?"},
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_each_event_is_one_json_payload_on_a_data_line(api, model):
    import json

    response = api.post(
        "/ask/stream",
        json={"question": "How is the median salary calculated?"},
        headers=headers(),
    )

    payloads = [
        json.loads(line[len("data:") :])
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert payloads
    assert payloads[-1]["type"] == "done"


def test_the_stream_ends_with_the_figures(api, model):
    import json

    response = api.post(
        "/ask/stream", json={"question": "What is our total payroll?"}, headers=headers()
    )

    last = [
        json.loads(line[len("data:") :])
        for line in response.text.splitlines()
        if line.startswith("data:")
    ][-1]
    assert "used_tools" in last and "figures" in last


def test_streaming_refuses_a_caller_without_a_role(api):
    response = api.post("/ask/stream", json={"question": "x"}, headers=headers(roles=""))

    assert response.status_code == 403


def test_streaming_refuses_without_the_gateway_key(api):
    response = api.post("/ask/stream", json={"question": "x"}, headers={"X-User-Sub": "s"})

    assert response.status_code == 401
