from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.retrieval.text import normalize

QueryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
SearchMode = Literal["hybrid", "lexical", "semantic"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: QueryText
    mode: SearchMode = "hybrid"
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not normalize(value):
            raise ValueError("Text must contain letters or numbers")
        return value


class MatchRequest(SearchRequest):
    location_id: str
    limit: Literal[3] = 3


class Candidate(BaseModel):
    object_id: str
    name: str
    creator: str | None = None
    year: int | str | None = None
    source_ids: list[str]
    content_available: bool
    score: float
    lexical_score: float
    vector_score: float | None = None
    signals: list[str]


class SearchResponse(BaseModel):
    location_id: str
    mode_used: Literal["hybrid", "lexical", "semantic", "lexical_exact"]
    warnings: list[str] = Field(default_factory=list)
    candidates: list[Candidate]


class MatchResponse(SearchResponse):
    status: Literal["matched", "ambiguous", "no_match"]
    matched_object_id: str | None = None
