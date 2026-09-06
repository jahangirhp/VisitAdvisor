from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.core.domain import NonBlank

LocationType = Literal[
    "museum",
    "historical_site",
    "landmark",
    "gallery",
    "exhibition",
    "public_space",
    "attraction",
    "other",
]


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: NonBlank
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=200)
    type: LocationType
    city: NonBlank
    country: NonBlank
    coordinates: Coordinates | None = None
    official_website: HttpUrl | None = None
    status: Literal["active", "inactive"] = "active"

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class Location(LocationCreate):
    """Domain record and public response; MongoDB conversion stays in repository."""

    id: str
    created_at: datetime
    updated_at: datetime
