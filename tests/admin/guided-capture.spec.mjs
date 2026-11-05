import { test, expect } from '@playwright/test';

const venueId = '1'.repeat(24), objectId = '2'.repeat(24), sourceId = '3'.repeat(24);
const photoId = '4'.repeat(24);
const modelHash = 'e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25';

async function mockAdmin(page) {
  const requests = [], uploads = [];
  let created = false;
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    requests.push(request);
    expect(request.headers().authorization).toBe('Bearer demo-token');
    if (path === '/api/v1/admin/venues') return route.fulfill({ json: [{ id: venueId, name: 'Museum' }] });
    if (path === `/api/v1/admin/venues/${venueId}/objects`) {
      if (request.method() === 'POST') {
        created = true;
        expect(request.postDataJSON()).toEqual({ name: 'Guided artwork', type: 'painting' });
        return route.fulfill({ status: 201, json: { id: objectId, name: 'Guided artwork', source_ids: [sourceId] } });
      }
      return route.fulfill({ json: { objects: created ? [{ id: objectId, name: 'Guided artwork',
        type: 'painting', prepared: 0, pending: uploads.length, missing: 2, target: 3, needs_photos: true }] : [] } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}/photos`) {
      expect(request.method()).toBe('POST');
      const form = await new Request(request.url(), { method: 'POST', body: request.postDataBuffer(),
        headers: request.headers() }).formData();
      uploads.push(form);
      return route.fulfill({ status: 201, json: { id: 'photo', status: 'saved_for_preparation' } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}`) {
      return route.fulfill({ json: { id: objectId, name: 'Guided artwork', type: 'painting',
        prepared: 0, pending: uploads.length, target: 3, sources: [], photos: uploads.length ? [{ _id: photoId,
          viewpoint: 'front', foreground_mask: { width: 640, height: 480, format: 'binary-png-v1' } }] : [], views: [], stale: false } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}/photos/${photoId}`
      || path === `/api/v1/admin/venues/${venueId}/objects/${objectId}/photos/${photoId}/mask`) {
      const file = uploads[0].get(path.endsWith('/mask') ? 'foreground_mask' : 'image');
      return route.fulfill({ contentType: file.type, body: Buffer.from(await file.arrayBuffer()) });
    }
    throw new Error(`Unexpected API request: ${request.method()} ${path}`);
  });
  return { requests, uploads };
}

async function mockCamera(page, { modelFailure = false, lateCamera = false } = {}) {
  await page.addInitScript(({ modelFailure, lateCamera, modelHash }) => {
    const fixture = window.__cameraFixture = { requests: 0, stopped: 0, segments: 0,
      workerCreations: 0, workerTerminations: 0, variant: 0, streams: [], pendingCamera: null };
    function makeStream() {
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const context = canvas.getContext('2d');
      function draw() {
        const image = context.createImageData(canvas.width, canvas.height);
        for (let y = 0; y < canvas.height; y++) {
          for (let x = 0; x < canvas.width; x++) {
            const index = (y * canvas.width + x) * 4;
            const inTarget = x >= 128 && x < 512 && y >= 96 && y < 384;
            const value = inTarget ? 32 + ((x * 17 + y * 29 + x * y * (3 + fixture.variant)) % 192) : 120;
            image.data[index] = image.data[index + 1] = image.data[index + 2] = value;
            image.data[index + 3] = 255;
          }
        }
        context.putImageData(image, 0, 0);
      }
      draw();
      const stream = canvas.captureStream(15), timer = setInterval(draw, 65);
      for (const track of stream.getTracks()) {
        const stop = track.stop.bind(track);
        track.stop = () => { fixture.stopped++; clearInterval(timer); stop(); };
      }
      fixture.streams.push(stream);
      return stream;
    }
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => {
        fixture.requests++;
        if (lateCamera) return new Promise(resolve => { fixture.pendingCamera = () => resolve(makeStream()); });
        return makeStream();
      },
    } });
    window.Worker = class {
      constructor() { this.terminated = false; fixture.workerCreations++; }
      postMessage(message) {
        setTimeout(() => {
          if (this.terminated) { message.image?.close(); return; }
          if (message.type === 'init') {
            this.onmessage?.({ data: modelFailure ? { type: 'error', id: message.id } : {
              type: 'ready', id: message.id, model: { name: 'mediapipe-magic-touch-legacy-float32-1', sha256: modelHash },
            } });
            return;
          }
          if (message.type === 'segment') {
            fixture.segments++;
            const width = message.image.width, height = message.image.height;
            const mask = new Uint8Array(width * height);
            for (let y = Math.round(height * .2); y < Math.round(height * .8); y++) {
              for (let x = Math.round(width * .2); x < Math.round(width * .8); x++) mask[y * width + x] = 255;
            }
            this.onmessage?.({ data: { type: 'result', id: message.id, width, height,
              bbox: [.2, .2, .8, .8], confidence: .99, foregroundFraction: .36, mask: mask.buffer } });
            message.image.close();
          }
        }, 0);
      }
      terminate() { this.terminated = true; fixture.workerTerminations++; }
    };
  }, { modelFailure, lateCamera, modelHash });
}

async function openCapture(page, { waitForDialog = true } = {}) {
  await page.goto('/');
  await page.getByLabel('Admin token', { exact: true }).fill('demo-token');
  await page.getByRole('button', { name: 'Unlock library', exact: true }).click();
  await expect(page.locator('#workspace')).toBeVisible();
  await page.getByRole('button', { name: 'Add object', exact: true }).click();
  if (waitForDialog) await expect(page.locator('#guidedDialog')).toBeVisible();
}

async function startAndSelect(page) {
  await page.locator('#guidedStart').click();
  await expect(page.locator('#guidedStatus')).toContainText('Tap the object', { timeout: 10000 });
  await page.locator('#guidedTarget').click();
  await expect(page.locator('#guidedStatus')).toContainText('Clear photo selected', { timeout: 10000 });
  await expect(page.locator('#guidedFinish')).toBeEnabled();
}

test('Add object opens guided setup directly without starting a camera or a legacy photo page', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await openCapture(page);
  await expect(page.locator('#guidedSetup')).toBeVisible();
  await expect(page.locator('#guidedStart')).toBeEnabled();
  await expect(page.locator('#choosePhotos')).toBeVisible();
  await expect(page.locator('#takePhoto')).toBeVisible();
  await expect(page.locator('#photoStep, #captureEmpty, #nextCapture, #captureTitle, #detailsPhotoSummary')).toHaveCount(0);
  expect(await page.evaluate(() => window.__cameraFixture.requests)).toBe(0);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(0);
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
  await page.locator('#guidedClose').click();
  await expect(page.locator('#collection')).toBeVisible();
  await expect(page.locator('#addObject')).toBeHidden();
});

test('fallback file chooser cancellation and invalid photos return to usable guided setup', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await openCapture(page);
  const [cancelledChooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Choose photos', exact: true }).click(),
  ]);
  expect(cancelledChooser.isMultiple()).toBe(true);
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await page.locator('#libraryInput').evaluate(input => input.dispatchEvent(new Event('cancel')));
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#guidedSetup')).toBeVisible();
  const [invalidChooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Choose photos', exact: true }).click(),
  ]);
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await invalidChooser.setFiles({ name: 'bad.heic', mimeType: 'image/heic', buffer: Buffer.from('not an image') });
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#guidedSetup')).toBeVisible();
  await expect(page.locator('#guidedUploadStatus')).toBeVisible();
  await expect(page.locator('#guidedUploadStatus')).toContainText("Couldn't open this photo");
  expect(await page.evaluate(() => window.__cameraFixture.requests)).toBe(0);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(0);
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
  await expect(page.locator('.capturePhoto')).toHaveCount(0);
});

test('guided photos stay local until save and require an explicitly reviewed angle', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await openCapture(page);
  await page.locator('#guidedProfile').selectOption('planar');
  await startAndSelect(page);
  await expect(page.locator('#guidedPrompt')).toContainText('1 of 3');
  await page.locator('#guidedFinish').click();
  await expect(page.locator('#guidedReview')).toBeVisible();
  await expect(page.locator('.guidedPhoto')).toHaveCount(1);
  await expect(page.getByAltText('Original selected photo')).toBeVisible();
  await expect(page.getByAltText('Segmented target preview')).toBeVisible();
  await page.screenshot({ path: 'test-results/guided-review-desktop.png', fullPage: true });
  await expect.poll(() => page.evaluate(() => window.__cameraFixture.stopped)).toBeGreaterThan(0);
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
  await page.locator('#guidedUse').click();
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await expect(page.locator('.capturePhoto')).toHaveCount(1);
  await page.getByRole('button', { name: 'Show isolated target', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Show original photo', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Show original photo', exact: true }).click();
  await expect(page.getByLabel('Photo 1 angle', { exact: true })).toHaveValue('');
  await expect(page.locator('#detailsStep')).toBeVisible();
  await page.getByLabel('Object name', { exact: true }).fill('Guided artwork');
  await page.getByLabel('Object type', { exact: true }).selectOption('painting');
  await page.getByRole('button', { name: 'Save object', exact: true }).click();
  await expect(page.locator('#detailsStep')).toBeVisible();
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
  await page.getByLabel('Photo 1 angle', { exact: true }).selectOption('front');
  await page.getByRole('button', { name: 'Save object', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Guided artwork saved with 1 photo');
  expect(api.uploads).toHaveLength(1);
  const form = api.uploads[0], image = form.get('image'), mask = form.get('foreground_mask');
  expect(image.type).toBe('image/jpeg');
  expect(image.size).toBeGreaterThan(1000);
  expect(form.get('viewpoint')).toBe('front');
  expect(mask.type).toBe('image/png');
  expect(mask.size).toBeGreaterThan(100);
  expect(Buffer.from(await mask.arrayBuffer()).subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const metadata = JSON.parse(form.get('capture_metadata'));
  expect(metadata).toMatchObject({ version: 'guided-capture-v1', profile: 'planar', requested_view: 'front',
    viewpoint_verified: true, target_box: [.2, .2, .8, .8],
    segmentation: { model: 'mediapipe-magic-touch-legacy-float32-1', model_hash: modelHash } });
  expect(metadata.quality.sharpness).toBeGreaterThan(40);
  await page.getByRole('button', { name: 'Show target mask', exact: true }).click();
  await expect(page.getByAltText('Saved binary target mask')).toBeVisible();
  await page.getByRole('button', { name: 'Hide target mask', exact: true }).click();
  await expect(page.getByAltText('Saved binary target mask')).toBeHidden();
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(2);
});

test('mobile guided live and review controls remain reachable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAdmin(page); await mockCamera(page); await openCapture(page); await startAndSelect(page);
  const noOverflow = () => page.evaluate(() => {
    const dialog = document.getElementById('guidedDialog');
    return document.documentElement.scrollWidth <= innerWidth && dialog.scrollWidth <= dialog.clientWidth;
  });
  expect(await noOverflow()).toBe(true);
  await page.screenshot({ path: 'test-results/guided-live-mobile.png' });
  for (const id of ['guidedBlocked', 'guidedNext', 'guidedFinish']) {
    await page.locator(`#${id}`).scrollIntoViewIfNeeded();
    expect(await page.locator(`#${id}`).evaluate(button => {
      const rect = button.getBoundingClientRect(), dialog = document.getElementById('guidedDialog').getBoundingClientRect();
      return rect.left >= dialog.left && rect.right <= dialog.right && rect.top >= dialog.top && rect.bottom <= dialog.bottom;
    })).toBe(true);
  }
  await page.screenshot({ path: 'test-results/guided-live-mobile-controls.png' });
  await page.locator('#guidedFinish').click();
  await expect(page.locator('#guidedReview')).toBeVisible();
  await expect(page.locator('.guidedPhoto')).toHaveCount(1);
  expect(await noOverflow()).toBe(true);
  await page.locator('#guidedUse').scrollIntoViewIfNeeded();
  await expect(page.locator('#guidedUse')).toBeInViewport();
  await page.screenshot({ path: 'test-results/guided-review-mobile.png' });
});

test('changing a requested prompt does not accept the same image as another angle', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await openCapture(page); await startAndSelect(page);
  await page.locator('#guidedNext').click();
  await expect(page.locator('#guidedPrompt')).toContainText('left side');
  await page.locator('#guidedTarget').click();
  await expect(page.locator('#guidedStatus')).toContainText('looks similar', { timeout: 10000 });
  await expect(page.locator('#guidedNext')).toBeDisabled();
  await page.locator('#guidedFinish').click();
  await expect(page.locator('.guidedPhoto')).toHaveCount(1);
  await page.getByRole('button', { name: 'Remove this photo', exact: true }).click();
  await expect(page.locator('.guidedPhoto')).toHaveCount(0);
  await expect(page.locator('#guidedUse')).toBeDisabled();
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
});

test('closing while camera permission is pending stops the late camera stream', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page, { lateCamera: true }); await openCapture(page);
  await page.locator('#guidedStart').click();
  await expect.poll(() => page.evaluate(() => window.__cameraFixture.requests)).toBe(1);
  await page.locator('#guidedClose').click();
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await page.evaluate(() => window.__cameraFixture.pendingCamera());
  await expect.poll(() => page.evaluate(() => window.__cameraFixture.stopped)).toBe(1);
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
});

test('model failure stops the camera and offers the existing photo capture fallback', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page, { modelFailure: true }); await openCapture(page);
  await page.locator('#guidedStart').click();
  await expect(page.locator('#guidedError')).toContainText('use Take a photo');
  await expect(page.locator('#guidedStart')).toBeEnabled();
  await expect.poll(() => page.evaluate(() => window.__cameraFixture.stopped)).toBeGreaterThan(0);
  await expect(page.locator('#takePhoto')).toBeVisible();
  await expect(page.locator('#choosePhotos')).toBeVisible();
  await expect(page.locator('#takePhoto')).toBeEnabled();
  await expect(page.locator('#choosePhotos')).toBeEnabled();
  await page.locator('#guidedClose').click();
  await expect(page.locator('#collection')).toBeVisible();
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
});

test('hiding the page closes guided capture and stops all camera tracks', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await openCapture(page);
  await page.locator('#guidedStart').click();
  await expect(page.locator('#guidedStatus')).toContainText('Tap the object', { timeout: 10000 });
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
  expect(await page.evaluate(() => window.__cameraFixture.workerTerminations)).toBeGreaterThan(0);
  expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
});

test('double launch during a pending module import creates one camera controller', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page);
  let releaseModule, markRequested;
  const released = new Promise(resolve => { releaseModule = resolve; });
  const requested = new Promise(resolve => { markRequested = resolve; });
  await page.route('**/guided-capture.mjs', async route => {
    markRequested();
    await released;
    await route.continue();
  });
  await openCapture(page, { waitForDialog: false });
  await requested;
  await page.locator('#openCapture').evaluate(button => button.click());
  releaseModule();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await page.locator('#guidedStart').click();
  await expect(page.locator('#guidedStatus')).toContainText('Tap the object', { timeout: 10000 });
  expect(await page.evaluate(() => window.__cameraFixture.requests)).toBe(1);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(1);
  await page.locator('#guidedClose').click();
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
});

for (const cancelWith of ['close', 'visibility']) {
  test(`cancelling a pending photo import by ${cancelWith} cannot alter a new session`, async ({ page }) => {
    const api = await mockAdmin(page); await mockCamera(page); await openCapture(page); await startAndSelect(page);
    await page.locator('#guidedFinish').click();
    await expect(page.locator('#guidedReview')).toBeVisible();
    await page.evaluate(() => {
      const digest = crypto.subtle.digest.bind(crypto.subtle);
      const state = window.__digestFixture = { calls: 0, released: false, release: null };
      crypto.subtle.digest = (...args) => {
        crypto.subtle.digest = digest;
        state.calls++;
        return new Promise(resolve => {
          state.release = async () => { resolve(await digest(...args)); state.released = true; };
        });
      };
    });
    await page.locator('#guidedUse').click();
    await expect.poll(() => page.evaluate(() => window.__digestFixture.calls)).toBe(1);
    if (cancelWith === 'close') await page.locator('#guidedClose').click();
    else await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    });
    await expect(page.locator('#guidedDialog')).not.toBeVisible();
    await page.getByRole('button', { name: 'Add object', exact: true }).click();
    await expect(page.locator('#guidedDialog')).toBeVisible();
    await expect(page.locator('#guidedSetup')).toBeVisible();
    await page.evaluate(() => window.__digestFixture.release());
    await expect.poll(() => page.evaluate(() => window.__digestFixture.released)).toBe(true);
    await expect(page.locator('#guidedDialog')).toBeVisible();
    await expect(page.locator('#guidedSetup')).toBeVisible();
    await expect(page.locator('.capturePhoto')).toHaveCount(0);
    await expect(page.locator('#captureCount')).toContainText('No photos added');
    expect(api.requests.filter(request => request.method() !== 'GET')).toHaveLength(0);
  });
}

test('unsupported crypto capabilities use the fallback before starting any camera', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page);
  await page.addInitScript(() => Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: undefined }));
  await openCapture(page);
  await page.locator('#guidedStart').click();
  await expect(page.locator('#guidedError')).toContainText('supported browser and HTTPS');
  await expect(page.locator('#guidedStart')).toBeEnabled();
  expect(await page.evaluate(() => window.__cameraFixture.requests)).toBe(0);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(0);
  await expect(page.locator('#takePhoto')).toBeVisible();
  await expect(page.locator('#takePhoto')).toBeEnabled();
  await page.locator('#guidedClose').click();
  await expect(page.locator('#collection')).toBeVisible();
});
