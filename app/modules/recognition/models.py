from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.objects.models import Object
from app.modules.retrieval.models import MatchResponse
from app.modules.retrieval.text import normalize


class OCRExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    text: str = Field(max_length=2000)
    titles: list[str] = Field(max_length=6)
    quality: Literal["readable", "partial", "unreadable", "no_text"]
    multiple_labels: bool
    truncated: bool

    @model_validator(mode="after")
    def titles_are_transcribed(self) -> "OCRExtraction":
        text = normalize(self.text)
        if any(
            not normalize(title)
            or len(title) > 300
            or f" {normalize(title)} " not in f" {text} "
            for title in self.titles
        ):
            raise ValueError("Titles must occur in the transcription")
        return self


class OCRResult(OCRExtraction):
    provider: Literal["openai"] = "openai"
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class RecognitionResponse(BaseModel):
    location_id: str
    status: Literal["matched", "ambiguous", "no_match", "no_text", "unreadable"]
    ocr: OCRResult
    match: MatchResponse | None = None
    object: Object | None = None
    warnings: list[str] = Field(default_factory=list)
