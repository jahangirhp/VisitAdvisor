# Camera candidate verification performance

The 2026-11-08 Circus box report concerns a fresh photo on a patterned rug.
The Rijksmuseum entry has four prepared black-table photographs, not that rug
photo. Saved-reference replay must not be reported as reproducing the fresh
failure. The chat attachment was visible to the agent, but its binary image was
not available in the workspace; locating the exact query remains necessary.

The original public server took 1379–1693 ms per saved Circus frame, with total
HTTP times of 1750–2596 ms from the development machine. The frontend additionally
rejects responses older than three seconds and requires three agreeing results.
Extra network delay or movement can therefore prevent acquisition despite a
strong server candidate. These observations do not establish the cause of the
fresh rug photo's failure.

The standard camera previously ran SIFT matching against every geometrically
verified reference before selecting three results. The corrected standard path
computes inexpensive embedding bounds first. Geometry can reject a view's score
but cannot increase it. It verifies views in descending bound order, checks
geometry against the exact source view, and stops once a view or object cannot
change the retained leading scores. Tied bounds are still considered.

For the current planar-and-scene-v2 policy, candidates whose upper bound is below
candidate_floor minus candidate_margin are omitted. They cannot establish a
match or prevent any match meeting the existing floor from passing its margin.
Close competitors below the display floor remain eligible for verification.
Omitted candidates are not returned with fabricated verification scores. The
current cutoff is 0.51, with the unchanged 0.55 floor and 0.04 margin. This can
change the weak candidates returned below the cutoff; accepted identity decisions
are preserved. No arbitrary shortlist count or lower acceptance threshold is
introduced. The experimental spatial-geometry and adaptive pipelines retain their
existing behavior. Uploaded frames, cropping, motion limits, freshness limits,
three-response requirement, photos and prepared descriptors are unchanged.

Regression tests exercise a 114-object scene where 113 weak paintings require no
SIFT work, and compare decisions with exhaustive evaluation across 101 seeded
mixed-view catalogs. The explicit close-competitor case still blocks acquisition.
Existing same-source-view geometric verification and rejection tests pass.
Validation: 513 Python tests passed, two opt-in tests skipped; Ruff lint and format,
mypy and 35 JavaScript tests passed.

Diagnostic limits: excluding each saved Circus photograph from its references
produced winning embedding scores from 0.7352 to 0.9631 against the other saved
photos. Replacing the backgrounds using the three stored foreground masks and
again excluding the source image produced 0.6447–0.8680. All exceed the unchanged
floor and margin against conservative embedding upper bounds for other objects.
These are same-session or synthetic diagnostics, not an independent holdout,
physical-phone acceptance measurement or a test of the unavailable rug image.
Private photographs, masks and numerical working files remain outside the
repository under /tmp/visitadvisor-circus-audit. No object, source, photo or prepared
reference records were changed by these diagnostics.

The isolated speed preview preserved all twelve baseline acquisition decisions,
including Circus variants, Santa, Milkmaid, a supported Dancing Couple photo and
abstentions. Circus server times fell to 111–147 ms before the object-reference
update. Both Chromium and WebKit completed the three-response moving-image loop.
The combined release is recorded in doc 33; its timings differ slightly and must
not be presented as end-to-end visitor latency.
