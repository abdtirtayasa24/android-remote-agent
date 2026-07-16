from fastapi.testclient import TestClient
from timelapse.api.main import app


def test_liveness_returns_minimal_response() -> None:
    client = TestClient(app)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_is_not_exposed() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 404
