import { test, expect } from '@playwright/test';
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
  await page.getByLabel('Photographer / owner').fill('Museum team');
  await page.getByLabel('Capture session').fill('visit-1');
  await page.getByLabel('Photo rights / permission').fill('Own photo, reference use permitted');
  await page.getByRole('checkbox', { name: /I checked/ }).check();
  await page.getByRole('button', { name: 'Save photo', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Photo saved privately');
  await expect(page.locator('#coverage')).toContainText('1 saved');
  await page.getByRole('button', { name: 'Prepare references' }).click();
  await expect(page.locator('#coverage')).toContainText('1 prepared');
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
  await expect(page.getByRole('status')).toContainText('valid admin token');
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
    expect(data).toEqual({ name: 'New gallery', slug: 'new-gallery', type: 'gallery', city: 'Utrecht', country: 'Netherlands' });
    await route.fulfill({ status: 201, json: { ...data, id } });
  });
  await page.route(`**/api/v1/admin/venues/${id}/objects?**`, route => route.fulfill({ json: { objects: [] } }));
  await page.getByText('Add a venue', { exact: true }).click();
  await page.getByLabel('Venue name', { exact: true }).fill('New gallery');
  await page.getByLabel('Unique slug').fill('new-gallery');
  await page.getByLabel('Venue type', { exact: true }).selectOption('gallery');
  await page.getByLabel('City', { exact: true }).fill('Utrecht');
  await page.getByLabel('Country', { exact: true }).fill('Netherlands');
  await page.getByRole('button', { name: 'Create venue', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Venue created');
  await expect(page.locator('#venue')).toHaveValue(id);
  await expect(page.locator('#objects')).toContainText('No objects match');
});
