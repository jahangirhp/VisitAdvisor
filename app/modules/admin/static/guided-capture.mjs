import { analyzeTarget, ReferenceShortlist, createTargetTemplate, trackTarget } from './guided-quality.mjs';

const PLANS = {
  planar: [['front', 'Face the artwork straight on'], ['oblique-left', 'Move a little left, keeping the whole artwork visible'], ['oblique-right', 'Move a little right, keeping the whole artwork visible']],
  multiview: [['front', 'Start with a clear full view from the front'], ['left', 'Walk to the left side of the same object'], ['back', 'Walk behind the same object, if accessible'], ['right', 'Walk to the right side of the same object']],
  'front-only': [['front', 'Start with a clear full view from the front'], ['oblique-left', 'Move a little left, staying in front of the object'], ['oblique-right', 'Move a little right, staying in front of the object'], ['elevated', 'Try a slightly higher view, if safe and accessible']],
  scene: [['front', 'Start with the main approach or full room view'], ['oblique-left', 'Move to an accessible position on the left'], ['oblique-right', 'Move to an accessible position on the right'], ['approach', 'Move closer while keeping the important features visible']],
};
const MESSAGES = {
  hold_steady: 'Hold still. The object needs a sharper photo.',
  change_lighting: 'Try a position with less glare or more even light.',
  move_closer: 'Move closer, keeping the whole object inside the frame.',
  duplicate_view: 'This looks similar to a selected view. Change your position around the object.',
  keep_previous: 'A clearer photo is already selected for this view.',
  target_lost: 'Target uncertain. Tap the same object again when it is clearly visible.',
  wait_for_clear_view: 'Wait until the entire object is visible, then tap it again.',
};
const $ = id => document.getElementById(id);
const blobFrom = (canvas, type, quality) => new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Photo conversion failed.')), type, quality));

/** In-memory camera session. No frame, credential or model call leaves this origin. */
export class GuidedCapture {
  constructor({ onUse, onCancel = () => {}, onComplete = () => {}, capacity = () => 4 }) {
    this.onUse = onUse; this.onCancel = onCancel; this.onComplete = onComplete; this.capacity = capacity; this.epoch = 0; this.pending = new Map(); this.requestId = 0; this.urls = [];
    $('guidedStart').addEventListener('click', () => this.start());
    $('guidedClose').addEventListener('click', () => this.cancel());
    $('guidedDialog').addEventListener('cancel', event => { event.preventDefault(); this.cancel(); });
    $('guidedTarget').addEventListener('click', event => this.select(event));
    $('guidedBlocked').addEventListener('click', () => this.unlock('Wait for a clear view, then tap the same object again.'));
    $('guidedNext').addEventListener('click', () => this.next());
    $('guidedFinish').addEventListener('click', () => this.review());
    $('guidedUse').addEventListener('click', () => this.use());
    document.addEventListener('visibilitychange', () => { if (document.hidden && $('guidedDialog').open) this.cancel(); });
    window.addEventListener('pagehide', () => { if ($('guidedDialog').open) this.cancel(); else this.close(); });
  }
  open(profile = 'multiview') {
    this.close();
    $('guidedError')?.remove();
    $('guidedProfile').value = PLANS[profile] ? profile : 'multiview';
    $('guidedSetup').hidden = false; $('guidedLive').hidden = true; $('guidedReview').hidden = true;
    $('guidedStart').disabled = false; $('guidedStart').textContent = 'Start camera';
    $('guidedDialog').showModal();
  }
  say(text) { $('guidedStatus').textContent = text; }
  async rpc(type, data = {}, transfer = [], timeout = 30000) {
    const id = ++this.requestId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id); this.worker?.terminate(); this.worker = null;
        const error = new Error('Analysis took too long. Retry the camera or choose photos.'); error.name = 'TimeoutError'; reject(error);
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try { this.worker.postMessage({ type, id, ...data }, transfer); }
      catch { clearTimeout(timer); this.pending.delete(id); reject(new Error('Camera analysis is unavailable in this browser.')); }
    });
  }
  async start() {
    if (this.running || this.starting) return;
    const epoch = ++this.epoch; this.starting = true;
    this.profile = $('guidedProfile').value; this.plan = PLANS[this.profile].slice(0, Math.min(4, this.capacity()));
    if (!this.plan.length) return;
    this.shortlist = new ReferenceShortlist(this.plan.map(([id]) => id)); this.removed = new Set(); this.step = 0;
    $('guidedError')?.remove();
    $('guidedStart').disabled = true; $('guidedSetup').hidden = true; $('guidedLive').hidden = false; $('guidedReview').hidden = true;
    this.say('Starting camera and loading the segmentation model…'); this.update();
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.Worker || !window.OffscreenCanvas || !window.createImageBitmap || !globalThis.crypto?.randomUUID || !globalThis.crypto?.subtle) {
        throw new Error('Guided capture needs a supported browser and HTTPS. Open this page in Safari or Chrome, or use Take a photo.');
      }
      this.sessionId = crypto.randomUUID();
      this.worker = new Worker(new URL('./guided-segmenter.worker.mjs', import.meta.url), { type: 'module' });
      this.worker.onmessage = ({ data }) => {
        const pending = this.pending.get(data.id); if (!pending) return;
        clearTimeout(pending.timer); this.pending.delete(data.id);
        if (data.type === 'error') pending.reject(new Error('Segmentation unavailable. Retry, or use Take a photo.'));
        else pending.resolve(data);
      };
      this.worker.onerror = () => {
        for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('Segmentation unavailable. Retry, or use Take a photo.')); }
        this.pending.clear();
      };
      const modelPromise = this.rpc('init', {}, [], 90000);
      // Attach rejection handling immediately while a camera permission prompt is open.
      modelPromise.catch(() => {});
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1600 }, height: { ideal: 1200 } } });
      if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => { if (this.running) this.close(); });
      const video = $('guidedVideo'); video.srcObject = stream; await video.play();
      $('guidedStage').style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
      const ready = await modelPromise;
      if (epoch !== this.epoch) return;
      this.model = ready.model; this.running = true; this.starting = false;
      this.unlock('Tap the object in the preview. Keep its full outline visible.');
      this.lastFrameTime = -1; this.schedule(epoch);
    } catch (error) {
      if (epoch !== this.epoch) return;
      this.stopCamera(); this.starting = false;
      $('guidedSetup').hidden = false; $('guidedLive').hidden = true;
      $('guidedStart').disabled = false; $('guidedStart').textContent = 'Retry camera';
      // The setup remains available after denied permissions or model failures.
      this.showError(error);
    }
  }
  showError(error) {
    let notice = $('guidedError');
    if (!notice) { notice = document.createElement('p'); notice.id = 'guidedError'; notice.setAttribute('role', 'alert'); $('guidedSetup').append(notice); }
    notice.textContent = error.name === 'NotAllowedError' ? 'Camera access was denied. Allow camera access, or close this and choose a photo.' : error.message;
  }
  update() {
    $('guidedPrompt').textContent = this.plan?.length ? `${this.step + 1} of ${this.plan.length} · ${this.plan[this.step][1]}` : '';
    const entries = (this.shortlist?.entries() || []).filter(entry => entry.file);
    $('guidedNext').disabled = this.processing || !entries.some(entry => entry.stepId === this.plan?.[this.step]?.[0]) || this.step >= this.plan.length - 1;
    $('guidedFinish').disabled = entries.length === 0;
  }
  select(event) {
    if (!this.running || this.processing) return;
    const video = $('guidedVideo'), rect = $('guidedStage').getBoundingClientRect();
    const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
    const width = video.videoWidth * scale, height = video.videoHeight * scale;
    const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
    const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    this.targetVersion = (this.targetVersion || 0) + 1; this.point = { x, y }; this.template = null; this.box = null; this.seed = null; this.referenceArea = null;
    this.say('Finding the selected object…');
  }
  unlock(message) {
    this.targetVersion = (this.targetVersion || 0) + 1; this.point = null; this.template = null; this.box = null; this.seed = null; this.referenceArea = null;
    const canvas = $('guidedOverlay'); canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    $('guidedAnalyzed').hidden = true;
    this.say(message);
  }
  schedule(epoch) {
    if (!this.running || epoch !== this.epoch) return;
    this.timer = setTimeout(async () => {
      if (!this.running || epoch !== this.epoch) return;
      const video = $('guidedVideo');
      if (this.point && video.readyState >= 2 && video.currentTime !== this.lastFrameTime) {
        this.lastFrameTime = video.currentTime;
        await this.analyze(epoch);
      }
      this.schedule(epoch);
    }, 650);
  }
  async analyze(epoch) {
    if (this.processing) return;
    this.processing = true; this.update();
    const version = this.targetVersion, stepId = this.plan[this.step][0];
    const fresh = () => epoch === this.epoch && version === this.targetVersion && this.running;
    try {
      const video = $('guidedVideo');
      const source = document.createElement('canvas'), scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
      source.width = Math.round(video.videoWidth * scale); source.height = Math.round(video.videoHeight * scale);
      source.getContext('2d').drawImage(video, 0, 0, source.width, source.height);
      const sample = document.createElement('canvas'), ratio = Math.min(1, 640 / Math.max(source.width, source.height));
      sample.width = Math.round(source.width * ratio); sample.height = Math.round(source.height * ratio);
      const context = sample.getContext('2d', { willReadFrequently: true }); context.drawImage(source, 0, 0, sample.width, sample.height);
      const pixels = context.getImageData(0, 0, sample.width, sample.height);
      let point = this.point;
      if (this.template) {
        const tracked = trackTarget(pixels, this.template, this.box);
        if (!tracked.found) { this.unlock(MESSAGES.target_lost); return; }
        point = { x: tracked.box[0] + this.seed.x * (tracked.box[2] - tracked.box[0]), y: tracked.box[1] + this.seed.y * (tracked.box[3] - tracked.box[1]) };
      }
      const bitmap = await createImageBitmap(sample);
      if (!fresh()) { bitmap.close(); return; }
      this.say('Checking the selected target. Keep still for a moment…');
      const result = await this.rpc('segment', { image: bitmap, point }, [bitmap]);
      if (!fresh()) return;
      if (result.width !== sample.width || result.height !== sample.height || !result.bbox || result.mask.byteLength !== sample.width * sample.height) { this.unlock(MESSAGES.target_lost); return; }
      const mask = new Uint8Array(result.mask), box = result.bbox;
      if (box[0] <= .005 || box[1] <= .005 || box[2] >= .995 || box[3] >= .995) { this.unlock('Keep the entire object inside the frame, then tap it again.'); return; }
      const quality = analyzeTarget(pixels, box, mask);
      // A large silhouette change is uncertain, not evidence of an unobstructed object.
      if (this.referenceArea && (quality.targetFraction < this.referenceArea * .65 || quality.targetFraction > this.referenceArea * 1.55)) { this.unlock(MESSAGES.wait_for_clear_view); return; }
      if (!this.template) {
        this.template = createTargetTemplate(pixels, box, mask); this.referenceArea = quality.targetFraction;
        this.seed = { x: Math.max(0, Math.min(1, (point.x - box[0]) / (box[2] - box[0]))), y: Math.max(0, Math.min(1, (point.y - box[1]) / (box[3] - box[1]))) };
      }
      this.box = box; this.drawMask(mask, sample.width, sample.height, sample);
      const candidate = { stepId, quality };
      // Blob creation is bounded to candidates that improve the shortlist.
      const preview = new ReferenceShortlist(this.plan.map(([id]) => id));
      for (const entry of this.shortlist.entries()) preview.consider(entry, { targetConfirmed: true, occluded: false });
      const trial = preview.consider(candidate, { targetConfirmed: true, occluded: false });
      if (!trial.accepted) { this.say(MESSAGES[trial.reason] || 'Check the target outline and hold still.'); this.update(); return; }
      const file = new File([await blobFrom(source, 'image/jpeg', .88)], 'guided-reference.jpg', { type: 'image/jpeg' });
      if (!fresh()) return;
      if (file.size > 5 * 1024 * 1024) throw new Error('Photo too large. Try a lower camera resolution.');
      const maskCanvas = document.createElement('canvas'); maskCanvas.width = sample.width; maskCanvas.height = sample.height;
      const maskContext = maskCanvas.getContext('2d'), maskPixels = maskContext.createImageData(sample.width, sample.height);
      for (let i = 0; i < mask.length; i++) { const j = i * 4; maskPixels.data[j] = maskPixels.data[j + 1] = maskPixels.data[j + 2] = mask[i]; maskPixels.data[j + 3] = 255; }
      maskContext.putImageData(maskPixels, 0, 0);
      const fullMask = document.createElement('canvas'); fullMask.width = source.width; fullMask.height = source.height;
      const fullContext = fullMask.getContext('2d'); fullContext.imageSmoothingEnabled = false; fullContext.drawImage(maskCanvas, 0, 0, fullMask.width, fullMask.height);
      const foregroundMask = await blobFrom(fullMask, 'image/png');
      const isolated = document.createElement('canvas'); isolated.width = sample.width; isolated.height = sample.height;
      const isolatedContext = isolated.getContext('2d'); isolatedContext.drawImage(sample, 0, 0);
      const isolatedPixels = isolatedContext.getImageData(0, 0, sample.width, sample.height);
      for (let i = 0; i < mask.length; i++) if (!mask[i]) isolatedPixels.data[i * 4 + 3] = 0;
      isolatedContext.putImageData(isolatedPixels, 0, 0);
      const isolatedBlob = await blobFrom(isolated, 'image/png');
      if (!fresh()) return;
      // The selector's retained object is updated only after all asynchronous work succeeds.
      this.shortlist.consider({ ...candidate, file, foregroundMask: foregroundMask.size <= 512 * 1024 ? foregroundMask : null, isolatedBlob, captureMetadata: {
        version: 'guided-capture-v1', session_id: this.sessionId, frame_id: crypto.randomUUID(), profile: this.profile,
        requested_view: stepId, viewpoint_verified: false, target_box: box,
        quality: { sharpness: quality.sharpness, clipped_fraction: quality.clippedFraction, target_fraction: quality.targetFraction },
        segmentation: { model: this.model.name, model_hash: this.model.sha256 },
      } }, { targetConfirmed: true, occluded: false });
      this.say('Clear photo selected. Check the outline, then move to the next view or review photos.'); this.update();
    } catch (error) {
      if (fresh()) {
        this.unlock(error.message || 'Analysis failed. Tap the object again.');
        if (error.name === 'TimeoutError') {
          this.stopCamera(); $('guidedSetup').hidden = false; $('guidedLive').hidden = true;
          $('guidedStart').disabled = false; $('guidedStart').textContent = 'Retry camera'; this.showError(error);
        }
      }
    }
    finally { this.processing = false; if (this.running) this.update(); }
  }
  drawMask(mask, width, height, source) {
    const canvas = $('guidedOverlay'); canvas.width = width; canvas.height = height;
    const small = document.createElement('canvas'); small.width = width; small.height = height;
    const context = small.getContext('2d'), pixels = context.createImageData(width, height);
    for (let i = 0; i < mask.length; i++) if (mask[i]) { pixels.data[i * 4] = 50; pixels.data[i * 4 + 1] = 220; pixels.data[i * 4 + 2] = 160; pixels.data[i * 4 + 3] = 65; }
    context.putImageData(pixels, 0, 0);
    const output = canvas.getContext('2d'); output.drawImage(source, 0, 0); output.drawImage(small, 0, 0);
    $('guidedAnalyzed').hidden = false;
  }
  next() {
    if ($('guidedNext').disabled || this.processing) return;
    this.step++; this.unlock('Move to the suggested position, then tap the same object to confirm it.'); this.update();
  }
  review() {
    this.stopCamera(); this.renderReview();
  }
  renderReview() {
    this.clearUrls(); $('guidedLive').hidden = true; $('guidedReview').hidden = false;
    const entries = this.shortlist.entries().filter(entry => entry.file); $('guidedPhotos').replaceChildren();
    for (const entry of entries) {
      const card = document.createElement('div'); card.className = 'guidedPhoto';
      for (const [blob, alt] of [[entry.file, 'Original selected photo'], [entry.isolatedBlob, 'Segmented target preview']]) {
        const image = document.createElement('img'); const url = URL.createObjectURL(blob); this.urls.push(url); image.src = url; image.alt = alt; card.append(image);
      }
      const comparison = document.createElement('p'); comparison.className = 'guidedComparison'; comparison.textContent = 'Original photo / isolated target';
      const text = document.createElement('p'); text.textContent = `Suggested view: ${entry.stepId}. Confirm the real angle after choosing photos.`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove this photo';
      remove.addEventListener('click', () => { this.removed.add(entry.stepId); card.remove(); $('guidedUse').disabled = !this.shortlist.entries().some(item => item.file && !this.removed.has(item.stepId)); });
      card.append(comparison, text, remove); $('guidedPhotos').append(card);
    }
    $('guidedUse').disabled = !entries.length;
    $('guidedGaps').textContent = `${entries.length} of ${this.plan.length} requested views captured. Check that the whole object is visible in each photo. You can add missing views later.`;
  }
  async use() {
    const entries = this.shortlist.entries().filter(entry => entry.file && !this.removed.has(entry.stepId));
    if (!entries.length) return;
    $('guidedUse').disabled = true;
    const epoch = this.epoch;
    const isCurrent = () => epoch === this.epoch && $('guidedDialog').open;
    try { await this.onUse(entries, isCurrent); if (isCurrent()) { this.close(); this.onComplete(); } }
    catch { if (isCurrent()) { $('guidedGaps').textContent = 'Could not add these photos. Please retry.'; $('guidedUse').disabled = false; } }
  }
  stopCamera() {
    this.running = false; this.starting = false; this.epoch++; clearTimeout(this.timer);
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    $('guidedVideo').pause(); $('guidedVideo').srcObject = null;
    this.worker?.terminate(); this.worker = null;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('Capture stopped.')); } this.pending.clear();
  }
  clearUrls() { this.urls.forEach(url => URL.revokeObjectURL(url)); this.urls = []; }
  cancel() { this.close(); this.onCancel(); }
  close() { this.stopCamera(); this.shortlist?.clear(); this.clearUrls(); $('guidedPhotos').replaceChildren(); $('guidedDialog').close(); }
}
