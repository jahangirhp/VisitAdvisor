import json

import pytest

from app.modules.visual.evaluation import (
    EvalPhoto,
    EvaluationError,
    judge,
    list_datasets,
    load_dataset,
    summarize,
)

LOCATION = "a" * 24
OBJECT = "b" * 24
OTHER = "c" * 24


def photo(**changes):
    row = {
        "id": 1,
        "path": "p.jpg",
        "location_id": LOCATION,
        "expected_object_id": OBJECT,
        "category": "full",
        "object_type": "painting",
    }
    return EvalPhoto.model_validate(row | changes)


def write(root, name, rows):
    (root / "datasets").mkdir(exist_ok=True)
    (root / "datasets" / f"{name}.json").write_text(json.dumps(rows))


def candidate(object_id, score=0.9):
    return {"object_id": object_id, "name": object_id[:1], "score": score}


def test_judge_ranks_expected_object():
    first = judge(photo(), "ambiguous", [candidate(OBJECT)])
    third = judge(
        photo(), "ambiguous", [candidate(OTHER), candidate(OTHER), candidate(OBJECT)]
    )
    missing = judge(photo(), "no_match", [])
    assert (first["rank"], first["top1"], first["top3"]) == (1, True, True)
    assert (third["rank"], third["top1"], third["top3"]) == (3, False, True)
    assert (missing["rank"], missing["top1"], missing["top3"]) == (None, False, False)


def test_summarize_by_category():
    results = [
        judge(photo(), "ambiguous", [candidate(OBJECT)]),
        judge(photo(id=2, category="partial", object_type="statue"), "no_match", []),
    ]
    summary = summarize(results)
    assert (summary["count"], summary["top1"], summary["top3"]) == (2, 1, 1)
    assert summary["by_category"]["partial"] == {"count": 1, "top1": 0, "top3": 0}
    assert summary["by_type"]["painting"] == {"count": 1, "top1": 1, "top3": 1}
    assert summary["by_type"]["statue"] == {"count": 1, "top1": 0, "top3": 0}


def test_load_dataset(tmp_path):
    (tmp_path / "p.jpg").write_bytes(b"x")
    row = photo().model_dump()
    write(tmp_path, "good", [row])
    write(tmp_path, "duplicate", [row, row])
    write(tmp_path, "escape", [row | {"path": "../p.jpg"}])
    write(tmp_path, "extra", [row | {"in_database": True}])
    write(tmp_path, "notype", [{k: v for k, v in row.items() if k != "object_type"}])
    assert list_datasets(tmp_path) == ["duplicate", "escape", "extra", "good", "notype"]
    assert len(load_dataset(tmp_path, "good")) == 1
    for name in ("duplicate", "escape", "extra", "notype"):
        with pytest.raises(EvaluationError):
            load_dataset(tmp_path, name)
    with pytest.raises(EvaluationError, match="DATASET_NOT_FOUND"):
        load_dataset(tmp_path, "../good")


def test_api_is_hidden_unless_enabled(tmp_path):
    import httpx

    from app.main import create_app

    class State:
        pass

    application = create_app()
    application.state.settings = State()
    application.state.settings.visual_evaluation_enabled = False

    async def call():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://t"
        ) as client:
            return await client.get("/api/v1/visual-evaluation/datasets")

    import asyncio

    assert asyncio.run(call()).status_code == 404
