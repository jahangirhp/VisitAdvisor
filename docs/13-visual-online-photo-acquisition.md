# Phase 5 online-photo acquisition decision — 2026-09-16

The Rijksmuseum remains the Phase 5 pilot location. Public sources contain enough
promising camera photographs to justify an online-only acquisition pass before
changing locations. This decision does not claim that the validation gate has
passed: every downloaded file still needs identity, independence, license and
viewpoint review before it enters a frozen dataset.

## Constraint and decision

No new photographs will be requested from a visitor. Validation photos will be
collected from public, licensed sources, with Wikimedia Commons as the first source.
The broad Commons category for photographs of paintings in the Rijksmuseum contains
more than one thousand files. Individual artwork categories also contain useful
camera photographs, but their raw counts include museum scans, crops, replicas,
historic reproductions and other files that are not valid visitor queries.

Changing the pilot now would discard the prepared Rijksmuseum catalog and reference
coverage without proving that another location has a better independent-photo set.
The location will change only if the reviewed online pool cannot supply the required
identity and viewpoint coverage.

## First reviewed acquisition batch

Thirty-six 1280-pixel candidates were downloaded into the ignored local directory
`.local/visual-public-photos/online-audit/`. Their source page, download URL, byte
count and SHA-256 hash are preserved locally before inference. Metadata and visual
review accepted twenty-six as positive camera-photo candidates, retained one distant
gallery view as an uninformative calibration candidate and excluded nine museum
reproductions or derived crops. No recognition result was inspected during this
review. The review found:

- **Banquet of the Amsterdam Civic Guard, SK-C-2:** one framed full view and two
  independently photographed details are strong candidates. This is the clearest
  complete online identity in the first batch.
- **The Battle of Waterloo, SK-A-1115:** a full camera photograph, a gallery view
  and a close file exist. The full photograph is a positive candidate, the distant
  gallery view is an uninformative candidate, and the close file was excluded
  because its source is a museum reproduction rather than an independent capture.
- **Self-Portrait with Grey Felt Hat, SK-A-3262:** multiple independent camera
  contexts exist, including a framed view and a gallery view with visitors. The
  current batch does not yet contain two useful close views.
- **The Windmill at Wijk bij Duurstede, SK-C-211:** two modern camera photographs
  and a historic gallery photograph exist. More independently photographed detail
  coverage is needed.
- **Winter Landscape with Skaters, SK-A-1718:** a framed visitor photograph and
  several detail files exist. The framed visitor photograph is a positive candidate;
  the reviewed reproduction and museum-derived detail were excluded.
- **The Night Watch, SK-C-5:** six independent camera photographs cover frontal,
  oblique and gallery-context views from several photographers and visits. Four
  additional camera files were accepted as independently photographed close views;
  five official or derivative detail files were excluded. This identity now has
  enough online full and partial candidates for the strict viewpoint target.
- **The Milkmaid, SK-A-2344:** four independent physical-object photographs were
  accepted. A fifth file showing a reproduction in an advertising display was
  excluded. More independently photographed close views are still needed.

The Night Watch category has 60 top-level files and a 32-file detail subcategory.
Only files supported by capture evidence were accepted; category membership alone
was not sufficient.

## Acceptance rules

A positive query photo is accepted only when all of the following are recorded
before inference:

1. The accession or exact object identity is supported by the Commons description,
   Wikidata link or another canonical record.
2. The file is a camera capture of the physical object. Museum master images,
   scans, screenshots, AI images, replicas and photographs of reproductions are
   excluded.
3. Photographer/uploader, license, source page, original timestamp when available,
   camera metadata when available, download URL and SHA-256 are recorded.
4. The capture group is independent of the reference set and remains entirely in
   one dataset split. Crops, edits and burst images from one original stay in the
   same capture group.
5. A partial view must have been photographed as a partial view. Cropping a full
   image does not create independent partial-view evidence.

Files are downloaded only to the ignored validation workspace. Atlas stores derived
embeddings and provenance records; it does not need a permanent copy of every public
validation image. The frozen manifest and credits artifact remain in `docs/` so the
dataset can be audited and reconstructed while the source remains available.

## Switch criterion

Complete the bounded Rijksmuseum audit first. Keep the location if the reviewed pool
supplies eight additional identities with three independent camera captures each and
the missing viewpoint coverage for existing identities. If it does not, compare a
single alternative location using the same rules before changing any production
catalog data. A sculpture-heavy public collection is the preferred alternative
because independently photographed angles are more common than for small paintings.

That bounded acquisition is now complete. The frozen v4 corpus supplies 20
identities and all required views without changing the Rijksmuseum pilot. See the
[v4 validation report](14-visual-online-validation-v4.md) for the source-audited
dataset, held-out measurements and the remaining automatic-coverage gate.
