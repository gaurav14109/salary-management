def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_response_carries_a_request_id(client):
    response = client.get("/health")

    assert response.headers["X-Request-ID"]


def test_supplied_request_id_is_echoed_back(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_core_settings_hold_no_third_party_credential():
    """The chat service is the only process that talks outward; it carries its own key.

    Guards the boundary in docs/architecture.md: a credential held by a process that
    never uses it is an unnecessary credential that can still leak from it.
    """
    from app.config import Settings

    assert "anthropic" not in " ".join(Settings.model_fields).lower()


def test_ready_reports_the_database_is_reachable(client, engine):
    """engine is requested so the schema exists before the endpoint queries it."""
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
