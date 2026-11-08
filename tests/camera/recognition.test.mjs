import { test } from 'node:test';
import assert from 'node:assert/strict';
import { motion, normalize, rank, signature, Stability, toTensor, validateCatalog } from '../../app/modules/live_camera/static/recognition.mjs';

const vector = (x = 1, y = 0) => [x, y, ...Array(382).fill(0)];
const policy = { candidate_floor: 0.55, candidate_margin: 0.04, stable_frames: 3, global_weight: 0.75, calibrated: false };
test('ranking combines scores within a view before selecting the best view', () => {
  const objects = [
    { id: 'a', views: [{ vectors: [vector(0.8), vector(0.8)] }, { vectors: [vector(0.2), vector(1)] }] },
    { id: 'b', views: [{ vectors: [vector(0.82), vector(0.82)] }] },
  ];
  assert.equal(rank(vector(), objects, policy)[0].id, 'b');
});
test('normalization rejects nonfinite, wrong-size and empty descriptors', () => {
  for (const value of [[], Array(384).fill(0), vector(NaN), vector(Infinity)]) assert.throws(() => normalize(value));
});
test('stability clears old identities on movement, uncertainty, switches and long gaps', () => {
  const state = new Stability(policy);
  const a = [{ id: 'a', score: 0.9 }, { id: 'b', score: 0.6 }];
  assert.equal(state.observe(a, 100), null);
  assert.equal(state.observe(a, 600), null);
  assert.equal(state.observe(a, 1100), 'a');
  assert.equal(state.observe(a, 1200, true), null);
  assert.equal(state.observe(a, 1300), null);
  assert.equal(state.observe([{ id: 'a', score: 0.6 }, { id: 'b', score: 0.59 }], 1400), null);
  for (const t of [1500, 2000, 2500]) state.observe(a, t);
  assert.equal(state.observe([{ id: 'b', score: 0.9 }], 2600), null);
  assert.equal(state.observe(a, 6000), null);
  assert.equal(state.observe([], 6100), null);
});
test('established matches survive a brief weak view without extending stale evidence', () => {
  const state = new Stability(policy);
  const strong = [{ id: 'a', score: 0.9 }];
  const weak = [{ id: 'a', score: 0.5 }];
  for (const t of [100, 600, 1100]) state.observe(strong, t);
  assert.equal(state.observe(weak, 1600), 'a');
  assert.equal(state.holding, true);
  assert.equal(state.observe(strong, 2100), 'a');
  assert.equal(state.holding, false);
  assert.equal(state.observe([], 2600), 'a');
  assert.equal(state.observe([], 3100), 'a');
  assert.equal(state.current(3601), null);
  assert.equal(state.observe([], 3601), null);
  assert.equal(state.observe(strong, 3700), null);
});
test('held matches clear immediately for a different object or large movement', () => {
  const state = new Stability(policy);
  const strong = [{ id: 'a', score: 0.9 }];
  for (const t of [100, 600, 1100]) state.observe(strong, t);
  assert.equal(state.observe([{ id: 'b', score: 0.9 }], 1200), null);
  for (const t of [1500, 2000, 2500]) state.observe(strong, t);
  assert.equal(state.observe(strong, 2600, true), null);
  assert.equal(state.current(2600), null);
});
test('rejected or ambiguous competing identities hold briefly without refreshing verification', () => {
  const state = new Stability(policy);
  const strong = [{ id: 'b', score: 0.9 }, { id: 'a', score: 0 }];
  for (const t of [100, 600, 1100]) state.observe(strong, t);
  assert.equal(state.observe([{ id: 'a', score: 0 }, { id: 'b', score: 0 }], 1600), 'b');
  assert.equal(state.observe([{ id: 'a', score: 0.6 }, { id: 'b', score: 0.59 }], 2100), 'b');
  assert.equal(state.lastConfirmedAt, 1100);
  assert.equal(state.holding, true);
  assert.equal(state.observe([{ id: 'a', score: 0.5 }], 2601), null);
  assert.equal(state.observe(strong, 2700), null);
  assert.equal(state.observe(strong, 3200), null);
  assert.equal(state.observe(strong, 3700), 'b');
});
test('RGB input uses NCHW ImageNet normalization and frame motion is detected', () => {
  const rgba = new Uint8ClampedArray(224 * 224 * 4).fill(255);
  const tensor = toTensor(rgba);
  assert.equal(tensor.length, 3 * 224 * 224);
  assert.ok(Math.abs(tensor[0] - (1 - 0.485) / 0.229) < 0.00001);
  assert.equal(motion([0, 0], [1, 1]), 1);
});
test('catalog refuses expired references, cross-location identities and automatic policies', () => {
  const catalog = {
    version: 'camera-server-v1', processing: 'server', reference_version: 'a'.repeat(64), automatic_identification: false,
    expires_at: '2099-01-01', model: { dimensions: 384, input_size: 224, sha256: 'a'.repeat(64) },
    location: { id: 'a'.repeat(24) }, policy,
    objects: ['b', 'c'].map(id => ({ id: id.repeat(24), location_id: 'a'.repeat(24), name: id, sources: [], views: [{ vectors: [vector()] }] })),
  };
  assert.equal(validateCatalog(structuredClone(catalog)).objects.length, 2);
  const fullVenue = structuredClone(catalog);
  fullVenue.objects = Array.from({length: 500}, (_, index) => ({
    ...structuredClone(catalog.objects[0]), id: index.toString(16).padStart(24, '0'),
  }));
  assert.equal(validateCatalog(fullVenue).objects.length, 500);
  fullVenue.objects.push({...fullVenue.objects[0], id: 'f'.repeat(24)});
  assert.throws(() => validateCatalog(fullVenue));
  for (const alter of [
    c => { c.expires_at = '2000-01-01'; },
    c => { c.objects[0].location_id = 'd'.repeat(24); },
    c => { c.automatic_identification = true; },
    c => { c.reference_version = 'invalid'; },
    c => { c.policy.candidate_floor = -1; },
  ]) { const copy = structuredClone(catalog); alter(copy); assert.throws(() => validateCatalog(copy)); }
});


function texturedObject(shift = 0) {
  const size = 224, rgba = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const sx = x - shift;
      const inside = sx >= 35 && sx < 189 && y >= 35 && y < 189;
      const value = inside ? (((sx >> 2) + (y >> 2)) % 2 ? 250 : 90) : 20;
      const i = (y * size + x) * 4;
      rgba[i] = rgba[i + 1] = rgba[i + 2] = value;
      rgba[i + 3] = 255;
    }
  }
  return rgba;
}

test('small hand movement on a detailed object permits three-frame acquisition', () => {
  const frames = [0, 6, 0, 6].map(shift => signature(texturedObject(shift)));
  const state = new Stability(policy);
  let result;
  for (let index = 0; index < frames.length; index++) {
    const moved = index > 0 && motion(frames[index - 1], frames[index]) > 0.09;
    result = state.observe([{id: 'santa', score: 0.9}], 100 + index * 500, moved);
  }
  assert.equal(result, 'santa');
});

test('movement away from a textured object still resets acquisition', () => {
  const before = signature(texturedObject());
  const after = signature(texturedObject(80));
  assert.ok(motion(before, after) > 0.09);
  const state = new Stability(policy);
  state.observe([{id: 'santa', score: 0.9}], 100);
  state.observe([{id: 'santa', score: 0.9}], 600);
  assert.equal(state.observe([{id: 'santa', score: 0.9}], 1100, true), null);
  assert.equal(state.count, 0);
});
