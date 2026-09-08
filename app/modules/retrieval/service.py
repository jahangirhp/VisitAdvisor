import math

from pymongo.errors import PyMongoError

from app.core.exceptions import ApplicationError
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.retrieval.models import (
    Candidate,
    MatchResponse,
    SearchRequest,
    SearchResponse,
)
from app.modules.retrieval.provider import Embedder, RetrievalFailure
from app.modules.retrieval.repository import RetrievalRepository
from app.modules.retrieval.text import fingerprint, lexical_score


class RetrievalService:
    def __init__(
        self, repository: RetrievalRepository, embedder: Embedder | None
    ) -> None:
        self.repository = repository
        self.embedder = embedder

    async def search(self, location_id: str, query: SearchRequest) -> SearchResponse:
        location = await LocationService(LocationRepository(self.repository.db)).get(
            location_id
        )
        location_id = location.id
        try:
            docs = await self.repository.objects(location_id)
        except RetrievalFailure as exc:
            raise ApplicationError(
                str(exc), "Museum exceeds the retrieval limit.", 503
            ) from None
        candidates = {}
        for doc in docs:
            score, signals = lexical_score(query.text, doc)
            candidate = Candidate(
                object_id=str(doc["_id"]),
                name=doc["name"],
                creator=doc.get("creator"),
                year=doc.get("year"),
                source_ids=doc.get("source_ids", []),
                content_available=bool(doc.get("prepared_content")),
                score=score,
                lexical_score=score,
                signals=signals,
            )
            candidates[candidate.object_id] = candidate
        lexical = sorted(candidates.values(), key=lambda c: (-c.score, c.object_id))
        mode = query.mode
        warnings: list[str] = []
        # Unique, strong identity evidence avoids a paid query embedding.
        exact = (
            bool(lexical)
            and lexical[0].score >= 0.86
            and (
                "identifier_exact" in lexical[0].signals
                or "title_exact" in lexical[0].signals
            )
            and (len(lexical) == 1 or lexical[0].score - lexical[1].score >= 0.15)
        )
        if mode == "hybrid" and exact:
            return SearchResponse(
                location_id=location_id,
                mode_used="lexical_exact",
                candidates=[c for c in lexical if c.score >= 0.25][: query.limit],
            )
        if mode != "lexical" and docs:
            try:
                if await self.repository.index_status() != "ready":
                    raise RetrievalFailure("VECTOR_INDEX_NOT_READY")
                if self.embedder is None:
                    raise RetrievalFailure("EMBEDDING_NOT_CONFIGURED")
                count = await self.repository.embedding_count(location_id)
                if count == 0:
                    raise RetrievalFailure("TEXT_EMBEDDINGS_NOT_PREPARED")
                if count < len(docs):
                    warnings.append("PARTIAL_EMBEDDING_COVERAGE")
                embedding = await self.embedder.embed([query.text])
                rows = await self.repository.vector_search(
                    location_id, embedding.vectors[0]
                )
                current = {str(d["_id"]): d for d in docs}
                for row in rows:
                    obj = current.get(row["object_id"])
                    if (
                        obj is None
                        or row["location_id"] != location_id
                        or row["content_hash"] != fingerprint(obj)
                    ):
                        warnings.append("STALE_VECTOR_RESULTS_IGNORED")
                        continue
                    value = float(row["score"])
                    if not math.isfinite(value) or not 0 <= value <= 1:
                        raise RetrievalFailure("INVALID_VECTOR_RESULT")
                    candidate = candidates[row["object_id"]]
                    candidate.vector_score = max(candidate.vector_score or 0, value)
                    candidate.signals.append("semantic")
            except (RetrievalFailure, PyMongoError) as exc:
                code = (
                    str(exc)
                    if isinstance(exc, RetrievalFailure)
                    else "VECTOR_SEARCH_UNAVAILABLE"
                )
                if mode == "semantic":
                    raise ApplicationError(
                        code, "Semantic retrieval is unavailable.", 503
                    ) from None
                warnings.append(code)
                mode = "lexical"
        ranked = []
        for candidate in candidates.values():
            vector = candidate.vector_score
            # Atlas cosine score is (1 + cosine) / 2. Ignore weak neighbors.
            semantic = max(0.0, ((vector or 0) - 0.5) * 2)
            if mode == "semantic":
                candidate.score = semantic
            elif mode == "hybrid" and vector is not None:
                candidate.score = max(
                    candidate.lexical_score,
                    0.65 * semantic,
                    0.7 * candidate.lexical_score + 0.3 * semantic,
                )
            threshold = 0.3 if mode == "semantic" else 0.25
            if candidate.score >= threshold:
                candidate.score = round(candidate.score, 6)
                ranked.append(candidate)
        ranked.sort(key=lambda c: (-c.score, c.object_id))
        return SearchResponse(
            location_id=location_id,
            mode_used=mode,
            warnings=sorted(set(warnings)),
            candidates=ranked[: query.limit],
        )

    async def match(self, location_id: str, query: SearchRequest) -> MatchResponse:
        result = await self.search(location_id, query.model_copy(update={"limit": 3}))
        candidates = result.candidates
        matched = (
            bool(candidates)
            and candidates[0].lexical_score >= 0.86
            and (
                "identifier_exact" in candidates[0].signals
                or "title_exact" in candidates[0].signals
            )
            and (
                len(candidates) == 1
                or candidates[0].score - candidates[1].score >= 0.15
            )
        )
        # Similarity scores are ranking heuristics, never calibrated probabilities.
        return MatchResponse(
            **result.model_dump(),
            status="matched" if matched else "ambiguous" if candidates else "no_match",
            matched_object_id=candidates[0].object_id if matched else None,
        )
