# Guided capture: real-image failure audit

The current browser capture model does not yet meet the intended arbitrary-object
capture requirement. The tracking fixes address interaction failures; they do not
establish complete, correct object segmentation. The iPhone report of repeated
sharpness warnings prompted this audit.

## Separate failures

The focus calculation enlarged target crops smaller than 256 pixels before
measuring their Laplacian variance. Interpolation alone could turn a sharp edge
into a rejected image: an unblurred step edge measured 114.29 at 256 pixels,
37.68 at 180 pixels and 14.29 at 128 pixels against the unchanged threshold of 40.
The local correction caps analysis at native resolution. The sharp 180-pixel
example then measures 163.64. Regression tests still reject blurred and smooth
targets from automatic selection. This corrects preprocessing, not detection.

Low interior variation also occurs on genuinely smooth objects. A low sharpness
score cannot establish whether such an object is out of focus. The numerical
gates remain uncalibrated for the physical phone and must not be interpreted as
recognition readiness or mask correctness; see [the capture design](27-guided-reference-capture.md).

## Actual segmentation observations

The pinned MagicTouch worker was run in local Chromium on four preserved source
photos, at a maximum 640-pixel edge, with explicit interior taps. No external
model requests or Atlas writes occurred. The [diagnostic measurements](guided-capture-quality-audit-2026-11-07.json)
record source hashes, taps, model identity and deliberately blurred derivatives.
Those derivatives are diagnostic cases, not independent photographs.

- Shiva front: the mask selects the central figure while omitting its ring,
  several arms and base. The selected pixels occupy about 3.59% of the frame,
  triggering the size gate despite sharpness around 1000. The mean foreground
  probability is about 0.975; it does not certify a complete object.
- The Milkmaid: the chosen tap selects the depicted woman, rather than the whole
  painting. The capture profile alone does not convey the intended object extent
  to this point-only segmenter.
- Summer: the statue/pedestal boundary changes inconsistently between diagnostic
  inputs. A saved mask needs visual review.

These are observed failure examples. They are not an estimated error rate, a
human-mask IoU benchmark, or physical iPhone acceptance. Diagnostic timings include
quality analysis and overlay encoding and must not be presented as model latency.

## Replacement-model comparison

Evaluate one real candidate on the same source-hashed scenes, retaining every
candidate mask and measuring whole-object retention, background contamination,
latency and peak memory. Use an explicit full-object prompt or correction when
a point admits several reasonable object extents. After freezing the method,
validate second-view capture on the actual iPhone and other supported browsers.

The completed local [SAM 2.1 tiny comparison](guided-capture-sam2-comparison-2026-11-07.json)
used the official checkpoint at revision
`de431c4043854a71d8101e17995dfe596bf101a5`. Compatible torchvision was isolated
under `/tmp`; application dependencies were unchanged. Loading reported no
missing, unexpected or mismatched image weights. All masks were retained.

With the same points, one SAM candidate covered Shiva's ring/body, but the
highest-scored candidate still selected body parts. Painting candidates selected
the depicted woman/clothing. An additional human-supplied box improved Shiva
coverage, while the highest-scored mask also included background plaques; the
painting still had no full-artwork candidate. This does not justify automatically
replacing the existing model or choosing its highest-scored output.

Local CPU forward time was about 1.1–1.7 seconds with four threads. One-thread
warm runs on Shiva took about 3.1–3.2 seconds. Standalone peak process memory was
about 1.06 GiB, excluding the existing application's other workloads. These are
development-host measurements, not Cloud Run or phone latency measurements.

The next iteration needs type-aware target definition and a reviewed real-image
comparison, as well as a stronger model. No model replacement, paid API, GPU
service or production configuration change has been made by this audit. The
native-resolution sharpness correction is local and has not been deployed.
