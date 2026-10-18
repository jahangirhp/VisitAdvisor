import pytest

from app.modules.visual.evaluation import (
    EvalPhoto,
    EvaluationError,
    create_series,
    delete_path,
    judge,
    library,
    list_datasets,
    load_dataset,
    save_image,
    summarize,
)

OBJECT = "b" * 24
OTHER = "c" * 24


def photo(**changes):
    row = {
        "id": 1,
        "path": "positive/s/o/p.jpg",
        "object": "o",
        "category": "full",
        "object_type": "painting",
    }
    return EvalPhoto.model_validate(row | changes)


def candidate(object_id, score=0.9):
    return {"object_id": object_id, "name": object_id[:1], "score": score}


def jpeg():
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(out, format="JPEG")
    return out.getvalue()


def test_judge_ranks_expected_object():
    first = judge(photo(), OBJECT, "ambiguous", [candidate(OBJECT)])
    third = judge(
        photo(),
        OBJECT,
        "ambiguous",
        [candidate(OTHER), candidate(OTHER), candidate(OBJECT)],
    )
    missing = judge(photo(), OBJECT, "no_match", [])
    assert (first["rank"], first["top1"], first["top3"]) == (1, True, True)
    assert (third["rank"], third["top1"], third["top3"]) == (3, False, True)
    assert (missing["rank"], missing["top1"], missing["top3"]) == (None, False, False)


def test_unnamed_photo_needs_any_candidate_over_threshold():
    unnamed = photo(object=None, object_type="unknown")
    good = judge(unnamed, None, "ambiguous", [candidate(OTHER, 0.6)], 0.5)
    weak = judge(unnamed, None, "ambiguous", [candidate(OTHER, 0.4)], 0.5)
    none = judge(unnamed, None, "no_match", [], 0.5)
    assert [r["top1"] for r in (good, weak, none)] == [True, False, False]
    assert good["named"] is False


def test_summarize_by_category():
    results = [
        judge(photo(), OBJECT, "ambiguous", [candidate(OBJECT)]),
        judge(
            photo(id=2, category="partial", object_type="statue"),
            OBJECT,
            "no_match",
            [],
        ),
    ]
    summary = summarize(results)
    assert (summary["count"], summary["top1"], summary["top3"]) == (2, 1, 1)
    assert summary["by_category"]["partial"] == {"count": 1, "top1": 0, "top3": 0}
    assert summary["by_type"]["painting"] == {"count": 1, "top1": 1, "top3": 1}
    assert summary["by_type"]["statue"] == {"count": 1, "top1": 0, "top3": 0}


def test_folder_library_and_dataset(tmp_path):
    ref = save_image(
        tmp_path,
        series=None,
        kind="painting",
        filename="The Milkmaid.JPG",
        category="full",
        data=jpeg(),
    )
    assert ref == "references/painting/the-milkmaid.jpg"
    dup = save_image(
        tmp_path,
        series=None,
        kind="statue",
        filename="the-milkmaid.jpg",
        category="full",
        data=jpeg(),
    )
    assert dup == "references/statue/the-milkmaid-2.jpg"
    create_series(tmp_path, "series-1")
    saved = [
        save_image(
            tmp_path,
            series="series-1",
            kind="other",
            filename=name,
            category=category,
            data=jpeg(),
        )
        for name, category in (("the-milkmaid.jpg", "full"), ("x.jpg", "partial"))
    ]
    assert saved[1] == "positive/series-1/x.partial.jpg"
    assert sorted(r.object or "-" for r in load_dataset(tmp_path, "series-1")) == [
        "-",
        "the-milkmaid",
    ]
    delete_path(tmp_path, saved[1])
    save_image(
        tmp_path,
        series="series-1",
        kind="other",
        filename="the-milkmaid-2.jpg",
        category="partial",
        data=jpeg(),
    )
    assert list_datasets(tmp_path) == ["series-1"]
    rows = load_dataset(tmp_path, "series-1")
    # "the-milkmaid-2" prefers the longest reference name.
    assert sorted((r.object, r.category) for r in rows) == [
        ("the-milkmaid", "full"),
        ("the-milkmaid-2", "partial"),
    ]
    assert len(library(tmp_path)["references"]) == 2


def test_folder_rejections(tmp_path):
    with pytest.raises(EvaluationError, match="UNSUPPORTED_IMAGE"):
        save_image(
            tmp_path,
            series=None,
            kind="painting",
            filename="a.gif",
            category="full",
            data=b"x",
        )
    with pytest.raises(EvaluationError, match="INVALID_IMAGE"):
        save_image(
            tmp_path,
            series=None,
            kind="painting",
            filename="a.jpg",
            category="full",
            data=b"x",
        )
    with pytest.raises(EvaluationError, match="INVALID_TYPE"):
        save_image(
            tmp_path,
            series=None,
            kind="../x",
            filename="a.jpg",
            category="full",
            data=jpeg(),
        )
    with pytest.raises(EvaluationError, match="INVALID_NAME"):
        create_series(tmp_path, "../x")
    for path in ("../x.jpg", "references", "positive/../../x"):
        with pytest.raises(EvaluationError, match="INVALID_PATH"):
            delete_path(tmp_path, path)


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
