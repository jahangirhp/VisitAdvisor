# Positive2 results

Recorded 2026-10-05T21:26:31.176454+00:00. Input: 50 distinct new source photos, four reference-covered identities, 34 capture groups. Labels and crops were fixed before inference.

| Group | Photos | Correct top 1 | Correct top 3 |
| --- | ---: | ---: | ---: |
| All | 50 | 42 (84.0%) | 45 (90.0%) |
| building | 41 | 39 (95.1%) | 40 (97.6%) |
| painting | 9 | 3 (33.3%) | 5 (55.6%) |
| cropped | 25 | 23 (92.0%) | 24 (96.0%) |
| other_view | 25 | 19 (76.0%) | 21 (84.0%) |

All 50 requests returned HTTP 200. One returned no candidates. Reference set IDs were unchanged before and after the run. Each request searched the full location catalog (112 covered objects out of 128).

Mean request duration: 0.96 seconds; median: 0.88 seconds. These timings include in-process upload recognition and Atlas reads, not browser tracking or mobile networking.

The API returned 49 ambiguous candidate responses and one no-match response; correct top-1 ranking does not mean automatic identification. Scores are uncalibrated similarities, not confidence probabilities. This positive-only test does not measure false positives or confidence calibration. Architecture supplies 41 of the 50 inputs, so the overall score should not be used as a painting recognition estimate.

## Results by expected object

| Expected object | Photos | Correct top 1 | Correct top 3 |
| --- | ---: | ---: | ---: |
| The Milkmaid | 1 | 1 | 1 |
| Rijksmuseum building | 40 | 38 | 39 |
| The Night Watch | 8 | 2 | 4 |
| Rijksmuseum Atrium | 1 | 1 | 1 |

## Incorrect first candidates

| Image | Expected | First candidate | Score | Expected in top 3 |
| --- | --- | --- | ---: | --- |
| [6aab0dbcdd73e04d96150a00-commons-88945969.jpg](6aab0dbcdd73e04d96150a00-commons-88945969.jpg) | The Night Watch | Rijksmuseum Night Watch Room | 0.8570 | No |
| [6aab0dbcdd73e04d96150a00-commons-88945971.jpg](6aab0dbcdd73e04d96150a00-commons-88945971.jpg) | The Night Watch | Lidded ewer for the Amsterdam Goldsmiths Guild | 0.6644 | No |
| [6aab0dbcdd73e04d96150a00-commons-88945987.jpg](6aab0dbcdd73e04d96150a00-commons-88945987.jpg) | The Night Watch | Rijksmuseum Night Watch Room | 0.7262 | No |
| [6aab0dbcdd73e04d96150a00-commons-544866.jpg](6aab0dbcdd73e04d96150a00-commons-544866.jpg) | The Night Watch | Rijksmuseum Gallery of Honour | 0.5355 | Yes |
| [6aab0dbcdd73e04d96150a00-commons-873656.jpg](6aab0dbcdd73e04d96150a00-commons-873656.jpg) | The Night Watch | Lidded ewer for the Amsterdam Goldsmiths Guild | 0.5912 | Yes |
| [6aab0dbcdd73e04d96150a00-commons-83441070.jpg](6aab0dbcdd73e04d96150a00-commons-83441070.jpg) | The Night Watch | Lidded ewer for the Amsterdam Goldsmiths Guild | 0.5773 | No |
| [6aa9a7fa60b7ca89b6355b9c-commons-149672829.jpg](6aa9a7fa60b7ca89b6355b9c-commons-149672829.jpg) | Rijksmuseum building | No candidates | — | No |
| [6aa9a7fa60b7ca89b6355b9c-commons-25891448.jpg](6aa9a7fa60b7ca89b6355b9c-commons-25891448.jpg) | Rijksmuseum building | Apollo | 0.5036 | Yes |

No images were replaced, relabelled or recropped after seeing recognition results. No thresholds or reference records were changed. Full candidate scores, timings, warnings, input hashes and reference snapshot IDs are in [accuracy-results.json](accuracy-results.json). Source provenance and transformations are in [manifest.json](manifest.json).
