# Prepare selected objects rather than their capture backgrounds

The Circus box report clarified that reference matching should survive moving an
object to a different background. Guided capture already stored selected areas
and foreground masks, but publication embedded the entire original photo. That
made the selection ineffective during reference preparation.

Admin publication now derives a separate object-focused encoder input when a
selection exists. A stored binary foreground mask removes background pixels;
manual box selections use a source crop. Eight-percent padding and a square white
canvas preserve the full selected extent through the encoder's center crop,
including wide objects. Original uploads and masks are never overwritten.
Room/building references and explicit scene profiles retain their contextual
input. Photos without a usable selection retain the existing fallback; they are
not described as segmented objects.

Each focused view records object-focus-v1 preparation metadata: method, original
and normalized source hashes, mask hash, source dimensions, crop coordinates and
derivative hash. The existing pinned image encoder and its preprocessing remain
unchanged: they process a different, explicitly source-linked input image. Source
image hashes continue to identify the originals; derived images are not presented
as independent observations or new viewpoints.

Publication can replace the derived representation of an already-published upload
without adding duplicate views. It stages a fresh set and vectors through the
existing evidence-checked compare-and-swap publisher. A repeat with unchanged
preparation performs no publication. Unrelated references remain untouched.

The Atlas camera loader prefers the focused views when an object has any. It
excludes unselected full-scene fallbacks for that object, so those views cannot
reintroduce background dependence. All private photos remain stored. A focused
view with inconsistent source linkage is rejected. Historical objects without
focused preparations retain their existing references.

The four existing Circus photographs yield three focused masked references. The
fourth capture explicitly skipped selection; its source and legacy descriptors
remain in storage but are excluded from the focused camera cohort. No viewpoint
labels, object coverage or perfect segmentation are inferred. An inaccurate mask
can still omit useful object parts; keeping originals permits later correction.

Tests cover source preservation, white background and complete wide-object
extent, scene/fallback behavior, mask integrity, repeat-safe re-preparation,
selected-view loading and source mismatch rejection. All 519 Python tests passed
(two opt-in skips), Ruff lint/format and mypy passed. Thirty existing unrelated
public images produced no Circus suggestions with focused references; their
maximum Circus embedding score was 0.227 against the unchanged 0.55 floor.

Different-source-photo checks excluded each query's original from its references.
All four original camera crops and three synthetic-background variants still
passed the existing floor and margin. These are same-session/synthetic diagnostics,
not independent acceptance tests. The rug-background chat photo was visible but
its image file was not available locally or among the saved uploads, so the exact
reported view remains unverified. Query frames themselves are not segmented by
this change; background invariance across arbitrary images is not established.

Deployment: visitadvisor-api-focus-08aa8978e91f serves 100% of traffic. Circus box
was republished through the existing source-checked publisher to reference set
423d9743299b481a9638cfab03097e97, preserving all four upload hashes and records.
The live catalog still has 114 objects, with version
788923d86ebebd833d8ea9d4d9fc4e1dc69b92e8dd338ff5979363eb6eff69cf.
Only Circus descriptors were republished; Santa and museum references were not.
Runtime environment, resources, service account and one warm instance were kept.

The combined preview preserved the twelve baseline acquisition decisions, with
Circus server inference at 160–182 ms. Both browser engines acquired Circus on a
synthetic changed-background image moving eight pixels during each actual server
request. Production repeated that check: Chromium acquired in 1.79 seconds and
WebKit in 4.02 seconds, each after three HTTP 200 recognition responses with no
page exceptions. Production per-frame server times ranged from 153 to 226 ms.
Those timings include a saved-image simulation, not a physical phone session or
the exact rug photo. No broad recognition-accuracy acceptance claim is made.


## Review polish

Manual box padding now copies only pixels inside the selected rectangle onto the
white square, rather than extending the source crop into the surrounding scene.
Floor/ceil pixel boundaries retain even small positive selections. Full-frame
selection handling accepts both normalized list and tuple representations.

Re-preparation compares the complete old/new preparation state. Changing an
object to a room/building can therefore restore the original scene descriptor
instead of silently retaining its old object crop. Original uploads, source hashes
and repeat-safe publication remain intact.

Regressions reproduced the manual-padding and scene-restoration failures before
these fixes. All 525 Python tests passed (two opt-in skips), together with Ruff
lint/format, mypy and diff checks. The three published Circus masked derivatives
were recomputed locally and matched their existing preparation metadata and image
hashes exactly. No stored references need rewriting for this review polish.

The polish release visitadvisor-api-polish-da3d4cb98e3e now serves 100% of traffic.
Only reference preparation and its refresh condition changed in this release;
the visitor matching and camera code match the preceding deployed source.
Chromium and WebKit both acquired Circus on the existing synthetic-background,
simulated-motion replay after three successful server responses. The public
endpoint repeated the Circus match at 231 ms server inference. Health, environment,
resource limits, service account and one warm instance were verified. The live
catalog had grown to 115 objects independently; this release wrote no photo or
reference records. These checks retain the image/device limitations above.
