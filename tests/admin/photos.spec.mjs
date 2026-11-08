import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { captureSubmittedForms, submittedForm } from './submitted-form.mjs';
const location = '1'.repeat(24), object = '2'.repeat(24), source = '3'.repeat(24);
const base = `/api/v1/admin/venues/${location}/objects/${object}`;
async function mock(page) {
  await captureSubmittedForms(page);
  let saved = false, prepared = false;
  const requests = [];
  await page.route('**/api/v1/admin/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    requests.push(request);
    if (request.headers().authorization !== 'Bearer demo-token') {
      return route.fulfill({ status: 401, json: { error: { message: 'Enter a valid admin token.' } } });
    }
    const row = { id: object, name: '<Painting & sculpture>', type: 'painting', target: 3,
      prepared: prepared ? 1 : 0, pending: saved && !prepared ? 1 : 0,
      missing: saved ? 2 : 3, needs_photos: true };
    if (path.endsWith('/venues')) return route.fulfill({ json: [{ id: location, name: 'Museum', city: 'Amsterdam' }] });
    if (path.endsWith('/objects')) return route.fulfill({ json: { objects: [row] } });
    if (path.endsWith('/photos')) {
      saved = true;
      expect(request.postDataBuffer().toString()).toContain('front');
      return route.fulfill({ status: 201, json: { id: 'photo', status: 'saved_for_preparation' } });
    }
    if (path.endsWith('/save')) { prepared = true; return route.fulfill({ json: { prepared: 1 } }); }
    return route.fulfill({ json: { ...row, sources: [{ id: source, title: 'Official museum record' }], photos: [], views: [], stale: false } });
  });
  return requests;
}
async function login(page) {
  await page.goto('/');
  await page.getByLabel('Admin token').fill('demo-token');
  await page.getByRole('button', { name: 'Unlock library' }).click();
  await expect(page.getByRole('button', { name: /Painting & sculpture/ })).toBeVisible();
}
test('saving a photo embeds it without a preparation action', async ({ page }) => {
  const requests = await mock(page);
  await login(page);
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  await expect(page.getByRole('heading', { name: '<Painting & sculpture>' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Prepare references' })).toHaveCount(0);
  await page.locator('#image').setInputFiles({ name: 'front.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
  await page.getByLabel('Viewpoint', { exact: true }).fill('front');
  await expect(page.getByLabel('Photographer / owner')).toHaveCount(0);
  await expect(page.getByLabel('Capture session')).toHaveCount(0);
  await expect(page.getByLabel('Object source')).toHaveCount(0);
  await expect(page.getByLabel('Photo rights / permission')).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: /I checked that this photo/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Save photo', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Photos saved.');
  expect(await page.locator('#status').evaluate(el => getComputedStyle(el).position)).not.toBe('fixed');
  expect(await page.locator('#status').evaluate(el => el.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
  await expect(page.locator('#coverage')).toHaveCount(0);
  await expect(page.getByRole('heading', {name:'Stored reference views'})).toHaveCount(0);
  expect(requests.filter(r => r.url().endsWith('/save') && r.method() === 'POST')).toHaveLength(1);
  await expect(page.getByRole('button', { name: 'Prepare references' })).toHaveCount(0);
  expect(requests.some(r => r.url().includes(base + '/photos') && r.method() === 'POST')).toBeTruthy();
  await page.getByRole('button', { name: 'Lock panel' }).click();
  await expect(page.getByLabel('Admin token')).toHaveValue('');
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});
test('mobile layout, search and rejected credentials', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page);
  await page.goto('/');
  await page.getByLabel('Admin token').fill('wrong');
  await page.getByRole('button', { name: 'Unlock library' }).click();
  await expect(page.locator('#status')).toContainText('valid admin token');
  await expect(page.locator('#status')).toBeVisible();
  expect(await page.locator('#status').evaluate(el => getComputedStyle(el).position)).not.toBe('fixed');
  await page.getByLabel('Admin token').fill('demo-token');
  await page.getByRole('button', { name: 'Unlock library' }).click();
  await expect(page.getByRole('button', { name: /Painting & sculpture/ })).toBeVisible();
  await page.getByLabel('Find an object').fill('missing');
  await expect(page.locator('#objects')).toContainText('No objects match');
  await page.getByLabel('Find an object').fill('');
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('an existing photo collection retries embedding without uploading again', async ({ page }) => {
  const requests = await mock(page); await login(page);
  let saves = 0;
  await page.route(`**${base}/save`, async route => {
    if (++saves === 1) return route.fulfill({ status: 503, json: { error: { message: 'Please retry saving.' } } });
    return route.fallback();
  });
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  await page.locator('#image').setInputFiles({ name: 'front.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
  await page.getByLabel('Viewpoint', { exact: true }).fill('front');
  await page.getByRole('button', { name: 'Save photo', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Please retry saving.');
  await page.getByRole('button', { name: 'Save photos', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Photos saved.');
  await expect(page.locator('#saveCollection')).toBeHidden();
  expect(saves).toBe(2);
  expect(requests.filter(r => r.url().endsWith('/photos') && r.method() === 'POST')).toHaveLength(1);
});

for (const lostResponse of [false, true]) {
  test(`new collection retries ${lostResponse ? 'a lost embedding response' : 'failed embedding'} without duplicate uploads`, async ({ page }) => {
    await mock(page); await login(page);
    const id = '8'.repeat(24), path = `/api/v1/admin/venues/${location}/objects/${id}`;
    let creates = 0, uploads = 0, saves = 0, published = false;
    const photos = [];
    await page.route(`**/api/v1/admin/venues/${location}/objects`, async route => {
      creates++;
      await route.fulfill({ status: 201, json: { id, name: 'Museum statue', source_ids: [source] } });
    });
    await page.route(`**${path}`, route => route.fulfill({ json: {
      id, name: 'Museum statue', type: 'sculpture', target: 4,
      pending: published ? 0 : photos.length, prepared: published ? photos.length : 0,
      sources: [{ id: source }], photos, views: [], stale: false,
    } }));
    await page.route(`**${path}/photos`, async route => {
      uploads++;
      const form = await submittedForm(page, route.request());
      photos.push({ sha256: createHash('sha256').update(Buffer.from(await form.get('image').arrayBuffer())).digest('hex'), capture_group: form.get('capture_group') });
      await route.fulfill({ status: 201, json: { id: String(uploads) } });
    });
    await page.route(`**${path}/save`, async route => {
      saves++;
      expect(photos).toHaveLength(2);
      if (saves === 1) {
        published = lostResponse;
        if (lostResponse) return route.abort('failed');
        return route.fulfill({ status: 503, json: { error: { message: 'Please retry saving.' } } });
      }
      published = true;
      await route.fulfill({ json: { prepared: 2, set_id: 'saved-set' } });
    });
    await selectCapturePhotos(page, ['red', 'blue']);
    await page.getByLabel('Photo 2 angle', { exact: true }).selectOption('left');
    await page.getByLabel('Object name', { exact: true }).fill('Museum statue');
    await page.getByLabel('Object type', { exact: true }).selectOption('sculpture');
    await page.getByRole('button', { name: 'Save object', exact: true }).click();
    await expect(page.locator('#captureProgress')).toContainText('Your uploaded photos are kept.');
    await expect(page.locator('#detailsStep')).toBeVisible();
    await page.getByRole('button', { name: 'Retry save', exact: true }).click();
    await expect(page.locator('#status')).toContainText('Museum statue saved with 2 photos');
    expect(creates).toBe(1); expect(uploads).toBe(2); expect(saves).toBe(2);
    await expect(page.locator('#saveCollection')).toBeHidden();
  });
}

test('admin creates and selects an empty venue', async ({ page }) => {
  await mock(page);
  await login(page);
  const id = '4'.repeat(24);
  await page.route('**/api/v1/admin/venues', async route => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().headers().authorization).toBe('Bearer demo-token');
    const data = route.request().postDataJSON();
    expect(data).toEqual({ name: 'New gallery' });
    await route.fulfill({ status: 201, json: { ...data, id } });
  });
  await page.route(`**/api/v1/admin/venues/${id}/objects?**`, route => route.fulfill({ json: { objects: [] } }));
  await page.getByRole('button', { name: 'Add a venue', exact: true }).click();
  await page.getByLabel('Venue name', { exact: true }).fill('New gallery');
  await page.getByRole('button', { name: 'Create venue', exact: true }).click();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#venue')).toHaveValue(id);
  await expect(page.locator('#objects')).toContainText('No objects match');
});

for (const newVenue of [false, true]) {
  test(`admin adds an object to ${newVenue ? 'a newly created' : 'an existing'} venue`, async ({ page }) => {
    await mock(page);
    await login(page);
    const venueId = newVenue ? '4'.repeat(24) : location;
    if (newVenue) {
      await page.route('**/api/v1/admin/venues', route => route.fulfill({ status: 201, json: { id: venueId, name: 'New gallery', city: 'Utrecht' } }));
      await page.route(`**/api/v1/admin/venues/${venueId}/objects?**`, route => route.fulfill({ json: { objects: [] } }));
      await page.getByRole('button', { name: 'Add a venue', exact: true }).click();
      await page.getByLabel('Venue name', { exact: true }).fill('New gallery');
      await page.getByRole('button', { name: 'Create venue', exact: true }).click();
      await expect(page.locator('#venue')).toHaveValue(venueId);
      await expect(page.locator('#guidedDialog')).toBeVisible();
    }
    await expect(page.locator('#objectVenue')).toContainText(newVenue ? 'New gallery' : 'Museum');
    const id = '5'.repeat(24);
    await page.route(`**/api/v1/admin/venues/${venueId}/objects`, async route => {
      expect(route.request().method()).toBe('POST');
      expect(route.request().headers().authorization).toBe('Bearer demo-token');
      expect(route.request().postDataJSON()).toEqual({ name: 'New sculpture', type: 'sculpture', year: '17th century' });
      await route.fulfill({ status: 201, json: { id, name: 'New sculpture', source_ids: [source], location_id: venueId } });
    });
    await page.route(`**/api/v1/admin/venues/${venueId}/objects/${id}`, route => route.fulfill({ json: {
      id, name: 'New sculpture', type: 'sculpture', prepared: 0, pending: 0, target: 4,
      sources: [], photos: [], views: [], stale: false,
    } }));
    await selectCapturePhotos(page, ['red']);
    await expect(page.locator('#detailsStep')).toBeVisible();
    await expect(page.locator('#guidedDialog')).not.toBeVisible();
    await page.getByLabel('Object name', { exact: true }).fill('New sculpture');
    await page.getByLabel('Object type', { exact: true }).selectOption('sculpture');
    await expect(page.locator('#captureObserver, #captureRights, #captureReviewed, input[name=creator]')).toHaveCount(0);
    await page.getByText('More details (optional)', { exact: true }).click();
    await page.getByLabel('Year or period (optional)', { exact: true }).fill('17th century');
    await page.getByRole('button', { name: 'Save object', exact: true }).click();
    await expect(page.locator('#status')).toContainText('New sculpture saved with 1 photo');
    await expect(page.getByRole('heading', { name: 'New sculpture', exact: true })).toBeVisible();
    await expect(page.getByLabel('Object name', { exact: true })).toHaveValue('');
    await expect(page.locator('#venue')).toHaveValue(venueId);
  });
}

async function selectCapturePhotos(page, colors, expectedCount = colors.length, size = [100, 80]) {
  const previousCount = await page.locator('.capturePhoto').count();
  if (!await page.locator('#guidedDialog').isVisible()) {
    if (await page.locator('#addObject').isHidden()) await page.getByRole('button', { name: 'Add object', exact: true }).click();
    else await page.getByRole('button', { name: 'Add more photos', exact: true }).click();
  }
  await expect(page.locator('#guidedDialog')).toBeVisible();
  const files = [];
  for (const color of colors) {
    const data = await page.evaluate(([color, width, height]) => {
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d'); context.fillStyle = color; context.fillRect(0, 0, width, height);
      return canvas.toDataURL('image/png').split(',')[1];
    }, [color, ...size]);
    files.push({ name: `${color}.png`, mimeType: 'image/png', buffer: Buffer.from(data, 'base64') });
  }
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Choose photos', exact: true }).click(),
  ]);
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await chooser.setFiles(files);
  await expect(page.locator('.capturePhoto')).toHaveCount(expectedCount);
  if (expectedCount > previousCount) {
    await expect(page.locator('#guidedDialog')).not.toBeVisible();
    await expect(page.locator('#detailsStep')).toBeVisible();
  }
  if (expectedCount < 4) await expect(page.locator('#guidedCapture')).toBeEnabled();
}

for (const lostResponse of [false, true]) {
  test(`mobile photo batch retries ${lostResponse ? 'a lost response' : 'a failed upload'} without duplicate objects or photos`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mock(page); await login(page);
    const id = '6'.repeat(24), path = `/api/v1/admin/venues/${location}/objects/${id}`;
    let creates = 0, uploads = 0, interrupted = false;
    const photos = [];
    await page.route(`**/api/v1/admin/venues/${location}/objects`, async route => {
      creates++;
      await route.fulfill({ status: 201, json: { id, name: 'Museum statue', source_ids: [source] } });
    });
    await page.route(`**${path}`, route => route.fulfill({ json: {
      id, name: 'Museum statue', type: 'sculpture', target: 4, pending: photos.length, prepared: 0,
      sources: [{ id: source, title: 'On-site identification: Museum statue' }], photos: photos.map(photo => ({ ...photo, _id: '7'.repeat(24), viewpoint: 'front' })), views: [], stale: false,
    } }));
    await page.route(`**${path}/photos`, async route => {
      uploads++;
      const form = await submittedForm(page, route.request());
      const file = form.get('image');
      expect(file.type).toBe('image/jpeg');
      expect(form.get('source_id')).toBe(source);
      expect(form.has('photographer')).toBe(false);
      expect(form.has('reviewed')).toBe(false);
      expect(form.get('rights')).toBe('Photo rights details not supplied.');
      const saved = { sha256: createHash('sha256').update(Buffer.from(await file.arrayBuffer())).digest('hex'), capture_group: form.get('capture_group') };
      if (!interrupted && uploads === 2) {
        interrupted = true;
        if (lostResponse) photos.push(saved);
        return route.fulfill({ status: 503, json: { error: { message: 'Connection interrupted.' } } });
      }
      photos.push(saved);
      await route.fulfill({ status: 201, json: { id: String(uploads), status: 'saved_for_preparation' } });
    });
    await selectCapturePhotos(page, ['red', 'blue']);
    await expect(page.locator('#cameraInput')).toHaveAttribute('capture', 'environment');
    await page.getByLabel('Photo 2 angle', { exact: true }).selectOption('left');
    await page.getByLabel('Object name', { exact: true }).fill('Museum statue');
    await page.getByLabel('Object type', { exact: true }).selectOption('sculpture');
    await expect(page.locator('#captureObserver, #captureRights, #captureReviewed, input[name=creator]')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `test-results/photo-first-${lostResponse ? 'retry' : 'mobile'}.png`, fullPage: true });
    await page.getByRole('button', { name: 'Save object', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Retry save' })).toBeEnabled();
    await expect(page.locator('#captureProgress')).toContainText('completed uploads are saved');
    await expect(page.locator('#venue')).toBeDisabled();
    await page.getByRole('button', { name: 'Back to collection', exact: true }).click();
    await expect(page.locator('#collection')).toBeVisible();
    await page.getByRole('button', { name: 'Add object', exact: true }).click();
    await expect(page.locator('#guidedDialog')).not.toBeVisible();
    await expect(page.locator('#detailsStep')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry save', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Retry save' }).click();
    await expect(page.locator('#status')).toContainText('Museum statue saved with 2 photos');
    expect(creates).toBe(1); expect(photos.length).toBe(2);
    expect(uploads).toBe(lostResponse ? 2 : 3);
    await expect(page.locator('.capturePhoto')).toHaveCount(0);
    await expect(page.getByLabel('Object name', { exact: true })).toBeEnabled();
    await expect(page.locator('#venue')).toBeEnabled();
  });
}

test('photo selection rejects duplicates, enforces four slots and supports removing photos', async ({ page }) => {
  await mock(page); await login(page);
  await selectCapturePhotos(page, ['red', 'blue', 'green', 'yellow']);
  await expect(page.locator('#guidedCapture')).toBeDisabled();
  await page.getByRole('button', { name: 'Remove photo 4', exact: true }).click();
  await expect(page.locator('.capturePhoto')).toHaveCount(3);
  await expect(page.locator('#guidedCapture')).toBeEnabled();
  await selectCapturePhotos(page, ['red'], 3);
  await expect(page.locator('#captureProgress')).toContainText('already selected');
  await page.locator('#libraryInput').setInputFiles({ name: 'bad.heic', mimeType: 'image/heic', buffer: Buffer.from('not an image') });
  await expect(page.locator('#captureProgress')).toContainText("Couldn't open this photo");
  await expect(page.locator('.capturePhoto')).toHaveCount(3);
  if (await page.locator('#guidedDialog').isVisible()) await page.locator('#guidedClose').click();
  await page.getByRole('button', { name: 'Back to collection' }).click();
  await page.getByRole('button', { name: 'Lock panel' }).click();
  await expect(page.locator('.capturePhoto')).toHaveCount(0);
  await expect(page.locator('#captureObserver')).toHaveCount(0);
});

test('phone-size images are resized locally and desktop layout stays within viewport', async ({ page }) => {
  await mock(page); await login(page);
  await selectCapturePhotos(page, ['red'], 1, [4000, 3000]);
  const dimensions = await page.locator('.capturePhoto img').evaluate(image => ({ width: image.naturalWidth, height: image.naturalHeight }));
  expect(dimensions).toEqual({ width: 1800, height: 1350 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'test-results/photo-first-desktop.png', fullPage: true });
});

test('direct capture uses the fallback, validates the name, and preserves cancelled draft edits', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const requests = await mock(page); await login(page);
  await page.screenshot({ path: 'test-results/collection-mobile.png' });
  await page.getByRole('button', { name: 'Add object', exact: true }).click();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await expect(page.locator('#photoStep, #captureEmpty, #nextCapture, #captureTitle, #detailsPhotoSummary')).toHaveCount(0);
  await expect(page.locator('#choosePhotos')).toBeVisible();
  await page.screenshot({ path: 'test-results/capture-setup-mobile.png' });
  await selectCapturePhotos(page, ['red']);
  await expect(page.locator('#guidedDialog')).not.toBeVisible();
  await expect(page.locator('#detailsStep')).toBeVisible();
  await page.getByRole('button', { name: 'Save object', exact: true }).click();
  expect(requests.some(request => request.method() === 'POST')).toBeFalsy();
  await page.getByLabel('Object name', { exact: true }).fill('Draft statue');
  await page.screenshot({ path: 'test-results/capture-name-mobile.png', fullPage: true });
  await page.getByLabel('Photo 1 angle', { exact: true }).evaluate(select => select.scrollIntoView({ block: 'center' }));
  await page.screenshot({ path: 'test-results/capture-name-mobile-photos.png' });
  await page.getByRole('button', { name: 'Add more photos', exact: true }).click();
  await expect(page.locator('#guidedDialog')).toBeVisible();
  await page.locator('#guidedClose').click();
  await expect(page.locator('#detailsStep')).toBeVisible();
  await expect(page.locator('.capturePhoto')).toHaveCount(1);
  await expect(page.getByLabel('Object name', { exact: true })).toHaveValue('Draft statue');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('#collection')).toBeVisible();
  await page.getByRole('button', { name: 'Add object', exact: true }).click();
  await expect(page.getByLabel('Object name', { exact: true })).toHaveValue('Draft statue');
});


test('admin stays signed in after refresh and locking clears the tab session', async ({ page }) => {
  await mock(page); await login(page);
  await page.reload();
  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#login')).toBeHidden();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  await page.getByRole('button', { name: 'Lock panel' }).click();
  await page.reload();
  await expect(page.locator('#login')).toBeVisible();
  await expect(page.locator('#workspace')).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem('visitadvisor.adminToken'))).toBeNull();
});

test('a rejected saved token is cleared on refresh', async ({ page }) => {
  await mock(page); await login(page);
  await page.route('**/api/v1/admin/**', route => route.fulfill({
    status: 401, json: { error: { message: 'Enter a valid admin token.' } },
  }));
  await page.reload();
  await expect(page.locator('#login')).toBeVisible();
  await expect(page.locator('#workspace')).toBeHidden();
  await expect(page.locator('#status')).toContainText('valid admin token');
  await expect(page.locator('#status')).toBeVisible();
  expect(await page.locator('#status').evaluate(el => getComputedStyle(el).position)).not.toBe('fixed');
  expect(await page.evaluate(() => sessionStorage.getItem('visitadvisor.adminToken'))).toBeNull();
});

test('admin login still works when tab storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {
    get() { throw new DOMException('Storage unavailable', 'SecurityError'); },
  }));
  await mock(page); await login(page);
  await page.getByRole('button', { name: 'Lock panel' }).click();
  await expect(page.locator('#login')).toBeVisible();
});

for (const mode of ['success', 'retry', 'lost response']) {
  test(`admin deletes an object and its embeddings with confirmation: ${mode}`, async ({ page }) => {
    const requests = await mock(page); await login(page);
    let deleted = false, attempts = 0;
    await page.route(`**${base}`, async route => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      attempts++;
      if (attempts === 1 && mode === 'retry') return route.fulfill({ status: 503, json: { error: { message: 'Could not finish deleting the object. Please retry.' } } });
      if (deleted) return route.fulfill({ status: 404, json: { error: { code: 'OBJECT_NOT_FOUND', message: 'Object not found in this venue.' } } });
      deleted = true;
      if (mode === 'lost response') return route.abort('failed');
      await route.fulfill({ status: 204 });
    });
    await page.route(`**/api/v1/admin/venues/${location}/objects?**`, async route => deleted
      ? route.fulfill({ json: { objects: [] } }) : route.fallback());
    await page.getByRole('button', { name: /Painting & sculpture/ }).click();
    await page.locator('#deleteObject').click();
    await expect(page.locator('#deleteDialog')).toBeVisible();
    await expect(page.locator('#deleteObjectName')).toHaveText('<Painting & sculpture>');
    await expect(page.locator('#deleteDescription')).toContainText('all image and text embeddings');
    expect(attempts).toBe(0);
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.locator('#deleteDialog')).not.toBeVisible();
    expect(attempts).toBe(0);
    await page.locator('#deleteObject').click();
    expect(await page.locator('#deleteDialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await page.locator('#confirmDelete').click();
    if (mode !== 'success') {
      await expect(page.locator('#deleteError')).not.toBeEmpty();
      await expect(page.locator('#deleteDialog')).toBeVisible();
      await expect(page.locator('#confirmDelete')).toBeEnabled();
      await page.locator('#confirmDelete').click();
    }
    await expect(page.locator('#deleteDialog')).not.toBeVisible();
    await expect(page.locator('#objects')).toContainText('No objects match');
    await expect(page.locator('#objectName')).toHaveCount(0);
    await expect(page.locator('#status')).toHaveText('Object deleted.');
    await expect(page.locator('#status')).toHaveClass('srOnly');
    expect(attempts).toBe(mode === 'success' ? 1 : 2);
    expect(requests.filter(r => ['POST', 'PUT', 'PATCH'].includes(r.method()))).toHaveLength(0);
  });
}

test('deleting the last object on a page returns to the previous page', async ({ page }) => {
  await mock(page); await login(page);
  let deleted = false;
  const firstPage = Array.from({ length: 50 }, (_, index) => ({ id: index.toString(16).padStart(24, '0'), name: `Other object ${index}`, type: 'artifact', prepared: 0, pending: 0, target: 4, missing: 4, needs_photos: true }));
  await page.route(`**/api/v1/admin/venues/${location}/objects?**`, route => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    if (!offset) return route.fulfill({ json: { objects: firstPage } });
    return route.fulfill({ json: { objects: deleted ? [] : [{ ...firstPage[0], id: object, name: '<Painting & sculpture>' }] } });
  });
  await page.locator('#refresh').click();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#page')).toHaveText('Page 2');
  await page.route(`**${base}`, route => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    deleted = true;
    return route.fulfill({ status: 204 });
  });
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  await page.locator('#deleteObject').click(); await page.locator('#confirmDelete').click();
  await expect(page.locator('#page')).toHaveText('Page 1');
  await expect(page.locator('.objectCard')).toHaveCount(50);
});
