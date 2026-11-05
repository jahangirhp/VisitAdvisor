import assert from 'node:assert/strict';
import test from 'node:test';

// Import the pure seed validation without starting a browser worker or model.
globalThis.self = { fetch, location: new URL('http://localhost/admin/worker.mjs'),
  addEventListener() {} };
const { seedIsForeground } = await import('../../app/modules/admin/static/guided-segmenter.worker.mjs');
delete globalThis.self;

test('reject a confident mask belonging to a distant object', () => {
  const mask = new Uint8Array(100 * 100);
  for (let row = 20; row < 70; row += 1) {
    for (let col = 95; col < 100; col += 1) mask[row * 100 + col] = 255;
  }
  assert.equal(seedIsForeground(mask, 100, 100, { x: 0.5, y: 0.5 }), false);
});

test('allow a small boundary error and normalized image-edge taps', () => {
  const mask = new Uint8Array(100 * 100);
  mask[50 * 100 + 51] = 255;
  assert.equal(seedIsForeground(mask, 100, 100, { x: 0.5, y: 0.5 }), true);
  mask[99 * 100 + 99] = 255;
  assert.equal(seedIsForeground(mask, 100, 100, { x: 1, y: 1 }), true);
  assert.equal(seedIsForeground(new Uint8Array(10000), 100, 100, { x: 0, y: 0 }), false);
});
