"""Render a visual 3D regression report beside its privately cached source photos."""

# ruff: noqa: E501
import argparse
import html
import json
from pathlib import Path


def escape(value):
    return html.escape(str(value), quote=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collection-dir", type=Path, default=Path(".local/3d-object-pilot")
    )
    args = parser.parse_args()
    root = args.collection_dir
    manifest = json.loads((root / "pilot-manifest.json").read_text())
    report = json.loads((root / "evaluation-results.json").read_text())
    credits = {
        p["photo_id"]: p for p in json.loads((root / "pilot-credits.json").read_text())
    }
    inventory = json.loads(
        Path("docs/3d-reference-inventory-2026-10-17.json").read_text()
    )
    names = {p["object_id"]: p["name"] for p in inventory["objects"]}
    photos = {p["id"]: p for p in manifest["photos"]}
    summary = report["summary"]
    cards = []
    for row in report["rows"]:
        p = photos[row["photo_id"]]
        credit = credits[p["id"]]
        expected = names.get(p["expected_object_id"], "No supported sculpture")
        first = row["candidates"][0] if row["candidates"] else None
        wrong = bool(row["suggestion"] and row["suggestion"] != p["expected_object_id"])
        failed = (
            row["http_status"] != 200
            or wrong
            or bool(p["expected_object_id"] and not row["top1_correct"])
        )
        label = (
            "Wrong simulated suggestion"
            if wrong
            else "First choice incorrect; target in top three"
            if failed and row["top3_correct"]
            else "Target missed"
            if failed
            else "Correct first choice"
            if p["expected_object_id"]
            else "Correctly withheld suggestion"
        )
        photographer = credit.get("photographer", "See source credit")
        license_url = credit.get("license_url") or p["source_page"]
        if not str(license_url).startswith("https://"):
            license_url = p["source_page"]
        candidates = "".join(
            f"<li>{escape(names.get(c['id'], 'Unknown object'))}<span>Similarity {c['score']:.3f} · {'minimum view coverage met' if c.get('coverage_ready') else 'reference coverage incomplete'}</span></li>"
            for c in row["candidates"]
        )
        cards.append(
            f'''<article data-positive="{str(bool(p["expected_object_id"])).lower()}" data-failed="{str(failed).lower()}"><a href="{escape(p["path"])}" target="_blank" rel="noopener"><img loading="lazy" src="{escape(p["path"])}" alt="{escape(p["viewpoint"])}"></a><div class="body"><span class="badge {"bad" if failed else "good"}">{escape(label)}</span><h2>Expected: {escape(expected)}</h2><p>{escape(p["viewpoint"])}</p><p><b>First choice:</b> {escape(names.get(first["id"], "Unknown object") if first else "No candidate")}</p><p><b>Simulated suggestion:</b> {escape(names.get(row["suggestion"], "Withheld"))}</p><ol>{candidates}</ol><p>HTTP {row["http_status"]} · {row["seconds"] * 1000:.0f} ms</p><footer><a href="{escape(p["path"])}" download>Download this photo to test</a><a href="{escape(p["source_page"])}" target="_blank" rel="noopener">Original source</a><a href="{escape(license_url)}" target="_blank" rel="noopener">{escape(p["license"])}</a><span>{escape(photographer)} · {escape(p["capture_group"])}</span></footer></div></article>'''
        )
    values = [
        (
            "Correct first choice",
            f"{summary['top1']['correct']}/{summary['top1']['total']} · {summary['top1']['rate']:.1%}",
            "Target ≥90% · FAIL",
        ),
        (
            "Correct in top three",
            f"{summary['top3']['correct']}/{summary['top3']['total']} · {summary['top3']['rate']:.1%}",
            "Target ≥95% · PASS",
        ),
        (
            "Correct suggestions",
            f"{summary['correct_suggestion_coverage']['correct']}/{summary['correct_suggestion_coverage']['total']}",
            "Single-frame simulation",
        ),
        (
            "Wrong suggestions",
            str(
                summary["suggestion_precision"]["total"]
                - summary["suggestion_precision"]["correct"]
            ),
            "Target zero · FAIL",
        ),
        (
            "False suggestions on negatives",
            f"{summary['negative_false_suggestions']}/{summary['negative_count']}",
            "Observed in this small test",
        ),
        (
            "Request p95",
            f"{summary['p95_request_seconds'] * 1000:.0f} ms",
            "Local CPU + Atlas; no mobile network",
        ),
    ]
    stats = "".join(
        f'<div class="stat"><small>{escape(label)}</small><strong>{escape(value)}</strong><small>{escape(note)}</small></div>'
        for label, value, note in values
    )
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VisitAdvisor · 3D results</title><style>*{box-sizing:border-box}body{margin:0;background:#f4f5ef;color:#19372c;font:15px/1.5 system-ui}main{max-width:1200px;margin:auto;padding:30px 20px}h1{font-size:38px;margin:10px 0}h2{font-size:18px}a{color:#285e43}header{display:flex;justify-content:space-between;align-items:center;gap:15px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0}.stat,article{background:white;border:1px solid #d5dfd5;border-radius:12px;overflow:hidden}.stat{padding:18px}.stat strong{display:block;font-size:28px}.stat small{display:block;color:#526458}.notice{background:#f8e9d3;border-left:4px solid #b27d30;padding:15px}.controls{display:flex;gap:10px;margin:24px 0;flex-wrap:wrap}button{font:inherit;cursor:pointer;border:1px solid #bed1c1;background:white;padding:10px 15px;border-radius:8px;color:#19372c}button[aria-pressed=true]{background:#19372c;color:white}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}article img{width:100%;height:290px;object-fit:contain;background:#e6eae1;display:block}.body{padding:20px;min-width:0;overflow-wrap:anywhere}.badge{font-size:12px;font-weight:700;border-radius:15px;padding:5px 9px;display:inline-block}.bad{background:#f7dfe0;color:#9c2931}.good{background:#e0efdf;color:#2e623a}li{margin:8px 0}li span{display:block;font-size:12px;color:#526458}footer{border-top:1px solid #d5dfd5;padding-top:14px;display:flex;gap:10px;flex-wrap:wrap;font-size:11px}footer span{width:100%}[hidden]{display:none!important}@media(max-width:650px){h1{font-size:29px}.stats{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}header{display:block}}</style><main><header><div><small>VISITADVISOR / 3D RECOGNITION / RECORDED REGRESSION</small><h1>3D photo recognition results</h1></div><a href="http://localhost:8005/lab.html" target="_blank" rel="noopener">Try your own photo →</a></header><p>Five sculptures compete: Shiva Nataraja, Spring, Autumn, Summer and Apollo. This evaluation contains 11 sculpture photos and 23 negatives.</p><div class="stats">STATS</div><p class="notice"><b>Validation failed. Automatic suggestions remain disabled in the live preview.</b> The fitted test policy made one incorrect suggestion. The report records that failure rather than tuning thresholds on these test results. Summer has only one valid reference after an incorrect image was removed. Calibration used 10 positives across only two identities; the 11 evaluation positives come from only three capture groups. This is a historical development regression, not proof of all-angle or production reliability. Suggestions shown here are static simulations; physical-phone tracking and concurrent-user capacity were not measured.</p><div class="controls"><button data-filter="all" aria-pressed="true">All photos</button><button data-filter="failed" aria-pressed="false">Needs attention</button><button data-filter="positive" aria-pressed="false">Sculpture photos</button><button data-filter="negative" aria-pressed="false">Negative photos</button></div><p id="count" role="status"></p><div class="grid">CARDS</div><p>Similarity scores are not confidence probabilities. Download an original photo and select it in the photo lab to inspect the actual camera crop and current candidates. Photos remain unchanged with source/license attribution.</p></main><script>const cards=[...document.querySelectorAll('article')];function show(filter){let count=0;for(const card of cards){card.hidden=!(filter==='all'||filter==='failed'&&card.dataset.failed==='true'||filter==='positive'&&card.dataset.positive==='true'||filter==='negative'&&card.dataset.positive==='false');if(!card.hidden)count++;}document.querySelector('#count').textContent=`Showing ${count} of ${cards.length} evaluation photos.`;}for(const button of document.querySelectorAll('button[data-filter]'))button.addEventListener('click',()=>{for(const other of document.querySelectorAll('button[data-filter]'))other.setAttribute('aria-pressed',String(button===other));show(button.dataset.filter);});show('all');</script></html>"""
    (root / "results.html").write_text(
        page.replace("STATS", stats).replace("CARDS", "".join(cards))
    )
    print(f"Built visual 3D report: {len(cards)} evaluation photos")


if __name__ == "__main__":
    main()
