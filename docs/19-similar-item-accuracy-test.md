# Phase 5 similar-item accuracy test

Status: test specification; no new accuracy measurements have been collected.
This remains Phase 5 and does not enable automatic identification. Use the
current [Atlas-backed camera references](18-atlas-camera-references.md) and retain
the [v5 acceptance gates](15-visual-online-validation-v5.md).

## Question

Can the camera suggest the correct artwork when another artwork looks similar,
and can it withhold a suggestion when the pictured artwork is outside the selected
catalog? Measure exact object identity, not artist, title similarity or style.
This tests identification of the composition, not authenticity of the original.

## Dataset

The current two-artwork trial cannot establish general similar-item accuracy.
For a bounded painting benchmark, select ten reference-covered paintings forming
five visually similar pairs at one location, within the camera's twelve-object
limit. Select pairs before running inference: similar composition, subject,
palette or framing. Record the reason for each pairing and canonical object IDs.
Do not choose pairs merely because their titles resemble each other.

Use independently sourced public photographs under the existing
[online acquisition policy](13-visual-online-photo-acquisition.md). Each positive
identity needs six unseen captures: two full views, two informative partial views
and two difficult views (angle, glare, distance or obstruction). This gives
60 positives. Independent partial photographs count; crops of the same photograph
do not add independent samples. If suitable sources cannot be found, report the
coverage gap rather than filling it with generated images or reference copies.

Add 60 negative captures:

- 30 visually similar paintings absent from the selected reference catalog;
- 10 room/gallery views without an identifiable target in the camera guide;
- 10 uninformative details or badly obstructed views;
- 10 photographs of objects at another location, queried against this location.

Confirm labels against canonical source evidence before viewing predictions.
Record capture ID, object ID (including known negative identities), location,
pair/group ID, view category, source URL, author, license, capture/session group,
image hash and split. Preserve source credits separately from derived predictions.
Keep derivatives, near duplicates, photographer/session groups and source pages
together; exclude reference images and previously inspected evaluation captures.
Review visual duplicates as well as exact/normalized hashes.

Prepare a separate calibration set with comparable difficult positives and
negatives. Freeze the 120 evaluation captures before any threshold fitting.
Previously inspected v5 and server replays remain regression data.

## Procedure

1. Audit coverage, labels, licensing and split independence. Freeze the manifest,
   model hash, reference-set IDs/version, preprocessing and decision policy.
2. Run all ten identities together in the candidate catalog. Each positive must
   compete with its similar partner; never restrict the catalog to its true ID.
3. Replay each photograph through the actual browser crop/JPEG path and server
   recognizer. Record candidate IDs/scores, decision, latency and HTTP outcome.
   Bind every response to the frozen reference version; a version change
   invalidates a comparable run. Count service failures separately, without
   silently dropping them or treating them as successful rejection.
4. Evaluate the current policy first: geometry gate 0.4, candidate floor 0.55,
   runner-up margin 0.04. Scores are uncalibrated similarities. Any revised policy
   must be fitted on calibration data and frozen before evaluation.
5. Report static-frame decisions separately from browser tracking. Repeating one
   image three times does not establish independent evidence or live-camera
   stability. A subsequent phone trial measures time to three-frame acquisition,
   wrong stable identities and clearing after moving away; it needs separately
   consented capture/session evidence and must not introduce frame retention into
   the normal camera service.

For a static frame, a suggestion requires the first candidate to pass the floor
and its score minus the second candidate's score to pass the margin. Below either
threshold means abstention. This is a tentative suggestion, not an automatic match.

## Required report

| Metric | Definition |
|---|---|
| Positive top-1 accuracy | Correct first-ranked ID / all positive captures |
| Positive top-3 recall | True ID among the first three / all positive captures |
| Correct suggestion coverage | Correct displayed suggestions / all positive captures |
| Suggestion precision | Correct suggestions / all suggestions on positives and negatives |
| Similar-pair confusion | Suggestions of the paired wrong ID / positives in each pair |
| Unknown false-suggestion rate | Any suggestion / similar out-of-catalog negatives |
| Abstention rate | No suggestion / valid completed captures, by category |
| Service failure rate | Failed requests / attempted captures |

Include counts and denominators, a confusion matrix with an abstention column,
per-object and per-pair results, full/partial/difficult slices, and macro-average
positive accuracy so easy objects cannot hide weak ones. Rank ties, including
all-zero candidates, remain in ranking metrics but do not count as suggestions.
Report inference and end-to-end latency separately. Report 95% Wilson intervals
for proportions and disclose capture grouping; frames from one session are not
independent trials. Precision with zero suggestions is undefined, not 100%.

## Proposed benchmark acceptance

These are targets for this painting experiment, not a replacement for the broader
Phase 5 release gate:

- At least 54/60 correct top-1 rankings and 57/60 correct top-3 rankings.
- At least 30/60 correct suggestions, with at least 3/6 for every identity.
- At least 98% suggestion precision across positives and negatives.
- Zero displayed substitutions between similar partners.
- Zero suggestions on all 60 negatives, with the 30 similar negatives reported
  separately.
- Complete required coverage and a valid response for every evaluation capture;
  disclose retries and initial failures.

Zero errors in 30 similar negatives is only a small-sample result, not proof of
a sub-1% false-suggestion rate. Passing this benchmark establishes evidence for
the selected painting catalog only. Broader object types, the existing Phase 5
gates and real phone/network validation remain necessary for release.

If evaluation results inform reference or threshold changes, preserve this run
as regression evidence and use a new unseen holdout for the next accuracy claim.
