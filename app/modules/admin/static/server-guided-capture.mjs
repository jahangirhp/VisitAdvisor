import { analyzeTarget } from './guided-quality.mjs?v=camera-20261107-2';

const PLANS = {
  planar: [['front', 'Face the whole artwork straight on'], ['oblique-left', 'Move a little left, keeping the whole artwork visible'], ['oblique-right', 'Move a little right, keeping the whole artwork visible']],
  multiview: [['front', 'Start with a clear full view from the front'], ['left', 'Walk to an accessible position on the left'], ['back', 'Try the back, only if accessible'], ['right', 'Move to an accessible position on the right']],
  'front-only': [['front', 'Start with a clear full view from the front'], ['oblique-left', 'Move a little left, staying in front of the object'], ['oblique-right', 'Move a little right, staying in front of the object'], ['elevated', 'Try a slightly higher view, if safe and accessible']],
  scene: [['front', 'Start with the main approach or full room view'], ['oblique-left', 'Move to an accessible position on the left'], ['oblique-right', 'Move to an accessible position on the right'], ['approach', 'Move closer while keeping the important features visible']],
};
const $ = id => document.getElementById(id);
const blobFrom = (canvas, type, quality) => new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Photo conversion failed. Try your phone camera.')), type, quality));
const sha256 = async blob => [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))].map(byte => byte.toString(16).padStart(2, '0')).join('');
const validBox = box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 && box[2] <= 1 && box[3] <= 1 && box[0] < box[2] && box[1] < box[3];

async function decodedCanvas(blob) {
  const url = URL.createObjectURL(blob), image = new Image();
  try {
    await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('Could not read this selection. Use the original photo or retry.')); image.src = url; });
    const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    canvas.getContext('2d').drawImage(image, 0, 0); return canvas;
  } finally { URL.revokeObjectURL(url); }
}

/** Only explicit snapshots go to the authenticated same-origin analysis callback. */
export class GuidedCapture {
  constructor({ analyze, onUse, onCancel = () => {}, onComplete = () => {}, capacity = () => 4 }) {
    this.analyze = analyze; this.onUse = onUse; this.onCancel = onCancel; this.onComplete = onComplete; this.capacity = capacity;
    this.epoch = 0; this.version = 0; this.entries = []; this.urls = []; this.choiceUrls = [];
    $('guidedStart').addEventListener('click', () => this.start());
    $('guidedClose').addEventListener('click', () => this.cancel());
    $('guidedDialog').addEventListener('cancel', event => { if (event.target === $('guidedDialog')) { event.preventDefault(); this.cancel(); } });
    $('guidedTarget').addEventListener('click', event => this.select(event));
    $('guidedTarget').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.select(); } });
    $('guidedSnapshot').addEventListener('pointerdown', event => this.beginBox(event));
    $('guidedSnapshot').addEventListener('pointermove', event => this.moveBox(event));
    $('guidedSnapshot').addEventListener('pointerup', event => this.endBox(event));
    $('guidedSnapshot').addEventListener('pointercancel', () => { this.drag = null; this.draw(); });
    $('guidedAnalyze').addEventListener('click', () => this.requestAnalysis());
    $('guidedFullFrame').addEventListener('click', () => this.choose(this.choices?.find(choice => choice.method === 'full-frame')));
    $('guidedConfirmed').addEventListener('change', () => this.update());
    $('guidedKeep').addEventListener('click', () => this.keep());
    $('guidedBlocked').addEventListener('click', () => this.retake());
    $('guidedNext').addEventListener('click', () => this.next());
    $('guidedFinish').addEventListener('click', () => this.review());
    $('guidedUse').addEventListener('click', () => this.use());
    document.addEventListener('visibilitychange', () => { if (document.hidden && $('guidedDialog').open) this.cancel(); });
    window.addEventListener('pagehide', () => { if ($('guidedDialog').open) this.cancel(); else this.close(); });
  }
  open(profile = 'multiview') {
    this.close(); $('guidedError')?.remove();
    $('guidedProfile').value = PLANS[profile] ? profile : 'multiview';
    $('guidedSetup').hidden = false; $('guidedLive').hidden = true; $('guidedReview').hidden = true; $('guidedFallback').hidden = false;
    $('guidedStart').disabled = false; $('guidedStart').textContent = 'Start camera'; $('guidedDialog').showModal();
  }
  say(message) { $('guidedStatus').textContent = message; }
  async start() {
    if (this.running || this.starting) return;
    const epoch = ++this.epoch; this.starting = true;
    this.profile = $('guidedProfile').value; this.plan = PLANS[this.profile].slice(0, Math.min(4, this.capacity())); this.step = 0;
    if (!this.plan.length) { this.starting = false; return; }
    $('guidedError')?.remove(); $('guidedStart').disabled = true; this.say('Starting camera…');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !globalThis.crypto?.randomUUID || !globalThis.crypto?.subtle) throw new Error('The browser camera needs HTTPS and a supported browser. Use Take a photo or Choose photos.');
      this.sessionId = crypto.randomUUID();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1600 }, height: { ideal: 1200 } } });
      if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      $('guidedSetup').hidden = true; $('guidedLive').hidden = false; $('guidedReview').hidden = true;
      const video = $('guidedVideo'); video.hidden = false; video.srcObject = stream;
      let playTimer;
      try {
        await Promise.race([video.play(), new Promise((_, reject) => { playTimer = setTimeout(() => reject(new Error('The camera preview did not start. Retry or use your phone camera.')), 12000); })]);
      } finally { clearTimeout(playTimer); }
      if (epoch !== this.epoch) return;
      for (const track of stream.getVideoTracks()) track.addEventListener('ended', () => {
        if (!this.running) return;
        this.stopCamera(); this.say('Camera stopped. Review your selected photos or use your phone camera.'); this.update();
      });
      this.running = true; this.starting = false;
      $('guidedSetup').hidden = true; $('guidedLive').hidden = false; $('guidedReview').hidden = true;
      $('guidedStage').style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
      this.live();
    } catch (error) {
      if (epoch !== this.epoch) return;
      this.stopCamera(); $('guidedSetup').hidden = false; $('guidedLive').hidden = true; $('guidedStart').disabled = false; $('guidedStart').textContent = 'Retry camera';
      const notice = document.createElement('p'); notice.id = 'guidedError'; notice.setAttribute('role', 'alert');
      notice.textContent = error.name === 'NotAllowedError' ? 'Camera access was denied. Allow camera access, or use Take a photo or Choose photos.' : error.message;
      $('guidedSetup').append(notice);
    }
  }
  point(event, element, width, height) {
    if (!event) return [.5, .5];
    const rect = element.getBoundingClientRect(), scale = Math.min(rect.width / width, rect.height / height);
    const renderedWidth = width * scale, renderedHeight = height * scale;
    const x = (event.clientX - rect.left - (rect.width - renderedWidth) / 2) / renderedWidth;
    const y = (event.clientY - rect.top - (rect.height - renderedHeight) / 2) / renderedHeight;
    return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? [x, y] : null;
  }
  async select(event) {
    if (!this.running || this.frame || this.encoding) return;
    const video = $('guidedVideo'), point = this.point(event?.detail === 0 ? null : event, $('guidedStage'), video.videoWidth, video.videoHeight);
    if (!point || video.readyState < 2) return;
    const epoch = this.epoch, source = document.createElement('canvas'), ratio = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
    source.width = Math.round(video.videoWidth * ratio); source.height = Math.round(video.videoHeight * ratio);
    source.getContext('2d').drawImage(video, 0, 0, source.width, source.height);
    this.frame = { canvas: source, point, id: crypto.randomUUID(), box: null }; this.encoding = true; this.showFrozen(); this.draw(); this.say('Preparing the selected photo…'); this.update();
    try {
      let blob;
      for (const quality of [.88, .78, .66, .5]) { blob = await blobFrom(source, 'image/jpeg', quality); if (blob.size <= 1024 * 1024) break; }
      if (blob.size > 1024 * 1024) throw new Error('This frame is too large to analyze. Use Take a photo or Choose photos.');
      const file = new File([blob], 'guided-reference.jpg', { type: 'image/jpeg' });
      const [hash, decoded] = await Promise.all([sha256(file), decodedCanvas(file)]);
      if (epoch !== this.epoch || !this.frame) return;
      Object.assign(this.frame, { file, sha: hash, canvas: decoded }); this.encoding = false;
      this.setChoices(); this.draw(); this.update(); await this.requestAnalysis();
    } catch (error) { if (epoch === this.epoch) { this.encoding = false; this.say(error.message); this.update(); } }
  }
  showFrozen() {
    $('guidedVideo').hidden = true; $('guidedTarget').hidden = true; $('guidedSnapshot').hidden = false; $('guidedExtent').hidden = false;
    $('guidedConfirmed').checked = false; $('guidedQuality').textContent = '';
  }
  setChoices(candidates = []) {
    this.clearChoiceUrls(); this.selected = null; $('guidedConfirmed').checked = false; $('guidedQuality').textContent = '';
    this.choices = [...candidates];
    if (this.frame?.box) this.choices.push({ method: 'manual-box', id: 'manual-box', box: [...this.frame.box] });
    this.choices.push({ method: 'full-frame', id: 'full-frame', box: [0, 0, 1, 1] });
    $('guidedCandidates').replaceChildren();
    for (const [index, choice] of this.choices.entries()) {
      const label = document.createElement('label'); label.className = 'guidedChoice';
      const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'guidedSelection'; radio.value = choice.id;
      radio.addEventListener('change', () => this.choose(choice));
      const text = document.createElement('span'); text.textContent = choice.method === 'server-mask' ? `Selection ${index + 1}` : choice.method === 'manual-box' ? 'My whole-object rectangle' : 'Full original photo';
      radio.setAttribute('aria-label', text.textContent);
      label.append(radio, text);
      if (choice.preview) { const image = document.createElement('img'), url = URL.createObjectURL(choice.preview); this.choiceUrls.push(url); image.src = url; image.alt = `${text.textContent}: check for missing parts`; label.append(image); }
      $('guidedCandidates').append(label);
    }
  }
  async requestAnalysis() {
    if (!this.frame?.file || this.encoding || this.processing || this.saving) return;
    const frame = this.frame, epoch = this.epoch, version = ++this.version;
    const fresh = () => epoch === this.epoch && version === this.version && frame === this.frame;
    this.controller?.abort(); const controller = this.controller = new AbortController();
    this.processing = true; this.setChoices(); this.draw(); this.say('Analyzing this selected photo on the server… The first analysis may take longer. You can still choose the full photo or your rectangle.'); this.update();
    const prompt = { session_id: this.sessionId, frame_id: frame.id, profile: this.profile, point_x: frame.point[0], point_y: frame.point[1] };
    if (frame.box) Object.assign(prompt, { box_x1: frame.box[0], box_y1: frame.box[1], box_x2: frame.box[2], box_y2: frame.box[3] });
    let timer;
    try {
      const timeout = new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Server analysis took too long. Select the full photo or draw the whole-object rectangle, then review it.')); }, 80000); });
      const response = await Promise.race([this.analyze(frame.file, prompt, controller.signal), timeout]);
      if (!fresh()) return;
      if (response.version !== 'server-capture-v1' || response.session_id !== this.sessionId || response.frame_id !== frame.id || response.source_sha256 !== frame.sha || response.width !== frame.canvas.width || response.height !== frame.canvas.height || !response.model?.name || !response.model?.revision || !Array.isArray(response.candidates) || response.candidates.length > 3) throw new Error('The analysis does not match this photo. Retry or review the full original photo.');
      frame.analysis = { model: response.model.name, model_hash: response.model.sha256, revision: response.model.revision, source_sha256: frame.sha, prompt_point: [...frame.point], ...(frame.box ? { prompt_box: [...frame.box] } : {}) };
      const candidates = [];
      for (const candidate of response.candidates) {
        if (!validBox(candidate.target_box) || typeof candidate.id !== 'string' || typeof candidate.mask_png_base64 !== 'string' || candidate.mask_png_base64.length > 700000) continue;
        const binary = atob(candidate.mask_png_base64), blob = new Blob([Uint8Array.from(binary, character => character.charCodeAt(0))], { type: 'image/png' });
        if (blob.size > 512 * 1024 || (candidate.mask_sha256 && await sha256(blob) !== candidate.mask_sha256)) continue;
        const maskCanvas = await decodedCanvas(blob);
        if (!fresh()) return;
        if (maskCanvas.width !== frame.canvas.width || maskCanvas.height !== frame.canvas.height) continue;
        const rgba = maskCanvas.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, maskCanvas.width, maskCanvas.height).data, mask = new Uint8Array(maskCanvas.width * maskCanvas.height);
        let valid = true, count = 0;
        for (let index = 0; index < mask.length; index++) { const offset = index * 4, value = rgba[offset]; if ((value !== 0 && value !== 255) || rgba[offset + 1] !== value || rgba[offset + 2] !== value || rgba[offset + 3] !== 255) { valid = false; break; } mask[index] = value; if (value) count++; }
        if (!valid || !count) continue;
        const preview = await this.isolated(frame.canvas, mask);
        candidates.push({ method: 'server-mask', id: candidate.id, box: candidate.target_box, mask, foregroundMask: blob, preview, model: response.model });
      }
      if (!fresh()) return;
      this.setChoices(candidates); this.draw(); this.say(candidates.length ? 'Choose the selection that includes the whole object. If parts are missing, draw a rectangle or use the full photo.' : 'No usable outline was returned. Draw a rectangle around the whole object, or use the full photo.');
    } catch (error) {
      if (fresh()) this.say(controller.signal.aborted ? 'Analysis stopped. You can review the full photo or your rectangle, or retry.' : `${error.message || 'Server analysis is unavailable.'} You can still review the full photo or draw a rectangle.`);
    } finally { clearTimeout(timer); if (fresh()) { this.processing = false; this.update(); } }
  }
  beginBox(event) {
    if (!this.frame?.file || this.encoding || this.saving) return;
    const point = this.point(event, $('guidedSnapshot'), this.frame.canvas.width, this.frame.canvas.height);
    if (!point) return;
    event.preventDefault(); this.controller?.abort(); this.version++; this.processing = false;
    this.drag = { start: point, end: point }; this.selected = null; $('guidedConfirmed').checked = false; $('guidedQuality').textContent = '';
    for (const radio of $('guidedCandidates').querySelectorAll('input')) radio.checked = false;
    $('guidedSnapshot').setPointerCapture(event.pointerId); this.draw(); this.update();
  }
  moveBox(event) {
    if (!this.drag) return;
    const point = this.point(event, $('guidedSnapshot'), this.frame.canvas.width, this.frame.canvas.height);
    if (point) { this.drag.end = point; this.draw(); }
  }
  endBox(event) {
    if (!this.drag) return;
    this.moveBox(event); const { start, end } = this.drag; this.drag = null;
    const box = [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])];
    if (box[2] - box[0] > .01 && box[3] - box[1] > .01) {
      this.frame.box = box; this.frame.point = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
      this.setChoices(); this.say('Rectangle updated. Choose My whole-object rectangle, or ask the server to analyze this area.');
    }
    this.draw(); this.update();
  }
  choose(choice) {
    if (!choice || !this.frame?.file || this.saving) return;
    if (this.processing) { this.controller?.abort(); this.version++; this.processing = false; }
    this.selected = choice; $('guidedConfirmed').checked = false;
    for (const radio of $('guidedCandidates').querySelectorAll('input')) radio.checked = radio.value === choice.id;
    const pixels = this.frame.canvas.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, this.frame.canvas.width, this.frame.canvas.height);
    choice.quality = analyzeTarget(pixels, choice.box, choice.mask || null);
    const guidance = [];
    if (choice.quality.sharpness < 40) guidance.push('Low detail can mean blur or a smooth surface. Check the object closely.');
    if (choice.quality.clippedFraction > .35) guidance.push('Check for glare or lost detail in bright and dark areas.');
    if (choice.quality.targetFraction < .08) guidance.push('The target is small; a closer photo may help.');
    $('guidedQuality').textContent = guidance.length ? `${guidance.join(' ')} You can keep it after checking the whole object.` : 'Check all edges and fine parts before keeping this photo.';
    this.draw(); this.update();
  }
  draw() {
    if (!this.frame) return;
    const canvas = $('guidedSnapshot'), source = this.frame.canvas; canvas.width = source.width; canvas.height = source.height;
    const context = canvas.getContext('2d'); context.drawImage(source, 0, 0);
    if (this.selected?.mask) {
      const tint = context.createImageData(canvas.width, canvas.height);
      for (let index = 0; index < this.selected.mask.length; index++) if (this.selected.mask[index]) { const offset = index * 4; tint.data[offset] = 35; tint.data[offset + 1] = 210; tint.data[offset + 2] = 140; tint.data[offset + 3] = 80; }
      const layer = document.createElement('canvas'); layer.width = canvas.width; layer.height = canvas.height; layer.getContext('2d').putImageData(tint, 0, 0); context.drawImage(layer, 0, 0);
    }
    const box = this.drag ? [Math.min(this.drag.start[0], this.drag.end[0]), Math.min(this.drag.start[1], this.drag.end[1]), Math.max(this.drag.start[0], this.drag.end[0]), Math.max(this.drag.start[1], this.drag.end[1])] : this.selected?.box || this.frame.box;
    if (box) { context.strokeStyle = '#29e6ad'; context.lineWidth = Math.max(3, canvas.width / 220); context.strokeRect(box[0] * canvas.width, box[1] * canvas.height, (box[2] - box[0]) * canvas.width, (box[3] - box[1]) * canvas.height); }
  }
  async isolated(source, mask) {
    const canvas = document.createElement('canvas'); canvas.width = source.width; canvas.height = source.height;
    const context = canvas.getContext('2d'); context.drawImage(source, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
    for (let index = 0; index < mask.length; index++) if (!mask[index]) pixels.data[index * 4 + 3] = 0;
    context.putImageData(pixels, 0, 0); return blobFrom(canvas, 'image/png');
  }
  async keep() {
    if (!this.frame?.file || !this.selected || !$('guidedConfirmed').checked || this.processing || this.saving) return;
    const epoch = this.epoch, version = this.version, frame = this.frame, choice = this.selected; this.saving = true; this.update();
    try {
      let mask = choice.mask, foregroundMask = choice.foregroundMask || null, isolatedBlob = choice.preview || frame.file;
      if (choice.method === 'manual-box') {
        const canvas = document.createElement('canvas'); canvas.width = frame.canvas.width; canvas.height = frame.canvas.height;
        const context = canvas.getContext('2d'); context.fillStyle = '#000'; context.fillRect(0, 0, canvas.width, canvas.height); context.fillStyle = '#fff';
        const left = Math.floor(choice.box[0] * canvas.width), top = Math.floor(choice.box[1] * canvas.height), right = Math.ceil(choice.box[2] * canvas.width), bottom = Math.ceil(choice.box[3] * canvas.height);
        context.fillRect(left, top, right - left, bottom - top);
        foregroundMask = await blobFrom(canvas, 'image/png'); const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
        mask = Uint8Array.from({ length: canvas.width * canvas.height }, (_, index) => rgba[index * 4]); isolatedBlob = await this.isolated(frame.canvas, mask);
      }
      const maskHash = foregroundMask ? await sha256(foregroundMask) : null;
      if (epoch !== this.epoch || version !== this.version || frame !== this.frame) return;
      const quality = choice.quality;
      const metadata = {
        version: 'guided-capture-v2', session_id: this.sessionId, frame_id: frame.id, profile: this.profile,
        requested_view: this.plan[this.step][0], viewpoint_verified: false, target_box: [...choice.box],
        quality: { sharpness: quality.sharpness, clipped_fraction: quality.clippedFraction, target_fraction: quality.targetFraction },
        selection: { method: choice.method, reviewed: true, source_sha256: frame.sha, ...(maskHash ? { mask_sha256: maskHash } : {}), ...(choice.method === 'server-mask' ? { candidate_id: choice.id } : {}) },
        ...(choice.method === 'server-mask' ? { segmentation: { model: choice.model.name, model_hash: choice.model.sha256, revision: choice.model.revision } } : {}),
        ...(frame.analysis ? { analysis: structuredClone(frame.analysis) } : {}),
      };
      const entry = { stepId: this.plan[this.step][0], file: frame.file, foregroundMask, isolatedBlob, captureMetadata: metadata };
      const existing = this.entries.findIndex(item => item.stepId === entry.stepId);
      if (existing >= 0) this.entries[existing] = entry; else if (this.entries.length < Math.min(4, this.capacity())) this.entries.push(entry);
      this.saving = false; this.say('Photo kept after your review. Choose Next view or review photos. Retake replaces only this view.'); this.update();
    } catch { if (epoch === this.epoch) { this.saving = false; this.say('Could not keep this selection. Retry or use the full original photo.'); this.update(); } }
  }
  update() {
    $('guidedPrompt').textContent = this.plan?.length ? `${this.step + 1} of ${this.plan.length} · ${this.plan[this.step][1]}` : '';
    const kept = this.entries.some(entry => entry.stepId === this.plan?.[this.step]?.[0]);
    $('guidedAnalyze').disabled = !this.frame?.file || this.encoding || this.processing || this.saving;
    $('guidedFullFrame').disabled = !this.frame?.file || this.encoding || this.saving;
    $('guidedKeep').disabled = !this.selected || !$('guidedConfirmed').checked || this.encoding || this.processing || this.saving;
    $('guidedConfirmed').disabled = !this.selected || this.saving;
    $('guidedNext').disabled = !kept || this.processing || this.encoding || this.saving || !this.running || this.step >= (this.plan?.length || 0) - 1;
    $('guidedFinish').disabled = !this.entries.length || this.saving;
    $('guidedBlocked').disabled = this.saving || this.encoding || !this.running;
  }
  live() {
    this.controller?.abort(); this.version++; this.processing = false; this.encoding = false; this.saving = false; this.frame = null; this.selected = null; this.drag = null; this.clearChoiceUrls();
    $('guidedVideo').hidden = false; $('guidedTarget').hidden = false; $('guidedSnapshot').hidden = true; $('guidedExtent').hidden = true;
    $('guidedConfirmed').checked = false; $('guidedCandidates').replaceChildren(); this.say('Tap the object to freeze a photo. Then check its whole outline.'); this.update();
  }
  retake() { if (this.running && !this.saving && !this.encoding) this.live(); }
  next() { if (!$('guidedNext').disabled) { this.step++; this.live(); } }
  review() { if (!this.entries.length || this.saving) return; this.stopCamera(); this.renderReview(); }
  renderReview() {
    this.clearUrls(); $('guidedLive').hidden = true; $('guidedSetup').hidden = true; $('guidedReview').hidden = false; $('guidedFallback').hidden = true; $('guidedPhotos').replaceChildren();
    for (const entry of this.entries) {
      const card = document.createElement('div'); card.className = 'guidedPhoto';
      const previews = [[entry.file, 'Original selected photo']];
      if (entry.foregroundMask) previews.push([entry.isolatedBlob, 'Reviewed target preview']);
      for (const [blob, alt] of previews) { const image = document.createElement('img'), url = URL.createObjectURL(blob); this.urls.push(url); image.src = url; image.alt = alt; card.append(image); }
      const text = document.createElement('p'); text.textContent = `Requested view: ${entry.stepId}. Confirm the actual angle on the next screen.`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove this photo'; remove.addEventListener('click', () => { this.entries = this.entries.filter(item => item !== entry); this.renderReview(); }); card.append(text, remove); $('guidedPhotos').append(card);
    }
    $('guidedUse').disabled = !this.entries.length;
    $('guidedGaps').textContent = `${this.entries.length} of ${this.plan.length} requested photos selected. Angles and recognition readiness have not been verified. You can add more photos later.`;
  }
  selectedPhotos() { return this.entries.slice(); }
  async use() {
    if (!this.entries.length) return;
    $('guidedUse').disabled = true; const epoch = this.epoch, entries = this.entries.slice();
    const isCurrent = () => epoch === this.epoch && $('guidedDialog').open;
    try { await this.onUse(entries, isCurrent); if (isCurrent()) { this.close(); this.onComplete(); } }
    catch { if (isCurrent()) { $('guidedGaps').textContent = 'Could not add these photos. Please retry.'; $('guidedUse').disabled = false; } }
  }
  stopCamera() {
    this.running = false; this.starting = false; this.epoch++; this.version++; this.controller?.abort(); this.controller = null; this.processing = false; this.encoding = false; this.saving = false;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null; $('guidedVideo').pause(); $('guidedVideo').srcObject = null;
  }
  clearUrls() { this.urls.forEach(url => URL.revokeObjectURL(url)); this.urls = []; }
  clearChoiceUrls() { this.choiceUrls.forEach(url => URL.revokeObjectURL(url)); this.choiceUrls = []; }
  cancel() { this.close(); this.onCancel(); }
  close() {
    this.stopCamera(); this.entries = []; this.frame = null; this.selected = null; this.clearUrls(); this.clearChoiceUrls(); $('guidedPhotos').replaceChildren(); $('guidedDialog').close();
  }
}
