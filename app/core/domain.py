"""Small shared validation types, not a repository or service framework."""

from typing import Annotated

from bson import ObjectId
from pydantic import StringConstraints

from app.core.exceptions import ApplicationError

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def validate_id(value: str) -> str:
    if not ObjectId.is_valid(value):
        raise ApplicationError(
            "INVALID_OBJECT_ID", "ID must be a 24-character hex string.", 422
        )
    return str(ObjectId(value))
