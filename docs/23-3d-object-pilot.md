# Internet-only 3D object pilot — 2026-10-17

Status: working candidate preview and measured development regression;
**reliability validation failed**. Automatic suggestions are disabled in the
running preview. There is no claim of completed production recognition.

## Implemented and measured

The existing multiview path now has a directly usable photo-testing page at
`http://localhost:8005/lab.html`, alongside the live camera at `/`. It uses the
actual shared browser crop/JPEG encoder and camera HTTP handler. It shows the
original photo, transmitted crop, named candidates, view readiness and timings.
Only one bounded JPEG is sent, without OCR, provider calls or saved visitor frames.
Server abstention is honored even for a high similarity score; reference changes
require reloading, and invalid/oversized files do not reach inference.

```bash
.venv/bin/uvicorn scripts.multiview_preview:create_app --factory \
  --host 127.0.0.1 --port 8005 --no-access-log
.venv/bin/python -m scripts.multiview_report
.venv/bin/python -m http.server 8004 --bind 127.0.0.1 \
  --directory .local/3d-object-pilot
```

The visual evaluation is `http://localhost:8004/results.html`. Download a photo
there, then select it in the photo lab. The report generator uses
`pilot-manifest.json`, `pilot-credits.json` and `evaluation-results.json` from the
ignored collection. Their archived copies are the matching `3d-*` files in this
directory; paths in manifests are relative to the local collection. Verify source
hashes when reconstructing missing originals from the credits.

### Concrete code and reference corrections

- Fixed object-region proposals rejecting the adaptive camera's 512-pixel frames.
  Proposals run at a bounded 224-pixel working size; normalized crops now use the
  actual input dimensions and preserve local detail. At most three model calls
  and the existing inference budget remain enforced.
- A distinct unlabeled image can no longer make two differently labeled copies
  of one image appear to satisfy multiview readiness. Null viewpoints abstain
  rather than failing reference loading. Image identity is required for 3D
  coverage; different embedding vectors alone do not establish different photos.
- Added three reviewed Shiva reference images: the retained canonical view and
  independent Alf van Beem / Eric de Redelijkheid captures. Their full capture
  groups are excluded from the new calibration and evaluation corpus. Preparation
  preserved the canonical source and used the existing atomic reference publisher.
- Removed an incorrect Summer reference: `Rijksmuseumtuinen 32.jpg` depicts a
  different male figure, not the canonical female Summer, BK-NM-8673. The original
  canonical Summer image remains. Summer now truthfully reports one valid view
  and cannot establish a suggestion as the winning object. This does not prevent
  other objects from being confused with Summer photos, as the evaluation shows.
  Do not restore the wrong image or duplicate a resized canonical photo to make
  the coverage indicator green.

Before/after reference metadata, source evidence, published set IDs and preparation
results are retained in `3d-reference-inventory*-2026-10-17.json`,
`3d-shiva-reference-{manifest,preparation}-2026-10-17.json` and
`3d-reference-correction-2026-10-17.json`. Full private BSON backups remain in the
ignored collection. No object identity or canonical source record was modified.

### Results and decision

The initial historical regression tested 12 sculpture positives and 20 room-only
negatives: top-one 9/12, top-three 11/12, p95 140 ms. Suggestions were uncalibrated
and disabled, so zero false suggestions did not establish precision. The repaired
region path returned 32/32 HTTP 200 but the same rankings, with p95 352 ms and up
to three model calls. Regions therefore remain off by default.

A new development manifest separates ten calibration positives and twenty
negatives from eleven evaluation positives and twenty-three negatives. Calibration
positives cover only two identities/five capture groups; evaluation positives
cover all five identities but only three capture groups. All eight Ceescamel
garden photos remain in the same evaluation visit group. The initial Alf capture
was retired into references; it was not silently retained as a test query.

Ninety-five commercially reusable Commons candidates and three Flickr photographs
were downloaded and visually reviewed. Most Commons candidates are unrelated
objects, broad garden scenes or historical material; 98 downloads are not 98
accepted positives. New selected Flickr photographs retain photographer, capture
date, source page, license URL and original bytes. Different crops or resolutions
were not used to manufacture independent captures. The selected original photos
are in the ignored directory; no photo binaries or credentials are committed.

Calibration fitted multiview floor 0.69 / margin 0.02 on its own split: five
correct static suggestions, zero wrong, 50% positive coverage. Those thresholds
were frozen before evaluation and were not adjusted after seeing the failures.

| Evaluation metric | Result | Pilot target |
| --- | ---: | ---: |
| Correct first candidate | 9/11 (81.8%) | >=90% — fail |
| Correct within top three | 11/11 (100%) | >=95% — pass |
| Correct static suggestions | 6/11 (54.5%) | >=50% — pass |
| Wrong static suggestions | 1 | 0 — fail |
| False static suggestions on negatives | 0/23 | 0 observed |
| Successful HTTP requests | 34/34 | All valid |
| In-process HTTP p95 | 143 ms | Reported |
| Cold reference/model preparation | 6.077 s | Reported separately |

Both first-choice errors are Summer photos ranked as Autumn; one crossed the
fitted suggestion thresholds. The correct Summer remains in the top three. All
model/code/photo/reference integrity checks passed, but these results **fail
reliability**. The fitted artifact is archived as
`3d-preview-policy-rejected-2026-10-17.json`; it is not enabled by the preview
launcher. The default launcher ignores unrelated adaptive/calibration settings
and explicitly selects this cohort without changing the normal application.

The evaluation is a development regression with historical inputs, not a fresh
release holdout. The original 25-positive collection target is unmet, angle
coverage remains incomplete, and calibration covers too few identities to claim
representative reliability. Initial and final accuracy numbers use different
photo selections and references; they must not be presented as a controlled
percentage improvement. Per-photo results, confidence intervals and failures
are in `3d-evaluation-results-2026-10-17.json`; setup failures remain in
`3d-attempts-2026-10-17.json`. Do not enable the rejected policy, lower thresholds
on this evaluation split, or hide Summer failures to manufacture completion.

These measurements exclude physical-phone tracking, mobile-network latency and
concurrent-user capacity. Visitor confirmation/manual selection remain required.

The user requested committing and pushing the artwork work first, then starting
3D recognition using existing internet photographs only. Commit `ed72354` was
pushed to `MVP-Fix-artwork-and-room-confusion-in-visual-recognition` before this
work began on `MVP-3D-object-recognition`. Artwork ticket #9 remains unvalidated;
this pilot does not replace or close it.

## First milestone: reference inventory

The provisional cohort stays at the Rijksmuseum: Shiva Nataraja, Spring, Autumn,
Summer and Apollo. The last four include visually similar sculptures, providing
useful competing identities. Exact canonical URIs and current reference-set IDs
are preserved in [the inventory](3d-reference-inventory-2026-10-17.json), with the
explicit five-object configuration in [the cohort](3d-pilot-cohort.json).

| Object | Existing reference images | Viewpoint labels | Structural minimum |
| --- | ---: | ---: | --- |
| Shiva Nataraja | 1 | 0 | Fail |
| Spring | 2 | 2 | Pass |
| Autumn | 2 | 2 | Pass |
| Summer | 2 | 2 | Pass |
| Apollo | 2 | 2 | Pass |

Two distinct images and labels satisfy only a structural minimum. Labels such as
"canonical front/full view" and "development capture; closer or oblique view"
do not verify front/side/back coverage. All five need visual angle review. All
five also contain a reference with `rights_status=unknown`; canonical visual
evidence includes public-domain statements, so review the evidence and derived
rights metadata rather than assuming either prohibition or approval from this
status alone. No reference or canonical source was changed.

The new read-only inventory command uses the existing evidence-validated Atlas
snapshot and immutable reference-set filters. It rechecks the snapshot after
reading, rejects changes and planar objects, and transfers no embeddings or local
descriptors. It performs zero recognition requests, provider calls or database
writes. Duplicate image hashes with different labels cannot establish multiple
views. Structural inventory completion is not recognition or release acceptance.

```bash
.venv/bin/python -m scripts.multiview_reference_audit \
  --cohort docs/3d-pilot-cohort.json \
  --output /tmp/3d-reference-inventory.json
```

Use a new output filename each time. Exit 0 means the inventory ran successfully,
including when it exposes gaps; exit 2 means it could not produce a valid inventory.

## Internet photo acquisition started

The [museum Spring record](https://www.rijksmuseum.nl/en/collection/object/Spring--f2e5e08b5274f73f49f6a0bb59e48f6e?tab=catalogue)
links the existing canonical Spring identity. The
[Commons sculpture-garden category](https://commons.wikimedia.org/wiki/Category:Rijksmuseum_Amsterdam_-_Sculpture_garden)
contains source leads, including Apollo visitor photographs. Candidates require
exact-object review, not merely a title such as "Apollo" or "Shiva". In particular,
the Rijksmuseum has different Shiva Nataraja bronzes; do not substitute another
accession or a replica for the selected object.

Four source files were resolved through public Commons metadata: three Apollo
photos by Ceescamel on 2018-08-30, and one Shiva photo by Oren Rozen on 2017-02-21.
The file metadata lists CC BY-SA 4.0. Source URLs, credits, dates, licenses and
history checks are in [the source-lead record](3d-online-photo-leads-2026-10-17.json).
All four belong to already used historical capture groups; the Apollo photos are
one visit, not three independent samples. None was accepted as a fresh holdout or
new reference, downloaded for this pilot, or sent through recognition. These
exclusions prevent recycling old evidence into a purported new accuracy result.

## Next implementation milestone

1. Acquire and visually review commercially reusable internet photos, retaining
   source page, exact museum identity, photographer, capture date/group, license,
   original download URL, hash and actual viewpoint. Preserve original bytes.
   Keep replicas, reproductions and unresolved identities outside accepted data.
2. Build up to four reference views per object, aiming for front, side and back
   where sources permit. Record missing angles. Distinct images and two real
   labeled views are the minimum for enabling the existing multiview profile,
   not a promise of all-angle recognition.
3. Keep reference, calibration and evaluation capture groups disjoint, including
   historical references and queries. Target ten calibration positives (two per
   identity) plus ten negatives, and twenty-five independent evaluation positives
   (five per identity) plus twenty negatives, including similar sculptures and
   room/background scenes. These are collection targets, not acquired counts.
   Do not fabricate extra samples by cropping or relabeling the same capture.
4. Run the existing DINOv2/ONNX multiview camera path with all five objects
   competing. It scores each reference view and takes the best view per object;
   do not average away viewpoint information or apply planar SIFT verification
   to the entire sculpture. Start with regions off; add cropping only if a
   controlled comparison demonstrates improvement.
5. Fit multiview score/margin thresholds on calibration only, freeze the artifact,
   and evaluate once on the held-out split. Use the existing
   `scripts.camera_pipeline_eval` workflow. Report top-one/top-three, precision,
   suggestion coverage, false suggestions, failures, per-angle results and p95
   latency separately, with a visual report. No 3D accuracy is measured yet.

Pilot behavioral targets retain top-one >=90%, top-three >=95%, zero wrong
suggestions on the frozen test, and correct suggestion coverage >=50%. On 25
evaluation positives this means at least 23 top-one, 24 top-three and 13 correct
suggestions. Keep the existing 1500 ms inference budget; report cold preparation
and HTTP latency separately. These targets do not establish population reliability.
The existing single-inference lock, physical-phone behavior and hundred-user
capacity remain separate limitations; this data milestone makes no scaling claim.

Visitor confirmation and manual selection remain required. No new model,
microservice, 3D reconstruction, paid provider call or deployment is introduced.

Final development validation: 331 Python tests passed, two opt-in tests skipped;
15 JavaScript tests and all 36 Chromium/WebKit browser tests passed.
`ruff check .`, `ruff format --check .` and `mypy app` passed. New tests cover
duplicate-image relabeling, missing/null viewpoints, 512-pixel proposals and crop
coordinates, injected preview settings, bounded browser JPEG uploads, server
abstention at high scores, reference changes and unsupported files. Both browsers
also completed a real Atlas-backed photo upload, ranked Shiva first and withheld
an uncalibrated suggestion; all 34 report photos, filters and mobile layout passed.

The final data-only audit (`3d-dataset-audit-2026-10-17.json`) found zero raw or
normalized query/reference byte overlaps, zero calibration/evaluation capture-group
overlaps and zero overlaps with reference groups explicitly recorded in metadata.
Older reference group fields remain incomplete; manual source/date review is still
necessary, and these checks do not make historical images a fresh holdout.
