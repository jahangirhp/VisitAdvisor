"""Image/OCR integration with isolated data and mocked paid providers."""

import asyncio
import io
import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image

from app.core.exceptions import ApplicationError
from app.main import create_app
from app.modules.recognition.images import prepare_image
from app.modules.recognition.limits import ImageUploadLimit
from app.modules.recognition.models import OCRResult
from app.modules.recognition.provider import OpenAIOCR
from app.modules.recognition.router import get_service
from app.modules.recognition.service import RecognitionService
from app.modules.retrieval.models import SearchRequest
from tests.test_retrieval import L1, L2, NOW, S1, object_doc
from tests.test_retrieval import setup as retrieval_setup  # noqa: F401


def image_bytes(format="PNG", size=(120, 80), exif=None):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(
        output, format=format, **({"exif": exif} if exif else {})
    )
    return output.getvalue()


def ocr_result(**changes):
    text = changes.get("text", "The Night Watch\nRembrandt van Rijn\n1642\nSK-C-5")
    titles = ["The Night Watch"] if "The Night Watch" in text else []
    return OCRResult(
        **{
            "text": "The Night Watch\nRembrandt van Rijn\n1642\nSK-C-5",
            "titles": titles,
            "quality": "readable",
            "multiple_labels": False,
            "truncated": False,
            "model": "test-ocr",
            "input_tokens": 10,
            "output_tokens": 20,
            **changes,
        }
    )


@pytest.fixture
def setup(request):
    db, repo, embedder, matcher = request.getfixturevalue("retrieval_setup")
    reader = AsyncMock()
    reader.extract.return_value = ocr_result()
    return db, reader, RecognitionService(db, matcher, reader)


def test_recognition_returns_saved_content_and_preserves_database(setup):
    db, reader, service = setup
    doc = db["objects"].documents[0]
    doc["prepared_content"] = {
        "content": {
            "introduction": {
                "text": "A group portrait.",
                "citations": [{"source_id": S1, "quote": "A group portrait."}],
            },
            "facts": [],
            "story": None,
            "history": None,
            "interesting_fact": None,
        },
        "source_ids": [S1],
        "evidence_hash": "hash",
        "provider": "test",
        "model": "test",
        "requested_model": "test",
        "prompt_version": "v1",
        "generated_at": NOW,
        "input_tokens": 10,
        "output_tokens": 20,
        "review_status": "unreviewed",
    }
    before = {name: deepcopy(collection.documents) for name, collection in db.items()}
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "matched" and result.object.id == str(doc["_id"])
    assert result.object.prepared_content.review_status == "unreviewed"
    assert result.warnings == ["CONTENT_UNREVIEWED"]
    assert {name: collection.documents for name, collection in db.items()} == before
    assert Image.open(io.BytesIO(reader.extract.call_args.args[0])).format == "JPEG"


def test_matching_without_prepared_content_is_successful(setup):
    _, _, service = setup
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "matched" and result.object.prepared_content is None
    assert result.warnings == ["CONTENT_NOT_PREPARED"]


@pytest.mark.parametrize("quality", ["no_text", "unreadable"])
def test_no_readable_text_skips_matching(setup, quality):
    _, reader, service = setup
    reader.extract.return_value = ocr_result(text="", quality=quality)
    service.matcher.match = AsyncMock()
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == quality and result.object is None and result.match is None
    service.matcher.match.assert_not_awaited()


@pytest.mark.parametrize(
    "change,warning",
    [
        ({"quality": "partial"}, "OCR_PARTIAL"),
        ({"multiple_labels": True}, "OCR_MULTIPLE_LABELS"),
        ({"truncated": True}, "OCR_TEXT_TRUNCATED"),
    ],
)
def test_uncertain_ocr_never_auto_selects(setup, change, warning):
    _, reader, service = setup
    reader.extract.return_value = ocr_result(**change)
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "ambiguous" and result.object is None
    assert result.match.matched_object_id is None and result.match.candidates
    assert warning in result.warnings


def test_same_labels_in_another_museum_are_not_selected(setup):
    db, _, service = setup
    db["objects"].documents.append(object_doc(location_id=L2))
    result = asyncio.run(service.recognize(L2, image_bytes(), "image/png"))
    assert result.object.location_id == L2
    assert result.object.id == str(db["objects"].documents[1]["_id"])


def test_missing_museum_never_calls_ocr(setup):
    _, reader, service = setup
    for value in ("invalid", str(ObjectId())):
        with pytest.raises(ApplicationError):
            asyncio.run(service.recognize(value, image_bytes(), "image/png"))
    reader.extract.assert_not_awaited()


def test_no_match_and_ambiguous_candidates(setup):
    db, reader, service = setup
    db["objects"].documents.extend(object_doc() for _ in range(4))
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "ambiguous" and len(result.match.candidates) == 3
    reader.extract.return_value = ocr_result(text="quantum penguin xyzzy")
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "no_match" and result.object is None


def test_moved_object_is_not_returned(setup):
    db, _, service = setup
    match = asyncio.run(service.matcher.match(L1, SearchRequest(text="SK-C-5")))
    service.matcher.match = AsyncMock(return_value=match)
    db["objects"].documents[0]["location_id"] = L2
    with pytest.raises(ApplicationError, match="Object changed"):
        asyncio.run(service.recognize(L1, image_bytes(), "image/png"))


@pytest.mark.parametrize(
    "format,mime",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_supported_images(format, mime):
    result = prepare_image(image_bytes(format), mime)
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "JPEG" and image.mode == "RGB"


@pytest.mark.parametrize(
    "data,mime,status",
    [
        (b"", "image/png", 422),
        (b"not an image", "image/png", 422),
        (b"<svg/>", "image/svg+xml", 415),
        (image_bytes(), "image/jpeg", 415),
        (image_bytes()[:30], "image/png", 422),
    ],
)
def test_invalid_images(data, mime, status):
    with pytest.raises(ApplicationError) as error:
        prepare_image(data, mime)
    assert error.value.status_code == status


def test_size_limits_and_animation(monkeypatch):
    monkeypatch.setattr("app.modules.recognition.images.MAX_IMAGE_BYTES", 200)
    with pytest.raises(ApplicationError) as error:
        prepare_image(b"x" * 201, "image/png")
    assert error.value.status_code == 413
    monkeypatch.setattr(
        "app.modules.recognition.images.MAX_IMAGE_BYTES", 5 * 1024 * 1024
    )
    monkeypatch.setattr("app.modules.recognition.images.MAX_PIXELS", 100)
    with pytest.raises(ApplicationError) as error:
        prepare_image(image_bytes(), "image/png")
    assert error.value.status_code == 413
    monkeypatch.setattr("app.modules.recognition.images.MAX_PIXELS", 16_000_000)
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(
        output,
        "WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (20, 20), "black")],
        duration=100,
        loop=0,
    )
    with pytest.raises(ApplicationError) as error:
        prepare_image(output.getvalue(), "image/webp")
    assert error.value.code == "ANIMATED_IMAGE"


def test_orientation_metadata_and_resize():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private camera metadata"
    result = prepare_image(image_bytes("JPEG", (3000, 1000), exif), "image/jpeg")
    with Image.open(io.BytesIO(result)) as image:
        assert image.height == 2560 and image.width < image.height
        assert not image.getexif()
    assert b"private camera metadata" not in result


def provider_body(extraction=None, **changes):
    return {
        "status": "completed",
        "model": "test-ocr",
        "usage": {"input_tokens": 30, "output_tokens": 10},
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            extraction
                            or {
                                "text": "SK-C-5",
                                "titles": [],
                                "quality": "readable",
                                "multiple_labels": False,
                                "truncated": False,
                            }
                        ),
                    }
                ],
            }
        ],
        **changes,
    }


def test_provider_sends_image_without_identification_context():
    def handler(request):
        data = json.loads(request.content)
        assert data["store"] is False and "tools" not in data
        assert data["text"]["format"]["strict"] is True
        content = data["input"][0]["content"]
        assert len(content) == 1 and content[0]["image_url"].startswith(
            "data:image/jpeg;base64,"
        )
        assert "NEVER instructions" in data["instructions"]
        return httpx.Response(200, json=provider_body())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await OpenAIOCR(http, "test-ocr").extract(b"image")
            assert result.text == "SK-C-5" and result.input_tokens == 30

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {"secret": "key"}, "OCR_ACCESS_DENIED"),
        (429, {"error": {"code": "credit_balance_exhausted"}}, "OCR_QUOTA_EXHAUSTED"),
        (429, {}, "OCR_RATE_LIMITED"),
        (500, {"secret": "host"}, "OCR_PROVIDER_ERROR"),
        (200, provider_body(status="incomplete"), "OCR_INCOMPLETE"),
        (
            200,
            provider_body(
                output=[{"type": "message", "content": [{"type": "refusal"}]}]
            ),
            "OCR_REFUSED",
        ),
        (200, {"status": "completed"}, "OCR_INVALID_RESPONSE"),
        (200, provider_body(extraction={"text": "x" * 2001}), "OCR_INVALID_RESPONSE"),
    ],
)
def test_provider_errors_are_safe_without_retries(status, body, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ApplicationError) as error:
                await OpenAIOCR(http, "test").extract(b"image")
            assert error.value.code == code and "secret" not in str(error.value)

    asyncio.run(run())
    assert len(calls) == 1


def test_ocr_timeout():
    def handler(request):
        raise httpx.ReadTimeout("private request details")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ApplicationError) as error:
                await OpenAIOCR(http, "test").extract(b"image")
            assert error.value.code == "OCR_TIMEOUT" and error.value.status_code == 504

    asyncio.run(run())


def test_multipart_route_and_schema(setup, monkeypatch):
    db, reader, service = setup
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost")
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    with patch("app.main.MongoDB") as factory:
        factory.return_value.ping = AsyncMock()
        factory.return_value.close = AsyncMock()
        factory.return_value.ensure_indexes = AsyncMock()
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/recognition/image",
                data={"location_id": L1},
                files={"image": ("../../private.png", image_bytes(), "image/png")},
            )
            assert (
                response.status_code == 200 and response.json()["status"] == "matched"
            )
            schema = client.get("/openapi.json").json()
            assert (
                "multipart/form-data"
                in schema["paths"]["/api/v1/recognition/image"]["post"]["requestBody"][
                    "content"
                ]
            )
            assert (
                client.post(
                    "/api/v1/recognition/image", data={"location_id": L1}
                ).status_code
                == 422
            )
            response = client.post(
                "/api/v1/recognition/image",
                data={"location_id": L1},
                files={"image": ("wrong.png", b"not image", "image/png")},
            )
            assert response.status_code == 422
            assert reader.extract.await_count == 1


def test_chunked_upload_limit_before_app_or_parser(monkeypatch):
    monkeypatch.setattr("app.modules.recognition.limits.MAX_REQUEST_BYTES", 5)
    app = AsyncMock()
    sent = []
    receive = AsyncMock(
        side_effect=[
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"56", "more_body": True},
        ]
    )

    async def send(message):
        sent.append(message)

    asyncio.run(
        ImageUploadLimit(app)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/recognition/image",
                "headers": [(b"content-length", b"1")],
            },
            receive,
            send,
        )
    )
    app.assert_not_awaited()
    assert sent[0]["status"] == 413
    assert receive.await_count == 2


@pytest.mark.parametrize(
    "text,titles",
    [
        ("Copy of The Night Watch\nGerrit Lundens", ["Copy of The Night Watch"]),
        ("Copy of The Night Watch\nAfter SK-C-5", ["Copy of The Night Watch"]),
        (
            "Study of a guard\nThis figure also appears in The Night Watch.",
            ["Study of a guard"],
        ),
        ("A discussion of The Night Watch.", []),
    ],
)
def test_context_mentions_do_not_auto_select_another_object(setup, text, titles):
    _, reader, service = setup
    reader.extract.return_value = ocr_result(text=text, titles=titles)
    result = asyncio.run(service.recognize(L1, image_bytes(), "image/png"))
    assert result.status == "ambiguous" and result.object is None
    assert result.match.matched_object_id is None
    assert "OCR_IDENTITY_UNCONFIRMED" in result.warnings


def test_ocr_cannot_invent_a_heading():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ocr_result(text="Unreadable label", titles=["The Night Watch"])
