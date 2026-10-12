# Positive-only Atlas diagnostic — 5 October 2026

Run from the project root:

```bash
VISUAL_IMAGE_ONLY_NEW_POSITIVES=1 RUN_VISUAL_IMAGE_CHECK=1 \
  VISUAL_ENABLED=true .venv/bin/pytest -s -q tests/test_visual_image_atlas.py
```

The run completed in 109.32 seconds. The correct object ranked first for
**42/50 (84%)** photographs, and appeared in the top three for **42/50**.
The test prints only the eight failed cases and the totals in this mode.

| Photo ID suffix | Expected artwork | First candidate | Result |
| --- | --- | --- | --- |
| 26253448 | The Fall of Man | None | No match |
| 66977722 | The Celebration of the Peace of Münster | Portrait of Jan Amos Comenius | Wrong ranking |
| 50088024 | The Night Watch | Rijksmuseum Night Watch Room | Wrong ranking |
| 161498270 | Vanitas stilleven | Crucifixion | Wrong ranking |
| 161498357 | Vanitas stilleven | None | No match |
| 47260825 | The Syndics | Rijksmuseum Gallery of Honour | Wrong ranking |
| 66977726 | The Celebration of the Peace of Münster | Lidded ewer for the Amsterdam Goldsmiths Guild | Wrong ranking |
| 161500766 | Vanitas stilleven | None | No match |

All eight results have their full candidate lists in the focused run output and
the [per-photo JSON result](accuracy-results.json). The latter was created by the
earlier complete 105-photo run; its 50 positive results match this focused run.

This checks the Atlas visual API's candidate ranking. Forty-one of these 50 photos
were used in earlier corpora, and the photos are grouped by broad visual subject.
There is no balanced, unseen matched-pair set here, so **84% is not the
similar-item benchmark accuracy**. The camera crop, geometry gate and temporal
tracking are also outside this diagnostic.
