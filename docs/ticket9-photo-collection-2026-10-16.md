# Ticket #9 public photo collection — 2026-10-16

The collection contains **21 reviewed artwork photographs and 20 reviewed
room-only negatives**. On 2026-10-17 the user requested stopping further acquisition
and completing ticket #9 using these 41 photographs. The nine missing captures
below describe the original sampling plan; they are no longer acquisition work
for this ticket. Paid-license research is stopped; nothing was purchased or sent.

No recognition inference was performed during collection. Evaluation started on
2026-10-17 using a separate `current-dataset-regression-2026-10-17.json` manifest.
These photos must therefore no longer be presented as an untouched holdout for
subsequent development. Using the reduced collection does not waive accuracy,
false-suggestion or camera coverage requirements, or establish the original
50-photo acceptance result.

The original downloaded image bytes, source metadata and collection artifacts are
in the ignored directory `.local/ticket9-collection-2026-10-16/`. Images are local
validation evidence, not production references or application assets. Open
`index.html` in that directory to review the selected photographs and their source
and license links. The complete machine-readable credits are in `credits-v2.json`;
`manifest-v2.json` is the current replay input and `preflight-v2.json` is the data-only audit.

| Artwork | Collected independent captures | Required | Remaining |
| --- | ---: | ---: | ---: |
| The Night Watch | 6 | 6 | 0 |
| The Milkmaid | 6 | 6 | 0 |
| The Syndics | 4 | 6 | 2 |
| The Jewish Bride | 2 | 6 | 4 |
| The Battle of Waterloo | 3 | 6 | 3 |
| **Artwork total** | **21** | **30** | **9** |
| **Room-only negatives** | **20** | **20** | **0** |

These five artwork identities remain the proposed bounded catalog, with seven
Rijksmuseum room/building identities competing. Incomplete artwork identities
may be replaced with eligible catalog artworks before the final freeze; Night Watch
and another previously confused artwork must remain. No model results were used to
choose identities or photographs.

## Evidence and selection

The initial source search obtained metadata for 1,583 Commons files. The main download pass
produced 237 files that passed image decoding/normalization, plus additional files
retained outside the manifest. Raw download counts are not accepted-photo counts.
Source errors and download rate limits are recorded in `download.log` and the search
logs. The downloaded pool contains many reproductions, scans, replicas and unrelated
subjects. Unselected files and unresolved candidates remain separate from the
41 selected photographs in `unselected-candidates.json` and the raw metadata.

Each selected record preserves the source page, download URL, original URL,
photographer, uploader, license and license URL, available capture date/camera
metadata, downloaded-byte SHA-256, normalized-image SHA-256 and visual review note.
Capture groups use photographer and capture date, rather than unique filenames or
importer account names. Multiple photographs from one photographer/visit are counted
once. The architecture photographs credited to Duccio Malagamba retain their source
licensor/uploader separately. Different photographers on the same event/day can still
share environmental conditions; this is a limitation of online acquisition.

Historical source IDs/titles, known photographer/date aliases and current Atlas
reference metadata were screened before selection. The data audit confirms that
none of the selected raw or normalized image hashes match known historical query
or reference bytes. This hash check does not prove independence by itself; public
source metadata and human visual review support the capture-group decisions.

Room negatives cover library, atrium, corridors and interior gallery context with
none of the five target paintings visible. They include visitors, equipment and
other museum objects. They are negatives for the selected artwork catalog, rather
than a claim that no object of any kind appears in the photographs.

Commercial-reuse Creative Commons/public-domain sources were used. Flickr candidates
with noncommercial licenses are excluded from the selected set. Two selected source
images use CC BY-ND 2.0: the source files remain unchanged, with attribution and any
existing image border preserved. Do not distribute modified copies. See the
[license terms](https://creativecommons.org/licenses/by-nd/2.0/). A screenshot,
reproduction, generated crop or edit of an old capture does not fill a missing slot.
PNG bytes were retained as PNG with the matching filename extension; source photos
were not cropped or edited. Normalized bytes are computed only for duplicate audits.

## Data-only validation

The preflight ran with:

```bash
.venv/bin/python -m scripts.artwork_room_replay \
  --manifest .local/ticket9-collection-2026-10-16/manifest-v2.json \
  --audit-only \
  --output .local/ticket9-collection-2026-10-16/preflight-v2.json
```

It exited **1**, as intended for incomplete dataset gates. Twenty room-only negatives,
evaluation-only splits, artwork target types, reviewed capture groups and historical
hash exclusion passed. Thirty artwork photographs and five identities with six
capture groups each failed. `recognition_requests_attempted` is **0** and
`ready_for_acceptance_run` is **false**. The existing audit file is immutable; use a
new output filename when checking a later revision.

The original acquisition plan called for nine additional independent artwork captures: two
Syndics, four Jewish Bride and three Waterloo.
That acquisition plan was stopped at the user’s request on 2026-10-17. The original
plan was to freeze the manifest, reference snapshot, code,
model and calibration policy before measuring recognition. This acquisition does
not establish accuracy, latency or ticket completion.

## Expanded Flickr acquisition

Public Flickr search pages were queried with commercial-use license filters. Only
public photo records were read; no account login, application API key or provider
call was used. Search metadata is preserved in the `flickr-search*` directories;
photo-page license links/capture dates are in `flickr/`. Downloaded search candidates
are in `flickr-downloaded.json`, and selection/exclusion notes are in
`flickr-review-decisions.json`. Candidate counts include unrelated subjects and
are never used as acceptance counts.

The additional pass admitted 16 photographs after visual, source-license and
source-history review. Flickr owner IDs and capture dates establish conservative
visit groups. Upasna is credited separately from uploader solarisgirl; that source
caption expressly records the photographer's CC BY-SA permission. The Dan Lundberg
full framed view is the photographer's published crop of a fresh capture, counted
once, not a newly generated partial-view example.

Flickr IDs were also extracted from historical artifacts and the source metadata
for previously used Commons files. A historical Flickr image excludes other images
from that same owner and capture date. This rejected Joao Araujo's 2005 visit even
where a different image from the visit had novel bytes. All 136 current reference
image hashes were additionally checked against selected raw and normalized hashes.

The first 25-photo revision and audit remain as `manifest-v1.json`,
`credits-v1.json` and `preflight.json`. The expanded revision is stored separately;
its audit still fails both positive coverage gates and has zero recognition
requests. The current reviewer gallery links only the 41 selected source files.
