import assert from 'node:assert/strict';
import test from 'node:test';
import { drawFrame, encodeFrame } from '../../app/modules/live_camera/static/capture.mjs';

test('live and replay crop the same central 87.5 percent of the short edge', () => {
  const calls = [];
  const pixels = new Uint8ClampedArray(224 * 224 * 4);
  const context = {
    drawImage: (...args) => calls.push(args),
    getImageData: () => ({ data: pixels }),
  };
  const source = {};
  assert.equal(drawFrame(context, source, 640, 480), pixels);
  assert.deepEqual(calls[0], [source, 110, 30, 420, 420, 0, 0, 224, 224]);
  assert.throws(() => drawFrame(context, source, 0, 480));
});

test('live and replay use JPEG quality 0.9', async () => {
  const blob = new Blob(['fixture'], { type: 'image/jpeg' });
  const canvas = { toBlob: (callback, type, quality) => {
    assert.equal(type, 'image/jpeg');
    assert.equal(quality, 0.9);
    callback(blob);
  } };
  assert.equal(await encodeFrame(canvas), blob);
});

test('detail frames preserve the same field of view at 512 pixels', () => {
  const calls = [];
  const context = { drawImage: (...args) => calls.push(args), getImageData: (...args) => {
    assert.deepEqual(args, [0, 0, 512, 512]);
    return { data: new Uint8ClampedArray(512 * 512 * 4) };
  } };
  const source = {};
  drawFrame(context, source, 640, 480, 512);
  assert.deepEqual(calls[0], [source, 110, 30, 420, 420, 0, 0, 512, 512]);
  assert.throws(() => drawFrame(context, source, 640, 480, 4096));
});
