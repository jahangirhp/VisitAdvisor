import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
const location = '1'.repeat(24), object = '2'.repeat(24), source = '3'.repeat(24);
const base = `/api/v1/admin/venues/${location}/objects/${object}`;
async function mock(page) {
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
    if (path.endsWith('/prepare')) { prepared = true; return route.fulfill({ json: { prepared: 1 } }); }
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
test('admin can find an object, upload a reviewed photo, then prepare it', async ({ page }) => {
  const requests = await mock(page);
  await login(page);
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  await expect(page.getByRole('heading', { name: '<Painting & sculpture>' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Prepare references' })).toBeDisabled();
  await page.locator('#image').setInputFiles({ name: 'front.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
  await page.getByLabel('Viewpoint', { exact: true }).fill('front');
  await expect(page.getByLabel('Photographer / owner')).toHaveCount(0);
  await expect(page.getByLabel('Capture session')).toHaveCount(0);
  await expect(page.getByLabel('Object source')).toHaveCount(0);
  await expect(page.getByLabel('Photo rights / permission')).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: /I checked that this photo/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Save photo', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Photo saved.');
  await expect(page.locator('#coverage')).toHaveCount(0);
  await expect(page.getByRole('heading', {name:'Stored reference views'})).toHaveCount(0);
  await page.getByRole('button', { name: 'Prepare references' }).click();
  await expect(page.getByRole('button', { name: 'Prepare references' })).toBeDisabled();
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
  await page.getByLabel('Admin token').fill('demo-token');
  await page.getByRole('button', { name: 'Unlock library' }).click();
  await expect(page.getByRole('button', { name: /Painting & sculpture/ })).toBeVisible();
  await page.getByLabel('Find an object').fill('missing');
  await expect(page.locator('#objects')).toContainText('No objects match');
  await page.getByLabel('Find an object').fill('');
  await page.getByRole('button', { name: /Painting & sculpture/ }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

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
  await expect(page.locator('#status')).toContainText('Venue created');
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
    await page.getByRole('button', { name: 'Next', exact: true }).click();
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
  if (await page.locator('#addObject').isHidden()) await page.getByRole('button', { name: 'Add object', exact: true }).click();
  const files = [];
  for (const color of colors) {
    const data = await page.evaluate(([color, width, height]) => {
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d'); context.fillStyle = color; context.fillRect(0, 0, width, height);
      return canvas.toDataURL('image/png').split(',')[1];
    }, [color, ...size]);
    files.push({ name: `${color}.png`, mimeType: 'image/png', buffer: Buffer.from(data, 'base64') });
  }
  await page.locator('#libraryInput').setInputFiles(files);
  await expect(page.locator('.capturePhoto')).toHaveCount(expectedCount);
  if (expectedCount < 4) await expect(page.locator('#choosePhotos')).toBeEnabled();
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
      const form = await new Request(route.request().url(), { method: 'POST', body: route.request().postDataBuffer(), headers: route.request().headers() }).formData();
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
    await page.screenshot({ path: 'test-results/photo-step-mobile.png' });
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await page.getByLabel('Object name', { exact: true }).fill('Museum statue');
    await page.getByLabel('Object type', { exact: true }).selectOption('sculpture');
    await expect(page.locator('#captureObserver, #captureRights, #captureReviewed, input[name=creator]')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `test-results/photo-first-${lostResponse ? 'retry' : 'mobile'}.png`, fullPage: true });
    await page.getByRole('button', { name: 'Save object', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Retry upload' })).toBeEnabled();
    await expect(page.locator('#captureProgress')).toContainText('completed uploads are saved');
    await expect(page.locator('#venue')).toBeDisabled();
    await page.getByRole('button', { name: 'Retry upload' }).click();
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
  await expect(page.locator('#takePhoto')).toBeDisabled();
  await page.getByRole('button', { name: 'Remove photo 4', exact: true }).click();
  await expect(page.locator('.capturePhoto')).toHaveCount(3);
  await expect(page.locator('#takePhoto')).toBeEnabled();
  await selectCapturePhotos(page, ['red'], 3);
  await expect(page.locator('#captureProgress')).toContainText('already selected');
  await page.locator('#libraryInput').setInputFiles({ name: 'bad.heic', mimeType: 'image/heic', buffer: Buffer.from('not an image') });
  await expect(page.locator('#captureProgress')).toContainText("Couldn't open this photo");
  await expect(page.locator('.capturePhoto')).toHaveCount(3);
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

test('two-step creation keeps details on Back and validates before saving', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const requests = await mock(page); await login(page);
  await page.screenshot({ path: 'test-results/collection-mobile.png' });
  await page.getByRole('button', { name: 'Add object', exact: true }).click();
  await expect(page.getByLabel('Object name', { exact: true })).toBeHidden();
  await expect(page.locator('#nextCapture')).toBeDisabled();
  await page.screenshot({ path: 'test-results/photo-empty-mobile.png' });
  await selectCapturePhotos(page, ['red']);
  await page.locator('#nextCapture').click();
  await expect(page.locator('#photoStep')).toBeHidden();
  await page.getByRole('button', { name: 'Save object', exact: true }).click();
  expect(requests.some(request => request.method() === 'POST')).toBeFalsy();
  await page.getByLabel('Object name', { exact: true }).fill('Draft statue');
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.locator('.capturePhoto')).toHaveCount(1);
  await page.locator('#nextCapture').click();
  await expect(page.getByLabel('Object name', { exact: true })).toHaveValue('Draft statue');
  await page.getByRole('button', { name: 'Back to collection' }).click();
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
