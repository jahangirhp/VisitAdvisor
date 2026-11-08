// Pure recognition logic, shared with behavioral tests. Scores are not probabilities.
export function normalize(vector) {
  if (vector.length !== 384 || !vector.every(Number.isFinite)) throw new Error('Invalid descriptor');
  const norm = Math.hypot(...vector);
  if (!norm) throw new Error('Empty descriptor');
  return Float32Array.from(vector, value => value / norm);
}

export function rank(vector, objects, policy) {
  const query = normalize(vector);
  const cosine = reference => reference.reduce((total, value, index) => total + value * query[index], 0);
  return objects.map(object => {
    const score = Math.max(...object.views.map(view => {
      const scores = view.vectors.map(cosine);
      return policy.global_weight * scores[0] + (1 - policy.global_weight) * Math.max(...scores);
    }));
    return { id: object.id, score: Math.min(1, score) };
  }).sort((a, b) => b.score - a.score || a.id.localeCompare(b.id)).slice(0, 3);
}

export class Stability {
  constructor(policy) { this.policy = policy; this.reset(); }
  reset() { this.id = null; this.count = 0; this.lastAt = 0; this.firstAt = 0; this.confirmed = null; this.lastConfirmedAt = 0; this.holding = false; this.box = null; this.views = new Set(); }
  current(now) {
    return this.confirmed && now - this.lastConfirmedAt <= (this.holding ? 1500 : 3000) ? this.confirmed : null;
  }
  observe(candidates, now, moving = false) {
    const [first, second] = candidates;
    const thresholds = first && this.policy.profiles?.[first.profile];
    const strong = first && first.coverage_ready !== false && thresholds?.enabled !== false &&
      first.score >= (thresholds?.floor ?? this.policy.candidate_floor) &&
      first.score - (second?.score ?? 0) >= (thresholds?.margin ?? this.policy.candidate_margin);
    if (strong && this.box && first.bbox && !sameRegion(this.box, first.bbox)) this.reset();
    // Rejected/tied candidates have no reliable identity. Their ordering must not
    // clear an established match, nor refresh the last verified timestamp.
    if (moving || (this.confirmed && (!this.current(now) || (strong && first.id !== this.confirmed)))) this.reset();
    if (moving) return null;
    if (!strong) {
      // Brief weak views may retain an existing candidate, never establish one.
      // Only fresh strong evidence extends its lifetime.
      this.holding = true;
      if (this.current(now)) return this.confirmed;
      this.reset();
      return null;
    }
    if (this.id !== first.id || now - this.lastAt > 3000) {
      this.count = 0;
      this.firstAt = now;
      this.views.clear();
    }
    this.id = first.id;
    this.lastAt = now;
    this.count++;
    this.box = first.bbox ?? null;
    if (Number.isInteger(first.view_index)) this.views.add(first.view_index);
    this.holding = false;
    if (this.count >= this.policy.stable_frames && now - this.firstAt >= (this.policy.minimum_stable_ms ?? 0)) {
      this.confirmed = first.id;
      this.lastConfirmedAt = now;
    }
    return this.confirmed;
  }
}

export function validBox(box) {
  return Array.isArray(box) && box.length === 4 && box.every(v => Number.isFinite(v) && v >= 0 && v <= 1) && box[0] < box[2] && box[1] < box[3];
}

export function sameRegion(a, b) {
  if (!validBox(a) || !validBox(b)) return false;
  const area = box => (box[2] - box[0]) * (box[3] - box[1]);
  // Full-frame fallback is not evidence of a new physical object.
  if (area(a) > 0.95 || area(b) > 0.95) return true;
  const intersection = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) *
    Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const iou = intersection / (area(a) + area(b) - intersection);
  const distance = Math.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2);
  return iou >= 0.2 || distance <= 0.15;
}

export function signature(rgba, size = 224) {
  const values = [];
  // Average small regions: single pixels react to texture, reflections and
  // ordinary hand jitter even when the same object remains in view.
  for (let row = 0; row < 16; row++) {
    const y0 = Math.floor(row * size / 16), y1 = Math.floor((row + 1) * size / 16);
    for (let column = 0; column < 16; column++) {
      const x0 = Math.floor(column * size / 16), x1 = Math.floor((column + 1) * size / 16);
      let sum = 0;
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) {
          const i = (y * size + x) * 4;
          sum += rgba[i] + rgba[i + 1] + rgba[i + 2];
        }
      }
      values.push(sum / (3 * 255 * (x1 - x0) * (y1 - y0)));
    }
  }
  return values;
}

export function motion(a, b) {
  if (!a || !b || a.length !== b.length) return 0;
  return a.reduce((sum, value, index) => sum + Math.abs(value - b[index]), 0) / a.length;
}

export function toTensor(rgba) {
  const pixels = 224 * 224;
  const result = new Float32Array(3 * pixels);
  const means = [0.485, 0.456, 0.406];
  const stds = [0.229, 0.224, 0.225];
  for (let c = 0; c < 3; c++) {
    for (let i = 0; i < pixels; i++) result[c * pixels + i] = (rgba[i * 4 + c] / 255 - means[c]) / stds[c];
  }
  return result;
}

export function validateCatalog(catalog, now = Date.now()) {
  const fail = () => { throw new Error('Reference download is invalid or expired. Reload to try again.'); };
  if (catalog.version !== 'camera-server-v1' || catalog.processing !== 'server' || catalog.automatic_identification !== false ||
      !Number.isFinite(Date.parse(catalog.expires_at)) || Date.parse(catalog.expires_at) <= now ||
      !/^[a-f0-9]{64}$/.test(catalog.reference_version) ||
      !/^[a-f0-9]{24}$/.test(catalog.location?.id) ||
      !Array.isArray(catalog.objects) || catalog.objects.length < 1 || catalog.objects.length > 500) fail();
  const policy = catalog.policy;
  if (!policy || policy.calibrated !== false || policy.global_weight !== 0.75 ||
      !Number.isFinite(policy.candidate_floor) || policy.candidate_floor < 0.45 || policy.candidate_floor > 1 ||
      !Number.isFinite(policy.candidate_margin) || policy.candidate_margin < 0.02 || policy.candidate_margin > 1 ||
      !Number.isInteger(policy.stable_frames) || policy.stable_frames < 3 || policy.stable_frames > 10) fail();
  if (catalog.readiness && (catalog.readiness.model_warmed !== true || catalog.readiness.references_current !== true)) fail();
  if (policy.recognition_pipeline && policy.recognition_pipeline !== 'object-regions-types-v5') fail();
  if (policy.frame_size !== undefined && ![224, 512].includes(policy.frame_size)) fail();
  if (policy.minimum_stable_ms !== undefined && (!Number.isFinite(policy.minimum_stable_ms) || policy.minimum_stable_ms < 0 || policy.minimum_stable_ms > 3000)) fail();
  if (policy.profiles) {
    if (!Object.hasOwn(policy.profiles, 'planar') || !Object.hasOwn(policy.profiles, 'multiview')) fail();
    for (const threshold of Object.values(policy.profiles)) {
      if (typeof threshold.enabled !== 'boolean' || !Number.isFinite(threshold.floor) || threshold.floor < 0.45 || threshold.floor > 1 ||
          !Number.isFinite(threshold.margin) || threshold.margin < 0.02 || threshold.margin > 1) fail();
    }
  }
  const ids = new Set();
  for (const object of catalog.objects) {
    if (object.location_id !== catalog.location.id || !/^[a-f0-9]{24}$/.test(object.id) ||
        ids.has(object.id) || typeof object.name !== 'string' || !Array.isArray(object.sources)) fail();
    ids.add(object.id);
  }
  return catalog;
}
