// Actual browser Canvas crop/JPEG encoding; frames travel over stdout only.
import { chromium, webkit } from 'playwright';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const request = JSON.parse(readFileSync(0, 'utf8'));
const engines = { chromium, webkit };
if (!engines[request.browser] || !Array.isArray(request.paths)) throw new Error('Invalid frame replay');
const frameSize = request.frame_size ?? 224;
if (![224, 512].includes(frameSize)) throw new Error('Invalid replay frame size');
const capture = readFileSync(resolve('app/modules/live_camera/static/capture.mjs'), 'utf8');
const browser = await engines[request.browser].launch();
try {
  const page = await browser.newPage();
  await page.goto('about:blank');
  await page.evaluate(async source => {
    window.capture = await import(URL.createObjectURL(new Blob([source], { type: 'text/javascript' })));
  }, capture);
  for (const path of request.paths) {
    const result = await page.evaluate(async ({ bytes, frameSize }) => {
      const image = new Image();
      image.src = `data:application/octet-stream;base64,${bytes}`;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = frameSize;
      window.capture.drawFrame(canvas.getContext('2d'), image, image.naturalWidth, image.naturalHeight, frameSize);
      const blob = await window.capture.encodeFrame(canvas);
      if (!blob) throw new Error('Frame encoding failed');
      const data = new Uint8Array(await blob.arrayBuffer());
      return btoa(Array.from(data, byte => String.fromCharCode(byte)).join(''));
    }, { bytes: readFileSync(path).toString('base64'), frameSize });
    process.stdout.write(`${JSON.stringify({ path, jpeg: result })}\n`);
  }
} finally {
  await browser.close();
}
