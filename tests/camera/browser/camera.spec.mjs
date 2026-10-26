import { test, expect } from '@playwright/test';

const location = 'a'.repeat(24);
const ids = ['b'.repeat(24), 'c'.repeat(24)];
const catalog = {
  version: 'camera-server-v1', processing: 'server', reference_version: 'a'.repeat(64), automatic_identification: false,
  created_at: '2026-09-19', expires_at: '2099-01-01', location: { id: location, name: 'Test museum' },
  model: { dimensions: 384, input_size: 224, sha256: 'a'.repeat(64), bytes: 100 },
  policy: { candidate_floor: 0.55, candidate_margin: 0.04, stable_frames: 3, global_weight: 0.75, calibrated: false },
  objects: ids.map((id, index) => ({ id, location_id: location, name: `Painting ${index + 1}`, sources: [], views: [{ vectors: [Array.from({ length: 384 }, (_, i) => i === index ? 1 : 0)] }] })),
};

async function fakeCamera(page, { denied = false, delayed = false } = {}) {
  await page.addInitScript(({ ids, denied, delayed }) => {
    window.cameraRequests = 0;
    window.frameMessages = 0;
    window.tracksStopped = 0;
    window.testCandidate = ids[0];
    window.frameColor = '#506070';
    window.pendingCamera = null;
    let currentTime = 0;
    const tracks = [{ stop: () => window.tracksStopped++, addEventListener: () => {} }];
    Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), 'getUserMedia', { value: async constraints => {
      window.cameraRequests++;
      window.cameraConstraints = constraints;
      if (denied) throw new DOMException('Denied', 'NotAllowedError');
      if (delayed) await new Promise(resolve => { window.pendingCamera = resolve; });
      return { getTracks: () => tracks, getVideoTracks: () => tracks };
    } });
    Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { set(value) { this.mockStream = value; }, get() { return this.mockStream; } });
    Object.defineProperty(HTMLMediaElement.prototype, 'readyState', { get: () => 4 });
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { get: () => currentTime += 0.04 });
    Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', { get: () => 640 });
    Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', { get: () => 480 });
    HTMLMediaElement.prototype.play = async () => {};
    CanvasRenderingContext2D.prototype.drawImage = function () { this.fillStyle = window.frameColor; this.fillRect(0, 0, this.canvas.width, this.canvas.height); };
  }, { ids, denied, delayed });
  await page.route('**/recognize?*', async route => {
    const candidates = await page.evaluate(() => {
      window.frameMessages++;
      return window.testCandidates ?? (window.testCandidate ? [{ id: window.testCandidate, score: 0.9 }] : []);
    });
    await route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, candidates, inferenceMs: 10,
    } });
  });
  await page.route('**/bundle/catalog.json', route => route.fulfill({ json: catalog }));
}

test('live loop posts bounded crops, switches candidates and stops uploads', async ({ page }) => {
  await fakeCamera(page);
  const requests = [];
  const errors = [];
  page.on('request', request => requests.push({ method: request.method(), url: request.url(), body: request.postData() }));
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#start')).toBeEnabled();
  expect(await page.evaluate(() => window.cameraRequests)).toBe(0);
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.evaluate(id => { window.testCandidate = id; }, ids[1]);
  await expect(page.locator('#object-name')).toHaveText('Painting 2');
  await page.evaluate(() => { window.testCandidate = null; });
  await expect(page.locator('#result')).toBeHidden();
  await expect(page.locator('#status')).toContainText('No supported match yet');
  await page.locator('#stop').click();
  expect(await page.evaluate(() => window.tracksStopped)).toBe(1);
  const frames = await page.evaluate(() => window.frameMessages);
  await page.waitForTimeout(700);
  expect(await page.evaluate(() => window.frameMessages)).toBe(frames);
  expect(await page.evaluate(() => window.cameraConstraints.audio)).toBe(false);
  expect(requests.every(request => new URL(request.url).origin === 'http://127.0.0.1:8002' && (request.method === 'GET' || (request.method === 'POST' && new URL(request.url).pathname === '/recognize')))).toBe(true);
  expect(errors).toEqual([]);
  await page.locator('#start').click();
  await expect.poll(() => page.evaluate(() => window.cameraRequests)).toBe(2);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await expect(page.locator('#start')).toBeVisible();
  expect(await page.evaluate(() => window.tracksStopped)).toBe(2);
});

test('small movement keeps a match but large scene changes clear it', async ({ page }) => {
  await fakeCamera(page);
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.evaluate(() => {
    window.resultWasCleared = false;
    new MutationObserver(() => {
      if (document.getElementById('result').hidden) window.resultWasCleared = true;
    }).observe(document.getElementById('result'), { attributes: true, attributeFilter: ['hidden'] });
    window.frameColor = '#708090';
    window.testCandidate = null;
  });
  await expect(page.locator('#status')).toContainText('Rechecking the previous possible match');
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.evaluate(id => { window.testCandidate = id; }, ids[0]);
  await expect(page.locator('#status')).toContainText('Possible match. Check');
  expect(await page.evaluate(() => window.resultWasCleared)).toBe(false);
  await page.evaluate(() => { window.frameColor = '#ffffff'; window.testCandidate = null; });
  await expect(page.locator('#result')).toBeHidden();
  await page.locator('#stop').click();
});

test('network failures are retried without overlapping requests and stop is final', async ({ page }) => {
  await fakeCamera(page);
  let active = 0;
  let peak = 0;
  await page.route('**/recognize?*', async route => {
    active++; peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 150));
    active--;
    await route.fulfill({ status: 503, json: { error: 'RECOGNITION_UNAVAILABLE' } });
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#status')).toContainText('Retrying');
  await page.locator('#stop').click();
  await page.waitForTimeout(1700);
  expect(peak).toBe(1);
  await expect(page.locator('#status')).toContainText('Camera stopped');
  await expect(page.locator('#result')).toBeHidden();
});

test('stop during a frame request ignores the late server reply', async ({ page }) => {
  await fakeCamera(page);
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let started;
  const received = new Promise(resolve => { started = resolve; });
  await page.route('**/recognize?*', async route => {
    started();
    await delayed;
    await route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, candidates: [{ id: ids[0], score: 0.9 }],
    } }).catch(() => {});
  });
  await page.goto('/');
  await page.locator('#start').click();
  await received;
  await page.locator('#stop').click();
  release();
  await page.waitForTimeout(800);
  await expect(page.locator('#result')).toBeHidden();
  await expect(page.locator('#status')).toContainText('Camera stopped');
  expect(await page.evaluate(() => window.tracksStopped)).toBe(1);
});

test('camera permission denial gives a recoverable message', async ({ page }) => {
  await fakeCamera(page, { denied: true });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#status')).toContainText('Camera permission was not granted');
  await expect(page.locator('#start')).toBeEnabled();
  await expect(page.locator('#result')).toBeHidden();
});

test('stopping while permission is pending closes the late stream', async ({ page }) => {
  await fakeCamera(page, { delayed: true });
  await page.goto('/');
  await page.locator('#start').click();
  await page.locator('#stop').click();
  await page.evaluate(() => window.pendingCamera());
  await expect.poll(() => page.evaluate(() => window.tracksStopped)).toBe(1);
  expect(await page.evaluate(() => window.frameMessages)).toBe(0);
});

test('unavailable references never enable camera access', async ({ page }) => {
  await page.route('**/bundle/catalog.json', route => route.fulfill({ status: 503, json: { error: 'RECOGNITION_UNAVAILABLE' } }));
  await page.goto('/');
  await expect(page.locator('#status')).toContainText('Museum references are unavailable');
  await expect(page.locator('#start')).toBeDisabled();
});

test('unwarmed model never enables camera access', async ({ page }) => {
  await fakeCamera(page);
  await page.route('**/bundle/catalog.json', route => route.fulfill({ json: {
    ...catalog, readiness: { model_warmed: false, references_current: true },
  } }));
  await page.goto('/');
  await expect(page.locator('#start')).toBeDisabled();
  expect(await page.evaluate(() => window.cameraRequests)).toBe(0);
});

test('adaptive camera sends bounded detail frames and still acquires stable matches', async ({ page }) => {
  await fakeCamera(page);
  await page.addInitScript(() => {
    window.uploadSizes = [];
    const originalEncode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
      const width = this.width, height = this.height;
      return originalEncode.call(this, blob => {
        if (blob) window.uploadSizes.push([width, height, blob.size]);
        callback(blob);
      }, type, quality);
    };
  });
  await page.route('**/bundle/catalog.json', route => route.fulfill({ json: {
    ...catalog, policy: { ...catalog.policy, frame_size: 512,
      recognition_pipeline: 'object-regions-types-v5', minimum_stable_ms: 600 },
  } }));
  await page.route('**/recognize?*', async route => {
    await route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, suggestion_allowed: true,
      candidates: [{ id: ids[0], score: 0.9 }], inferenceMs: 10,
    } });
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.locator('#stop').click();
  const sizes = await page.evaluate(() => window.uploadSizes);
  expect(sizes.length).toBeGreaterThanOrEqual(3);
  expect(sizes.every(size => size[0] === 512 && size[1] === 512 && size[2] <= 256 * 1024)).toBe(true);
});

test('server abstention prevents strong scores from establishing a suggestion', async ({ page }) => {
  await fakeCamera(page);
  await page.route('**/recognize?*', async route => {
    await page.evaluate(() => window.frameMessages++);
    await route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, suggestion_allowed: false,
      candidates: [{ id: ids[0], score: 0.99 }], inferenceMs: 10,
    } });
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect.poll(() => page.evaluate(() => window.frameMessages)).toBeGreaterThanOrEqual(4);
  await expect(page.locator('#result')).toBeHidden();
  await page.locator('#stop').click();
});

test('phone never requests model, worker, or runtime assets', async ({ page }) => {
  await fakeCamera(page);
  const urls = [];
  page.on('request', request => urls.push(request.url()));
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.locator('#stop').click();
  expect(urls.some(url => /model\.onnx|worker\.mjs|model-cache|ort\.|\.wasm/.test(url))).toBe(false);
  await page.getByLabel('Location and settings').click();
  await expect(page.locator('.privacy-note')).toContainText('sent to VisitAdvisor');
});

test('zero-score ordering does not flicker a verified second painting', async ({ page }) => {
  await fakeCamera(page);
  await page.goto('/');
  await page.evaluate(id => { window.testCandidate = id; }, ids[1]);
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 2');
  await page.evaluate(ids => {
    window.resultWasCleared = false;
    new MutationObserver(() => {
      if (document.getElementById('result').hidden) window.resultWasCleared = true;
    }).observe(document.getElementById('result'), { attributes: true, attributeFilter: ['hidden'] });
    window.frameColor = '#607080';
    window.testCandidates = ids.map(id => ({ id, score: 0 }));
  }, ids);
  await expect(page.locator('#status')).toContainText('Rechecking the previous possible match');
  await expect(page.locator('#object-name')).toHaveText('Painting 2');
  await page.evaluate(() => { window.testCandidates = null; });
  await expect(page.locator('#status')).toContainText('Possible match. Check');
  expect(await page.evaluate(() => window.resultWasCleared)).toBe(false);
  await page.evaluate(ids => { window.testCandidates = ids.map(id => ({ id, score: 0 })); }, ids);
  await expect(page.locator('#result')).toBeHidden({ timeout: 2500 });
  await page.locator('#stop').click();
});

test('changed references reload metadata and require a new camera start', async ({ page }) => {
  await fakeCamera(page);
  let catalogReads = 0;
  await page.route('**/bundle/catalog.json', route => {
    catalogReads++;
    return route.fulfill({ json: {
      ...catalog,
      reference_version: (catalogReads > 1 ? 'd' : 'a').repeat(64),
    } });
  });
  await page.route('**/recognize?*', route => route.fulfill({
    status: 409, json: { error: 'REFERENCES_CHANGED' },
  }));
  await page.goto('/');
  await page.locator('#start').click();
  await expect.poll(() => catalogReads).toBe(2);
  await expect(page.locator('#start')).toBeVisible();
  await expect(page.locator('#start')).toBeEnabled();
  await expect(page.locator('#result')).toBeHidden();
  expect(await page.evaluate(() => window.tracksStopped)).toBe(1);
  expect(await page.evaluate(() => window.cameraRequests)).toBe(1);
});

function preparedResponse(object, selectedCatalog = catalog) {
  const text = value => ({ text: value, citations: [{ source_id: 'source', quote: 'Canonical evidence' }] });
  return {
    object_id: object.id, location_id: selectedCatalog.location.id,
    reference_version: selectedCatalog.reference_version, source_ids: ['source'], review_status: 'unreviewed',
    content: { introduction: text(`Introduction for ${object.name}`), facts: [text(`Fact for ${object.name}`)],
      story: text(`Story for ${object.name}`), history: text(`History for ${object.name}`), interesting_fact: null },
  };
}

async function contentCamera(page, count = 2) {
  await fakeCamera(page);
  const fixture = { ...catalog, objects: Array.from({ length: count }, (_, index) => ({
    id: (index + 1).toString(16).padStart(24, '0'), location_id: location,
    name: `Exhibit ${index + 1}`, sources: [{ id: 'source', title: 'Museum record', url: 'https://example.com/evidence' }],
  })) };
  await page.route('**/bundle/catalog.json', route => route.fulfill({ json: fixture }));
  await page.route('**/objects/*/experience?*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2);
    const object = fixture.objects.find(object => object.id === id);
    return route.fulfill({ json: preparedResponse(object, fixture) });
  });
  await page.goto('/');
  await page.evaluate(id => { window.testCandidate = id; }, fixture.objects[0].id);
  await page.locator('#start').click();
  return fixture;
}

test('ten camera objects load their own prepared content and source citations at 320px', async ({ page }) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 320, height: 740 });
  const fixture = await contentCamera(page, 10);
  for (const object of fixture.objects) {
    await page.evaluate(id => { window.testCandidate = id; }, object.id);
    await expect(page.locator('#object-name')).toHaveText(object.name);
    for (const section of ['Introduction', 'Fact', 'Story', 'History']) {
      await expect(page.locator('#experience')).toContainText(`${section} for ${object.name}`);
    }
    await expect(page.locator('#experience')).toContainText('Not yet fact-checked');
    await expect(page.locator('#result-kind')).toHaveText('POSSIBLE MATCH · PLEASE VERIFY');
    await expect(page.locator('#experience')).toContainText('Not available for this object.');
  }
  await page.locator('#experience summary').first().click();
  await expect(page.locator('#experience blockquote').first()).toHaveText('Canonical evidence');
  await expect(page.locator('#experience a').first()).toHaveAttribute('href', 'https://example.com/evidence');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('prepared-content-320.png'), fullPage: true });
  await page.locator('#stop').click();
  await expect(page.locator('#experience')).toBeHidden();
});

for (const [error, status, expected] of [
  ['CONTENT_NOT_PREPARED', 404, 'has not been prepared'],
  ['CONTENT_OUTDATED', 409, 'needs a source update'],
  ['CONTENT_UNAVAILABLE', 503, 'is unavailable'],
]) {
  test(`${error} has an honest fallback without breaking scanning`, async ({ page }) => {
    const fixture = await contentCamera(page);
    await page.route('**/objects/*/experience?*', route => route.fulfill({ status, json: { error } }));
    await expect(page.locator('#experience')).toContainText(expected);
    await expect(page.locator('#object-name')).toHaveText(fixture.objects[0].name);
    await expect(page.locator('#stop')).toBeVisible();
    await page.locator('#stop').click();
  });
}

for (const action of ['switch', 'stop', 'background', 'location']) {
  test(`late content cannot return after ${action}`, async ({ page }) => {
    const fixture = await contentCamera(page);
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let entered = false;
    await page.route('**/objects/*/experience?*', async route => {
      const object = fixture.objects[0];
      if (route.request().url().includes(object.id)) {
        entered = true;
        await held;
        await route.fulfill({ json: preparedResponse(object, fixture) }).catch(() => {});
      } else {
        await route.fulfill({ json: preparedResponse(fixture.objects[1], fixture) });
      }
    });
    await expect.poll(() => entered).toBe(true);
    if (action === 'switch') {
      await page.evaluate(id => { window.testCandidate = id; }, fixture.objects[1].id);
      await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 2');
    } else if (action === 'stop') {
      await page.locator('#stop').click();
    } else if (action === 'background') {
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
        document.dispatchEvent(new Event('visibilitychange'));
      });
    } else {
      await page.locator('#menu summary').first().click();
      await page.locator('#location').dispatchEvent('change');
    }
    release();
    await page.waitForTimeout(300);
    await expect(page.locator('#experience')).not.toContainText('Introduction for Exhibit 1');
    if (action !== 'switch') await expect(page.locator('#result')).toBeHidden();
  });
}

for (const invalid of ['identity', 'location', 'version', 'citation']) {
  test(`mismatched ${invalid} content is never rendered`, async ({ page }) => {
    const fixture = await contentCamera(page);
    await page.route('**/objects/*/experience?*', route => {
      const data = preparedResponse(fixture.objects[0], fixture);
      if (invalid === 'identity') data.object_id = fixture.objects[1].id;
      if (invalid === 'location') data.location_id = 'f'.repeat(24);
      if (invalid === 'version') data.reference_version = 'f'.repeat(64);
      if (invalid === 'citation') data.content.introduction.citations[0].source_id = 'foreign';
      return route.fulfill({ json: data });
    });
    await expect(page.locator('#experience')).toContainText('could not load');
    await expect(page.locator('#experience')).not.toContainText('Introduction for');
    await page.locator('#stop').click();
  });
}

test('sparse prepared content shows missing sections and treats text as text', async ({ page }) => {
  const fixture = await contentCamera(page);
  await page.route('**/objects/*/experience?*', route => {
    const data = preparedResponse(fixture.objects[0], fixture);
    data.content.facts = [];
    data.content.story = data.content.history = null;
    data.content.introduction.text = '<img src=x onerror=alert(1)>';
    return route.fulfill({ json: data });
  });
  await expect(page.locator('#experience')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#experience img')).toHaveCount(0);
  await expect(page.locator('.content-missing')).toHaveCount(4);
  await page.locator('#stop').click();
});
