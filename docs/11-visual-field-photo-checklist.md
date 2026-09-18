# Phase 5 validation-photo checklist

The current acquisition path is online-only; no visitor will be asked to take new
photos. See the [online acquisition decision](13-visual-online-photo-acquisition.md).
The capture instructions below remain the acceptance standard for public camera
photographs and document what viewpoint evidence is still missing.

This is a collection worksheet, not a completed test or a claim that these objects are currently on display. Confirm the accession and gallery availability before taking a photo. All listed targets already have visual reference coverage; the last eight are replaceable suggestions from the current indexed catalog.

Selected location: `6a9dd4106d52c3bc3965869d` (Rijksmuseum).

## Capture instructions

Find one full view and two separately photographed informative partial views for each target. For paintings, prefer different angles, glare and framing. For sculptures, include another side and a distinctive close view. For the building, include the facade and distinctive architectural portions. Do not crop a full photo to manufacture the two partial captures. Do not use reference reproductions or AI-generated images as validation queries.

Save original JPEG, PNG or WebP files within the API limits (5 MiB and 16 megapixels). Record object ID, original filename, photographer, visit/date, camera, view and expected identity before looking at recognition results. Assign each independent capture a stable capture-group ID; derivatives and near-duplicate bursts stay together. Keep the same photographer/visit entirely within calibration or evaluation.

Collect calibration photos in a different session from evaluation. Include representative unknown objects, similar objects, wrong-location queries and uninformative details in calibration as well as evaluation. The current eight calibration negatives cover all four categories but remain too small for production calibration.

For a fresh blind evaluation, collect all three views for each of twenty objects (60 positives) and at least 30 negatives spanning the four categories. The table describes the smaller numerical gap in the existing regression corpus; filling it does not make already inspected results a fresh blind test.

## Current regression-corpus gaps

| Object | Accession | Object ID | Additional full | Additional partial |
|---|---|---|---:|---:|
| Woman Playing the Virginal | SK-C-140 | `6a9dd812332abe8469d55919` | 0 | 2 |
| Portrait of a Man, possibly Reijnier Pauw (1591-1676) | SK-C-1440 | `6a9dd817332abe8469d55937` | 0 | 2 |
| Portrait of a woman | SK-A-3843 | `6a9dd818332abe8469d55938` | 0 | 2 |
| Landscape by Moonlight | SK-A-3245 | `6a9dd820332abe8469d55942` | 0 | 2 |
| Dancing Couple | SK-A-2568 | `6a9dd841332abe8469d5596a` | 0 | 2 |
| The merry family | SK-C-229 | `6aa9a744dd73e04d9614615e` | 0 | 2 |
| The Sampling Officials of the Amsterdam Drapers’ Guild, known as ‘The Syndics’ | SK-C-6 | `6aa9a745dd73e04d96146160` | 0 | 1 |
| Shiva Nataraja | AK-MAK-187 | `6aa9a746dd73e04d96146161` | 0 | 2 |
| The Threatened Swan; Interpreted later as an Allegory on Johan de Witt | SK-A-4 | `6aa9a747dd73e04d96146163` | 0 | 1 |
| Rijksmuseum building | Building | `6aa9a7fa60b7ca89b6355b9c` | 0 | 0 |
| The Fall of Man | SK-A-129 | `6aaad084dd73e04d9614e9e7` | 0 | 0 |
| View of Itamaracá Island | SK-A-4271 | `6aaad084dd73e04d9614e9e8` | 0 | 0 |
| Misty Sea | SK-C-1726 | `6a9dd7f8332abe8469d558f5` | 1 | 2 |
| The cat at play | SK-A-3089 | `6a9dd7fa332abe8469d558f7` | 1 | 2 |
| Pinken in de branding | SK-A-2670 | `6a9dd7fb332abe8469d558f8` | 1 | 2 |
| Portret van een Surinaams meisje | SK-A-2637 | `6a9dd7fc332abe8469d558f9` | 1 | 2 |
| Portret van Surinaams meisje | SK-A-2641 | `6a9dd7fd332abe8469d558fa` | 1 | 2 |
| Portret van een Surinaams meisje | SK-A-2635 | `6a9dd7fe332abe8469d558fb` | 1 | 2 |
| Zelfportret | SK-A-5110 | `6a9dd7ff332abe8469d558fc` | 1 | 2 |
| Landscape with Stone Bridge | SK-A-1935 | `6a9dd800332abe8469d558fd` | 1 | 2 |

Minimum additional positive captures for this worksheet: **40**. The existing evaluation split already has 30 negatives and all required object types, but automatic-match sample size and accuracy gates remain separate requirements.

## Phone/network test

After offline accuracy gates pass, exercise the frozen regional policy through the API. Use the actual phone and intended connection and record request-to-result times, device/network conditions, HTTP status, candidates and ground truth. The existing in-process API harness measures the backend only; it cannot establish mobile end-to-end latency. There is no camera UI in the current phase.

Keep automatic identification disabled until the recorded gates pass. Do not adjust thresholds using the held-out evaluation answers; collect a new held-out set if development uses those results.
