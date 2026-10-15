"""Central environment configuration, loaded when the application starts."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: str = Field(default="development", min_length=1)
    app_host: str = Field(default="0.0.0.0", min_length=1)
    app_port: int = Field(default=8001, ge=1, le=65535)
    openai_api_key: SecretStr | None = None
    llm_model: str = Field(default="gpt-4.1-mini-2025-04-14", min_length=1)
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    embedding_dimensions: int = Field(default=1536, ge=1, le=3072)
    vector_index_name: str = Field(default="visitadvisor_identity_v1", min_length=1)

    ocr_model: str = Field(default="gpt-4.1-mini-2025-04-14", min_length=1)
    ocr_timeout_seconds: float = Field(default=30, ge=1, le=60)
    visual_enabled: bool = False
    visual_evaluation_enabled: bool = False
    visual_evaluation_root: Path = Path("tests/data/visual")
    live_camera_location_id: str | None = None
    live_camera_object_ids: list[str] = Field(default_factory=list, max_length=12)
    live_camera_refresh_seconds: int = Field(default=30, ge=5, le=120)
    live_camera_bundle: Path = Path(".local/live-camera")

    mongodb_uri: SecretStr
    mongodb_database: str = Field(default="visitadvisor", min_length=1)

    @field_validator("mongodb_uri")
    @classmethod
    def validate_uri(cls, value: SecretStr) -> SecretStr:
        uri = value.get_secret_value()
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGODB_URI must be a non-empty MongoDB connection URI")
        return value

    @field_validator("mongodb_database")
    @classmethod
    def validate_database(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in '/\\. "$*<>:|?\x00'):
            raise ValueError("MONGODB_DATABASE must be a valid database name")
        return value
