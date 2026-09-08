from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl, JsonValue

from app.core.domain import NonBlank

SourceType = Literal[
    "official_api",
    "official_website",
    "public_dataset",
    "wikipedia",
    "wikidata",
    "document",
    "manual",
    "other",
]


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: HttpUrl | None = None
    title: NonBlank
    publisher: NonBlank | None = None
    type: SourceType
    external_id: NonBlank | None = None
    language: NonBlank | None = None


class Source(SourceCreate):
    """Domain record and public response."""

    import_provider: str | None = None
    raw_record: dict[str, JsonValue] | None = None
    last_fetched_at: datetime | None = None
    id: str
    location_id: str
    created_at: datetime
    updated_at: datetime
