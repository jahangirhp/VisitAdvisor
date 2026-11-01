import { test, expect } from '@playwright/test';
import { resolve } from 'node:path';

const catalog = {
  automatic_identification: false, reference_version: 'frozen-reference',
  location: { id: 'museum', name: 'Museum' }, policy: { frame_size: 512 },
  objects: [{ id: 'shiva', name: 'Shiva' }, { id: 'apollo', name: 'Apollo' }],
};
const photo = resolve('tests/data/visual/positive/6a9dd818332abe8469d55938-commons-29948991.jpg');

async function openLab(page) {
  await page.route('**/bundle/catalog.json', route => route.fulfill({ json: catalog }));
  await page.goto('/lab.html');
  await expect(page.locator('#photo')).toBeEnabled();
}

test('photo lab sends a bounded JPEG and honors server abstention at high scores', async ({ page }) => {
  let requests = 0;
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (url, options) => {
      if (String(url).includes('/recognize?')) {
        const bytes = new Uint8Array(await options.body.arrayBuffer());
        window.testUpload = { size: bytes.length, type: options.body.type, head: [...bytes.slice(0, 2)] };
      }
      return original(url, options);
    };
  });
  await page.route('**/recognize?*', async route => {
    requests++;
    expect(route.request().headers()['content-type']).toBe('image/jpeg');
    await route.fulfill({ json: {
      location_id: 'museum', reference_version: 'frozen-reference', automatic_identification: false,
      suggestion_allowed: false, inferenceMs: 120,
      candidates: [{ id: 'shiva', score: 0.99, reference_views: 1, coverage_ready: false }],
    } });
  });
  await openLab(page);
  await page.locator('#photo').setInputFiles(photo);
  await expect(page.locator('#decision')).toHaveText('Suggestion withheld');
  await expect(page.locator('#candidates')).toContainText('Shiva');
  await expect(page.locator('#candidates')).toContainText('view coverage incomplete');
  expect(requests).toBe(1);
  const upload = await page.evaluate(() => window.testUpload);
  expect(upload.size).toBeLessThanOrEqual(256 * 1024);
  expect(upload.type).toBe('image/jpeg');
  expect(upload.head).toEqual([255, 216]);
  expect(await page.locator('#frame').evaluate(canvas => [canvas.width, canvas.height])).toEqual([512, 512]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('a changed reference version requires loading references again', async ({ page }) => {
  await page.route('**/recognize?*', route => route.fulfill({ status: 409, json: { error: 'REFERENCES_CHANGED' } }));
  await openLab(page);
  await page.locator('#photo').setInputFiles(photo);
  await expect(page.locator('#retry')).toBeVisible();
  await expect(page.locator('#photo')).toBeDisabled();
  await expect(page.locator('#result')).toBeHidden();
});

test('unsupported files never reach recognition', async ({ page }) => {
  let requests = 0;
  await page.route('**/recognize?*', route => { requests++; return route.abort(); });
  await openLab(page);
  await page.locator('#photo').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') });
  await expect(page.locator('#status')).toContainText('Choose a JPEG');
  expect(requests).toBe(0);
});
