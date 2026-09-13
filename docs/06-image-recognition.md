# Phase 5 — Current label photo recognition

**Scope revised 2026-09-11:** The full Phase 5 target is label-free object
recognition, including partial views. See `07-visual-recognition-pilot.md`.
This document describes the existing OCR implementation only; it does not meet
the revised visual-recognition requirement.

## Implemented flow

Upload a label photo with a selected museum → validate image → transcribe visible
text → run Phase 4 hybrid matching → return the identified object and its existing
`prepared_content`, or up to three candidates for selection.

This phase implements the upload API, not a camera UI, live video, image embeddings,
or recognition of unlabeled artworks. A future camera client can submit still
images to this endpoint. Visual reference matching is the next planned extension.
Museum import and embedding preparation remain explicit CLI
operations; no scheduler or worker has been added.

## API

`POST /api/v1/recognition/image`, `multipart/form-data`:

- `location_id`: required existing museum ID.
- `image`: required JPEG, PNG, or WebP file.

```bash
curl -X POST http://localhost:8001/api/v1/recognition/image \
  -F 'location_id=6a9dd4106d52c3bc3965869d' \
  -F 'image=@/path/to/label.jpg;type=image/jpeg'
```

Swagger at `/docs` provides a file picker. A camera client should send a single
still image when the visitor requests a scan, not continuously submit video frames.

The JSON response contains:

| Field | Meaning |
| --- | --- |
| `location_id` | Validated selected museum |
| `status` | `matched`, `ambiguous`, `no_match`, `no_text`, or `unreadable` |
| `ocr` | Transcription and visible title headings, readability assessment, flags, model and token usage |
| `match` | Phase 4 match result, including ranked candidates; null when there is no readable text |
| `object` | Full saved object for an automatic match, including nullable `prepared_content`; otherwise null |
| `warnings` | OCR, retrieval availability, and content-readiness warnings |

`matched` does not mean visitor content has been factually reviewed. Existing
Phase 3 content is returned exactly as stored, with citations and
`review_status=unreviewed`, plus `CONTENT_UNREVIEWED`. When no content is prepared,
recognition still succeeds and returns `CONTENT_NOT_PREPARED`. This endpoint never
generates a new story or modifies canonical evidence.

For ambiguous results, a client can display the returned candidates and fetch the
selected object via the existing `/api/v1/objects/{object_id}` endpoint. If needed,
`/api/v1/objects/{object_id}/experience` returns its prepared content (404 if absent).
No OCR call is needed for candidate selection.

## OCR behavior

OpenAI Responses image input is used only to transcribe visible text, with strict
structured output. Default `OCR_MODEL=gpt-4.1-mini-2025-04-14`; the existing
`OPENAI_API_KEY` is reused. `OCR_TIMEOUT_SECONDS` defaults to 30. Normal API startup
and lexical matching do not require a key; image recognition reports
`OCR_NOT_CONFIGURED` if it is missing.

The OCR prompt forbids identifying the artwork from appearance or memory,
translating/correcting names, following instructions in images, or inventing letters.
Only the image is sent: no candidate list or museum collection is given to OCR.
The transcription remains in the source language and preserves accession numbers.
OCR also returns visible title headings; each must occur in the transcription.
A title-only automatic match requires an exact normalized heading/name-or-alias
match. A heading such as "Copy of The Night Watch" cannot automatically select
"The Night Watch". A conflicting heading also blocks an accession-based selection;
when no heading is legible, an exact accession can still support identification.
Otherwise, `OCR_IDENTITY_UNCONFIRMED` downgrades the result to ambiguous.
No tools or web browsing are enabled. Output is capped at 2000 characters, matching
Phase 4's query limit. Requests use `store=false` and have no automatic paid retry.
This is not a claim about the provider's overall data-retention policy.

Empty/non-alphanumeric text skips matching. `no_text` and `unreadable` responses
include `RESCAN_LABEL`. For partial, multiple-label, or truncated transcriptions,
any automatic match is downgraded to `ambiguous`; its selected ID/object is cleared.
Semantic similarity alone still cannot auto-select an object. OCR readability and
matching scores are heuristics, not calibrated confidence probabilities. A model
can misread text, and label paragraphs can mention other exhibits; real visitor
photos and negative examples are needed to calibrate accuracy.

## Image handling

- Maximum file size: 5 MiB; complete multipart body: 5 MiB + 64 KiB.
- The ASGI upload limit counts actual received bytes before multipart parsing,
  including chunked uploads, without trusting Content-Length.
- Pillow verifies the actual file format against the declared MIME type, checks
  dimensions (maximum 16 megapixels), and decodes the image. Damaged images,
  animation, format mismatches, and unsupported formats are rejected.
- EXIF orientation is applied, then the longest edge is limited to 2560 pixels.
  Transparency is composited onto white. A fresh JPEG strips camera metadata before
  provider submission. The filename is never used as a local path.
- Decoding runs in the thread pool. Uploaded images and OCR text are not written
  to MongoDB or application logs. Multipart parsing may use a temporary upload
  file, which is closed at request completion; there is no durable image archive.

HEIC, GIF, SVG, PDF, and remote image URLs are not supported in this MVP endpoint.
Clients should export a JPEG, PNG, or WebP and frame a single label clearly.

## Errors and limitations

Bad form fields/IDs use the existing 422 validation contract; missing museums use
404. Unsupported or mismatched image types use 415, damaged images 422, and size
limits 413. Provider failures expose safe OCR error codes without raw responses,
headers, or credentials. Timeouts use 504; quota/access/network failures use 503;
invalid/incomplete provider output uses 502; refusal uses 422. A matched object
moved/deleted during processing produces 409 `MATCH_CHANGED`.

Location existence is checked before any paid OCR request, and matching uses the
same museum. Before returning content, the saved object's location is checked again.
Phase 4's explicit lexical fallback warnings propagate to the recognition response.

Each valid scan uses one OCR request and may use one query-embedding request if
lexical identity evidence is insufficient. There is no scan cache. The local API
has no public authentication/rate limiting; do not treat this implementation as a
public production launch. No source import, vector update, or content generation
runs during a scan. Phase 3's factual-quality review remains open.

## Technical references

- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
- [Pillow image verification](https://pillow.readthedocs.io/en/stable/reference/Image.html)
- [Starlette upload handling](https://www.starlette.io/requests/)
