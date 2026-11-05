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
explicit identity/permission confirmation. The photo-first creation form records the reviewed identification as a named manual
on-site source and links it to the new object. For objects created by other flows,
source membership must already exist before uploads.
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
only its name. The protected `POST /api/v1/admin/venues` generates a unique
internal slug, uses type `other`, and leaves city, country and website unset.
Existing full-detail API requests retain catalog validation and duplicate-slug
handling; the public location-creation API still requires city and country.
The new empty venue is selected immediately and **Add an object** opens below it.

## Photo-first object creation

Choose an existing venue or create one, then tap **Add object** from the collection. A two-step full-screen editor
separates photos from object details, with fixed bottom Back/Next/Save controls. **Take a photo** opens the native file/camera picker with
`capture="environment"`; **Choose photos** accepts multiple gallery images. Camera
behavior depends on the device/browser. Up to four photos can be previewed,
removed, and assigned an angle before saving. The first defaults to Front;
additional angles need explicit selection. A browser may fall back to a file
picker, and HEIC support depends on its decoder. Unsupported files produce a
recoverable message without clearing other selections.

The browser decodes and orients photos, composites transparency over white, and
encodes JPEG at up to 1800 pixels on the longest side before upload. Files over
40 MiB or decoded images over 64 megapixels are rejected locally. The server
still enforces its existing image and request limits. This flow stores the
optimized JPEG, not the original phone file; EXIF/GPS is not sent. The original
single-photo upload form continues to retain its uploaded original privately.

Object creation requires object name, type and observer name alongside photos.
More details contains only year/period and description. Creator, photo-permission
text and the identity/permission checkbox have been removed. Photo rights are
stored as details not supplied; no permission or review is inferred.
Observer name and last object type are reused in memory for the next object.
Locking the panel clears the draft photos and observer. Neither token nor photos
are stored in localStorage or sent before Save.

The protected `POST /api/v1/admin/venues/{location_id}/objects` accepts
`observation: {observer}` and records a manual source titled Admin object entry.
Legacy explicit `reviewed: true` requests retain their On-site identification
source title. Neither flow labels the assertion as official museum evidence,
fetches sources or generates knowledge. Existing API clients can still create
objects without observations. If a later database write fails, a standalone
manual source can remain; it is not published as object knowledge.

**Save object & photos** creates the object and uploads the selected photos
sequentially with one shared capture-session ID. It reports upload progress and
reuses the created object and successful uploads when retrying a partial failure.
Before retry, it reconciles photo hashes and capture-session IDs to handle a lost
upload response. Draft fields and venue selection stay fixed during retries.
**Keep saved photos & finish later** keeps the object and completed uploads and
opens its detail panel. Unsaved photos stay only in the current browser tab and
are lost on reload or lock. Object creation is not an atomic batch with photo
uploads; a lost response to the initial object creation itself requires checking
the inventory before trying again. Saved photos remain private, pending separate
reference preparation; this flow does not enable automatic identification or
expand the configured live-camera cohort.


## Creation editor design

The editor follows the photo-upload and title-entry pattern shown in Airbnb's
host onboarding: [visual reference](https://medium.com/@ava30528/belonging-everywhere-with-airbnb-957eac9d8c02)
and [official photo-management workflow](https://www.airbnb.com/help/article/477).
VisitAdvisor retains its own name and content. It uses a white background, black
primary buttons, a centered narrow column, thumbnail removal controls and a fixed
bottom step indicator/action bar. No external scripts, fonts or brand assets load
in the application.

The collection has a single Add object action; creation hides the collection and
makes it inert. Step 1 handles photos and viewpoint validation. Next shows Step 2
with a photo summary, name/type/observer and consent. Back and Edit preserve the
in-memory draft. Closing returns to the collection; reopening resumes the draft.
No object or image writes occur until Save object. A successful save returns to
the selected object's collection detail. Venue creation uses a native dialog.

## Simplified admin testing upload

The existing-object test photo form asks only for the image and viewpoint.
No identity/permission confirmation is collected or inferred in this form.
Photographer, capture session, source selection and rights text are removed from
this form. Uploads use explicit admin-test metadata and an automatically generated
capture ID. The server reuses a linked source; if none exists, it records a manual
admin-test association, without claiming official identity evidence. Photo rights
are recorded as details not supplied. The photo-first creation editor is unchanged.
