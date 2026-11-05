import { motion, signature, Stability, validateCatalog, validBox } from './recognition.mjs';
import { drawFrame, encodeFrame } from './capture.mjs';
import { clearExperience, showExperience } from './experience.mjs';

const $ = id => document.getElementById(id);
const video = $('camera');
const canvas = document.createElement('canvas');
canvas.width = canvas.height = 224;
const context = canvas.getContext('2d', { willReadFrequently: true });
let catalog;
let requestController;
let networkErrors = 0;
let stream;
let generation = 0;
let running = false;
let timer;
let timeout;
let pending;
let stability;
let previous;
let lastResult = 0;
let displayed;
let catalogRequest = 0;
let venues = [];

function status(message) { $('status').textContent = message; }
function clearResult() {
  clearExperience();
  displayed = null;
  delete $('result').dataset.state;
  $('result').hidden = true;
  $('object-name').textContent = '';
  $('object-description').textContent = '';
  $('object-meta').textContent = '';
  $('sources').replaceChildren();
}
function resultState(state) {
  if ($('result').dataset.state === state) return;
  $('result').dataset.state = state;
  $('result-kind').textContent = state === 'live' ? 'POSSIBLE MATCH · PLEASE VERIFY' : 'LAST SEEN · CAMERA RECHECKING';
}
function lastSeen() { if (displayed) resultState('last-seen'); }
function resetTracking() { stability?.reset(); previous = null; lastSeen(); }
// After acquisition, tolerate hand jitter while fresh recognition verifies identity.
function motionLimit() { return stability?.confirmed ? 0.22 : 0.09; }

function stop(message = 'Camera stopped.') {
  running = false;
  generation++;
  clearTimeout(timer);
  clearTimeout(timeout);
  requestController?.abort();
  requestController = null;
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
  video.srcObject = null;
  pending = null;
  context.clearRect(0, 0, canvas.width, canvas.height);
  resetTracking();
  clearResult();
  $('placeholder').hidden = false;
  $('live-label').hidden = true;
  $('stop').hidden = true;
  $('start').hidden = false;
  $('start').disabled = !catalog;
  status(message);
}

function frame() {
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return null;
  // Match the model's shortest-edge 256 resize then 224 center crop geometry.
  // Browser interpolation differs from Pillow bicubic: this is a new, uncalibrated
  // preprocessing identity and must be measured on the actual phone.
  return drawFrame(context, video, video.videoWidth, video.videoHeight, canvas.width);
}

function showCandidate(id) {
  const object = catalog.objects.find(item => item.id === id);
  if (!object) return;
  resultState('live');
  if (displayed === id) return;
  displayed = id;
  $('object-name').textContent = object.name;
  $('object-meta').textContent = [object.creator, object.year, object.type].filter(Boolean).join(' · ');
  $('object-description').textContent = object.description || 'No description is available in this reference snapshot.';
  $('sources').replaceChildren();
  for (const source of object.sources) {
    try {
      const url = new URL(source.url);
      if (url.protocol !== 'https:' || url.username || url.password) continue;
      const link = document.createElement('a');
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `Source: ${source.title}`;
      $('sources').append(link);
    } catch { /* A source without a public URL remains in the reference bundle. */ }
  }
  $('result').hidden = false;
  showExperience(object, catalog);
}

async function nextFrame(run) {
  if (!running || run !== generation || pending) return;
  if (Date.now() >= Date.parse(catalog.expires_at)) {
    await loadCatalog($('location').value);
    return;
  }
  const started = performance.now();
  const rgba = frame();
  if (!rgba) {
    resetTracking();
    timer = setTimeout(() => nextFrame(run), 250);
    return;
  }
  const current = signature(rgba, canvas.width);
  if (motion(previous, current) > motionLimit()) resetTracking();
  previous = current;
  pending = { id: started, signature: current, videoTime: video.currentTime };
  const controller = new AbortController();
  requestController = controller;
  timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const blob = await encodeFrame(canvas);
    if (run !== generation || !running) return;
    if (!blob) throw new Error('capture');
    const query = new URLSearchParams({ location_id: catalog.location.id, reference_version: catalog.reference_version });
    const response = await fetch(`./recognize?${query}`, {
      method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob, signal: controller.signal,
    });
    if (run !== generation || !running) return;
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (response.status === 409 || ['REFERENCES_EXPIRED', 'REFERENCES_UNAVAILABLE'].includes(error.error)) {
        await loadCatalog($('location').value);
        return;
      }
      throw new Error('network');
    }
    const data = await response.json();
    if (run !== generation || !running) return;
    if (data.version !== 'camera-server-v1' || data.location_id !== catalog.location.id ||
        data.reference_version !== catalog.reference_version || data.automatic_identification !== false ||
        !Array.isArray(data.candidates) || data.candidates.length > 3 ||
        !data.candidates.every(item => catalog.objects.some(obj => obj.id === item.id) && Number.isFinite(item.score) && item.score >= -1 && item.score <= 1 &&
          (item.bbox === undefined || validBox(item.bbox)) && (item.profile === undefined || ['planar', 'multiview'].includes(item.profile)))) throw new Error('response');
    networkErrors = 0;
    handleResult({ ...data, id: started }, run);
  } catch {
    if (run !== generation || !running) return;
    clearTimeout(timeout);
    pending = null;
    resetTracking();
    networkErrors++;
    if (networkErrors >= 3) { stop('Recognition server could not respond. Check your connection, then tap Start to retry.'); return; }
    status('Connection interrupted or server busy. Retrying…');
    timer = setTimeout(() => nextFrame(run), 1500);
  } finally {
    if (requestController === controller) requestController = null;
  }
}

function handleResult(data, run) {
  if (!pending || data.id !== pending.id || run !== generation) return;
  clearTimeout(timeout);
  const duration = performance.now() - pending.id;
  const rgba = frame();
  const moved = !rgba || motion(pending.signature, signature(rgba, canvas.width)) > motionLimit();
  const stalled = video.currentTime <= pending.videoTime;
  pending = null;
  lastResult = performance.now();
  const candidates = data.suggestion_allowed === false ? [] : data.candidates;
  const id = stability.observe(candidates, lastResult, moved || stalled || duration > 3000);
  if (id) {
    if (stability.holding) lastSeen();
    else showCandidate(id);
    status(stability.holding ? 'Rechecking the previous possible match… hold steady.' : 'Possible match. Check the name against the object you are looking at.');
  } else {
    lastSeen();
    const found = stability.count > 0;
    const guidance = data.warnings?.includes('CAMERA_CALIBRATION_REQUIRED') ? 'Camera matching is not ready yet.' :
      data.warnings?.includes('REFERENCE_VIEW_COVERAGE_INCOMPLETE') ? 'This view is not supported reliably yet. Try another supported object.' :
      data.decision === 'deferred' ? 'Recognition is taking longer. Hold steady while we check the next frame.' :
      data.decision === 'unreadable' ? 'Not enough detail. Move closer or improve the lighting.' : null;
    status((moved ? 'Move slowly, then pause on one object.' : stalled ? 'Waiting for a fresh camera frame…' : duration > 3000 ? 'Connection is too slow to show a fresh match. Hold steady or try a stronger connection.' : guidance || (found ? 'Checking a possible match… hold steady.' : 'No supported match yet. Center a supported object and hold still.')));
  }
  // No frame queue: capture the newest frame only after the previous inference.
  timer = setTimeout(() => nextFrame(run), Math.max(250, 500 - duration));
}

async function start() {
  if (running || !catalog) return;
  running = true;
  const run = ++generation;
  networkErrors = 0;
  lastResult = 0;
  stability = new Stability(catalog.policy);
  resetTracking();
  $('start').hidden = true;
  $('stop').hidden = false;
  status('Allow camera access.');
  try {
    const granted = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 15, max: 24 } },
    });
    if (run !== generation || !running) {
      granted.getTracks().forEach(track => track.stop());
      return;
    }
    stream = granted;
    granted.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
      if (run === generation) stop('Camera access ended. Start again to reconnect.');
    }));
    video.srcObject = granted;
    await video.play();
    if (run !== generation) return;
    $('placeholder').hidden = true;
    $('live-label').hidden = false;
    status('Scanning… Hold steady on an object.');
    nextFrame(run);
  } catch (error) {
    if (run !== generation) return;
    const messages = {
      NotAllowedError: 'Camera permission was not granted. Allow camera access in Safari, then try again.',
      NotFoundError: 'No camera was found on this device.',
      NotReadableError: 'The camera is busy. Close other camera apps and try again.',
    };
    stop(messages[error.name] || 'The camera could not start. Check Safari camera permissions and try again.');
  }
}

document.addEventListener('click', event => {
  if (!$('menu').contains(event.target)) $('menu').open = false;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('menu').open) {
    $('menu').open = false;
    $('menu').querySelector('summary').focus();
  }
});

const viewer = document.querySelector('.viewer');
function togglePreview() {
  const expanded = viewer.getAttribute('aria-expanded') !== 'true';
  viewer.setAttribute('aria-expanded', String(expanded));
  viewer.setAttribute('aria-label', expanded ? 'Shrink camera preview' : 'Expand camera preview');
  document.querySelector('.capture-bar').classList.toggle('expanded', expanded);
}
viewer.addEventListener('click', togglePreview);
viewer.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    togglePreview();
  }
});

$('start').addEventListener('click', start);
$('stop').addEventListener('click', () => stop());
document.addEventListener('visibilitychange', () => { if (document.hidden && running) stop('Camera paused when you left this page. Tap Start to resume.'); });
window.addEventListener('pagehide', () => stop());
for (const event of ['waiting', 'stalled', 'emptied']) video.addEventListener(event, resetTracking);
setInterval(() => {
  if (!running || !displayed) return;
  if (Date.now() >= Date.parse(catalog.expires_at)) {
    loadCatalog($('location').value);
    return;
  }
  const rgba = frame();
  if (!rgba || motion(previous, signature(rgba, canvas.width)) > motionLimit()) resetTracking();
  else if (!stability.current(performance.now())) {
    // Retaining a reading card must not reset an acquisition in progress.
    if (stability.confirmed) resetTracking();
    else lastSeen();
  }
}, 200);

async function loadCatalog(key) {
  const request = ++catalogRequest;
  catalog = null;
  stop('Loading venue references…');
  $('target-note').hidden = true;
  try {
    const response = await fetch(`./bundle/catalog.json?${new URLSearchParams({ location_id: key })}`, { cache: 'no-store' });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const messages = {
        NO_PREPARED_REFERENCES: 'This venue has no prepared photo references yet. Prepare reference photos in admin, then reselect this venue.',
        CAMERA_OBJECT_LIMIT: 'This venue exceeds the current camera limit of 12 prepared objects.',
      };
      throw new Error(messages[data.error] || 'Venue references are unavailable. Try reloading shortly.');
    }
    const loaded = validateCatalog(await response.json());
    if (request !== catalogRequest) return;
    if (loaded.location.id !== key) throw new Error("References belong to a different venue.");
    catalog = loaded;
    canvas.width = canvas.height = loaded.policy.frame_size || 224;
    const option = [...$('location').options].find(item => item.value === key);
    option.textContent = `${loaded.location.name} · ${loaded.objects.length} supported ${loaded.objects.length === 1 ? 'object' : 'objects'}`;
    $('target-note').hidden = loaded.test_mode !== 'single-target';
    $('target-note').textContent = 'Single-object test: the Justice Panel only. Other exhibits are not supported. A possible match still needs your confirmation.';
    $('start').disabled = false;
    status('Ready to scan.');
  } catch (error) {
    if (request === catalogRequest) status(error.message || 'Venue references could not load. Reload to try again.');
  }
}

async function init() {
  // Retire the previous on-device model cache; frames are never stored here.
  try { indexedDB.deleteDatabase('visitadvisor-model-v1'); } catch { /* Optional cleanup. */ }
  try { caches.delete('visitadvisor-model-v1').catch(() => {}); } catch { /* Optional cleanup. */ }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    status('Open this page using a trusted HTTPS address in Safari to use the live camera.');
    $('phone-setup').hidden = false;
    return;
  }
  if (!window.fetch || !window.AbortController || !context) {
    status('This browser cannot send camera frames. Try an updated browser.');
    return;
  }
  try {
    for (let offset = 0; ; offset += 100) {
      const response = await fetch(`./venues?offset=${offset}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('Venues could not load. Reload to try again.');
      const batch = await response.json();
      if (!Array.isArray(batch) || !batch.every(venue => /^[a-f0-9]{24}$/.test(venue.id) && typeof venue.name === 'string')) throw new Error('Invalid venue list.');
      venues.push(...batch);
      if (batch.length < 100) break;
    }
  } catch (error) { status(error.message); return; }
  if (!venues.length) {
    $('location').replaceChildren(new Option('No venues yet', ''));
    status('Add a venue in admin to get started.');
    return;
  }
  const requested = new URLSearchParams(window.location.search).get('location');
  const key = venues.find(venue => venue.id === requested || venue.slug === requested)?.id || venues[0].id;
  $('location').replaceChildren(...venues.map(venue => new Option(venue.name, venue.id)));
  $('location').value = key;
  $('location').disabled = false;
  $('location').addEventListener('change', () => {
    const key = $('location').value;
    const url = new URL(window.location.href);
    url.searchParams.set('location', key);
    history.replaceState(null, '', url);
    $('menu').open = false;
    loadCatalog(key);
  });
  await loadCatalog(key);
}
init();
