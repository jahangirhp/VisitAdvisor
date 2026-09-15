"""Bounded museum link resolution and transient image downloads."""

import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.modules.recognition.images import MAX_IMAGE_BYTES, prepare_image
from app.modules.visual.features import VisualFailure

HOSTS = {
    "data.rijksmuseum.nl",
    "id.rijksmuseum.nl",
    "iiif.micr.io",
    "upload.wikimedia.org",
}
MAX_REQUESTS = 32
MAX_VIEWS = 4


def validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in HOSTS
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise VisualFailure("UNEXPECTED_REFERENCE_URL")


def links(value: Any) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, dict) else value
    if not isinstance(items, list) or any(
        not isinstance(v, dict) or not isinstance(v.get("id"), str) for v in items
    ):
        raise VisualFailure("INVALID_VISUAL_METADATA")
    return list(dict.fromkeys(v["id"] for v in items))


def linked_url(identifier: str) -> str:
    if not re.fullmatch(r"https://id\.rijksmuseum\.nl/\d+", identifier):
        raise VisualFailure("UNEXPECTED_REFERENCE_ID")
    return (
        identifier.replace("id.rijksmuseum.nl", "data.rijksmuseum.nl")
        + "?_profile=la-framed"
    )


class ReferenceClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.requests = 0

    async def fetch(self, url: str, maximum: int) -> tuple[bytes, str]:
        try:
            async with asyncio.timeout(35):
                for _ in range(4):
                    validate_url(url)
                    if self.requests >= MAX_REQUESTS:
                        raise VisualFailure("REFERENCE_REQUEST_LIMIT")
                    self.requests += 1
                    async with self.http.stream(
                        "GET", url, follow_redirects=False, timeout=15
                    ) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > maximum:
                                raise VisualFailure("REFERENCE_TOO_LARGE")
                        return bytes(body), response.headers.get(
                            "content-type", ""
                        ).split(";")[0].strip().lower()
                raise VisualFailure("REFERENCE_REDIRECT_LIMIT")
        except (httpx.HTTPError, TimeoutError):
            raise VisualFailure("REFERENCE_FETCH_FAILED") from None

    async def metadata(self, url: str) -> dict[str, Any]:
        body, _ = await self.fetch(url, 1024 * 1024)
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError:
            raise VisualFailure("INVALID_VISUAL_METADATA") from None

    async def image(self, url: str) -> tuple[bytes, str]:
        body, mime = await self.fetch(url, MAX_IMAGE_BYTES)
        content_hash = hashlib.sha256(body).hexdigest()
        return await asyncio.to_thread(prepare_image, body, mime), content_hash

    async def resolve(
        self, sources: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        references: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for source in sources:
            raw = source.get("raw_record") or {}
            for visual_id in links(raw.get("shows")):
                visual = await self.metadata(linked_url(visual_id))
                if visual.get("id") != visual_id:
                    raise VisualFailure("REFERENCE_ID_MISMATCH")
                for digital_id in links(visual.get("digitally_shown_by")):
                    digital = await self.metadata(linked_url(digital_id))
                    if digital.get("id") != digital_id:
                        raise VisualFailure("REFERENCE_ID_MISMATCH")
                    for original_url in links(digital.get("access_point")):
                        validate_url(original_url)
                        match = re.fullmatch(
                            r"https://iiif\.micr\.io/([A-Za-z0-9_-]+)/full/[^/]+/0/default\.(?:jpg|png|webp)",
                            original_url,
                        )
                        if not match:
                            warnings.append("UNSUPPORTED_IMAGE_SERVICE")
                            continue
                        service = "https://iiif.micr.io/" + match[1]
                        image_url = service + "/full/800,/0/default.jpg"
                        if image_url in seen:
                            continue
                        if len(references) == MAX_VIEWS:
                            return references, sorted(
                                set(warnings + ["REFERENCE_VIEWS_TRUNCATED"])
                            )
                        seen.add(image_url)
                        manifest = await self.metadata(service + "/manifest")
                        rights = manifest.get("rights")
                        if not rights:
                            warnings.append("REFERENCE_RIGHTS_UNKNOWN")
                        references.append(
                            {
                                "source_ids": [str(source["_id"])],
                                "object_uri": raw.get("id"),
                                "visual_uri": visual_id,
                                "digital_uri": digital_id,
                                "image_url": image_url,
                                "original_image_url": original_url,
                                "rights_status": "recorded" if rights else "unknown",
                                "evidence": {
                                    "visual": visual,
                                    "digital": digital,
                                    "manifest": manifest,
                                },
                            }
                        )
        if not references:
            warnings.append("REFERENCE_IMAGES_MISSING")
        return references, sorted(set(warnings))
