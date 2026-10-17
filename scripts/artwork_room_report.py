"""Build the ticket #9 visual report from the local reviewed photo collection."""

# ruff: noqa: E501
import argparse
import html
import json
import math
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--collection-dir", type=Path, default=Path(".local/ticket9-collection-2026-10-16")
)
args = parser.parse_args()
root = args.collection_dir.resolve()
manifest = json.loads((root / "manifest-v2.json").read_text())
report = json.loads((root / "retest-result-2026-10-17.json").read_text())
names = json.loads((root / "result-object-names.json").read_text())
credits = {p["path"]: p for p in json.loads((root / "credits-v2.json").read_text())}


def e(value):
    return html.escape(str(value), quote=True)


def name(oid):
    return names.get(oid, "Unknown catalog object")


def attention(row):
    return (
        row["http_status"] != 200
        or (bool(row["expected_object_id"]) and not row["top3_correct"])
        or bool(
            row["static_suggestion"]
            and row["static_suggestion"] != row["expected_object_id"]
        )
    )


def metric(label, value, passed, note):
    return f'<div class="metric"><div>{e(label)}</div><strong>{e(value)}</strong><span class="pill {"good" if passed else "bad"}">{"PASS" if passed else "FAIL"}</span><small>{e(note)}</small></div>'


metrics = ""
for path, label in [("upload", "Upload"), ("camera", "Camera")]:
    p = report["paths"][path]
    times = sorted(r["seconds"] for r in report["rows"] if r["path"] == path)
    p95 = times[math.ceil(len(times) * 0.95) - 1]
    metrics += f'<section class="panel"><h2>{label}</h2><div class="metrics">'
    for key, text, target in [
        ("top1", "Correct first choice", "Target ≥90%"),
        ("top3", "Correct within top three", "Target ≥95%"),
    ]:
        v = p[key]
        metrics += metric(
            text,
            f"{v['correct']}/{v['total']} · {v['rate']:.1%}",
            p["gates"]["top1_90_percent" if key == "top1" else "top3_95_percent"],
            target,
        )
    metrics += metric(
        "False suggestions on rooms",
        f"{p['room_only_false_suggestions']}/20",
        p["room_only_false_suggestions"] == 0,
        "Target: zero · static policy simulation",
    )
    if path == "camera":
        v = p["correct_suggestion_coverage"]
        metrics += metric(
            "Correct suggestion coverage",
            f"{v['correct']}/{v['total']} · {v['rate']:.1%}",
            p["gates"]["correct_suggestion_coverage_50_percent"],
            "Target ≥50% · static policy simulation",
        )
    metrics += f'</div><p class="timing">Response time p95 <b>{p95:.3f} s</b> · slowest <b>{max(times):.3f} s</b></p></section>'

cards = []
for photo in manifest["photos"]:
    rows = {r["path"]: r for r in report["rows"] if r["photo_id"] == photo["id"]}
    issue = any(attention(r) for r in rows.values())
    title = (
        name(photo["expected_object_id"])
        if photo["expected_object_id"]
        else "Room-only negative"
    )
    c = credits[photo["path"]]
    card = f'<article class="photo-card" data-kind="{"room" if photo["room_only"] else "artwork"}" data-issue="{str(issue).lower()}"><a href="{e(photo["path"])}" target="_blank" rel="noopener"><img loading="lazy" src="{e(photo["path"])}" alt="{e(photo["viewpoint"])}"></a><div class="card-body"><div class="card-heading"><h3>{e(title)}</h3><span class="pill {"bad" if issue else "good"}">{"NEEDS ATTENTION" if issue else "PASS"}</span></div><p class="description">{e(photo["viewpoint"])}</p><p class="expected">Expected: <b>{e(name(photo["expected_object_id"]) if photo["expected_object_id"] else "No target artwork suggestion")}</b></p>'
    for path, label in [("upload", "Upload"), ("camera", "Camera replay")]:
        r = rows[path]
        failed = attention(r)
        if r["room_only"]:
            status = (
                "False room suggestion"
                if r["static_suggestion"]
                else "Correctly withheld a suggestion"
            )
        else:
            status = (
                "Correct first choice"
                if r["top1_correct"]
                else "Correct within top three"
                if r["top3_correct"]
                else "Target missed"
            )
        best = r["candidates"][0] if r["candidates"] else None
        prediction = (
            name(best["id"]) if best and best["score"] > 0 else "No verified match"
        )
        suggestion = (
            name(r["static_suggestion"]) if r["static_suggestion"] else "Withheld"
        )
        candidates = (
            "".join(
                f"<li>{e(name(x['id']))} <span>{x['score']:.3f}{' · no match evidence' if x['score'] == 0 else ''}</span></li>"
                for x in r["candidates"]
            )
            or "<li>No candidates returned</li>"
        )
        card += f'<div class="path-result" data-path="{path}"><div class="path-title"><b>{label}</b><span class="{"bad-text" if failed else "good-text"}">{e(status)}</span></div><p class="prediction">{e(prediction)}</p><div class="facts"><span>Simulated suggestion: <b>{e(suggestion)}</b></span><span>HTTP {r["http_status"]} · {r["seconds"]:.3f} s</span></div><details><summary>Top candidates and similarity scores</summary><ol>{candidates}</ol></details></div>'
    card += f'<footer><span>{e(photo["id"])}</span><a href="{e(photo["source_page"])}" target="_blank" rel="noopener">Photo source</a><span>{e(c["photographer"])}</span><a href="{e(c["license_url"])}" target="_blank" rel="noopener">{e(c["license"])}</a></footer></div></article>'
    cards.append(card)

page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>VisitAdvisor · Recognition test results</title><style>
:root{color-scheme:light;--ink:#18342d;--muted:#586a64;--line:#dbe3dc;--red:#a42a34;--green:#246447}*{box-sizing:border-box}body{margin:0;background:#f5f6f0;color:var(--ink);font:15px/1.5 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:36px 24px 70px}a{color:#246447}header{display:flex;justify-content:space-between;gap:20px;align-items:start}.eyebrow{font-size:12px;letter-spacing:.12em;font-weight:700;color:var(--muted)}h1{font-size:clamp(28px,4vw,42px);line-height:1.15;margin:10px 0}h2{font-size:22px;margin:0 0 16px}h3{font-size:18px;margin:0}.subtitle{color:var(--muted);max-width:780px}.pill{display:inline-block;white-space:nowrap;font-size:10px;font-weight:800;letter-spacing:.04em;padding:5px 8px;border-radius:20px}.good{background:#e2f0e6;color:#246447}.bad{background:#f9e2e3;color:var(--red)}.bad-text{color:var(--red)}.good-text{color:var(--green)}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:26px 0}.stat,.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:20px}.stat strong{font-size:28px;display:block}.stat span{color:var(--muted)}.path-panels{display:grid;grid-template-columns:1fr 1fr;gap:16px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:18px}.metric strong{font-size:22px;display:block;margin:3px 0}.metric small{display:block;color:var(--muted);margin-top:5px}.timing{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}.notice{border-left:3px solid #bd8034;padding:12px 18px;background:#fcf1df;margin:20px 0;font-size:13px}.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:26px 0 12px}button,select{font:inherit;padding:9px 13px;border:1px solid #bdcfc3;border-radius:8px;background:white;color:var(--ink);cursor:pointer}button[aria-pressed=true]{background:var(--ink);color:white}button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #c59346;outline-offset:3px}#visible-count{color:var(--muted);font-size:13px;margin:0 0 16px}.photos{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.photo-card{background:white;border:1px solid var(--line);border-radius:14px;overflow:hidden}.photo-card img{width:100%;height:290px;object-fit:contain;background:#e9ece6;display:block}.card-body{padding:20px}.card-heading{display:flex;justify-content:space-between;gap:8px;align-items:start}.description{color:var(--muted);font-size:13px;margin:10px 0}.expected{font-size:13px}.path-result{border-top:1px solid var(--line);padding:14px 0 6px}.path-title{display:flex;justify-content:space-between;gap:8px;font-size:12px}.prediction{font-size:17px;font-weight:650;margin:7px 0}.facts{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--muted);flex-wrap:wrap}details{font-size:12px;margin-top:10px}summary{cursor:pointer;color:var(--muted)}li span{color:var(--muted)}footer{border-top:1px solid var(--line);padding-top:14px;margin-top:12px;display:flex;gap:7px 12px;flex-wrap:wrap;font-size:11px;color:var(--muted)}[hidden]{display:none!important}@media(max-width:760px){main{padding:24px 14px}.summary{grid-template-columns:1fr 1fr}.path-panels,.photos{grid-template-columns:1fr}.card-heading{flex-wrap:wrap}.photo-card img{height:260px}header{display:block}}
</style><main><header><div><div class="eyebrow">VISITADVISOR / TICKET #9 / RECORDED TEST</div><h1>Artwork recognition results</h1><p class="subtitle">See what each photo should identify, what the upload and camera paths returned, and where recognition failed.</p></div><span class="pill bad">VALIDATION FAILED</span></header><div class="summary"><div class="stat"><strong>41</strong><span>21 artwork + 20 room photos</span></div><div class="stat"><strong>82 / 82</strong><span>HTTP requests succeeded</span></div><div class="stat"><strong>19 / 21</strong><span>Correct first choice, each path</span></div><div class="stat"><strong>4 photos</strong><span>Need attention across both paths</span></div></div><div class="path-panels">METRICS</div><div class="notice"><b>What this measures:</b> recorded image uploads and actual WebKit camera crop/JPEG replay. Suggestions below simulate single-frame display thresholds; they do not measure three-frame tracking or a physical phone. Uploads remain manual candidate previews. Similarity scores are not confidence probabilities. Timings exclude physical camera and mobile network; camera reference preparation occurs before requests. This reduced, previously used dataset is a regression test, not a fresh release holdout.</div><div class="controls" aria-label="Photo filters"><button data-filter="all" aria-pressed="true">All photos</button><button data-filter="issues" aria-pressed="false">Needs attention</button><button data-filter="artwork" aria-pressed="false">Artwork photos</button><button data-filter="room" aria-pressed="false">Room-only photos</button><label for="path-filter">Show</label><select id="path-filter"><option value="both">Both test paths</option><option value="upload">Upload only</option><option value="camera">Camera only</option></select></div><p id="visible-count" role="status"></p><div class="photos">CARDS</div><p class="subtitle">Recorded RUNDATE. Original source images are displayed unchanged with attribution. Click a photo to inspect its original file. <a href="index.html">Open the source collection</a>.</p></main><script>
let filter='all';const pathSelect=document.querySelector('#path-filter');function update(){let visible=0;for(const card of document.querySelectorAll('.photo-card')){card.hidden=!(filter==='all'||filter==='issues'&&card.dataset.issue==='true'||filter===card.dataset.kind);if(!card.hidden)visible++;for(const result of card.querySelectorAll('.path-result'))result.hidden=pathSelect.value!=='both'&&result.dataset.path!==pathSelect.value;}document.querySelector('#visible-count').textContent=`Showing ${visible} of 41 photos. Attention badges reflect both test paths.`;}for(const button of document.querySelectorAll('[data-filter]'))button.addEventListener('click',()=>{filter=button.dataset.filter;for(const b of document.querySelectorAll('[data-filter]'))b.setAttribute('aria-pressed',String(b===button));update();});pathSelect.addEventListener('change',update);update();
</script></html>"""
page = (
    page.replace("METRICS", metrics)
    .replace("CARDS", "".join(cards))
    .replace("RUNDATE", e(report["created_at"]))
)
(root / "results.html").write_text(page)
print("Visual report built:", len(cards), "photo cards")
