# Visual test photo

`positive/the-milkmaid.jpg` is an independent visitor photograph of The Milkmaid at the
Rijksmuseum, taken on September 29, 2005 by Flickr user jnkypt (Olympus C40Z).
It is not an Atlas reference image or part of the frozen v5 evaluation corpus.

- Source: https://www.flickr.com/photos/25400263@N07/3085955962
- Download: https://live.staticflickr.com/3246/3085955962_4901d433a8_z.jpg
- License: CC BY-SA 2.0, https://creativecommons.org/licenses/by-sa/2.0/
- Stored image: unmodified 640x480 JPEG
- Expected object: `6a9dd4126d52c3bc396586a0` at location
  `6a9dd4106d52c3bc3965869d`

`positive/the-jewish-bride.jpg` is Richard Mortel's independent visitor
photograph of The Jewish Bride at the Rijksmuseum, captured February 23, 2019
with a Sony DSC-RX100M6. The Atlas references are different source images.

- Source: https://www.flickr.com/photos/43714545@N06/47307520772
- Image: https://live.staticflickr.com/7832/47307520772_405b91f3a0_z.jpg
- License: CC BY 2.0, https://creativecommons.org/licenses/by/2.0/
- Stored image: Flickr's 640x427 JPEG served on the photo page
- Expected object: `6a9dd4136d52c3bc396586a1` at location
  `6a9dd4106d52c3bc3965869d`

`negative/perseverance-looks-toward-santa-cruz.jpg` is an easy real-photo
negative, not a museum-art hard negative. It shows a Perseverance rover panorama on Mars (NASA ID PIA25172),
outside the Rijksmuseum reference set. Credit: NASA/JPL-Caltech/ASU/MSSS.

- Source: https://images.nasa.gov/details/PIA25172
- Download: https://images-assets.nasa.gov/image/PIA25172/PIA25172~medium.jpg
- Usage: https://www.nasa.gov/nasa-brand-center/images-and-media/
- Stored image: unmodified 1280x222 JPEG
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

`negative/portrait-of-a-family-playing-music.jpg` is a related negative: Pieter de Hooch's
Dutch domestic interior at the Cleveland Museum of Art (accession 1951.355),
not an object in the selected Rijksmuseum location. It is an official artwork
reproduction, not an independent visitor photograph; use it as a diagnostic
similar-object case, not as evidence of field-photo accuracy.

- Source: https://clevelandart.org/art/1951.355
- Download: https://openaccess-cdn.clevelandart.org/1951.355/1951.355_web.jpg
- Rights: CC0, https://www.clevelandart.org/open-access
- Credit: Cleveland Museum of Art, Gift of the Hanna Fund
- Stored image: unmodified 900x759 JPEG
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

`negative/wineglass-and-a-bowl-of-fruit.jpg` is another related negative: Willem Kalf's
Dutch still life at the Cleveland Museum of Art (accession 1962.292), not an
object in the selected Rijksmuseum location. It is an official artwork
reproduction and a diagnostic case, not a visitor photograph.

- Source: https://clevelandart.org/art/1962.292
- Download: https://openaccess-cdn.clevelandart.org/1962.292/1962.292_web.jpg
- Rights: CC0, https://www.clevelandart.org/open-access
- Credit: Cleveland Museum of Art, Leonard C. Hanna Jr. Fund
- Stored image: unmodified 747x893 JPEG
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

Datasets live in `datasets/<name>.json` (lowercase letters, digits, hyphens). Each
row has a numeric `id`, the photo `path` relative to this directory, the
`location_id`, the `expected_object_id`, a `category` (the view: `full` or `partial`)
and an `object_type` (lowercase, e.g. `painting`, `statue`; see `type` in `objects.json`).
Only positives are supported for now: every photo shows an object that has
prepared Atlas visual references (the JPEG itself is not stored in MongoDB).
Run a dataset from the UI (`VISUAL_ENABLED=true`, `VISUAL_EVALUATION_ENABLED=true`,
then open `/api/v1/visual-evaluation`) or with
`RUN_VISUAL_IMAGE_CHECK=1 .venv/bin/pytest -s tests/test_visual_image_atlas.py`.
With Docker: `docker compose --profile visual up --build visual-evaluation`, then
open http://localhost:8002/api/v1/visual-evaluation. It mounts this directory and the
local Hugging Face cache (`HF_CACHE_DIR`, default `~/.cache/huggingface`) read-only.
UI runs are saved in the `visual_evaluation_runs` collection to draw the history.

`positive/` holds the positive photos. `negative/` holds images of objects outside
the reference set (two are official reproductions, not camera photos); they are
parked there until negative evaluation is added.

`objects.json` is a read-only Atlas metadata snapshot of all current objects
and their reference-presence flags, for choosing test targets. It does not
contain original image pixels, source records, or generated content; it is not
the canonical database or a guarantee that references remain active later.

Keep any additional fixture photos independently captured and source-attributed.
Do not add source/reference images as evaluation queries.
