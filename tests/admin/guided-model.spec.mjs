import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const modelHash = 'e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25';
const assetTypes = {
  'vision_bundle.mjs': 'text/javascript',
  'magic_touch.tflite': 'application/octet-stream',
  'wasm/vision_wasm_module_internal.js': 'text/javascript',
  'wasm/vision_wasm_module_internal.wasm': 'application/wasm',
};

test('actual local model isolates the selected target without external requests', async ({ context, page, baseURL }) => {
  test.skip(process.env.RUN_GUIDED_MODEL !== '1',
    'Opt in with RUN_GUIDED_MODEL=1 after python -m scripts.prepare_guided_capture.');
  test.setTimeout(60000);
  const target = process.env.CAPTURE_SMOKE_URL || new URL('/', baseURL).href;
  const origin = new URL(target).origin;
  const external = [];
  context.on('request', request => {
    if (new URL(request.url()).origin !== origin) external.push(request.url());
  });
  if (!process.env.CAPTURE_SMOKE_URL) {
    // The usual admin suite uses a static server. Keep the runtime real while
    // serving locally prepared assets through that origin. An explicit URL uses
    // the application's actual asset routes and worker CSP instead.
    await context.route('**/admin/capture-assets/**', async route => {
      const name = new URL(route.request().url()).pathname.split('/admin/capture-assets/')[1];
      expect(Object.hasOwn(assetTypes, name)).toBe(true);
      const path = resolve('.local/guided-capture', name);
      expect(existsSync(path), 'Prepare the local capture model assets first.').toBe(true);
      await route.fulfill({ path, contentType: assetTypes[name] });
    });
  }
  await page.goto(target);
  const workerURL = new URL('guided-segmenter.worker.mjs', target).href;
  if (process.env.CAPTURE_SMOKE_URL) {
    const response = await context.request.get(workerURL);
    expect(response.ok()).toBe(true);
    expect(response.headers()['content-security-policy']).toContain("connect-src 'self'");
  }
  const output = await page.evaluate(async workerURL => {
    const worker = new Worker(workerURL, { type: 'module' });
    const pending = new Map();
    worker.onmessage = ({ data }) => {
      const item = pending.get(data.id);
      if (item) { clearTimeout(item.timer); pending.delete(data.id); item.resolve(data); }
    };
    worker.onerror = () => {
      for (const item of pending.values()) {
        clearTimeout(item.timer); item.reject(new Error('Capture worker failed.'));
      }
      pending.clear();
    };
    const request = (message, transfer = []) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Capture worker timed out.')), 30000);
      pending.set(message.id, { resolve, reject, timer });
      worker.postMessage(message, transfer);
    });
    try {
      const ready = await request({ type: 'init', id: 1 });
      if (ready.type !== 'ready') return { ready, frames: [] };
      const frames = [];
      for (let index = 0; index < 3; index++) {
        const canvas = new OffscreenCanvas(512, 512);
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ddd'; ctx.fillRect(0, 0, 512, 512);
        ctx.fillStyle = '#395164'; ctx.fillRect(160 + index, 80, 192, 360);
        ctx.fillStyle = '#d2d4d3'; ctx.fillRect(175 + index, 85, 160, 20);
        const image = canvas.transferToImageBitmap();
        const start = performance.now();
        const result = await request({ type: 'segment', id: 2 + index, image,
          point: { x: 0.5, y: 0.5 } }, [image]);
        const mask = result.mask ? new Uint8Array(result.mask) : [];
        frames.push({ ...result, mask: undefined, bytes: mask.length,
          seed: mask[256 * 512 + 256], background: mask[0], ms: performance.now() - start });
      }
      const closed = await request({ type: 'dispose', id: 5 });
      return { ready, frames, closed };
    } finally {
      worker.terminate();
    }
  }, workerURL);
  expect(output.ready.type).toBe('ready');
  expect(output.ready.model).toEqual({ name: 'mediapipe-magic-touch-legacy-float32-1', sha256: modelHash });
  expect(output.frames).toHaveLength(3);
  for (const frame of output.frames) {
    expect(frame.type).toBe('result');
    expect(frame.width).toBe(512); expect(frame.height).toBe(512);
    expect(frame.bytes).toBe(512 * 512);
    expect(frame.seed).toBe(255); expect(frame.background).toBe(0);
    expect(frame.bbox[0]).toBeGreaterThan(0.27); expect(frame.bbox[0]).toBeLessThan(0.36);
    expect(frame.bbox[2]).toBeGreaterThan(0.65); expect(frame.bbox[2]).toBeLessThan(0.74);
    expect(frame.foregroundFraction).toBeGreaterThan(0.20);
    expect(frame.foregroundFraction).toBeLessThan(0.35);
  }
  expect(output.closed.type).toBe('closed');
  expect(external).toEqual([]);
  console.log(`Actual local model frame times (ms): ${output.frames.map(frame => Math.round(frame.ms)).join(', ')}`);
});
