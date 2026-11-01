import assert from 'node:assert/strict';
import test from 'node:test';
import { Stability, sameRegion, validBox } from '../../app/modules/live_camera/static/recognition.mjs';

const policy = { candidate_floor: 0.55, candidate_margin: 0.04, stable_frames: 3, minimum_stable_ms: 600,
  profiles: { planar: { floor: 0.85, margin: 0.1, enabled: true }, multiview: { floor: 0.8, margin: 0.1, enabled: true } } };
const candidate = { id: 'sculpture', score: 0.9, profile: 'multiview', coverage_ready: true, bbox: [0.1, 0.1, 0.4, 0.7], view_index: 0 };

test('rapid duplicate responses do not establish a match before elapsed-time gate', () => {
  const state = new Stability(policy);
  assert.equal(state.observe([candidate], 100), null);
  assert.equal(state.observe([candidate], 110), null);
  assert.equal(state.observe([candidate], 120), null);
  assert.equal(state.observe([candidate], 700), 'sculpture');
});

test('disabled profiles and incomplete reference coverage never acquire', () => {
  const state = new Stability(policy);
  for (const time of [100, 500, 1000]) assert.equal(state.observe([{ ...candidate, coverage_ready: false }], time), null);
  const disabled = new Stability({ ...policy, profiles: { ...policy.profiles, multiview: { ...policy.profiles.multiview, enabled: false } } });
  for (const time of [100, 500, 1000]) assert.equal(disabled.observe([candidate], time), null);
});

test('different tracked regions require reacquisition even for the same catalog identity', () => {
  const state = new Stability(policy);
  for (const time of [100, 400, 700]) state.observe([candidate], time);
  assert.equal(state.current(700), 'sculpture');
  assert.equal(state.observe([{ ...candidate, bbox: [0.6, 0.1, 0.9, 0.7], view_index: 1 }], 800), null);
  assert.equal(state.count, 1);
  assert.equal(state.views.size, 1);
});

test('boxes reject invalid coordinates; full-frame fallback tolerates intermittent proposals', () => {
  assert.equal(validBox([0, 0, 1, 1]), true);
  for (const box of [[0, 0, Infinity, 1], [0.5, 0, 0.1, 1], [-1, 0, 1, 1], [0, 0, 1]]) assert.equal(validBox(box), false);
  assert.equal(sameRegion(candidate.bbox, [0, 0, 1, 1]), true);
  assert.equal(sameRegion(candidate.bbox, [0.6, 0.1, 0.9, 0.7]), false);
});
