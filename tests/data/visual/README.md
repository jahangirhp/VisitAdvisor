# Visual test photo

`positive/milkmaid.jpg` is an independent visitor photograph of The Milkmaid at the
Rijksmuseum, taken on September 29, 2005 by Flickr user jnkypt (Olympus C40Z).
It is not an Atlas reference image or part of the frozen v5 evaluation corpus.

- Source: https://www.flickr.com/photos/25400263@N07/3085955962
- Download: https://live.staticflickr.com/3246/3085955962_4901d433a8_z.jpg
- License: CC BY-SA 2.0, https://creativecommons.org/licenses/by-sa/2.0/
- Stored image: unmodified 640x480 JPEG
- SHA-256: 1c61f9344999ee28bd5107e6ccf75ee4b375e7995cee2d61890361dfcd495609
- Expected object: `6a9dd4126d52c3bc396586a0` at location
  `6a9dd4106d52c3bc3965869d`

`positive/jewish-bride-mortel.jpg` is Richard Mortel's independent visitor
photograph of The Jewish Bride at the Rijksmuseum, captured February 23, 2019
with a Sony DSC-RX100M6. The Atlas references are different source images.

- Source: https://www.flickr.com/photos/43714545@N06/47307520772
- Image: https://live.staticflickr.com/7832/47307520772_405b91f3a0_z.jpg
- License: CC BY 2.0, https://creativecommons.org/licenses/by/2.0/
- Stored image: Flickr's 640x427 JPEG served on the photo page
- SHA-256: 15415d181cb5033d66818b4e74c2676572829b751eadb48db476fb35c5ce9cc4
- Expected object: `6a9dd4136d52c3bc396586a1` at location
  `6a9dd4106d52c3bc3965869d`

`negative/mars-perseverance.jpg` is an easy real-photo negative, not a museum-art hard
negative. It shows a Perseverance rover panorama on Mars (NASA ID PIA25172),
outside the Rijksmuseum reference set. Credit: NASA/JPL-Caltech/ASU/MSSS.

- Source: https://images.nasa.gov/details/PIA25172
- Download: https://images-assets.nasa.gov/image/PIA25172/PIA25172~medium.jpg
- Usage: https://www.nasa.gov/nasa-brand-center/images-and-media/
- Stored image: unmodified 1280x222 JPEG
- SHA-256: f9cf75bf2e51b85791bfdcc7d90866a5f1577e50a496e6301fcc1f9f0c3be509
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

`negative/de-hooch-family-music.jpg` is a related negative: Pieter de Hooch's
Dutch domestic interior at the Cleveland Museum of Art (accession 1951.355),
not an object in the selected Rijksmuseum location. It is an official artwork
reproduction, not an independent visitor photograph; use it as a diagnostic
similar-object case, not as evidence of field-photo accuracy.

- Source: https://clevelandart.org/art/1951.355
- Download: https://openaccess-cdn.clevelandart.org/1951.355/1951.355_web.jpg
- Rights: CC0, https://www.clevelandart.org/open-access
- Credit: Cleveland Museum of Art, Gift of the Hanna Fund
- Stored image: unmodified 900x759 JPEG
- SHA-256: 1c1882ad925f4eaf6c98cd974be3d0d10a491d3de856f9e9b1d63c30ae069ed8
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

`negative/kalf-wineglass-fruit.jpg` is another related negative: Willem Kalf's
Dutch still life at the Cleveland Museum of Art (accession 1962.292), not an
object in the selected Rijksmuseum location. It is an official artwork
reproduction and a diagnostic case, not a visitor photograph.

- Source: https://clevelandart.org/art/1962.292
- Download: https://openaccess-cdn.clevelandart.org/1962.292/1962.292_web.jpg
- Rights: CC0, https://www.clevelandart.org/open-access
- Credit: Cleveland Museum of Art, Leonard C. Hanna Jr. Fund
- Stored image: unmodified 747x893 JPEG
- SHA-256: 2f909ff83699ab851e7fecf8e6a36f64408c2cedc356b99f2ac93c9e752546c1
- Expected object: none at location `6a9dd4106d52c3bc3965869d`

`photos.json` is the test list. Each row supplies the photo path and raw SHA-256,
location, expected object, capture group, split, and provenance. `in_database`
means the depicted object has prepared Atlas visual references, not that the
test JPEG itself is stored in MongoDB. Set it to `true` with an
`expected_object_id` for a covered object; use `false` and `null` for an
out-of-reference image. A negative row expects no candidates from the current
visual API. The opt-in test reports false candidates without hiding them or
failing the diagnostic run. Set `VISUAL_IMAGE_STRICT=1` to fail on wrong results.

`positive/` contains photos of covered objects. `negative/` contains images
whose depicted objects are outside the selected reference set; this says nothing
about whether the model will answer correctly. `independent: false` flags an
official reproduction that cannot count as an independent camera-photo result.

`objects.json` is a read-only Atlas metadata snapshot of all current objects
and their reference-presence flags, for choosing test targets. It does not
contain original image pixels, source records, or generated content; it is not
the canonical database or a guarantee that references remain active later.

Keep any additional fixture photos independently captured and source-attributed.
Do not add source/reference images as evaluation queries.

## Current fixture audit

The current `positive/jewish-bride-mortel.jpg` is 800x648 with SHA-256 `e17f477193f38b0a4176e0430fe9e32ec93bbd757f03853ac2710894ee7372cb`. It differs from the historical Flickr file described above. The depicted painting was visually checked, but attribution and capture independence remain unverified; `photos.json` labels it as a local diagnostic with `independent: false`.

The test list now also includes the 50 Cleveland Museum negatives described in `negative/manifest.json`.
