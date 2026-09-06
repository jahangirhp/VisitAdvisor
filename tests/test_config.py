import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def test_missing_uri():
    with pytest.raises(ValidationError, match="mongodb_uri"):
        Settings()


@pytest.mark.parametrize("uri", ["", "   ", "https://secret.example"])
def test_invalid_uri(uri, monkeypatch):
    monkeypatch.setenv("MONGODB_URI", uri)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert "input_value" not in str(error.value)


def test_defaults_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://user:secret@localhost:27017")
    settings = Settings()
    assert settings.app_port == 8001
    assert settings.mongodb_database == "visitadvisor"
    assert "secret" not in repr(settings)


@pytest.mark.parametrize("field,value", [("APP_PORT", "0"), ("MONGODB_DATABASE", " ")])
def test_invalid_settings(field, value, monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings()
