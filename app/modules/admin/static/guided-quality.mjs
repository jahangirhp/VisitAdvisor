// Experimental capture heuristics. These measure visible pixels, not object identity,
// actual camera angles, future recognition accuracy, or semantic occlusion.

export const DEFAULT_QUALITY_THRESHOLDS = Object.freeze({
  minTargetFraction: 0.08,
  maxClippedFraction: 0.35,
  minSharpness: 40,
  duplicateDistance: 3,
});

const WORK_SIZE = 256;
const HASH_SIZE = 32;
const HASH_LOW = 8;
const TRACK_SIZE = 24;
const cosines = Array.from({ length: HASH_LOW }, (_, frequency) =>
  Float64Array.from({ length: HASH_SIZE }, (_, position) =>
    Math.cos(Math.PI * (position + 0.5) * frequency / HASH_SIZE)));

function checkImage(image) {
  if (!image || !Number.isSafeInteger(image.width) || !Number.isSafeInteger(image.height)
    || image.width < 1 || image.height < 1 || image.width * image.height > 16_777_216) {
    throw new RangeError('Use a decoded frame of at most 16 megapixels');
  }
  if (!(image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray)
    || image.data.length !== image.width * image.height * 4) {
    throw new TypeError('Frame must contain width × height RGBA bytes');
  }
}

function checkBox(box) {
  if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite)
    || box[0] < 0 || box[1] < 0 || box[2] > 1 || box[3] > 1
    || box[0] >= box[2] || box[1] >= box[3]) {
    throw new RangeError('Use a normalized target box within the frame');
  }
}

function checkMask(image, mask) {
  if (mask !== null && (!(mask instanceof Uint8Array)
    || mask.length !== image.width * image.height)) {
    throw new TypeError('Target mask must be a full-frame Uint8Array');
  }
}

function grayAt(image, x, y) {
  const left = Math.max(0, Math.min(image.width - 1, Math.floor(x)));
  const top = Math.max(0, Math.min(image.height - 1, Math.floor(y)));
  const right = Math.min(image.width - 1, left + 1);
  const bottom = Math.min(image.height - 1, top + 1);
  const dx = Math.max(0, Math.min(1, x - left));
  const dy = Math.max(0, Math.min(1, y - top));
  const at = (column, row) => {
    const index = (row * image.width + column) * 4;
    return image.data[index] * 0.299 + image.data[index + 1] * 0.587
      + image.data[index + 2] * 0.114;
  };
  return (at(left, top) * (1 - dx) + at(right, top) * dx) * (1 - dy)
    + (at(left, bottom) * (1 - dx) + at(right, bottom) * dx) * dy;
}

function sample(image, box, width, height, mask = null) {
  const gray = new Float32Array(width * height);
  const visible = new Uint8Array(width * height);
  const left = box[0] * image.width;
  const top = box[1] * image.height;
  const spanX = (box[2] - box[0]) * image.width;
  const spanY = (box[3] - box[1]) * image.height;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const sourceX = left + (x + 0.5) * spanX / width - 0.5;
      const sourceY = top + (y + 0.5) * spanY / height - 0.5;
      const index = y * width + x;
      gray[index] = grayAt(image, sourceX, sourceY);
      const column = Math.max(0, Math.min(image.width - 1, Math.round(sourceX)));
      const row = Math.max(0, Math.min(image.height - 1, Math.round(sourceY)));
      visible[index] = mask === null || mask[row * image.width + column] !== 0 ? 1 : 0;
    }
  }
  return { gray, visible };
}

function emptyQuality(box, reason, targetFraction = 0) {
  return { valid: false, reasons: [reason], box: [...box], sharpness: 0,
    clippedFraction: 1, targetFraction, fingerprint: null, score: 0 };
}

function fingerprint(gray, visible, width, height, mean) {
  const small = new Float32Array(HASH_SIZE * HASH_SIZE);
  // Fill outside the supplied target with its own mean so background changes
  // cannot make a second view. Sample the same target-relative geometry each time.
  for (let y = 0; y < HASH_SIZE; y++) {
    for (let x = 0; x < HASH_SIZE; x++) {
      const left = Math.min(width - 1, Math.floor(x * width / HASH_SIZE));
      const top = Math.min(height - 1, Math.floor(y * height / HASH_SIZE));
      const right = Math.max(left + 1, Math.floor((x + 1) * width / HASH_SIZE));
      const bottom = Math.max(top + 1, Math.floor((y + 1) * height / HASH_SIZE));
      let sum = 0;
      for (let row = top; row < bottom; row++) {
        for (let column = left; column < right; column++) {
          const index = row * width + column;
          sum += visible[index] ? gray[index] : mean;
        }
      }
      small[y * HASH_SIZE + x] = sum / ((right - left) * (bottom - top));
    }
  }
  const horizontal = new Float64Array(HASH_SIZE * HASH_LOW);
  for (let y = 0; y < HASH_SIZE; y++) {
    for (let u = 0; u < HASH_LOW; u++) {
      let sum = 0;
      for (let x = 0; x < HASH_SIZE; x++) sum += small[y * HASH_SIZE + x] * cosines[u][x];
      horizontal[y * HASH_LOW + u] = sum * (u === 0 ? Math.SQRT1_2 : 1);
    }
  }
  const values = [];
  for (let v = 0; v < HASH_LOW; v++) {
    for (let u = 0; u < HASH_LOW; u++) {
      if (u === 0 && v === 0) continue;
      let sum = 0;
      for (let y = 0; y < HASH_SIZE; y++) sum += horizontal[y * HASH_LOW + u] * cosines[v][y];
      values.push(sum * (v === 0 ? Math.SQRT1_2 : 1));
    }
  }
  const median = [...values].sort((a, b) => a - b)[31];
  return values.map(value => value > median ? '1' : '0').join('');
}

/** Analyze only the supplied target. A mask uses full-frame coordinates and nonzero = target. */
export function analyzeTarget(image, box, mask = null) {
  checkImage(image);
  checkBox(box);
  checkMask(image, mask);
  const sourceWidth = (box[2] - box[0]) * image.width;
  const sourceHeight = (box[3] - box[1]) * image.height;
  if (Math.min(sourceWidth, sourceHeight) < 8) return emptyQuality(box, 'insufficient_target');
  const scale = WORK_SIZE / Math.max(sourceWidth, sourceHeight);
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const { gray, visible } = sample(image, box, width, height, mask);
  let visibleCount = 0;
  for (const value of visible) visibleCount += value;
  const targetFraction = visibleCount / visible.length
    * (box[2] - box[0]) * (box[3] - box[1]);
  if (visibleCount === 0) return emptyQuality(box, 'empty_target');
  let count = 0;
  let sum = 0;
  let laplacianSum = 0;
  let laplacianSquares = 0;
  let clipped = 0;
  const interiorPixels = new Uint8Array(visible.length);
  for (let y = 2; y < height - 2; y++) {
    for (let x = 2; x < width - 2; x++) {
      // Erode by two working pixels: sharp mask/crop boundaries must not pass a blurred target.
      let interior = true;
      for (let dy = -2; dy <= 2 && interior; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          if (!visible[(y + dy) * width + x + dx]) { interior = false; break; }
        }
      }
      if (!interior) continue;
      const index = y * width + x;
      interiorPixels[index] = 1;
      const value = gray[index];
      const laplacian = gray[index - 1] + gray[index + 1]
        + gray[index - width] + gray[index + width] - 4 * value;
      count++;
      sum += value;
      laplacianSum += laplacian;
      laplacianSquares += laplacian * laplacian;
      if (value <= 8 || value >= 247) clipped++;
    }
  }
  if (count < 32) return emptyQuality(box, 'insufficient_target', targetFraction);
  const sharpness = Math.max(0, laplacianSquares / count - (laplacianSum / count) ** 2);
  const clippedFraction = clipped / count;
  return { valid: true, reasons: [], box: [...box], sharpness, clippedFraction,
    targetFraction, fingerprint: fingerprint(gray, interiorPixels, width, height, sum / count),
    score: Math.log1p(sharpness) * (1 - clippedFraction) };
}

export function fingerprintDistance(first, second) {
  if (typeof first !== 'string' || typeof second !== 'string'
    || !/^[01]{63}$/.test(first) || !/^[01]{63}$/.test(second)) {
    throw new TypeError('Fingerprints must contain 63 binary digits');
  }
  let distance = 0;
  for (let index = 0; index < 63; index++) if (first[index] !== second[index]) distance++;
  return distance;
}

function checkThresholds(options) {
  const thresholds = { ...DEFAULT_QUALITY_THRESHOLDS, ...options };
  if (!Number.isFinite(thresholds.minTargetFraction) || thresholds.minTargetFraction < 0
    || thresholds.minTargetFraction > 1 || !Number.isFinite(thresholds.maxClippedFraction)
    || thresholds.maxClippedFraction < 0 || thresholds.maxClippedFraction > 1
    || !Number.isFinite(thresholds.minSharpness) || thresholds.minSharpness < 0
    || !Number.isInteger(thresholds.duplicateDistance) || thresholds.duplicateDistance < 0
    || thresholds.duplicateDistance > 63) throw new RangeError('Invalid capture quality thresholds');
  return Object.freeze(thresholds);
}

function validQuality(quality) {
  return quality?.valid === true && Number.isFinite(quality.sharpness) && quality.sharpness >= 0
    && Number.isFinite(quality.targetFraction) && quality.targetFraction >= 0 && quality.targetFraction <= 1
    && Number.isFinite(quality.clippedFraction) && quality.clippedFraction >= 0 && quality.clippedFraction <= 1
    && typeof quality.fingerprint === 'string' && /^[01]{63}$/.test(quality.fingerprint);
}

/** One best candidate for each requested prompt. Prompts do not verify actual viewpoints. */
export class ReferenceShortlist {
  #steps;
  #selected = new Map();
  constructor(steps, { thresholds = {} } = {}) {
    if (!Array.isArray(steps) || steps.length < 1 || steps.length > 4
      || steps.some(step => typeof step !== 'string' || !step.trim() || step.length > 64)
      || new Set(steps).size !== steps.length) {
      throw new RangeError('Use one to four distinct requested capture prompts');
    }
    this.#steps = [...steps];
    this.thresholds = checkThresholds(thresholds);
  }

  consider(candidate, { targetConfirmed = false, occluded = false } = {}) {
    const reject = reason => ({ accepted: false, reason });
    if (!candidate || !this.#steps.includes(candidate.stepId)) return reject('unknown_step');
    if (targetConfirmed !== true) return reject('target_lost');
    if (occluded !== false) return reject('wait_for_clear_view');
    const quality = candidate.quality;
    if (!validQuality(quality)) return reject('invalid_quality');
    if (quality.targetFraction < this.thresholds.minTargetFraction) return reject('move_closer');
    if (quality.clippedFraction > this.thresholds.maxClippedFraction) return reject('change_lighting');
    if (quality.sharpness < this.thresholds.minSharpness) return reject('hold_steady');
    for (const [step, saved] of this.#selected) {
      if (step !== candidate.stepId && fingerprintDistance(quality.fingerprint,
        saved.quality.fingerprint) <= this.thresholds.duplicateDistance) return reject('duplicate_view');
    }
    const previous = this.#selected.get(candidate.stepId);
    const score = Math.log1p(quality.sharpness) * (1 - quality.clippedFraction);
    if (previous && score <= previous.quality.score) return reject('keep_previous');
    // Freeze scalar measurements so a later analysis cannot mutate the selection policy.
    const selected = { ...candidate, quality: Object.freeze({ ...quality, score,
      box: quality.box ? Object.freeze([...quality.box]) : undefined }) };
    this.#selected.set(candidate.stepId, Object.freeze(selected));
    return { accepted: true, reason: previous ? 'improved' : 'selected', replaced: previous ?? null };
  }

  entries() { return this.#steps.flatMap(step => this.#selected.has(step) ? [this.#selected.get(step)] : []); }
  clear() { this.#selected.clear(); }
}

/** A short-lived appearance template, not an object detector or proof of identity. */
export function createTargetTemplate(image, box, mask = null) {
  checkImage(image);
  checkBox(box);
  checkMask(image, mask);
  const { gray, visible } = sample(image, box, TRACK_SIZE, TRACK_SIZE, mask);
  let count = 0;
  let sum = 0;
  for (let index = 0; index < gray.length; index++) {
    if (visible[index]) { count++; sum += gray[index]; }
  }
  const mean = count ? sum / count : 0;
  let energy = 0;
  for (let index = 0; index < gray.length; index++) {
    if (visible[index]) energy += (gray[index] - mean) ** 2;
  }
  return { version: 1, box: [...box], gray, visible, mean, energy, count,
    usable: count >= 32 && energy / count >= 36 };
}

/** Conservative local translation/scale search; reject loss and request a fresh selection. */
export function trackTarget(image, template, previousBox = template?.box, options = {}) {
  checkImage(image);
  checkBox(previousBox);
  const minConfidence = options.minConfidence ?? 0.8;
  const searchFraction = options.searchFraction ?? 0.2;
  if (!Number.isFinite(minConfidence) || minConfidence < 0 || minConfidence > 1
    || !Number.isFinite(searchFraction) || searchFraction < 0 || searchFraction > 0.5) {
    throw new RangeError('Invalid target tracking options');
  }
  if (!template || template.version !== 1 || !(template.gray instanceof Float32Array)
    || template.gray.length !== TRACK_SIZE ** 2 || !(template.visible instanceof Uint8Array)
    || template.visible.length !== TRACK_SIZE ** 2 || !template.usable) {
    return { found: false, confidence: 0, box: [...previousBox], reason: 'insufficient_texture' };
  }
  const width = previousBox[2] - previousBox[0];
  const height = previousBox[3] - previousBox[1];
  const centerX = (previousBox[0] + previousBox[2]) / 2;
  const centerY = (previousBox[1] + previousBox[3]) / 2;
  let best = { confidence: -1, box: [...previousBox] };
  for (const scale of [1, 0.92, 1.08]) {
    for (const offsetY of [0, -0.5, 0.5, -1, 1]) {
      for (const offsetX of [0, -0.5, 0.5, -1, 1]) {
        const x = centerX + offsetX * searchFraction * width;
        const y = centerY + offsetY * searchFraction * height;
        const box = [x - width * scale / 2, y - height * scale / 2,
          x + width * scale / 2, y + height * scale / 2];
        if (box[0] < 0 || box[1] < 0 || box[2] > 1 || box[3] > 1) continue;
        const { gray } = sample(image, box, TRACK_SIZE, TRACK_SIZE);
        let sum = 0;
        for (let index = 0; index < gray.length; index++) if (template.visible[index]) sum += gray[index];
        const mean = sum / template.count;
        let energy = 0;
        let product = 0;
        for (let index = 0; index < gray.length; index++) {
          if (!template.visible[index]) continue;
          const value = gray[index] - mean;
          energy += value * value;
          product += value * (template.gray[index] - template.mean);
        }
        const confidence = energy > 0 ? product / Math.sqrt(energy * template.energy) : 0;
        if (confidence > best.confidence) best = { confidence, box };
      }
    }
  }
  const confidence = Math.max(0, Math.min(1, best.confidence));
  const found = confidence >= minConfidence;
  return { found, confidence, box: found ? best.box : [...previousBox],
    reason: found ? 'appearance_match' : 'target_lost' };
}
