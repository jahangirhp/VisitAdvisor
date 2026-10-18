# Positive2 visual recognition dataset

This dataset contains 50 separate new Wikimedia Commons source photos of objects
covered by the current visual references. No source photo contributes more than
one test image. Source page IDs, URLs, licences, photographers, source hashes,
output hashes and crop coordinates are recorded in [manifest.json](manifest.json).
The selected source IDs and hashes were checked against the previous datasets,
reference sources and previously cached acquisition photos before inference.

The reviewed labels and transformations were frozen before the test run. A positive
label means the depicted object has a known reference identity; it does not imply
that the recognizer will return that identity.

| Content | Photos |
| --- | ---: |
| Rijksmuseum building | 40 |
| Rijksmuseum Atrium | 1 |
| The Night Watch | 8 |
| The Milkmaid | 1 |
| Total | 50 |

There are 25 deterministic crops and 25 other-view images, from 50 distinct
sources and 34 capture groups. Crop coordinates refer to oriented source pixels.
Crops are derived images, not independently photographed partial views. Some
sources share a photographer and capture date. The architecture-heavy set covers
four identities and cannot establish general artwork recognition accuracy.

Run the positive test with this folder as its input argument:

```sh
.venv/bin/python -m scripts.visual_positive_test tests/data/visual/positive2 \
  --output tests/data/visual/positive2/accuracy-results-new.json
```

Choose a fresh output path for each run. Add `--strict` to fail if any top-1
identity is wrong. Service failures always fail the command and remain in the
accuracy denominator.

The runner uploads each photo to the in-process visual API, using the cached
encoder and live Atlas references against the full location catalog. It does not
modify reference data. It measures neither browser camera tracking nor mobile
network performance. This positive-only diagnostic does not measure negative
rejection or calibrate confidence: candidate scores are similarities, not
probabilities. See [accuracy-results.json](accuracy-results.json) and
[accuracy-results.md](accuracy-results.md) for the recorded run.
