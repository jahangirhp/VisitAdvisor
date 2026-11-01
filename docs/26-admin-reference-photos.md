# Admin reference-photo library

The existing FastAPI application now serves `/admin/`. Set `ADMIN_TOKEN` in the
ignored runtime environment to enable its protected API at `/api/v1/admin`.
The page requests the token at login and holds it in memory only. It never embeds
application credentials or stores the token in browser storage. Use HTTPS when
accessing it outside localhost. The standalone camera/photo-lab launcher is not
the main API and does not serve this admin panel.

Choose any venue, browse its objects (50 per page), search/filter the current page,
and select an object to see prepared reference counts, stored viewpoint labels,
private admin uploads and remaining collection gaps. Venue selection is paginated
in batches of 100. Counts measure distinct original image hashes, not embeddings
or generated regional crops. Stale source evidence does not count as prepared
coverage. The inventory does not certify model compatibility, viewpoint quality,
calibration, readiness or recognition accuracy.

The configurable defaults are three photos for planar types and four for other
objects, using the existing recognizer's type mapping. These are proposed
collection goals, not experimentally established minima. Set
`ADMIN_PLANAR_PHOTO_TARGET` (1–4) and `ADMIN_MULTIVIEW_PHOTO_TARGET` (2–4) to change
them. Viewpoint labels need human review: four front views are not four sides.
Existing recognition still supports at most four active reference photos per
object. Its structural minimum is one planar image, or two genuinely distinct
images with distinct viewpoint labels for multiview objects. Neither establishes
release accuracy. Collect additional independent captures for calibration and
held-out testing; never count reference derivatives as independent tests.

## Upload and publication

An upload requires an existing venue/object, a source already linked to the object,
viewpoint, photographer/owner, capture-session ID, photo-rights evidence, and an
explicit identity/permission confirmation. An object without source evidence must
first receive canonical source membership through the existing catalog workflow.
The source membership establishes the object association; photo permission is the
operator's recorded assertion, not proof supplied by the museum's source record.
JPEG, PNG and WebP uploads use the existing 5 MiB, 16-megapixel and single-still
validation. The complete multipart request is bounded before parsing. Original and
normalized hashes reject exact duplicates; visually similar captures and edited
variants still need manual review.

Unlike transient visitor frames and earlier remotely fetched references, these
explicit admin uploads retain original bytes in a new private Atlas collection,
`admin_reference_photos`, with one bounded document per photo. This is an intentional
extension of the earlier no-image-storage reference workflow. Each object has at
most four upload slots, with unique per-object slot and normalized-hash indexes.
Inventory reads exclude binary data. Preview responses require admin authentication,
remove EXIF/GPS metadata, and disable caching. Originals can contain metadata and
remain private database data; include this collection in access and backup policies.
Uploaded bytes do not appear in public object/source responses or visitor frames.

Saving a photo does not yet change recognition. **Prepare references** runs the
existing cached local DINOv2 encoder (install the `visual` extra and cached model
using the existing model setup command) and publishes through `VisualRepository`.
No OpenAI calls, content generation, training or model download occurs. It retains
all existing views, stages the full new vector set, rechecks canonical source
membership, then uses the existing atomic set replacement. Failure keeps the prior
set active. It refuses stale/model-incompatible previous sets rather than mixing
preprocessing versions; reprepare existing sources with the visual CLI first.

Preparation runs in the foreground, with one operation at a time per application
process; no worker queue is added. For this bounded admin MVP use one API worker.
The same process lock prevents pending-upload removal during publication. External
CLI writers still use the publisher's compare-and-swap source/set checks. Saved
unpublished photos can be removed; published uploads cannot be deleted by this UI.
Private image references use opaque `admin-upload:` identifiers; public catalog
export strips these non-HTTP identifiers rather than exposing private originals.

Existing camera reference polling observes new sets. A calibration bound to the
old reference signature must be regenerated and validated on separate data.
Changing coverage does not enable automatic identification or expand the configured
2–12-object live-camera cohort.

## Verification

Admin API tests exercise authentication/default-disabled behavior, private reads,
venue/object/source isolation, malformed and oversized chunked uploads, required
review, duplicates/concurrent-slot conflicts, configurable counts, stale evidence,
publication preserving existing views, failure preservation, and removal rules.
Browser tests exercise login, name/type filtering, upload and preparation, safe text
rendering, logout, rejected credentials and mobile layout in Chromium and WebKit:

```bash
npx playwright test -c playwright.admin.config.mjs
```

These tests mock Atlas and image descriptors. They do not write production photo
records, establish recognition accuracy or demonstrate physical-phone performance.

## Venue creation

Authenticated administrators can use **Add a venue** to create a location with
name, unique slug, type, city, country and optional official website. The protected
`POST /api/v1/admin/venues` reuses catalog validation and duplicate-slug handling.
The new empty venue is selected immediately. Populate objects and source evidence
through the existing catalog API; material ingestion is not performed by this form.
