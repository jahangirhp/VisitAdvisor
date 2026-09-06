"""Consistent public errors without internal exception details."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error(
        request: Request, exc: ApplicationError
    ) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(exc.status_code, "HTTP_ERROR", "Request failed.")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unexpected application error")
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
