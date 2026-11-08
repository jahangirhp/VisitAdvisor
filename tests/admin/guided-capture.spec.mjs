import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { captureSubmittedForms, submittedForm, submittedBlob } from './submitted-form.mjs';

const venueId = '1'.repeat(24), objectId = '2'.repeat(24), sourceId = '3'.repeat(24);
const photoId = '4'.repeat(24);
const modelHash = 'e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25';

async function mockAdmin(page) {
  await captureSubmittedForms(page);
  const requests = [], uploads = [];
  let created = false, published = 0;
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
        type: 'painting', prepared: published, pending: uploads.length - published, missing: 2, target: 3, needs_photos: true }] : [] } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}/photos`) {
      expect(request.method()).toBe('POST');
      const form = await submittedForm(page, request);
      uploads.push(form);
      return route.fulfill({ status: 201, json: { id: 'photo', status: 'saved_for_preparation' } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}/save`) {
      published = uploads.length;
      return route.fulfill({ json: { prepared: published, set_id: 'saved-set' } });
    }
    if (path === `/api/v1/admin/venues/${venueId}/objects/${objectId}`) {
      return route.fulfill({ json: { id: objectId, name: 'Guided artwork', type: 'painting',
        prepared: published, pending: uploads.length - published, target: 3, sources: [], photos: uploads.length ? [{ _id: photoId,
          viewpoint: 'front', ...(uploads[0]?.get('foreground_mask') ? { foreground_mask: { width: 640, height: 480, format: 'binary-png-v1' } } : {}) }] : [], views: [], stale: false } });
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

async function mockCamera(page, { modelFailure = false, lateCamera = false, portrait = false } = {}) {
  await page.addInitScript(({ modelFailure, lateCamera, modelHash, portrait }) => {
    const fixture = window.__cameraFixture = { requests: 0, stopped: 0, segments: 0,
      workerCreations: 0, workerTerminations: 0, variant: 0, streams: [], pendingCamera: null,
      segmentDelay: 0, submitted: 0, points: [], segmentFailure: false, mode: 'normal' };
    function makeStream() {
      const canvas = document.createElement('canvas'); canvas.width = portrait ? 480 : 640; canvas.height = portrait ? 640 : 480;
      const context = canvas.getContext('2d');
      function draw() {
        const image = context.createImageData(canvas.width, canvas.height);
        for (let y = 0; y < canvas.height; y++) {
          for (let x = 0; x < canvas.width; x++) {
            const index = (y * canvas.width + x) * 4;
            const inTarget = x >= canvas.width * .2 && x < canvas.width * .8 && y >= canvas.height * .2 && y < canvas.height * .8;
            const texture = 32 + ((x * 17 + y * 29 + x * y * (3 + fixture.variant)) % 192);
            const value = !inTarget || fixture.mode === 'smooth' ? 120
              : fixture.mode === 'glare' && x < canvas.width * .5 ? 255
                : fixture.mode === 'corrected' && x < canvas.width * .5 ? 230 : texture;
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
        if (modelFailure) throw new DOMException('Denied', 'NotAllowedError');
        if (lateCamera) return new Promise(resolve => { fixture.pendingCamera = () => resolve(makeStream()); });
        return makeStream();
      },
    } });
    window.Worker = class { constructor() { fixture.workerCreations++; throw new Error('Guided capture must not require a worker.'); } };
  }, { modelFailure, lateCamera, modelHash, portrait });
}

async function openCapture(page, { waitForDialog = true, lastType = '' } = {}) {
  await page.goto('/');
  await page.getByLabel('Admin token', { exact: true }).fill('demo-token');
  await page.getByRole('button', { name: 'Unlock library', exact: true }).click();
  await expect(page.locator('#workspace')).toBeVisible();
  if (lastType) await page.locator('#newObjectType').evaluate((select, value) => { select.value = value; }, lastType);
  await page.getByRole('button', { name: 'Add object', exact: true }).click();
  if (waitForDialog) await expect(page.locator('#guidedDialog')).toBeVisible();
}

const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function mockAnalysis(page, { fail = false, mismatch = false, portrait = false, delayed = false, busyResponses = 0, limited = false } = {}) {
  const calls = [];
  let release;
  const ready = delayed ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  const width = portrait ? 480 : 640, height = portrait ? 640 : 480;
  const mask = Buffer.from(await page.evaluate(({ width, height }) => {
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d'); context.fillStyle = '#000'; context.fillRect(0, 0, width, height);
    context.fillStyle = '#fff'; context.fillRect(width * .2, height * .2, width * .6, height * .6);
    return canvas.toDataURL('image/png').split(',')[1];
  }, { width, height }), 'base64');
  await page.route('**/api/v1/admin/capture/analyze?*', async route => {
    const request = route.request(), query = Object.fromEntries(new URL(request.url()).searchParams);
    expect(request.headers().authorization).toBe('Bearer demo-token');
    expect(request.headers()['content-type']).toBe('image/jpeg');
    const bytes = await submittedBlob(page, request); expect(bytes.length).toBeLessThanOrEqual(1024 * 1024);
    calls.push({ query, bytes });
    if (calls.length <= busyResponses || limited) return route.fulfill({ status: 429, json: { error: { code: limited ? 'CAPTURE_ANALYSIS_LIMIT' : 'CAPTURE_ANALYSIS_BUSY', message: 'Analysis is busy.' } } });
    await ready;
    if (fail) return route.fulfill({ status: 503, json: { error: { message: 'Analysis is temporarily unavailable.' } } }).catch(() => {});
    await route.fulfill({ json: {
      version: 'server-capture-v1', session_id: query.session_id, frame_id: query.frame_id,
      source_sha256: mismatch ? 'b'.repeat(64) : digest(bytes), width, height,
      model: { name: 'facebook/sam2.1-hiera-tiny', revision: 'de431c4043854a71d8101e17995dfe596bf101a5', sha256: 'a'.repeat(64) },
      candidates: [0, 1, 2].map(index => ({ id: `mask-${index}`, score: .99 - index * .1,
        target_box: [.2, .2, .8, .8], mask_png_base64: mask.toString('base64'), mask_sha256: digest(mask) })),
      inference_ms: 100,
    } }).catch(() => {});
  });
  return { calls, release };
}
async function takePhoto(page, variant = 0) {
  await expect(page.locator('#quickShutter')).toBeEnabled();
  await page.evaluate(value => { window.__cameraFixture.variant = value; }, variant);
  await page.waitForTimeout(90);
  const count = await page.locator('.quickPhoto').count();
  await page.locator('#quickShutter').click();
  await expect(page.locator('.quickPhoto')).toHaveCount(count + 1);
}
async function saveObject(page) {
  await page.getByLabel('Object name', { exact: true }).fill('Guided artwork');
  await page.getByLabel('Object type', { exact: true }).selectOption('painting');
  await page.getByRole('button', { name: 'Save object', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Guided artwork saved with');
}
async function drawBox(page) {
  const visible = await page.locator('#quickSnapshot').boundingBox();
  await page.mouse.move(visible.x + visible.width * .12, visible.y + visible.height * .12);
  await page.mouse.down();
  await page.mouse.move(visible.x + visible.width * .88, visible.y + visible.height * .88, { steps: 4 });
  await page.mouse.up();
}

test('Add object opens the camera directly with one shutter and no setup questions', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); await openCapture(page);
  await expect(page.locator('#quickShutter')).toBeEnabled();
  await expect(page.locator('#guidedSetup')).not.toBeVisible();
  await expect(page.locator('#guidedProfile, #guidedConfirmed, #guidedCandidates')).toHaveCount(0);
  await expect(page.locator('#quickDone')).toBeDisabled();
  await expect(page.locator('#choosePhotos')).toBeVisible();
  expect(await page.evaluate(() => window.__cameraFixture.requests)).toBe(1);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(0);
});

test('four photos and Save complete while the first detection is still unresolved', async ({ page }, testInfo) => {
  const api = await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page);
  await page.evaluate(() => {
    const timings = window.__quickTimings = { shutter_to_thumbnail_ms: [], done_to_name_form_ms: null };
    let shutterAt = null, lastCount = 0, doneAt = null;
    document.getElementById('quickShutter').addEventListener('click', () => { shutterAt = performance.now(); }, { capture: true });
    new MutationObserver(() => {
      const count = document.getElementById('quickPhotos').children.length;
      if (count > lastCount && shutterAt !== null) { timings.shutter_to_thumbnail_ms.push(performance.now() - shutterAt); shutterAt = null; }
      lastCount = count;
    }).observe(document.getElementById('quickPhotos'), { childList: true });
    document.getElementById('quickDone').addEventListener('click', () => { doneAt = performance.now(); }, { capture: true });
    new MutationObserver(() => {
      if (doneAt !== null && !document.getElementById('addObject').hidden && !document.getElementById('guidedDialog').open) {
        timings.done_to_name_form_ms = performance.now() - doneAt; doneAt = null;
      }
    }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden', 'open'] });
  });
  for (let index = 0; index < 4; index++) await takePhoto(page, index + 1);
  await page.locator('.quickPhoto img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
  await page.screenshot({ path: testInfo.outputPath('quick-four-photos.png'), fullPage: true });
  expect(server.calls).toHaveLength(1);
  await expect(page.locator('#quickShutter')).toBeDisabled();
  await expect(page.locator('#quickDone')).toBeEnabled();
  await page.locator('#quickDone').click();
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await expect(page.locator('.capturePhoto')).toHaveCount(4);
  const timings = await page.evaluate(() => window.__quickTimings);
  await writeFile(testInfo.outputPath('capture-ui-timings.json'), JSON.stringify({ project: testInfo.project.name, ...timings }, null, 2));
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
  await saveObject(page);
  expect(api.uploads).toHaveLength(4);
  for (const form of api.uploads) {
    expect(form.get('foreground_mask')).toBeNull(); expect(form.get('viewpoint')).toBe('unknown');
    expect(JSON.parse(form.get('capture_metadata'))).toMatchObject({ version: 'guided-capture-v3', requested_view: null,
      viewpoint_verified: false, quality_region: 'full-frame', detection_status: 'skipped', selection: { method: 'full-frame', reviewed: false } });
  }
  server.release(); await page.waitForTimeout(150);
  expect(server.calls).toHaveLength(1);
  await expect(page.locator('.capturePhoto')).toHaveCount(0);
});

test('background detection continues after Done and stores an unreviewed suggestion with the exact source', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page);
  await takePhoto(page); await expect.poll(() => server.calls.length).toBe(1);
  await page.locator('#quickDone').click(); await expect(page.locator('.capturePhoto')).toHaveCount(1);
  await expect(page.locator('.guidedHint')).toContainText('Checking photo');
  server.release(); await expect(page.locator('.guidedHint')).toContainText('Outline suggested');
  await expect(page.getByRole('button', { name: 'Show suggested target', exact: true })).toBeVisible();
  await saveObject(page);
  const form = api.uploads[0], source = Buffer.from(await form.get('image').arrayBuffer()), mask = Buffer.from(await form.get('foreground_mask').arrayBuffer());
  expect(source).toEqual(server.calls[0].bytes);
  expect(JSON.parse(form.get('capture_metadata'))).toMatchObject({ version: 'guided-capture-v3', quality_region: 'full-frame', detection_status: 'suggested',
    viewpoint_verified: false, selection: { method: 'server-suggestion', reviewed: false, candidate_id: 'mask-0', source_sha256: digest(source), mask_sha256: digest(mask) },
    segmentation: { model: 'facebook/sam2.1-hiera-tiny', model_hash: 'a'.repeat(64) } });
});

test('only captured stills are analyzed, sequentially, and tapping preview only changes the target', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page);
  await expect(page.locator('#quickShutter')).toBeEnabled();
  // Tap inside the rendered video; the shorter preview can letterbox its sides.
  const target = await page.locator('#quickStage').evaluate(stage => {
    const video = document.getElementById('quickVideo');
    const scale = Math.min(stage.clientWidth / video.videoWidth, stage.clientHeight / video.videoHeight);
    const width = video.videoWidth * scale, height = video.videoHeight * scale;
    return { x: (stage.clientWidth - width) / 2 + width * .2, y: (stage.clientHeight - height) / 2 + height * .3 };
  });
  await page.locator('#quickTarget').click({ position: target });
  expect(server.calls).toHaveLength(0); await expect(page.locator('.quickPhoto')).toHaveCount(0);
  await takePhoto(page, 1); await takePhoto(page, 2);
  expect(server.calls).toHaveLength(1);
  expect(Number(server.calls[0].query.point_x)).toBeLessThan(.3);
  server.release(); await expect.poll(() => server.calls.length).toBe(2);
  await expect(page.locator('.quickPhoto span').first()).toContainText('Object suggestion');
  await expect(page.locator('.quickPhoto span').nth(1)).toContainText('Object suggestion');
  expect(Number(server.calls[1].query.point_x)).toBe(.5);
  expect(server.calls[0].query.frame_id).not.toBe(server.calls[1].query.frame_id);
});

for (const mode of ['failure', 'mismatch']) {
  test(`${mode} leaves the original available without blocking another photo or Done`, async ({ page }) => {
    await mockAdmin(page); await mockCamera(page); await mockAnalysis(page, { fail: mode === 'failure', mismatch: mode === 'mismatch' }); await openCapture(page);
    await takePhoto(page); await expect(page.locator('.quickPhoto span').first()).toContainText('Original kept');
    await expect(page.locator('#quickShutter')).toBeEnabled(); await page.locator('#quickDone').click();
    await expect(page.locator('.capturePhoto')).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Show suggested target', exact: true })).toHaveCount(0);
  });
}

test('optional rectangle correction preserves the original and records human review only for that correction', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { fail: true }); await openCapture(page);
  await page.evaluate(() => { window.__cameraFixture.mode = 'smooth'; });
  await takePhoto(page); await expect(page.locator('.quickPhoto span')).toContainText('Original kept');
  await page.locator('.quickPhoto').click(); await expect(page.locator('#quickInspector')).toBeVisible();
  await drawBox(page); await page.locator('#quickApplyBox').click();
  await expect(page.locator('#quickInspector')).not.toBeVisible();
  await page.locator('#quickDone').click(); await saveObject(page);
  const form = api.uploads[0], source = Buffer.from(await form.get('image').arrayBuffer()), mask = Buffer.from(await form.get('foreground_mask').arrayBuffer());
  expect(source).toEqual(server.calls[0].bytes);
  expect(JSON.parse(form.get('capture_metadata'))).toMatchObject({ quality_region: 'full-frame', detection_status: 'skipped',
    selection: { method: 'manual-box', reviewed: true, source_sha256: digest(source), mask_sha256: digest(mask) } });
});

test('optional full-photo correction defeats an obsolete delayed detection', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page);
  await takePhoto(page); await expect.poll(() => server.calls.length).toBe(1);
  await page.locator('.quickPhoto').click(); await page.locator('#quickFullPhoto').click();
  server.release(); await page.waitForTimeout(100);
  await page.locator('#quickDone').click(); await saveObject(page);
  const form = api.uploads[0]; expect(form.get('foreground_mask')).toBeNull();
  expect(JSON.parse(form.get('capture_metadata'))).toMatchObject({ target_box: [0, 0, 1, 1],
    selection: { method: 'full-frame', reviewed: true }, detection_status: 'skipped' });
});

test('native picker cancellation preserves photos captured without waiting for detection', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page); await takePhoto(page);
  await Promise.all([page.waitForEvent('filechooser'), page.locator('#choosePhotos').click()]);
  await page.locator('#libraryInput').evaluate(input => input.dispatchEvent(new Event('cancel', { bubbles: true })));
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await expect(page.locator('#detailsStep')).toBeVisible(); await expect(page.locator('.capturePhoto')).toHaveCount(1);
  await expect(page.locator('.guidedHint')).toContainText('Photo kept');
  server.release();
});

test('closing capture discards delayed analysis and stops tracks', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page); await takePhoto(page);
  await page.locator('#guidedClose').click(); server.release(); await page.waitForTimeout(100);
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
  await expect(page.locator('#guidedDialog')).not.toBeVisible(); await expect(page.locator('.capturePhoto')).toHaveCount(0);
});

test('page hiding stops camera without losing the photos kept in the dialog', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); await mockAnalysis(page); await openCapture(page); await takePhoto(page);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  expect(await page.evaluate(() => window.__cameraFixture.streams.every(stream => !stream.active))).toBe(true);
  await expect(page.locator('.quickPhoto')).toHaveCount(1); await expect(page.locator('#quickDone')).toBeEnabled();
});

test('late camera permission after close stops its stream', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page, { lateCamera: true }); await openCapture(page);
  await expect.poll(() => page.evaluate(() => !!window.__cameraFixture.pendingCamera)).toBe(true);
  await page.locator('#guidedClose').click(); await page.evaluate(() => window.__cameraFixture.pendingCamera());
  await expect.poll(() => page.evaluate(() => window.__cameraFixture.stopped)).toBeGreaterThan(0);
});

test('denied camera leaves native camera and gallery immediately available', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page, { modelFailure: true }); await openCapture(page);
  await expect(page.locator('#guidedError')).toContainText('Camera access was denied');
  await expect(page.locator('#takePhoto')).toBeVisible(); await expect(page.locator('#choosePhotos')).toBeVisible();
  await expect(page.locator('#guidedStart')).toBeEnabled();
});

test('portrait camera keeps shutter, thumbnails and Save within the phone viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAdmin(page); await mockCamera(page, { portrait: true }); await mockAnalysis(page, { portrait: true, delayed: true }); await openCapture(page);
  await takePhoto(page);
  for (const id of ['quickShutter', 'quickDone', 'quickPhotos', 'takePhoto']) {
    await expect(page.locator(`#${id}`)).toBeVisible();
    const rect = await page.locator(`#${id}`).boundingBox(); expect(rect.x).toBeGreaterThanOrEqual(0); expect(rect.x + rect.width).toBeLessThanOrEqual(391);
    expect(rect.y).toBeGreaterThanOrEqual(0); expect(rect.y + rect.height).toBeLessThanOrEqual(844);
  }
  expect(await page.locator('#guidedDialog').evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('quick-capture-phone.png'), fullPage: true });
});

test('known flat-artwork sessions keep the whole original rather than auto-selecting a figure within it', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page); await openCapture(page);
  // The profile is normally supplied from the preceding object's type. Exercise
  // that module input directly without introducing another UI question.
  await page.locator('#guidedClose').click();
  await page.evaluate(async () => {
    const { GuidedCapture } = await import('/quick-capture.mjs?v=quick-20261107-1');
    window.__planarCapture = new GuidedCapture({ analyze: () => { throw new Error('No point-only artwork segmentation'); }, onUse: () => {} });
    window.__planarCapture.open('planar');
  });
  await takePhoto(page); await expect(page.locator('.quickPhoto span')).toContainText('Original kept');
  expect(server.calls).toHaveLength(0);
  const metadata = await page.evaluate(() => window.__planarCapture.selectedPhotos()[0].captureMetadata);
  expect(metadata).toMatchObject({ profile: 'planar', detection_status: 'skipped', target_box: [0, 0, 1, 1], selection: { method: 'full-frame', reviewed: false } });
});

test('opening capture bypasses old cached scripts and never loads a segmentation worker', async ({ page }) => {
  const oldAssetRequests = [], errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/admin.js*', async route => {
    const url = new URL(route.request().url());
    if (!url.searchParams.has('v')) { oldAssetRequests.push(url.pathname); return route.fulfill({ contentType: 'text/javascript', body: 'window.__staleAdminLoaded = true;' }); }
    return route.continue();
  });
  await page.route('**/server-guided-capture.mjs*', async route => { oldAssetRequests.push(route.request().url()); return route.abort(); });
  await mockAdmin(page); await mockCamera(page); await mockAnalysis(page); await openCapture(page); await takePhoto(page);
  await expect(page.locator('.quickPhoto span')).toContainText('Object suggestion');
  expect(oldAssetRequests).toEqual([]); expect(errors).toEqual([]);
  expect(await page.evaluate(() => window.__cameraFixture.workerCreations)).toBe(0);
});

test('starting another capture marks an interrupted background check as skipped on the existing photo', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { delayed: true }); await openCapture(page);
  await takePhoto(page); await expect.poll(() => server.calls.length).toBe(1);
  await page.locator('#quickDone').click(); await expect(page.locator('.guidedHint')).toContainText('Checking photo');
  await page.getByRole('button', { name: 'Add more photos', exact: true }).click();
  await expect(page.locator('#guidedDialog')).toBeVisible(); await page.locator('#guidedClose').click();
  await expect(page.locator('.guidedHint')).toContainText('Photo kept');
  server.release(); await page.waitForTimeout(100);
  await expect(page.locator('.guidedHint')).toContainText('Photo kept');
});


test('a temporarily busy server retries the same still in the background after Done', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { busyResponses: 1 }); await openCapture(page);
  await takePhoto(page); await expect.poll(() => server.calls.length).toBe(1);
  await page.locator('#quickDone').click(); await expect(page.locator('#detailsStep')).toBeVisible();
  await expect(page.locator('.guidedHint')).toContainText('Outline suggested');
  expect(server.calls).toHaveLength(2);
  expect(server.calls[0].query.frame_id).toBe(server.calls[1].query.frame_id);
  expect(server.calls[0].bytes).toEqual(server.calls[1].bytes);
});

test('Save aborts a pending busy-server retry immediately', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { busyResponses: 100 }); await openCapture(page);
  await takePhoto(page); await expect.poll(() => server.calls.length).toBe(1);
  await page.locator('#quickDone').click(); await saveObject(page);
  const callsAtSave = server.calls.length;
  await page.waitForTimeout(1150); expect(server.calls).toHaveLength(callsAtSave);
});

test('server budget limits retain the photo without retrying', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page, { limited: true }); await openCapture(page);
  await takePhoto(page); await expect(page.locator('.quickPhoto span')).toContainText('Original kept');
  await page.waitForTimeout(1150); expect(server.calls).toHaveLength(1);
  await expect(page.locator('#quickDone')).toBeEnabled();
});


test('a new object ignores the previous painting type and still receives automatic analysis', async ({ page }) => {
  await mockAdmin(page); await mockCamera(page); const server = await mockAnalysis(page);
  await openCapture(page, { lastType: 'painting' }); await takePhoto(page);
  await expect(page.locator('.quickPhoto span')).toContainText('Object suggestion');
  expect(server.calls).toHaveLength(1); expect(server.calls[0].query.profile).toBe('multiview');
});

test('one Save from the camera uploads and embeds the entire named collection', async ({ page }) => {
  const api = await mockAdmin(page); await mockCamera(page); await mockAnalysis(page, { delayed: true });
  await openCapture(page);
  await page.getByLabel('Object name', { exact: true }).fill('Guided artwork');
  await page.getByLabel('Object type', { exact: true }).selectOption('painting');
  for (let index = 0; index < 4; index++) await takePhoto(page, index + 1);
  await expect(page.locator('#quickDone')).toHaveText('Save');
  await page.locator('#quickDone').click();
  await expect(page.locator('#collection')).toBeVisible();
  await expect(page.locator('#status')).toContainText('Guided artwork saved with 4 photos');
  expect(api.uploads).toHaveLength(4);
  const writes = api.requests.filter(r => r.method() === 'POST').map(r => new URL(r.url()).pathname);
  expect(writes.at(-1)).toBe(`/api/v1/admin/venues/${venueId}/objects/${objectId}/save`);
  expect(writes.filter(path => path.endsWith('/save'))).toHaveLength(1);
  await expect(page.getByRole('button', { name: /Prepare|^OK$|^Done$/ })).toHaveCount(0);
  await expect(page.locator('#saveCollection')).toBeHidden();
});
