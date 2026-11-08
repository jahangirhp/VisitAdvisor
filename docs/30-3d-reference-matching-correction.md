# Correct 3D matching in the deployed camera

The deployed non-adaptive camera required a planar SIFT/homography match for every
object, including sculptures and artifacts. A new view of a three-dimensional
object can have a good image embedding match while failing that planar gate.
This is separate from request timeouts and cannot be established by replaying the
same saved image against itself.

Atlas camera catalogs now advertise geometry_scope: planar-and-scene-v2.
Known 3D categories (sculpture, statue, artifact, artefact, figurine,
rock, stone, toy) use the best source-photo embedding score without the
planar gate. Paintings, rooms, buildings and unknown categories retain geometric
verification against the same view that supplies their embedding score. Explicit
spatial-geometry experiments and historical file catalogs retain their previous
behavior. The separate adaptive prototype and its rejected calibration artifacts
are not enabled by this change.

The encoder, reference vectors, display floor (0.55), candidate margin (0.04) and
three-frame stability requirement are unchanged. Flat frames abstain. 3D reference
loading no longer requires planar keypoints; it still checks source provenance,
current evidence, model, preprocessing and immutable reference-set versions.
No angle labels or viewpoint coverage are fabricated. Saved originals and their
embeddings are not rewritten. The policy change updates the reference version,
so existing clients discard old results and reload the catalog.

## Diagnostic evidence

The private four-photo Santa train reference set was checked by excluding each
query photograph and matching against the other three. All four queries previously
received a zero geometry-gated score. All four corrected scores pass the existing
display floor and margin, even when every other object's embedding score is used
as a conservative upper bound for its geometrically verified score. This compares
different saved photos from one capture session; it is not an independent holdout
or a physical-phone accuracy measurement.

The separate 30-image public sculpture/room calibration corpus produced no new
Santa suggestions; all those images are unrelated to Santa. This is a targeted
negative diagnostic, not validation of every 3D category. Canonical museum type
strings outside the explicit list continue to use their previous matching path.

Numerical results and limits are in
[santa-recognition-diagnostic-2026-11-08.json](santa-recognition-diagnostic-2026-11-08.json).
Private source images remain in ignored local files, with no image data in this
document or the numerical report. No production object, source, photo or prepared
reference records were modified.

Validation: 511 Python tests passed (two opt-in tests skipped), Ruff lint/format
and mypy passed, 33 JavaScript tests passed, and six selected Chromium/WebKit
regressions passed. The preview loaded all 113 current objects with the new policy
and changed reference version, ranked Santa first on all four saved camera crops,
abstained on a blank image and retained the Milkmaid regression match.
Both actual-browser loops acquired Santa after three HTTP 200 responses on a
replayed oblique camera crop. This browser check uses saved-image input and is
separate from the different-reference-photo diagnostic above. A concurrent API
check initially caused expected HTTP 429 responses; the browser recovered and
acquired Santa. The isolated browser rerun passed in both engines without busy
responses. Runtime secrets, CPU/memory limits and the one warm instance were
preserved. This is not a claim that all fresh viewpoints can be identified.
