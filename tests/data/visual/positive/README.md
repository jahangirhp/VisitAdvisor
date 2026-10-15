# Positive photo batch

50 additional, unchanged Wikimedia Commons camera photographs are stored here.
They depict 19 paintings marked as reference-covered in the read-only
[`objects.json`](../objects.json) snapshot. Each filename includes the database
object ID and Commons page ID. The two existing fixture photos are preserved.

[`manifest.json`](manifest.json) records exact identities, source and download URLs,
licenses, credits, camera/capture metadata, dimensions, checksums, similarity
groups and previous corpus membership. Commons sometimes lists the painter in
its Artist field; this is preserved as `recorded_artist_or_photographer`, with
the uploader recorded separately rather than asserted to be the photographer.

The photographs include civic group portraits, domestic interiors, portraits,
landscapes, seascapes and still-life details. Positive means the depicted identity
has references in the snapshot; it does not mean recognition will succeed. The
snapshot is not a fresh Atlas readiness check, and the owning collection location
does not assert the physical venue where each photo was captured.

All 50 files were visually checked against the named composition and checked for
camera metadata, byte/decoded duplicates, API image limits and byte equality with
recorded originals. Known reference source pages, obvious derived crops, scans,
wrong identities and room-only photographs were excluded. Multiple views from
the same uploader/capture day share a capture group. They are not 50 independent
sessions. No local cropping or re-encoding was applied.

This is a diagnostic regression batch: 41 source photographs already occur in
earlier manifests. It is not the unseen five-pair dataset specified in
[`docs/19-similar-item-accuracy-test.md`](../../../../docs/19-similar-item-accuracy-test.md).
No accuracy inference was used to choose these files, and no new accuracy result
is claimed. These fixtures are local evaluation data, not canonical Atlas evidence.

The batch has been added to [`photos.json`](../photos.json), so the existing
opt-in Atlas diagnostic includes it:

```bash
RUN_VISUAL_IMAGE_CHECK=1 .venv/bin/pytest -s tests/test_visual_image_atlas.py
```

For only this 50-photo positive batch, with failed rankings printed at the end:

```bash
VISUAL_IMAGE_ONLY_NEW_POSITIVES=1 RUN_VISUAL_IMAGE_CHECK=1 \
  VISUAL_ENABLED=true .venv/bin/pytest -s -q tests/test_visual_image_atlas.py
```

These commands perform recognition against live Atlas. Add
`VISUAL_IMAGE_STRICT=1` to fail on wrong results. The full fixture list now has
52 positives and 53 negatives. See the
[positive-only result](positive-only-results.md), the
[latest complete text log](positive-only-run-2026-10-05.txt), the
[full run](accuracy-results.md) and its
[per-photo result](accuracy-results.json). This diagnostic evaluates the visual
API; it does not measure browser camera preprocessing or three-frame tracking.
