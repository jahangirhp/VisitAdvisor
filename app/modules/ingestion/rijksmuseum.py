"""One bounded connector for the official Rijksmuseum Linked Art API."""

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.modules.objects.models import ObjectCreate

SEARCH_URL = "https://data.rijksmuseum.nl/search/collection"
PROVIDER = "rijksmuseum"
ENGLISH = "http://vocab.getty.edu/aat/300388277"
PRIMARY_TITLE = "http://vocab.getty.edu/aat/300417200"
OBJECT_NUMBER = "https://id.rijksmuseum.nl/22015218"


class ImportFailure(Exception):
    """Contains only a safe, public diagnostic code."""


class ImportRecord(BaseModel):
    external_id: str
    type_provided: bool = True
    object: ObjectCreate
    raw_record: dict[str, Any]


def nodes(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ImportFailure("INVALID_RECORD_STRUCTURE")


def text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def classified(node: dict[str, Any], classification: str) -> bool:
    return any(
        item.get("id") == classification for item in nodes(node.get("classified_as"))
    )


def preferred_content(items: list[dict[str, Any]]) -> str | None:
    english = [
        item
        for item in items
        if any(
            language.get("id") == ENGLISH for language in nodes(item.get("language"))
        )
    ]
    return next(
        (
            text(item.get("content"))
            for item in english + items
            if text(item.get("content"))
        ),
        None,
    )


def notation(node: dict[str, Any]) -> str | None:
    items = nodes(node.get("notation"))
    english = [item for item in items if item.get("@language") == "en"]
    return next(
        (
            text(item.get("@value"))
            for item in english + items
            if text(item.get("@value"))
        ),
        None,
    )


def map_record(external_id: str, raw: dict[str, Any]) -> ImportRecord:
    if raw.get("id") != external_id or raw.get("type") != "HumanMadeObject":
        raise ImportFailure("INVALID_RECORD_ID_OR_TYPE")
    names = [
        item for item in nodes(raw.get("identified_by")) if item.get("type") == "Name"
    ]
    primary = [item for item in names if classified(item, PRIMARY_TITLE)]
    name = preferred_content(primary or names)
    if not name:
        raise ImportFailure("MISSING_TITLE")
    aliases = list(
        dict.fromkeys(
            value
            for item in names
            if (value := text(item.get("content"))) and value != name
        )
    )
    production = raw.get("produced_by", {})
    if production is None:
        production = {}
    if not isinstance(production, dict):
        raise ImportFailure("INVALID_RECORD_STRUCTURE")
    # Prefer the museum's attribution statement; it preserves uncertainty/roles.
    creator = preferred_content(
        [
            item
            for item in nodes(production.get("referred_to_by"))
            if classified(item, "http://vocab.getty.edu/aat/300435416")
        ]
    )
    if creator is None:
        actors = [
            actor
            for part in [production] + nodes(production.get("part"))
            for actor in nodes(part.get("carried_out_by"))
        ]
        creator = (
            "; ".join(
                dict.fromkeys(value for actor in actors if (value := notation(actor)))
            )
            or None
        )
    dates = [
        value
        for span in nodes(production.get("timespan"))
        if (value := preferred_content(nodes(span.get("identified_by"))))
    ]
    year = "; ".join(dict.fromkeys(dates)) or None
    types = [
        value for item in nodes(raw.get("classified_as")) if (value := notation(item))
    ]
    description = preferred_content(
        [
            item
            for item in nodes(raw.get("referred_to_by"))
            if classified(item, "http://vocab.getty.edu/aat/300435452")
        ]
    )
    if description is None:
        paragraphs = [
            {**part, "language": part.get("language", subject.get("language"))}
            for subject in nodes(raw.get("subject_of"))
            for part in nodes(subject.get("part"))
            if classified(part, "http://vocab.getty.edu/aat/300048722")
        ]
        description = preferred_content(paragraphs)
    object_number = next(
        (
            text(item.get("content"))
            for item in nodes(raw.get("identified_by"))
            if classified(item, OBJECT_NUMBER)
        ),
        None,
    )
    materials = [
        value for item in nodes(raw.get("made_of")) if (value := notation(item))
    ]
    metadata = {
        key: value
        for key, value in {
            "object_number": object_number,
            "materials": materials,
        }.items()
        if value
    }
    return ImportRecord(
        external_id=external_id,
        type_provided=bool(types),
        object=ObjectCreate(
            name=name,
            aliases=aliases,
            type=types[0] if types else "artwork",
            creator=creator,
            year=year,
            description=description,
            metadata={"rijksmuseum": metadata},
        ),
        raw_record=raw,
    )


class RijksmuseumClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def get_json(self, url: str) -> dict[str, Any]:
        # Follow only known museum links; never crawl arbitrary linked resources.
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"data.rijksmuseum.nl", "id.rijksmuseum.nl"}
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ImportFailure("UNEXPECTED_PROVIDER_URL")
        for attempt in range(3):
            try:
                response = await self.http.get(url, follow_redirects=False)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                        continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ImportFailure("INVALID_PROVIDER_RESPONSE")
                return data
            except httpx.TransportError:
                if attempt == 2:
                    raise ImportFailure("PROVIDER_NETWORK_ERROR") from None
                await asyncio.sleep(2**attempt)
            except httpx.HTTPStatusError:
                raise ImportFailure("PROVIDER_HTTP_ERROR") from None
            except ValueError:
                raise ImportFailure("INVALID_PROVIDER_JSON") from None
        raise ImportFailure("PROVIDER_NETWORK_ERROR")

    async def identifiers(self, limit: int) -> AsyncIterator[str]:
        url: str | None = SEARCH_URL + "?type=painting"
        seen: set[str] = set()
        pages: set[str] = set()
        while url and len(seen) < limit:
            if url in pages:
                raise ImportFailure("REPEATED_PROVIDER_PAGE")
            pages.add(url)
            if not url.startswith(SEARCH_URL + "?"):
                raise ImportFailure("UNEXPECTED_PROVIDER_PAGE")
            page = await self.get_json(url)
            if "orderedItems" not in page:
                raise ImportFailure("INVALID_PROVIDER_PAGE")
            items = nodes(page["orderedItems"])
            for item in items:
                identifier = item.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(
                    r"https://id\.rijksmuseum\.nl/\d+", identifier
                ):
                    raise ImportFailure("INVALID_PROVIDER_ID")
                if identifier not in seen:
                    seen.add(identifier)
                    yield identifier
                    if len(seen) == limit:
                        return
            next_page = page.get("next")
            if next_page and not items:
                raise ImportFailure("EMPTY_PROVIDER_PAGE")
            if next_page is not None and (
                not isinstance(next_page, dict)
                or not isinstance(next_page.get("id"), str)
            ):
                raise ImportFailure("INVALID_PROVIDER_PAGE")
            url = next_page["id"] if next_page else None

    async def record(self, identifier: str) -> ImportRecord:
        raw = await self.get_json(identifier + "?_profile=la-framed")
        return map_record(identifier, raw)
