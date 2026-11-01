# Persistent object information — ticket #31

Development date: 2026-10-26. Branch: `MVP-31-persistent-object-results`,
based on the content-integration commit `76716ae`.

Status: browser implementation and regression testing; physical-phone acceptance
remains pending. This change does not improve object identity accuracy, localize
an object, add an outline, suppress frame uploads, or enable automatic recognition.
Those are separate recognition/localization/optimization issues.

## Cause and implementation

Previously `resetTracking()` reset recognition state **and** erased the object
card and cancelled its content request. Motion checks, missing video frames,
video events and transient transport failures used that same reset. Separately,
weak/missing recognition results called `clearResult()`. A retained reading card
therefore depended on uninterrupted fresh recognition.

The implementation separates the card from live evidence:

| Event | Tracking | Information card |
| --- | --- | --- |
| First stable tentative candidate | Existing acquisition rules | Load its canonical summary and prepared content |
| Fresh evidence for same candidate | Live tentative match | Keep existing DOM/content; no new content request |
| Weak frames, motion, missing/stalled video | Recheck/reset as appropriate | Keep it labelled `LAST SEEN · CAMERA RECHECKING` |
| No recognizable object for a prolonged period | No live identity | Retain last-seen information for this session |
| One competing candidate / ambiguous scene | Require fresh acquisition | Keep previous card explicitly last-seen |
| Different candidate becomes stable | New live tentative candidate | Replace card and invalidate previous content request |
| Transient 429/503/timeout | Reset live evidence, retry | Keep last-seen information |
| Three consecutive transport failures | End scanning session | Clear result/content under existing Stop behavior |
| Stop, background/page exit, new session or location | Reset/cancel | Clear result/content |
| References changed, expired or unavailable | Stop and reload catalog | Clear result/content; no stale fallback |

The freshness timer must not continually reset an acquisition while a retained
card exists. It resets an expired confirmed track, but lets an unconfirmed new
acquisition collect its required observations. It also checks catalog expiry when
video frames are missing. Repeated same-state updates do not recreate the label.

`data-state=live` means a fresh **tentative** candidate, never a verified identity.
`data-state=last-seen` means remembered information; it makes no claim that the
object is currently visible. There is no live spatial overlay in this client.
Future overlays must follow fresh region tracking, not the retained card.

The existing score/margin policy, three agreeing frames, optional minimum stable
time, three-second freshness and 1.5-second weak-evidence grace remain unchanged.
Local motion does not refresh recognition timestamps. Nothing is persisted to
browser storage or the database; this is only a current-session reading card.

Same-object reacquisition preserves the currently loaded content and any expanded
citation, avoiding another fetch. A different stable object, session/catalog
invalidation or Stop still cancels/invalidates old asynchronous content. This
supersedes the transient-loss/refetch behavior described historically in docs/24.

## Development comparison

A two-mode Chromium diagnostic used the baseline script from commit `76716ae`
and the changed script with identical mocked camera/catalog/content responses.
After acquisition it changed the frame signature substantially, supplied no
candidate for 3.2 seconds, then restored the original candidate. Source photos,
real walking and identity accuracy were not evaluated by this fixture.

| Observation | Baseline | Updated |
| --- | ---: | ---: |
| Card visible after pan/unknown interval | No | Yes, explicitly last-seen |
| Content requests after same-object reacquisition | 2 | 1 |

Both diagnostic assertions passed. The temporary comparison harness was removed;
the committed parent script and the permanent movement/reacquisition browser
regressions retain the inputs/behavior needed to reproduce this comparison.

## Validation and remaining work

Permanent browser regressions cover retained cards after weak/zero/tied scores,
large motion, a 30-second jitter/lost-view interval, transient 429/503, video
waiting/stalled/emptied, same-object recovery without refetching, a single competing
frame followed by stable target switching, content completion during tracking loss,
reference unavailability and expiry with missing video, persistent transport
failure, and existing Stop/background/location/late-response isolation.

The 30-second fixture asserts no card disappearance or content replacement and one
content fetch through loss and same-target reacquisition. Synthetic color changes
are controlled motion signatures, not proof of real handheld recognition quality.

Final development checks passed: 349 Python tests (two opt-in tests skipped),
15 JavaScript unit tests, Ruff lint/format and `mypy app`. The final browser run
passed all 74 tests: 37 Chromium and 37 WebKit. A browser job initially failed because another
job shut down their shared managed server; reruns use a dedicated persistent test
server to avoid that setup race. Such setup failures are not accuracy results.

Physical iPhone walking, blur/focus, viewpoint/zoom, real occlusions, similar or
crossing objects, and before/after timing still require the ticket's consented
phone sessions. Static/mocked browser tests do not satisfy that acceptance. #31
stays open; #7/#12/#19 share the physical evidence. #32 owns objects amid clutter.

## Uncertainty verification — #13

After the 74-test run, three additional cases passed in both Chromium and WebKit
(six targeted tests): initial tied scores, below-floor scores, and unknown/wrong-
location candidate IDs with keyboard operation of camera/menu controls. Together
with the preceding run this covers 40 camera cases per engine. #13 can close for
its browser-only scope; physical-phone and recognition accuracy gates remain open.

Under #31, a last-seen card is intentionally retained on target loss. It is
explicitly historical, not obsolete live evidence. A stable replacement object or
session/location/reference invalidation still removes or replaces the old card.
