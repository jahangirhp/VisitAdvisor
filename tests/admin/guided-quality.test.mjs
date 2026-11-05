import assert from 'node:assert/strict';
import test from 'node:test';
import {
  analyzeTarget, createTargetTemplate, DEFAULT_QUALITY_THRESHOLDS,
  fingerprintDistance, ReferenceShortlist, trackTarget,
} from '../../app/modules/admin/static/guided-quality.mjs';

const BOX = [0.2, 0.2, 0.8, 0.8];
const confirmed = { targetConfirmed: true, occluded: false };

function frame(width = 100, height = width, value = 120) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let index = 0; index < data.length; index += 4) {
    data[index] = data[index + 1] = data[index + 2] = value;
    data[index + 3] = 255;
  }
  return { width, height, data };
}

function paint(image, box, valueAt) {
  for (let y = Math.round(box[1] * image.height); y < Math.round(box[3] * image.height); y++) {
    for (let x = Math.round(box[0] * image.width); x < Math.round(box[2] * image.width); x++) {
      const value = valueAt(x - Math.round(box[0] * image.width), y - Math.round(box[1] * image.height));
      const index = (y * image.width + x) * 4;
      image.data[index] = image.data[index + 1] = image.data[index + 2] = value;
    }
  }
  return image;
}

function texture(x, y, seed = 0) { return 32 + (x * 17 + y * 29 + x * y * 3 + seed * x * y) % 192; }
function sharp(seed = 0) { return paint(frame(), BOX, (x, y) => texture(x, y, seed)); }
function candidate(stepId, quality, id = stepId) { return { stepId, quality, id }; }

test('target interior quality rejects a blurred target even with a sharp background', () => {
  const crisp = analyzeTarget(sharp(), BOX);
  const distracted = paint(frame(), [0, 0, 1, 1], texture);
  paint(distracted, BOX, (x, y) => 110 + Math.sin(x / 12) * 8 + Math.cos(y / 15) * 8);
  const blurred = analyzeTarget(distracted, BOX);
  assert.ok(crisp.sharpness > blurred.sharpness * 100);
  assert.ok(analyzeTarget(distracted, [0, 0, 1, 1]).sharpness > blurred.sharpness * 100);
  const selected = new ReferenceShortlist(['front']);
  assert.equal(selected.consider(candidate('front', blurred), confirmed).reason, 'hold_steady');
  assert.equal(selected.entries().length, 0);
});

test('mask excludes sharp distractors inside the box from quality and novelty', () => {
  const first = paint(frame(), [0, 0, 1, 1], texture);
  const second = paint(frame(), [0, 0, 1, 1], (x, y) => texture(x, y, 7));
  const mask = new Uint8Array(100 * 100);
  for (let y = 30; y < 70; y++) for (let x = 30; x < 70; x++) mask[y * 100 + x] = 255;
  const smooth = (x, y) => 110 + Math.sin(x / 12) * 8 + Math.cos(y / 15) * 8;
  paint(first, [0.3, 0.3, 0.7, 0.7], smooth);
  paint(second, [0.3, 0.3, 0.7, 0.7], smooth);
  const masked = analyzeTarget(first, BOX, mask);
  assert.ok(analyzeTarget(first, BOX).sharpness > masked.sharpness * 100);
  assert.ok(Math.abs(masked.targetFraction - 0.16) < 0.005);
  assert.equal(fingerprintDistance(masked.fingerprint, analyzeTarget(second, BOX, mask).fingerprint), 0);
});

test('empty target fails closed; clipped or small target requests recapture', () => {
  const selected = new ReferenceShortlist(['front']);
  const empty = analyzeTarget(sharp(), BOX, new Uint8Array(100 * 100));
  assert.equal(empty.valid, false);
  assert.deepEqual(empty.reasons, ['empty_target']);
  assert.equal(selected.consider(candidate('front', empty), confirmed).reason, 'invalid_quality');
  const clipped = analyzeTarget(paint(frame(), BOX, () => 255), BOX);
  assert.equal(clipped.clippedFraction, 1);
  assert.equal(selected.consider(candidate('front', clipped), confirmed).reason, 'change_lighting');
  const tiny = analyzeTarget(paint(frame(), [0.4, 0.4, 0.5, 0.5], texture), [0.4, 0.4, 0.5, 0.5]);
  assert.equal(selected.consider(candidate('front', tiny), confirmed).reason, 'move_closer');
  assert.equal(analyzeTarget(frame(), [0, 0, 0.01, 0.01]).valid, false);
  assert.equal(selected.entries().length, 0);
});

test('requested view labels and background changes cannot make another view', () => {
  const first = sharp();
  const second = paint(frame(), [0, 0, 1, 1], (x, y) => texture(x, y, 11));
  paint(second, BOX, texture);
  const selected = new ReferenceShortlist(['requested-front', 'requested-left']);
  assert.equal(selected.consider(candidate('requested-front', analyzeTarget(first, BOX)), confirmed).reason, 'selected');
  assert.equal(selected.consider(candidate('requested-left', analyzeTarget(second, BOX)), confirmed).reason, 'duplicate_view');
  assert.equal(selected.entries().length, 1);
});

test('a real JPEG reencoding of the same source does not establish another view', () => {
  // Grayscale bytes decoded from a Pillow JPEG quality=82 export of the texture
  // below. Embedded decoded pixels keep this test independent of browser codecs.
  const jpeg = Buffer.from('HjpCT2p8dZ2gusTgLjhVX3GDkqOxxdcjOUFfcnaLm7M3Ul10i6DEyeY2RldsgY2tuNEsN1Brdoyav77KNkRbZVpyjKnCxhw7SWOAm67O0zBNXXSWn7zJKUVYc5uXrMsgcJKsutU/W2mSoMTML0J0epivySc+Vn+MobrdJ1prfaWdp9QmVlyEnrTWOlVviKjFJDtjdpi41itLdoKlwuQ5U7fRLU9ykrHYMlJwhbffJFNyhbrPME5mmrnINVBwi6rewDhfd5e+Gz5glarQNkWJl7knSV2MvcgwUHmewyVAdIIzTneaxClYdZ++I0t3msElS3GTwCQ9dJfAH0pnlbncSkp1lrooXXqr0UFcibPaQ3GZvidccqDbNF6UrdhMYpW+WpTIJVqNot09YqrLOWKJtyNMf6vXQGuWwiZkiLIlSnqLqhlWfqrZS4Kt3zxsqNFFdqrSNXWdyj9umckvdpbDMJrVQXeZ6EZ/tRlCmLc1WZDDIWGjxzNxndNEarHMRIekwDRaqtI2hqwkTKO8Mmec1U+QtyJcm8o4d5/iV4W8MG/ZToW5P3ehIFmcuUOErzFeh9dIgsIwb6TbWYXBPW6sGTVvuCBgmt5VisdDfb4pbrUsY5nVUobMQHysO2utI2CiVZDNUI3IR4PJQ3+/MIC9LHCvMXKiNGGrJWGh4VeU4FBxsCt8rzt5tDl3uDl+wUF9ukOLv0SKvkmHxkyFz1SM1IXdUY/jY6UrcLo8ecM+hsVchc9UjyljqixzsTyEtEaEqDhrx0WJ012iH2+oOJDJW5EqbLg+ecNRld1jlih+tknKTpcub7RGhttctUN5xWCSJXC1PY7baqM3c89bpCl/rithwkSSNGvHTpYhd75JjDJ8u1WTL3XEVJ8qgb5Hoh55OpIweLdQoSyE2GKoQpkmaMRIrSyCy2ukPZMYadZOmDppqzuQNXPXZLVAnjKLzWPDS6YtktBtxVKhRoznaMVfpHPXZsBLsziVKIrVbrlgs0CgKonZe8VftlGTP4Ypecdpmi2MJX/JbstfulCpS5o0kR6K23HEa7FisUioQI0ti967UrdBoVSpK5I2jDB+JH3cedFywnHAXqxVtUCoSaQyms11xHLKXsR5y2PJYdBcxF3CZLBrtFq/ZbFlt1i1WLdWLpY/jkePSZJAoEGnTrBNsUe7WrZbuF21Z75mwm7Ab8dMplK+XcRw0W/lgSCBHJM4ojelR6ZcuGK/adFp2IgZkHLSeCKKK5s+q0a1XOB3130lmTelTK1dxG7idSWNJqU2gDuSS7hZyHjlgkCZP7NoynoZlC+WWa9dynUfkjWsTcGkTMdv1Y03pE/JaOGKOqFWxGzfjUKlVMxxJJBDqFTJcg==', 'base64');
  assert.equal(jpeg.length, 32 * 32);
  const original = paint(frame(64), [0.25, 0.25, 0.75, 0.75], texture);
  const encoded = paint(frame(64), [0.25, 0.25, 0.75, 0.75], (x, y) => jpeg[y * 32 + x]);
  const box = [0.25, 0.25, 0.75, 0.75];
  const first = analyzeTarget(original, box);
  const second = analyzeTarget(encoded, box);
  assert.ok(fingerprintDistance(first.fingerprint, second.fingerprint) <= DEFAULT_QUALITY_THRESHOLDS.duplicateDistance);
  const selected = new ReferenceShortlist(['front', 'left'], { thresholds: { minSharpness: 0 } });
  assert.equal(selected.consider(candidate('front', first), confirmed).reason, 'selected');
  assert.equal(selected.consider(candidate('left', second), confirmed).reason, 'duplicate_view');
});

test('one best frame per prompt replaces only when target quality improves', () => {
  const quality = analyzeTarget(sharp(), BOX);
  const weaker = { ...quality, sharpness: quality.sharpness / 2 };
  const selected = new ReferenceShortlist(['front']);
  assert.equal(selected.consider(candidate('front', weaker, 'weak'), confirmed).reason, 'selected');
  const result = selected.consider(candidate('front', quality, 'best'), confirmed);
  assert.equal(result.reason, 'improved');
  assert.equal(result.replaced.id, 'weak');
  assert.equal(selected.consider(candidate('front', weaker, 'weaker-again'), confirmed).reason, 'keep_previous');
  assert.equal(selected.entries()[0].id, 'best');
  assert.equal(selected.entries().length, 1);
});

test('unconfirmed, lost, and occluded targets cannot enter the shortlist', () => {
  const selected = new ReferenceShortlist(['front']);
  const entry = candidate('front', analyzeTarget(sharp(), BOX));
  assert.equal(selected.consider(entry).reason, 'target_lost');
  assert.equal(selected.consider(entry, { targetConfirmed: false }).reason, 'target_lost');
  assert.equal(selected.consider(entry, { targetConfirmed: true, occluded: true }).reason, 'wait_for_clear_view');
  assert.equal(selected.consider(entry, { targetConfirmed: true, occluded: null }).reason, 'wait_for_clear_view');
  assert.deepEqual(selected.entries(), []);
});

test('shortlist remains bounded, preserves prompt order, and copies measurements', () => {
  const steps = ['front', 'left', 'right', 'back'];
  const selected = new ReferenceShortlist(steps);
  for (let index = steps.length - 1; index >= 0; index--) {
    const quality = analyzeTarget(sharp(index + 1), BOX);
    assert.equal(selected.consider(candidate(steps[index], quality), confirmed).accepted, true);
    quality.sharpness = 0;
  }
  steps.push('unplanned');
  assert.deepEqual(selected.entries().map(entry => entry.stepId), ['front', 'left', 'right', 'back']);
  assert.ok(selected.entries().every(entry => entry.quality.sharpness > 0));
  assert.equal(selected.consider(candidate('unplanned', analyzeTarget(sharp(), BOX)), confirmed).reason, 'unknown_step');
  selected.entries().pop();
  assert.equal(selected.entries().length, 4);
  selected.clear();
  assert.deepEqual(selected.entries(), []);
});

test('malformed geometry, masks, quality, capacity, and options fail closed', () => {
  assert.throws(() => analyzeTarget(frame(), [-1, 0, 1, 1]), RangeError);
  assert.throws(() => analyzeTarget(frame(), [0, 0, NaN, 1]), RangeError);
  assert.throws(() => analyzeTarget(frame(), [0.8, 0, 0.2, 1]), RangeError);
  assert.throws(() => analyzeTarget({ width: 10, height: 10, data: new Uint8Array(4) }, BOX), TypeError);
  assert.throws(() => analyzeTarget(frame(), BOX, new Uint8Array(1)), TypeError);
  for (const steps of [[], ['same', 'same'], ['a', 'b', 'c', 'd', 'e'], ['']]) {
    assert.throws(() => new ReferenceShortlist(steps), RangeError);
  }
  assert.throws(() => new ReferenceShortlist(['front'], { thresholds: { minSharpness: NaN } }), RangeError);
  assert.throws(() => fingerprintDistance('1', '0'), TypeError);
  const selected = new ReferenceShortlist(['front']);
  assert.equal(selected.consider(null, confirmed).accepted, false);
  const quality = analyzeTarget(sharp(), BOX);
  for (const patch of [{ sharpness: NaN }, { targetFraction: 2 }, { fingerprint: 'not-a-hash' }]) {
    assert.equal(selected.consider(candidate('front', { ...quality, ...patch }), confirmed).reason, 'invalid_quality');
  }
});

test('appearance continuity follows a small translation and rejects replacement/occlusion', () => {
  const original = sharp();
  const template = createTargetTemplate(original, BOX);
  assert.equal(template.usable, true);
  assert.equal(trackTarget(original, template).found, true);
  const movedBox = [0.26, 0.2, 0.86, 0.8];
  const moved = paint(frame(), movedBox, texture);
  const result = trackTarget(moved, template);
  assert.equal(result.found, true);
  assert.ok(Math.abs(result.box[0] - movedBox[0]) < 0.001);
  assert.ok(result.confidence > 0.99);
  assert.equal(trackTarget(sharp(17), template).found, false);
  assert.equal(trackTarget(paint(sharp(), [0.2, 0.2, 0.8, 0.65], () => 120), template).found, false);
  assert.equal(trackTarget(frame(), template).found, false);
});

test('low-texture templates do not invent a tracking lock', () => {
  const image = frame();
  const template = createTargetTemplate(image, BOX);
  assert.equal(template.usable, false);
  assert.equal(trackTarget(image, template).reason, 'insufficient_texture');
  assert.throws(() => trackTarget(image, template, BOX, { minConfidence: NaN }), RangeError);
});
