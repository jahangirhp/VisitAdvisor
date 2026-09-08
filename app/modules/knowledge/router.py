from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_database
from app.modules.knowledge.models import PreparedContent
from app.modules.knowledge.service import KnowledgeService

router = APIRouter(tags=["knowledge"])


@router.get(
    "/objects/{object_id}/experience", summary="Retrieve prepared visitor content"
)
async def experience(
    object_id: str,
    database: Annotated[AsyncDatabase[dict[str, Any]], Depends(get_database)],
) -> PreparedContent:
    return await KnowledgeService(database).experience(object_id)
