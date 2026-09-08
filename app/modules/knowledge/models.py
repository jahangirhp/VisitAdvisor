from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import NonBlank


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: NonBlank
    quote: NonBlank


class SupportedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: NonBlank
    citations: list[Citation] = Field(min_length=1, max_length=10)


class VisitorContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    introduction: SupportedText | None
    facts: list[SupportedText] = Field(max_length=5)
    story: SupportedText | None
    history: SupportedText | None
    interesting_fact: SupportedText | None


class PreparedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: VisitorContent
    source_ids: list[str]
    evidence_hash: str
    provider: str
    model: str
    requested_model: str
    prompt_version: str
    generated_at: datetime
    input_tokens: int
    output_tokens: int
    review_status: Literal["unreviewed"] = "unreviewed"
