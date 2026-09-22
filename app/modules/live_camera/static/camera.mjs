import { motion, signature, Stability, validateCatalog } from './recognition.mjs';

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
let timings = [];
let frameCount = 0;
let catalogRequest = 0;
const catalogs = {
  rijksmuseum: { file: 'catalog.json', label: 'Rijksmuseum' },
};

function status(message) { $('status').textContent = message; }
function clearResult() {
  displayed = null;
  $('result').hidden = true;
  $('object-name').textContent = '';
  $('object-description').textContent = '';
  $('object-meta').textContent = '';
  $('sources').replaceChildren();
}
function resetTracking() { stability?.reset(); previous = null; clearResult(); }
// After acquisition, tolerate hand jitter while fresh recognition verifies identity.
function motionLimit() { return stability?.confirmed ? 0.22 : 0.09; }

function stop(message = 'Camera stopped. Start again when you are ready.') {
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
  context.clearRect(0, 0, 224, 224);
  resetTracking();
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
  const side = Math.min(video.videoWidth, video.videoHeight) * 224 / 256;
  context.drawImage(video, (video.videoWidth - side) / 2, (video.videoHeight - side) / 2,
    side, side, 0, 0, 224, 224);
  return context.getImageData(0, 0, 224, 224).data;
}

function showCandidate(id) {
  if (displayed === id) return;
  const object = catalog.objects.find(item => item.id === id);
  if (!object) return;
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
}

async function nextFrame(run) {
  if (!running || run !== generation || pending) return;
  if (Date.now() >= Date.parse(catalog.expires_at)) {
    stop('Museum references expired. Refresh the reference download before continuing.');
    return;
  }
  const started = performance.now();
  const rgba = frame();
  if (!rgba) {
    resetTracking();
    timer = setTimeout(() => nextFrame(run), 250);
    return;
  }
  const current = signature(rgba);
  if (motion(previous, current) > motionLimit()) resetTracking();
  previous = current;
  pending = { id: started, signature: current, videoTime: video.currentTime };
  const controller = new AbortController();
  requestController = controller;
  timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
    if (run !== generation || !running) return;
    if (!blob) throw new Error('capture');
    const query = new URLSearchParams({ location_id: catalog.location.id, reference_version: catalog.reference_version });
    const response = await fetch(`./recognize?${query}`, {
      method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob, signal: controller.signal,
    });
    if (run !== generation || !running) return;
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (response.status === 409 || error.error === 'REFERENCES_EXPIRED') {
        stop('Museum references changed or expired. Reload the page to continue.');
        return;
      }
      throw new Error('network');
    }
    const data = await response.json();
    if (run !== generation || !running) return;
    if (data.version !== 'camera-server-v1' || data.location_id !== catalog.location.id ||
        data.reference_version !== catalog.reference_version || data.automatic_identification !== false ||
        !Array.isArray(data.candidates) || data.candidates.length > 3 ||
        !data.candidates.every(item => catalog.objects.some(obj => obj.id === item.id) && Number.isFinite(item.score) && item.score >= -1 && item.score <= 1)) throw new Error('response');
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
  const moved = !rgba || motion(pending.signature, signature(rgba)) > motionLimit();
  const stalled = video.currentTime <= pending.videoTime;
  pending = null;
  frameCount++;
  timings.push(duration);
  if (timings.length > 300) timings.shift();
  const sorted = [...timings].sort((a, b) => a - b);
  $('timing').textContent = `${Math.round(duration)} ms`;
  $('p95').textContent = `${Math.round(sorted[Math.ceil(sorted.length * 0.95) - 1])} ms (last ${sorted.length})`;
  $('frames').textContent = frameCount;
  lastResult = performance.now();
  const id = stability.observe(data.candidates, lastResult, moved || stalled || duration > 3000);
  if (id) {
    showCandidate(id);
    status(stability.holding ? 'Rechecking the previous possible match… hold steady.' : 'Possible match. Check the name against the object you are looking at.');
  } else {
    clearResult();
    const found = stability.count > 0;
    const checked = `Checked ${frameCount} ${frameCount === 1 ? 'frame' : 'frames'}. `;
    status(checked + (moved ? 'Move slowly, then pause on one object.' : stalled ? 'Waiting for a fresh camera frame…' : duration > 3000 ? 'Connection is too slow to show a fresh match. Hold steady or try a stronger connection.' : found ? 'Checking a possible match… hold steady.' : 'No supported match yet. Center a supported artwork and hold still.'));
  }
  // No frame queue: capture the newest frame only after the previous inference.
  timer = setTimeout(() => nextFrame(run), Math.max(250, 500 - duration));
}

async function start() {
  if (running || !catalog) return;
  running = true;
  const run = ++generation;
  timings = [];
  frameCount = 0;
  networkErrors = 0;
  lastResult = 0;
  stability = new Stability(catalog.policy);
  for (const id of ['timing', 'p95']) $(id).textContent = '—';
  $('frames').textContent = '0';
  $('start').hidden = true;
  $('stop').hidden = false;
  status('Allow camera access to start exploring.');
  try {
    const granted = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15, max: 24 } },
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
    status('Point at a supported object and pause briefly. Sending small camera crops for server recognition.');
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

$('start').addEventListener('click', start);
$('stop').addEventListener('click', () => stop());
document.addEventListener('visibilitychange', () => { if (document.hidden && running) stop('Camera paused when you left this page. Tap Start to resume.'); });
window.addEventListener('pagehide', () => stop());
for (const event of ['waiting', 'stalled', 'emptied']) video.addEventListener(event, resetTracking);
setInterval(() => {
  if (!running || !displayed) return;
  const rgba = frame();
  if (!rgba || motion(previous, signature(rgba)) > motionLimit() || !stability.current(performance.now())) resetTracking();
}, 200);

async function loadCatalog(key) {
  const request = ++catalogRequest;
  catalog = null;
  stop('Loading museum references…');
  $('objects').replaceChildren();
  $('reference-credits').replaceChildren();
  $('target-note').hidden = true;
  for (const id of ['snapshot', 'download-size', 'runtime', 'timing', 'p95']) $(id).textContent = '—';
  $('frames').textContent = '0';
  try {
    const response = await fetch(`./bundle/${catalogs[key].file}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Museum references are not ready. Prepare the camera bundle first.');
    const loaded = validateCatalog(await response.json());
    if (request !== catalogRequest) return;
    catalog = loaded;
    const option = [...$('location').options].find(item => item.value === key);
    option.textContent = `${loaded.location.name} · ${loaded.objects.length} supported ${loaded.objects.length === 1 ? 'object' : 'objects'}`;
    $('objects').replaceChildren(...loaded.objects.map(object => {
      const item = document.createElement('li');
      item.textContent = object.name;
      return item;
    }));
    $('target-note').hidden = loaded.test_mode !== 'single-target';
    $('target-note').textContent = 'Single-object test: the Justice Panel only. Other exhibits are not supported. A possible match still needs your confirmation.';
    for (const credit of loaded.credits || []) {
      const line = document.createElement('p');
      const link = document.createElement('a');
      try {
        const url = new URL(credit.source_page);
        if (url.protocol !== 'https:' || url.username || url.password) continue;
        link.href = url.href;
      } catch { continue; }
      link.textContent = `${credit.title} — ${credit.credit} · ${credit.license}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      line.append(link);
      if (credit.license_url) {
        try {
          const url = new URL(credit.license_url);
          if (url.protocol === 'https:' && !url.username && !url.password) {
            const license = document.createElement('a');
            license.href = url.href;
            license.textContent = ' · License';
            license.target = '_blank';
            license.rel = 'noopener noreferrer';
            line.append(license);
          }
        } catch { /* Source link still supplies the credit record. */ }
      }
      $('reference-credits').append(line);
    }
    $('snapshot').textContent = `${new Date(loaded.created_at).toLocaleDateString()}${loaded.atlas_freshness_verified === false ? ' · recorded-source demo; Atlas freshness unverified' : ''}`;
    $('download-size').textContent = 'None — model runs on the server';
    $('runtime').textContent = 'Server · internet required';
    $('start').disabled = false;
    status('Ready when you are. Camera access starts only when you tap the button.');
  } catch (error) {
    if (request === catalogRequest) status(error.message || 'Museum references could not load. Reload to try again.');
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
  const requested = new URLSearchParams(window.location.search).get('location');
  const key = Object.hasOwn(catalogs, requested) ? requested : 'rijksmuseum';
  $('location').replaceChildren(...Object.entries(catalogs).map(([value, entry]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = entry.label;
    return option;
  }));
  $('location').value = key;
  $('location').disabled = false;
  $('location').addEventListener('change', () => {
    const key = $('location').value;
    const url = new URL(window.location.href);
    url.searchParams.set('location', key);
    history.replaceState(null, '', url);
    loadCatalog(key);
  });
  await loadCatalog(key);
}
init();
