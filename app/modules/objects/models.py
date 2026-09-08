from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt

from app.core.domain import NonBlank
from app.modules.knowledge.models import PreparedContent


class ObjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: NonBlank
    aliases: list[NonBlank] = Field(default_factory=list)
    type: NonBlank
    creator: NonBlank | None = None
    year: StrictInt | NonBlank | None = None
    description: NonBlank | None = None
    facts: list[NonBlank] = Field(default_factory=list)
    story: NonBlank | None = None
    history: NonBlank | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Object(ObjectCreate):
    """Domain record and public response."""

    prepared_content: PreparedContent | None = None
    external_id: str | None = None
    import_provider: str | None = None
    import_hash: str | None = Field(default=None, exclude=True)
    id: str
    location_id: str
    created_at: datetime
    updated_at: datetime
