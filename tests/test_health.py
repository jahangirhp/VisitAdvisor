from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure

from app.core.exceptions import ApplicationError
from app.main import create_app


@pytest.fixture
def mongodb(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "visitadvisor_test")
    with patch("app.main.MongoDB") as factory:
        database = factory.return_value
        database.ping = AsyncMock()
        database.close = AsyncMock()
        database.ensure_indexes = AsyncMock()
        yield database


def test_health_and_docs_and_shutdown(mongodb):
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.get("/health/database")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "connected"}
        assert client.get("/docs").status_code == 200
        assert set(client.get("/openapi.json").json()["paths"]) >= {
            "/health",
            "/health/database",
        }
    assert mongodb.ping.await_count == 2
    mongodb.close.assert_awaited_once()


def test_database_unavailable(mongodb):
    with TestClient(create_app()) as client:
        mongodb.ping.side_effect = ConnectionFailure("secret database host")
        response = client.get("/health/database")
        assert response.status_code == 503
        assert response.json() == {
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "Database is unavailable.",
            }
        }
        assert client.get("/health").status_code == 200


def test_startup_failure_closes_client(mongodb):
    mongodb.ping.side_effect = ConnectionFailure("secret database host")
    with pytest.raises(RuntimeError, match="^MongoDB connection failed$"):
        with TestClient(create_app()):
            pytest.fail("Startup should fail")
    mongodb.close.assert_awaited_once()


def test_error_responses(mongodb):
    app = create_app()

    @app.get("/unexpected")
    def unexpected():
        raise RuntimeError("secret internal details")

    @app.get("/expected")
    def expected():
        raise ApplicationError("EXAMPLE_ERROR", "Safe public message.", 409)

    @app.get("/validation")
    def validation(value: int):
        return value

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unexpected")
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
            }
        }
        response = client.get("/expected")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EXAMPLE_ERROR"
        response = client.get("/validation?value=secret")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "secret" not in response.text
        assert client.get("/missing").json()["error"]["code"] == "HTTP_ERROR"


def test_index_initialization_failure_closes_client(mongodb):
    mongodb.ensure_indexes.side_effect = ConnectionFailure("secret index details")
    with pytest.raises(RuntimeError, match="^MongoDB index initialization failed$"):
        with TestClient(create_app()):
            pytest.fail("Startup should fail")
    mongodb.close.assert_awaited_once()
