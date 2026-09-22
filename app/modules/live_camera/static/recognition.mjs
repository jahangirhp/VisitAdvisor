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
  reset() { this.id = null; this.count = 0; this.lastAt = 0; this.confirmed = null; this.lastConfirmedAt = 0; this.holding = false; }
  current(now) {
    return this.confirmed && now - this.lastConfirmedAt <= (this.holding ? 1500 : 3000) ? this.confirmed : null;
  }
  observe(candidates, now, moving = false) {
    const [first, second] = candidates;
    const strong = first && first.score >= this.policy.candidate_floor &&
      first.score - (second?.score ?? 0) >= this.policy.candidate_margin;
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
    if (this.id !== first.id || now - this.lastAt > 3000) this.count = 0;
    this.id = first.id;
    this.lastAt = now;
    this.count++;
    this.holding = false;
    if (this.count >= this.policy.stable_frames) {
      this.confirmed = first.id;
      this.lastConfirmedAt = now;
    }
    return this.confirmed;
  }
}

export function signature(rgba) {
  const values = [];
  // Regular samples from the whole crop, not only its first scanline.
  for (let y = 7; y < 224; y += 14) {
    for (let x = 7; x < 224; x += 14) {
      const i = (y * 224 + x) * 4;
      values.push((rgba[i] + rgba[i + 1] + rgba[i + 2]) / (3 * 255));
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
      !Array.isArray(catalog.objects) || catalog.objects.length < 2 || catalog.objects.length > 12) fail();
  const policy = catalog.policy;
  if (!policy || policy.calibrated !== false || policy.global_weight !== 0.75 ||
      !Number.isFinite(policy.candidate_floor) || policy.candidate_floor < 0.45 || policy.candidate_floor > 1 ||
      !Number.isFinite(policy.candidate_margin) || policy.candidate_margin < 0.02 || policy.candidate_margin > 1 ||
      !Number.isInteger(policy.stable_frames) || policy.stable_frames < 3 || policy.stable_frames > 10) fail();
  const ids = new Set();
  for (const object of catalog.objects) {
    if (object.location_id !== catalog.location.id || !/^[a-f0-9]{24}$/.test(object.id) ||
        ids.has(object.id) || typeof object.name !== 'string' || !Array.isArray(object.sources)) fail();
    ids.add(object.id);
  }
  return catalog;
}
