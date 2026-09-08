"""Deterministic canonical identity text and conservative lexical signals."""

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

TEXT_VERSION = "canonical-identity-v1"
MAX_CORPUS = 500
STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "de",
    "den",
    "der",
    "een",
    "en",
    "het",
    "in",
    "of",
    "on",
    "op",
    "the",
    "van",
    "von",
    "with",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def identifiers(obj: dict[str, Any]) -> list[str]:
    values = [obj.get("external_id")]
    metadata = obj.get("metadata") or {}
    values.append(metadata.get("object_number"))
    museum_metadata = metadata.get("rijksmuseum")
    if isinstance(museum_metadata, dict):
        values.append(museum_metadata.get("object_number"))
    return [v for v in values if isinstance(v, str) and v.strip()]


def snapshot(obj: dict[str, Any]) -> dict[str, Any]:
    # Deliberately exclude generated content, even when present on the object.
    return {
        "version": TEXT_VERSION,
        "location_id": obj["location_id"],
        "object_id": str(obj["_id"]),
        "source_ids": sorted(set(obj.get("source_ids", []))),
        "name": obj["name"],
        "aliases": obj.get("aliases", []),
        "creator": obj.get("creator"),
        "year": obj.get("year"),
        "type": obj.get("type"),
        "identifiers": identifiers(obj),
        "description": obj.get("description"),
    }


def fingerprint(obj: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot(obj), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def search_text(obj: dict[str, Any]) -> str:
    data = snapshot(obj)
    lines = []
    for field in (
        "name",
        "aliases",
        "creator",
        "year",
        "type",
        "identifiers",
        "description",
    ):
        value = data[field]
        if value is not None and value != []:
            if isinstance(value, list):
                value = "; ".join(value)
            lines.append(f"{field}: {value}")
    return "\n".join(lines)


def phrase_in(phrase: str, query: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {query} "


def lexical_score(query: str, obj: dict[str, Any]) -> tuple[float, list[str]]:
    q = normalize(query)
    signals: list[str] = []
    for identifier in identifiers(obj):
        # Allow OCR to omit accession punctuation/spaces, never digit prefixes.
        parts = re.findall(r"[^\W_]+", normalize(identifier))
        compact_pattern = r"[\W_]*".join(re.escape(p) for p in parts)
        if compact_pattern and re.search(rf"(?<!\w){compact_pattern}(?!\w)", q):
            return 1.0, ["identifier_exact"]
    title_score = 0.0
    for name in [obj["name"], *obj.get("aliases", [])]:
        title = normalize(name)
        if not title:
            continue
        if phrase_in(title, q):
            score = 0.86 if len(title.split()) >= 2 else 0.65
            signal = "title_exact"
        else:
            # Slide over OCR lines to tolerate surrounding label text and typos.
            words = q.split()
            length = len(title.split())
            similarity = max(
                (
                    SequenceMatcher(
                        None, title, " ".join(words[i : i + length])
                    ).ratio()
                    for i in range(len(words))
                ),
                default=0.0,
            )
            title_tokens = set(title.split()) - STOPWORDS
            overlap = (
                len(title_tokens & set(words)) / len(title_tokens)
                if title_tokens
                else 0.0
            )
            score = max(0.70 * similarity if similarity >= 0.8 else 0, 0.5 * overlap)
            signal = "title_partial"
        if score > title_score:
            title_score = score
            signals = [signal] if score else []
    creator = normalize(obj.get("creator") or "")
    creator_tokens = {t for t in creator.split() if len(t) > 3 and t not in STOPWORDS}
    creator_match = bool(creator_tokens) and (
        phrase_in(creator, q) or len(creator_tokens & set(q.split())) >= 1
    )
    year = normalize(str(obj.get("year") or ""))
    year_match = phrase_in(year, q)
    if creator_match:
        signals.append("creator")
    if year_match:
        signals.append("year")
    score = min(0.96, title_score + 0.08 * creator_match + 0.04 * year_match)
    if title_score == 0 and creator_match:
        score = 0.40 + 0.15 * year_match
    return score, signals
