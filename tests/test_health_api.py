"""GET /api/health: the public liveness check for the host's health probe and for waking a sleeping service."""

from fastapi.testclient import TestClient


def test_health_answers_ok_without_signing_in_even_when_sign_in_is_required(secure_client: TestClient) -> None:
    resp = secure_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_exposes_nothing_beyond_the_status(secure_client: TestClient) -> None:
    resp = secure_client.get("/api/health")
    assert set(resp.json()) == {"status"}
    assert "set-cookie" not in resp.headers


def test_the_other_endpoints_still_need_sign_in(secure_client: TestClient) -> None:
    assert secure_client.get("/api/students").status_code in (401, 403)
