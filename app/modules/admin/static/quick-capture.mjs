import { analyzeTarget } from './guided-quality.mjs?v=mobile-20261108-1';

const $ = id => document.getElementById(id);
const sha256 = async blob => [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))].map(byte => byte.toString(16).padStart(2, '0')).join('');
const blobFrom = (canvas, type, quality) => new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Could not prepare this photo. Try your phone camera.')), type, quality));
const validBox = box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 && box[2] <= 1 && box[3] <= 1 && box[0] < box[2] && box[1] < box[3];
const contextOf = canvas => {
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('Photo capture is unavailable. Use your phone camera or choose photos.');
  return context;
};

const waitForRetry = signal => new Promise((resolve, reject) => {
  const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Analysis stopped', 'AbortError')); };
  const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1000);
  if (signal.aborted) abort(); else signal.addEventListener('abort', abort, { once: true });
});

async function decode(blob) {
  const url = URL.createObjectURL(blob), image = new Image();
  try {
    await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('Could not read this photo.')); image.src = url; });
    const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    contextOf(canvas).drawImage(image, 0, 0); return canvas;
  } finally { URL.revokeObjectURL(url); }
}

/** A shutter never waits for detection. Only captured stills reach our server. */
export class GuidedCapture {
  constructor({ analyze, onUse, onUpdate = () => {}, onCancel = () => {}, onComplete = () => {}, capacity = () => 4 }) {
    Object.assign(this, { analyze, onUse, onUpdate, onCancel, onComplete, capacity });
    this.epoch = 0; this.cameraVersion = 0; this.entries = []; this.urls = [];
    $('guidedStart').addEventListener('click', () => this.start());
    $('guidedClose').addEventListener('click', () => this.cancel());
    $('guidedDialog').addEventListener('cancel', event => { if (event.target === $('guidedDialog')) { event.preventDefault(); this.cancel(); } });
    $('quickTarget').addEventListener('click', event => this.aim(event));
    $('quickShutter').addEventListener('click', () => this.capture());
    $('quickDone').addEventListener('click', () => this.finish());
    $('quickBack').addEventListener('click', () => this.hideInspector());
    $('quickRemove').addEventListener('click', () => this.removeInspected());
    $('quickFullPhoto').addEventListener('click', () => this.keepFullPhoto());
    $('quickApplyBox').addEventListener('click', () => this.applyBox());
    $('quickSnapshot').addEventListener('pointerdown', event => this.beginBox(event));
    $('quickSnapshot').addEventListener('pointermove', event => this.moveBox(event));
    $('quickSnapshot').addEventListener('pointerup', event => this.endBox(event));
    $('quickSnapshot').addEventListener('pointercancel', () => { this.drag = null; this.drawInspector(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.stopCamera(); });
    window.addEventListener('pagehide', () => this.close());
  }
  open(profile = 'multiview') {
    this.close();
    this.profile = ['planar', 'multiview', 'front-only', 'scene'].includes(profile) ? profile : 'multiview';
    this.limit = Math.max(0, Math.min(4, this.capacity())); this.point = [.5, .5]; this.delivered = false; this.analysisStopped = false;
    $('guidedError')?.remove(); $('guidedSetup').hidden = true; $('quickCapture').hidden = false; $('quickInspector').hidden = true;
    $('guidedFallback').hidden = false; $('guidedStart').disabled = false; $('guidedStart').textContent = 'Retry camera';
    $('quickCamera').hidden = false; $('quickAim').style.left = '50%'; $('quickAim').style.top = '50%';
    this.say('Keep the whole object in view. Take photos from different angles.'); this.render();
    $('guidedDialog').showModal(); void this.start();
  }
  say(message) { $('quickStatus').textContent = message; }
  async start() {
    if (this.running || this.starting || this.delivered) return;
    const epoch = this.epoch, cameraVersion = ++this.cameraVersion; const fresh = () => epoch === this.epoch && cameraVersion === this.cameraVersion; this.starting = true; $('guidedStart').disabled = true; this.say('Starting camera…');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !globalThis.crypto?.randomUUID || !globalThis.crypto?.subtle) throw new Error('Use your phone camera or choose photos below.');
      this.sessionId ||= crypto.randomUUID();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1600 }, height: { ideal: 1200 } } });
      if (!fresh()) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream; const video = $('quickVideo'); video.srcObject = stream;
      let timer;
      try { await Promise.race([video.play(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('The camera did not start. Try your phone camera below.')), 12000); })]); }
      finally { clearTimeout(timer); }
      if (!fresh()) return;
      if (document.hidden) { this.stopCamera(); return; }
      for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => { if (this.running) { this.stopCamera(); this.showCameraError('Camera stopped. Retry, or use your phone camera below.'); } });
      this.running = true; this.starting = false; $('guidedSetup').hidden = true;
      $('quickStage').style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
      this.say('Keep the whole object in view. Take photos from different angles.'); this.render();
    } catch (error) {
      if (!fresh()) return;
      this.stopCamera(); this.showCameraError(error.name === 'NotAllowedError' ? 'Camera access was denied. Use your phone camera or choose photos below.' : error.message);
    }
  }
  showCameraError(message) {
    $('guidedSetup').hidden = false; $('guidedStart').disabled = false;
    let notice = $('guidedError');
    if (!notice) { notice = document.createElement('p'); notice.id = 'guidedError'; notice.setAttribute('role', 'status'); $('guidedSetup').prepend(notice); }
    notice.textContent = message; this.say(this.entries.length ? 'Your selected photos are kept.' : ''); this.render();
  }
  normalizedPoint(event, element, width, height) {
    const rect = element.getBoundingClientRect(), scale = Math.min(rect.width / width, rect.height / height);
    const renderedWidth = width * scale, renderedHeight = height * scale;
    const x = (event.clientX - rect.left - (rect.width - renderedWidth) / 2) / renderedWidth;
    const y = (event.clientY - rect.top - (rect.height - renderedHeight) / 2) / renderedHeight;
    return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 1 && y >= 0 && y <= 1 ? [x, y] : null;
  }
  aim(event) {
    if (!this.running) return;
    const video = $('quickVideo'), point = event.detail === 0 ? [.5, .5] : this.normalizedPoint(event, $('quickStage'), video.videoWidth, video.videoHeight);
    if (!point) return;
    const rect = $('quickStage').getBoundingClientRect(); this.point = point;
    $('quickAim').style.left = `${event.detail === 0 ? 50 : (event.clientX - rect.left) / rect.width * 100}%`;
    $('quickAim').style.top = `${event.detail === 0 ? 50 : (event.clientY - rect.top) / rect.height * 100}%`;
  }
  async capture() {
    if (!this.running || this.encoding || this.finishing || this.entries.length >= this.limit) return;
    const video = $('quickVideo'); if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    const epoch = this.epoch, source = document.createElement('canvas'), scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
    source.width = Math.max(1, Math.round(video.videoWidth * scale)); source.height = Math.max(1, Math.round(video.videoHeight * scale));
    this.encoding = true; this.render();
    try {
      contextOf(source).drawImage(video, 0, 0, source.width, source.height);
      const point = [...this.point]; let blob;
      for (const quality of [.88, .78, .66, .5]) { blob = await blobFrom(source, 'image/jpeg', quality); if (blob.size <= 1024 * 1024) break; }
      if (blob.size > 1024 * 1024) throw new Error('This photo is too large. Try your phone camera below.');
      const file = new File([blob], 'reference.jpg', { type: 'image/jpeg' });
      const [sha, canvas] = await Promise.all([sha256(file), decode(file)]);
      if (epoch !== this.epoch) return;
      const quality = analyzeTarget(contextOf(canvas).getImageData(0, 0, canvas.width, canvas.height), [0, 0, 1, 1]);
      const entry = { file, canvas, point, sha, id: crypto.randomUUID(), version: 0, state: 'queued', url: URL.createObjectURL(file),
        captureMetadata: { version: 'guided-capture-v3', session_id: this.sessionId, frame_id: crypto.randomUUID(), profile: this.profile,
          requested_view: null, viewpoint_verified: false, target_box: [0, 0, 1, 1], quality_region: 'full-frame', detection_status: 'pending',
          quality: { sharpness: quality.sharpness, clipped_fraction: quality.clippedFraction, target_fraction: quality.targetFraction },
          selection: { method: 'full-frame', reviewed: false, source_sha256: sha } } };
      entry.id = entry.captureMetadata.frame_id;
      if (['planar', 'scene'].includes(this.profile)) { entry.state = 'skipped'; entry.captureMetadata.detection_status = 'skipped'; }
      this.entries.push(entry);
      this.point = [.5, .5]; $('quickAim').style.left = '50%'; $('quickAim').style.top = '50%';
      this.say(this.entries.length >= this.limit ? 'Photos added. Tap Save when ready.' : 'Photo added. Move to another angle and take the next photo.');
      void this.processQueue();
    } catch (error) { if (epoch === this.epoch) this.say(error.message); }
    finally { if (epoch === this.epoch) { this.encoding = false; this.render(); } }
  }
  exportEntry(entry) {
    return { stepId: entry.id, file: entry.file, foregroundMask: entry.foregroundMask || null,
      isolatedBlob: entry.isolatedBlob || entry.file, captureMetadata: structuredClone(entry.captureMetadata) };
  }
  selectedPhotos() { return this.entries.map(entry => this.exportEntry(entry)); }
  notify(entry) { if (this.delivered && !this.analysisStopped) this.onUpdate(this.exportEntry(entry)); this.render(); }
  async processQueue() {
    if (this.processing || this.analysisStopped) return;
    const epoch = this.epoch; this.processing = true;
    try {
      let entry;
      while (epoch === this.epoch && !this.analysisStopped && (entry = this.entries.find(item => item.state === 'queued'))) {
        await this.analyzeEntry(entry, epoch);
      }
    } finally { if (epoch === this.epoch) this.processing = false; }
  }
  async analyzeEntry(entry, epoch) {
    const version = entry.version, fresh = () => epoch === this.epoch && !this.analysisStopped && entry.version === version && this.entries.includes(entry);
    const controller = this.controller = new AbortController(); this.analyzingId = entry.id; entry.state = 'analyzing'; this.render();
    let timer;
    try {
      const prompt = { session_id: this.sessionId, frame_id: entry.id, profile: this.profile, point_x: entry.point[0], point_y: entry.point[1] };
      const timeout = new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Analysis took too long.')); }, 80000); });
      const request = async () => {
        const retryUntil = performance.now() + 20000;
        while (true) {
          try { return await this.analyze(entry.file, prompt, controller.signal); }
          catch (error) {
            if (error.code !== 'CAPTURE_ANALYSIS_BUSY' || !fresh() || controller.signal.aborted || performance.now() >= retryUntil) throw error;
            await waitForRetry(controller.signal);
            if (!fresh()) throw new DOMException('Analysis stopped', 'AbortError');
            if (performance.now() >= retryUntil) throw error;
          }
        }
      };
      const response = await Promise.race([request(), timeout]);
      if (!fresh()) return;
      if (response.version !== 'server-capture-v1' || response.session_id !== this.sessionId || response.frame_id !== entry.id || response.source_sha256 !== entry.sha || response.width !== entry.canvas.width || response.height !== entry.canvas.height || typeof response.model?.name !== 'string' || !response.model.name.length || response.model.name.length > 200 || typeof response.model?.revision !== 'string' || !response.model.revision.length || response.model.revision.length > 100 || !/^[a-f0-9]{64}$/.test(response.model?.sha256 || '') || !Array.isArray(response.candidates) || response.candidates.length > 3) throw new Error('Analysis did not match this photo.');
      const analysis = { model: response.model.name, model_hash: response.model.sha256, revision: response.model.revision, source_sha256: entry.sha, prompt_point: [...entry.point] };
      // The first candidate is only a suggestion. Predicted model scores cannot
      // certify the whole object, and the original photograph is always retained.
      const candidate = response.candidates[0];
      if (!candidate || !validBox(candidate.target_box) || typeof candidate.id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(candidate.id) || typeof candidate.mask_png_base64 !== 'string' || candidate.mask_png_base64.length > 700000) throw new Error('No usable suggestion.');
      const binary = atob(candidate.mask_png_base64), foregroundMask = new Blob([Uint8Array.from(binary, value => value.charCodeAt(0))], { type: 'image/png' });
      if (foregroundMask.size > 512 * 1024) throw new Error('Invalid mask.');
      const maskHash = await sha256(foregroundMask);
      if (candidate.mask_sha256 && maskHash !== candidate.mask_sha256) throw new Error('Mask did not match.');
      const maskCanvas = await decode(foregroundMask);
      if (!fresh()) return;
      if (maskCanvas.width !== entry.canvas.width || maskCanvas.height !== entry.canvas.height) throw new Error('Mask dimensions did not match.');
      const pixels = contextOf(maskCanvas).getImageData(0, 0, maskCanvas.width, maskCanvas.height).data, mask = new Uint8Array(maskCanvas.width * maskCanvas.height);
      let count = 0;
      for (let index = 0; index < mask.length; index++) {
        const offset = index * 4, value = pixels[offset];
        if ((value !== 0 && value !== 255) || pixels[offset + 1] !== value || pixels[offset + 2] !== value || pixels[offset + 3] !== 255) throw new Error('Invalid mask pixels.');
        mask[index] = value; if (value) count++;
      }
      if (!count) throw new Error('Empty mask.');
      const isolatedBlob = await this.isolate(entry.canvas, mask);
      if (!fresh()) return;
      Object.assign(entry, { mask, foregroundMask, isolatedBlob, state: 'suggested' });
      Object.assign(entry.captureMetadata, { target_box: [...candidate.target_box], detection_status: 'suggested',
        selection: { method: 'server-suggestion', reviewed: false, source_sha256: entry.sha, mask_sha256: maskHash, candidate_id: candidate.id },
        segmentation: { model: response.model.name, model_hash: response.model.sha256, revision: response.model.revision }, analysis });
      this.notify(entry);
      if (this.inspected === entry) this.drawInspector();
    } catch {
      if (fresh()) { entry.state = 'unavailable'; entry.captureMetadata.detection_status = 'unavailable'; this.notify(entry); }
    } finally {
      clearTimeout(timer);
      if (this.controller === controller) { this.controller = null; this.analyzingId = null; }
    }
  }
  async isolate(source, mask) {
    const canvas = document.createElement('canvas'); canvas.width = source.width; canvas.height = source.height;
    const context = contextOf(canvas); context.drawImage(source, 0, 0); const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
    for (let index = 0; index < mask.length; index++) if (!mask[index]) pixels.data[index * 4 + 3] = 0;
    context.putImageData(pixels, 0, 0); return blobFrom(canvas, 'image/png');
  }
  render() {
    $('quickShutter').disabled = !this.running || this.encoding || this.finishing || this.entries.length >= (this.limit || 0);
    $('quickDone').disabled = !this.entries.length || this.encoding || this.finishing;
    $('quickCount').textContent = `${this.entries.length} of ${this.limit || 4} photos`;
    $('quickPhotos').replaceChildren();
    for (const [index, entry] of this.entries.entries()) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'quickPhoto'; button.setAttribute('aria-label', `Photo ${index + 1}: view or adjust`);
      const image = document.createElement('img'); image.src = entry.url; image.alt = `Captured photo ${index + 1}`;
      const status = document.createElement('span'); status.textContent = ['queued', 'analyzing'].includes(entry.state) ? 'Finding object…' : entry.state === 'suggested' ? 'Object suggestion' : entry.state === 'adjusted' ? 'Area adjusted' : 'Original kept';
      button.append(image, status); button.addEventListener('click', () => this.inspect(entry)); $('quickPhotos').append(button);
    }
  }
  inspect(entry) {
    if (!this.entries.includes(entry) || this.encoding || this.finishing) return;
    this.inspected = entry; this.draftBox = null; this.drag = null;
    $('quickCamera').hidden = true; $('quickInspector').hidden = false; this.drawInspector();
  }
  hideInspector() { this.inspected = null; this.drag = null; this.draftBox = null; $('quickInspector').hidden = true; $('quickCamera').hidden = false; }
  drawInspector() {
    const entry = this.inspected; if (!entry) return;
    const canvas = $('quickSnapshot'); canvas.width = entry.canvas.width; canvas.height = entry.canvas.height;
    const context = contextOf(canvas); context.drawImage(entry.canvas, 0, 0);
    if (entry.mask && !this.draftBox && !this.drag) {
      const tint = context.createImageData(canvas.width, canvas.height);
      for (let index = 0; index < entry.mask.length; index++) if (entry.mask[index]) { const offset = index * 4; tint.data[offset] = 35; tint.data[offset + 1] = 210; tint.data[offset + 2] = 140; tint.data[offset + 3] = 75; }
      const layer = document.createElement('canvas'); layer.width = canvas.width; layer.height = canvas.height; contextOf(layer).putImageData(tint, 0, 0); context.drawImage(layer, 0, 0);
    }
    const box = this.drag ? [Math.min(this.drag.start[0], this.drag.end[0]), Math.min(this.drag.start[1], this.drag.end[1]), Math.max(this.drag.start[0], this.drag.end[0]), Math.max(this.drag.start[1], this.drag.end[1])] : this.draftBox || (entry.captureMetadata.selection.method !== 'full-frame' ? entry.captureMetadata.target_box : null);
    if (box) { context.strokeStyle = '#29e6ad'; context.lineWidth = Math.max(3, canvas.width / 220); context.strokeRect(box[0] * canvas.width, box[1] * canvas.height, (box[2] - box[0]) * canvas.width, (box[3] - box[1]) * canvas.height); }
    $('quickApplyBox').disabled = !this.draftBox;
    $('quickInspectionStatus').textContent = entry.state === 'suggested' ? 'Suggested outline. Drag around the whole object if you want to adjust it.' : ['queued', 'analyzing'].includes(entry.state) ? 'Finding the object in the background. Your original photo is kept.' : entry.state === 'adjusted' ? 'Your selected area is kept with the original photo.' : 'Your original photo is kept. You can draw around the object if needed.';
  }
  beginBox(event) {
    const entry = this.inspected; if (!entry || this.correcting) return;
    const point = this.normalizedPoint(event, $('quickSnapshot'), entry.canvas.width, entry.canvas.height); if (!point) return;
    event.preventDefault(); this.drag = { start: point, end: point }; $('quickSnapshot').setPointerCapture(event.pointerId); this.drawInspector();
  }
  moveBox(event) { if (this.drag && this.inspected) { const point = this.normalizedPoint(event, $('quickSnapshot'), this.inspected.canvas.width, this.inspected.canvas.height); if (point) { this.drag.end = point; this.drawInspector(); } } }
  endBox(event) {
    if (!this.drag) return;
    this.moveBox(event); const { start, end } = this.drag; this.drag = null;
    const box = [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])];
    if (box[2] - box[0] > .01 && box[3] - box[1] > .01) this.draftBox = box;
    this.drawInspector();
  }
  invalidate(entry) { entry.version++; if (this.analyzingId === entry.id) this.controller?.abort(); }
  keepFullPhoto() {
    const entry = this.inspected; if (!entry || this.correcting) return;
    this.invalidate(entry); entry.state = 'adjusted'; entry.mask = null; entry.foregroundMask = null; entry.isolatedBlob = null;
    Object.assign(entry.captureMetadata, { target_box: [0, 0, 1, 1], detection_status: 'skipped', selection: { method: 'full-frame', reviewed: true, source_sha256: entry.sha } });
    delete entry.captureMetadata.segmentation; this.notify(entry); this.hideInspector();
  }
  async applyBox() {
    const entry = this.inspected, box = this.draftBox; if (!entry || !box || this.correcting) return;
    this.correcting = true; const epoch = this.epoch; this.invalidate(entry); const version = entry.version;
    try {
      const canvas = document.createElement('canvas'); canvas.width = entry.canvas.width; canvas.height = entry.canvas.height;
      const context = contextOf(canvas); context.fillStyle = '#000'; context.fillRect(0, 0, canvas.width, canvas.height); context.fillStyle = '#fff';
      const left = Math.floor(box[0] * canvas.width), top = Math.floor(box[1] * canvas.height), right = Math.ceil(box[2] * canvas.width), bottom = Math.ceil(box[3] * canvas.height);
      context.fillRect(left, top, right - left, bottom - top);
      const foregroundMask = await blobFrom(canvas, 'image/png'), maskHash = await sha256(foregroundMask);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data, mask = Uint8Array.from({ length: canvas.width * canvas.height }, (_, index) => pixels[index * 4]);
      const isolatedBlob = await this.isolate(entry.canvas, mask);
      if (epoch !== this.epoch || version !== entry.version || !this.entries.includes(entry)) return;
      Object.assign(entry, { foregroundMask, mask, isolatedBlob, state: 'adjusted' });
      Object.assign(entry.captureMetadata, { target_box: [...box], detection_status: 'skipped', selection: { method: 'manual-box', reviewed: true, source_sha256: entry.sha, mask_sha256: maskHash } });
      delete entry.captureMetadata.segmentation; this.notify(entry); this.hideInspector();
    } catch { if (epoch === this.epoch) $('quickInspectionStatus').textContent = 'Could not adjust this area. Your original photo is still kept.'; }
    finally { this.correcting = false; }
  }
  removeInspected() {
    const entry = this.inspected; if (!entry || this.correcting) return;
    this.invalidate(entry); URL.revokeObjectURL(entry.url); this.entries = this.entries.filter(item => item !== entry); this.hideInspector(); this.render();
  }
  async finish() {
    if (!this.entries.length || this.encoding || this.finishing || this.correcting) return;
    this.finishing = true; this.render(); const epoch = this.epoch;
    const isCurrent = () => epoch === this.epoch && $('guidedDialog').open;
    try {
      await this.onUse(this.selectedPhotos(), isCurrent);
      if (!isCurrent()) return;
      this.delivered = true; this.stopCamera(); $('guidedDialog').close(); this.finishing = false;
      // Reconcile an analysis that completed while the import was being hashed.
      for (const entry of this.entries) this.onUpdate(this.exportEntry(entry));
      this.onComplete();
    } catch { if (isCurrent()) { this.finishing = false; this.say('Could not add these photos. Tap Save to retry.'); this.render(); } }
  }
  stopAnalysis() {
    this.analysisStopped = true; this.controller?.abort(); this.controller = null;
    for (const entry of this.entries) {
      entry.version++;
      if (['queued', 'analyzing'].includes(entry.state)) {
        entry.state = 'skipped'; entry.captureMetadata.detection_status = 'skipped';
        if (this.delivered) this.onUpdate(this.exportEntry(entry));
      }
    }
  }
  stopCamera() {
    this.cameraVersion++; this.running = false; this.starting = false; this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    $('quickVideo').pause(); $('quickVideo').srcObject = null;
    if ($('guidedDialog').open && !this.delivered && !this.finishing) { $('guidedSetup').hidden = false; $('guidedStart').disabled = false; }
    this.render();
  }
  cancel() { this.close(); this.onCancel(); }
  close() {
    this.epoch++; this.stopAnalysis(); this.controller?.abort(); this.controller = null; this.analyzingId = null; this.processing = false;
    this.stopCamera(); this.entries.forEach(entry => URL.revokeObjectURL(entry.url)); this.entries = []; this.sessionId = null;
    this.encoding = false; this.finishing = false; this.correcting = false; this.inspected = null; this.delivered = false;
    $('quickPhotos').replaceChildren(); $('guidedDialog').close();
  }
}
