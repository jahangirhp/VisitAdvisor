"""Cap the complete upload before multipart parsing, including chunked bodies."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import error_response
from app.modules.recognition.images import MAX_REQUEST_BYTES


class ImageUploadLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not (
                scope["path"].rstrip("/") == "/api/v1/recognition/image"
                or (
                    scope["path"].startswith("/api/v1/admin/venues/")
                    and scope["path"].rstrip("/").endswith("/photos")
                )
            )
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        # Guided admin photos may include one bounded binary foreground mask.
        # The original image limit and visitor upload allowance stay unchanged.
        request_limit = MAX_REQUEST_BYTES
        if scope["path"].startswith("/api/v1/admin/venues/"):
            request_limit += 512 * 1024 + 32 * 1024
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > request_limit:
                await error_response(
                    413, "UPLOAD_TOO_LARGE", "Upload exceeds the photo/form size limit."
                )(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
