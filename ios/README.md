# VisitAdvisor iOS

Native offline exhibit recognition for iPhone. It ports the server camera
pipeline (`app/modules/live_camera`, DINOv2-small + OpenCV) to the phone; the
verified contract is in [docs/37-ios-discovery.md](../docs/37-ios-discovery.md).

**Status: written on Linux without Xcode. No Swift or Objective-C++ code here
has been compiled yet**, so expect a round of compiler fixes on the first Mac
build. Python parts are tested (`tests/test_venue_package.py`).

## Layout

| Path | What |
|---|---|
| `VisitAdvisorKit/` | Swift package: package format + validator, Pillow-exact preprocessing, Accelerate/Metal search, ranking, decision and stability logic. Unit tests with synthetic fixtures. |
| `VisitAdvisorApp/Sources/` | SwiftUI app: venue list, download/update/delete, camera screen, exhibit detail, diagnostics; package store; Core ML engine; AVFoundation capture. |
| `VisitAdvisorApp/OpenCV/` | Objective-C++ bridge: region proposals, SIFT, RANSAC homography evidence (ports of `regions.py` and `features.py`). |
| `project.yml` | XcodeGen spec for the app target. |
| `scripts/build_opencv.sh` | Builds `Vendor/opencv2.xcframework` from OpenCV 4.14.0 (the server's version). |

## Pipeline on the phone

1. Camera (720p, portrait) → sampled at 3 Hz; AVFoundation drops late frames and
   the app keeps one running plus one newest pending frame.
2. Centre square of 224/256 of the short side → 512×512 frame, using a
   **bit-exact port of Pillow's bicubic resampler** (verified by SHA-256 against
   the server's Pillow output in `PreprocessingTests`).
3. Flat-frame check → OpenCV region proposals (up to 2) → for each box: crop,
   224 bicubic, ImageNet normalization → DINOv2 (Core ML) → L2-normalized query.
4. Exact search over the package matrix (Accelerate, or Metal in diagnostics),
   view score `0.75·region0 + 0.25·max`, max over views.
5. Paintings, drawings, prints and photographs score **only** through SIFT +
   homography (≥ 0.4), as on the server. Without OpenCV they cannot match.
6. Best per distinct exhibit, top 3 → server decision rule and the browser's
   stability rule (3 frames, 600 ms). The package policy is uncalibrated, so
   results show as **"Possible match"**, never "Identified".

## Server side

- `GET /live/venues/{id}/package/manifest` (ETag/304) and
  `GET /live/venues/{id}/package/{version}/{file}` serve an immutable package
  built from the exact Atlas snapshot the server recognizes against.
- **Disabled by default.** Set `LIVE_CAMERA_OFFLINE_PACKAGES=true` only after
  approving that derived reference descriptors (DINOv2 vectors and SIFT data,
  not photos) may be downloaded to phones.
- The venue must use the adaptive pipeline (`LIVE_CAMERA_ADAPTIVE=true`);
  otherwise the manifest returns 409 `PACKAGE_UNAVAILABLE`.

## Build on a Mac

```bash
# 1. Swift package tests (no OpenCV or model needed)
cd ios/VisitAdvisorKit && swift test

# 2. Core ML model (Python 3.12, internet access to Hugging Face)
cd ../.. && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[visual]" "coremltools>=8,<9"
python -m scripts.convert_dinov2_coreml --download --output ios/VisitAdvisorApp/Models

# 3. OpenCV (20-40 min; needs: brew install cmake)
ios/scripts/build_opencv.sh

# 4. Xcode project
brew install xcodegen && cd ios && xcodegen && open VisitAdvisor.xcodeproj
```

Then select your iPhone, set your signing team, and run. To test against a
local backend, change `VISITADVISOR_API_BASE_URL` in `project.yml`.

## Known gaps

- Nothing compiled or run yet; no device measurements (see the brief's report).
- Phone frames are not JPEG-encoded as the browser's are; the model sees
  slightly different pixels. Measure on real photos before trusting thresholds.
- Motion detection (`signature`/`motion` in `recognition.mjs`) is not ported;
  movement does not reset the tentative result yet.
- No calibration: confident identification stays disabled until held-out
  museum photos are collected and the policy is fitted.
- SIFT/RANSAC parity between OpenCV builds is not yet measured.

## Ray-Ban Meta glasses as the camera

The glasses are a second `FrameSource` (`GlassesFrameSource.swift`) beside the
iPhone camera. Both emit the same 512 contract frame, so recognition, ranking
and stability are unchanged.

- SDK: Meta Wearables Device Access Toolkit 1.1.0 (`MWDATCore`, `MWDATCamera`,
  `MWDATMockDevice`), added in `project.yml`. Minimum iOS 17.2.
- Stream: raw 720x1280 at 15 fps over the glasses' Wi-Fi, sampled at 3 Hz.
  The SDK may lower resolution on a weak link. A cap-touch tap pauses it.
- Flow: Glasses screen (eyeglasses icon) → Connect → Meta AI approves →
  returns via `visitadvisor://`.
- Venue flow: the list shows available venues; selecting one starts its download
  (progress also shows in the list). Scanning is offered once it is installed.
- Two modes: each venue screen has a Camera switch (iPhone / Glasses), remembered
  between launches, then "Start scanning". The first glasses scan asks for camera
  permission in Meta AI.
- Testing without glasses (Debug builds): Glasses screen → "Use simulated
  glasses". The simulated glasses show the iPhone's back camera.

Before the first glasses run:

1. Glasses firmware v20 or later; glasses paired in the Meta AI app.
2. Meta AI app → Settings → your glasses → Developer Mode: on. While it is on, `MetaAppID` can stay empty.
3. Signing & Capabilities must accept Hotspot Configuration and Access Wi-Fi
   Information (`VisitAdvisor.entitlements`). If your signing team cannot
   provide them, Xcode reports it there.

Distribution limit: the toolkit is a developer preview. Apps using it cannot be
published on the App Store yet; testers get builds through Meta's release
channels (Wearables Developer Center) plus TestFlight/ad-hoc installs.
