"""Number exact source strings so the model selects evidence without rewriting it."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.core.domain import NonBlank
from app.modules.knowledge.models import Citation, SupportedText, VisitorContent


class ReferencedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: NonBlank
    evidence_ids: list[StrictInt] = Field(min_length=1, max_length=10)


class ReferencedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    introduction: ReferencedText | None
    facts: list[ReferencedText] = Field(max_length=5)
    story: ReferencedText | None
    history: ReferencedText | None
    interesting_fact: ReferencedText | None


def evidence_catalog(serialized: str) -> list[dict[str, Any]]:
    sources = json.loads(serialized)
    if not isinstance(sources, dict):
        raise ValueError("Invalid evidence")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def walk(source_id: str, value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {"id", "type", "@context", "_label", "@language"}:
                    walk(source_id, child, f"{path}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(source_id, child, f"{path}/{index}")
        elif (
            isinstance(value, str)
            and value.strip()
            and not value.startswith(("http://", "https://"))
        ):
            if (source_id, value) not in seen:
                seen.add((source_id, value))
                entries.append(
                    {
                        "id": len(entries),
                        "source_id": source_id,
                        "path": path,
                        "quote": value,
                    }
                )

    for source_id, raw in sources.items():
        walk(source_id, raw, "")
    return entries


def resolve_content(
    content: ReferencedContent, catalog: list[dict[str, Any]]
) -> VisitorContent:
    def resolve(section: ReferencedText | None) -> SupportedText | None:
        if section is None:
            return None
        citations = []
        for index in dict.fromkeys(section.evidence_ids):
            if index < 0 or index >= len(catalog):
                raise ValueError("Unknown evidence ID")
            entry = catalog[index]
            citations.append(
                Citation(source_id=entry["source_id"], quote=entry["quote"])
            )
        return SupportedText(text=section.text, citations=citations)

    return VisitorContent(
        introduction=resolve(content.introduction),
        facts=[item for fact in content.facts if (item := resolve(fact)) is not None],
        story=resolve(content.story),
        history=resolve(content.history),
        interesting_fact=resolve(content.interesting_fact),
    )
