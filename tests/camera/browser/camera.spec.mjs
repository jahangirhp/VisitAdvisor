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

test.beforeEach(async ({ page }) => {
  await page.route('**/venues?*', route => route.fulfill({ json: [{ id: location, name: 'Test museum' }] }));
});

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
      if (window.testSequence?.length) return window.testSequence.shift();
      return window.testCandidates ?? (window.testCandidate ? [{ id: window.testCandidate, score: 0.9 }] : []);
    });
    await route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, candidates, inferenceMs: 10,
    } });
  });
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ json: catalog }));
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
  await expect(page.locator('#result-kind')).toHaveText('LAST SEEN · CAMERA RECHECKING');
  await expect(page.locator('#object-name')).toHaveText('Painting 2');
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

test('movement preserves the reading card and marks lost tracking last-seen', async ({ page }) => {
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
  await expect(page.locator('#result-kind')).toHaveText('LAST SEEN · CAMERA RECHECKING');
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  expect(await page.evaluate(() => window.resultWasCleared)).toBe(false);
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
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ status: 503, json: { error: 'RECOGNITION_UNAVAILABLE' } }));
  await page.goto('/');
  await expect(page.locator('#status')).toContainText('Venue references are unavailable');
  await expect(page.locator('#start')).toBeDisabled();
});

test('unwarmed model never enables camera access', async ({ page }) => {
  await fakeCamera(page);
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ json: {
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
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ json: {
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
  await expect(page.locator('#result-kind')).toHaveText('LAST SEEN · CAMERA RECHECKING');
  await page.waitForTimeout(1700);
  await expect(page.locator('#object-name')).toHaveText('Painting 2');
  await page.locator('#stop').click();
});

test('changed references reload metadata and require a new camera start', async ({ page }) => {
  await fakeCamera(page);
  let catalogReads = 0;
  await page.route('**/bundle/catalog.json*', route => {
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

async function contentCamera(page, count = 2, overrides = {}) {
  await fakeCamera(page);
  const fixture = { ...catalog, ...overrides, objects: Array.from({ length: count }, (_, index) => ({
    id: (index + 1).toString(16).padStart(24, '0'), location_id: location,
    name: `Exhibit ${index + 1}`, sources: [{ id: 'source', title: 'Museum record', url: 'https://example.com/evidence' }],
  })) };
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ json: fixture }));
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

test('30 seconds of jitter and lost views retain content without refetching', async ({ page }) => {
  test.setTimeout(45000);
  let contentReads = 0;
  page.on('request', request => { if (request.url().includes('/experience?')) contentReads++; });
  const fixture = await contentCamera(page);
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  await page.evaluate(() => {
    window.cardHidden = false;
    window.originalContent = document.querySelector('#experience h3');
    new MutationObserver(() => {
      if (document.getElementById('result').hidden) window.cardHidden = true;
    }).observe(document.getElementById('result'), { attributes: true });
    window.testCandidate = null;
    let bright = false;
    window.jitter = setInterval(() => {
      bright = !bright;
      window.frameColor = bright ? '#708090' : '#506070';
    }, 170);
  });
  await page.waitForTimeout(30000);
  await expect(page.locator('#result-kind')).toHaveText('LAST SEEN · CAMERA RECHECKING');
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  expect(await page.evaluate(() => window.cardHidden)).toBe(false);
  expect(contentReads).toBe(1);
  await page.evaluate(id => {
    clearInterval(window.jitter);
    window.testCandidate = id;
  }, fixture.objects[0].id);
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'live');
  expect(await page.evaluate(() => window.originalContent === document.querySelector('#experience h3'))).toBe(true);
  expect(contentReads).toBe(1);
  await page.locator('#stop').click();
  await expect(page.locator('#result')).toBeHidden();
});

test('one competing frame and tied objects cannot replace a retained card', async ({ page }) => {
  const fixture = await contentCamera(page);
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  const before = await page.evaluate(() => window.frameMessages);
  await page.evaluate(ids => {
    window.testCandidate = null;
    window.testSequence = [
      [{ id: ids[1], score: 0.9 }],
      [{ id: ids[0], score: 0.60 }, { id: ids[1], score: 0.59 }],
      [{ id: ids[1], score: 0 }, { id: ids[0], score: 0 }],
    ];
  }, fixture.objects.map(object => object.id));
  await expect.poll(() => page.evaluate(() => window.frameMessages)).toBeGreaterThanOrEqual(before + 5);
  await expect(page.locator('#object-name')).toHaveText('Exhibit 1');
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
  await page.evaluate(id => { window.testCandidate = id; }, fixture.objects[1].id);
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 2');
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'live');
  await page.locator('#stop').click();
});

for (const event of ['waiting', 'stalled', 'emptied']) {
  test(`${event} retains the card and allows fresh acquisition`, async ({ page }) => {
    const fixture = await contentCamera(page);
    await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
    await page.evaluate(event => {
      window.testCandidate = null;
      document.getElementById('camera').dispatchEvent(new Event(event));
    }, event);
    await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
    await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
    await page.evaluate(id => { window.testCandidate = id; }, fixture.objects[1].id);
    await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 2');
    await page.locator('#stop').click();
  });
}

for (const status of [429, 503]) {
  test(`transient ${status} keeps last-seen content and recovery does not refetch`, async ({ page }) => {
    let contentReads = 0;
    page.on('request', request => { if (request.url().includes('/experience?')) contentReads++; });
    const fixture = await contentCamera(page);
    await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
    let fail = true;
    await page.route('**/recognize?*', route => fail ? route.fulfill({ status, json: { error: 'RECOGNITION_BUSY' } }) : route.fulfill({ json: {
      version: 'camera-server-v1', location_id: location, reference_version: catalog.reference_version,
      automatic_identification: false, candidates: [{ id: fixture.objects[0].id, score: 0.9 }],
    } }));
    await expect(page.locator('#status')).toContainText(status === 429 ? 'Checking the object' : 'Retrying');
    await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
    await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
    fail = false;
    await expect(page.locator('#result')).toHaveAttribute('data-state', 'live');
    expect(contentReads).toBe(1);
    await page.locator('#stop').click();
  });
}

test('persistent transport failures end the session and clear retained content', async ({ page }) => {
  await contentCamera(page);
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  await page.route('**/recognize?*', route => route.fulfill({ status: 503, json: { error: 'RECOGNITION_BUSY' } }));
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
  await expect(page.locator('#start')).toBeVisible({ timeout: 8000 });
  await expect(page.locator('#result')).toBeHidden();
  await expect(page.locator('#experience')).toBeHidden();
});

test('reference invalidation clears retained content and disables scanning', async ({ page }) => {
  await contentCamera(page);
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  await page.route('**/bundle/catalog.json*', route => route.fulfill({ status: 503, json: { error: 'REFERENCES_UNAVAILABLE' } }));
  await page.route('**/recognize?*', route => route.fulfill({ status: 503, json: { error: 'REFERENCES_UNAVAILABLE' } }));
  await expect(page.locator('#result')).toBeHidden();
  await expect(page.locator('#start')).toBeDisabled();
});

test('expired references clear a last-seen card even when no useful frame arrives', async ({ page }) => {
  await contentCamera(page, 2, { expires_at: new Date(Date.now() + 6000).toISOString() });
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  await page.evaluate(() => {
    window.testCandidate = null;
    Object.defineProperty(document.getElementById('camera'), 'videoWidth', { get: () => 0 });
  });
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
  await expect(page.locator('#result')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#start')).toBeDisabled();
});

test('content can finish loading during tracking loss without changing last-seen state', async ({ page }) => {
  const fixture = await contentCamera(page);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let entered = false;
  await page.route('**/objects/*/experience?*', async route => {
    entered = true;
    await held;
    await route.fulfill({ json: preparedResponse(fixture.objects[0], fixture) }).catch(() => {});
  });
  await expect.poll(() => entered).toBe(true);
  await page.evaluate(() => {
    window.testCandidate = null;
    document.getElementById('camera').dispatchEvent(new Event('waiting'));
  });
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
  release();
  await expect(page.locator('#experience')).toContainText('Introduction for Exhibit 1');
  await expect(page.locator('#result')).toHaveAttribute('data-state', 'last-seen');
  await page.locator('#stop').click();
});

for (const candidates of [
  [{ id: ids[0], score: 0.60 }, { id: ids[1], score: 0.59 }],
  [{ id: ids[0], score: 0.50 }],
]) {
  test(`initial uncertain scores ${candidates.map(c => c.score).join('/')} cannot create a card`, async ({ page }) => {
    await fakeCamera(page);
    await page.addInitScript(candidates => { window.testCandidates = candidates; }, candidates);
    await page.goto('/');
    await page.locator('#start').click();
    await expect.poll(() => page.evaluate(() => window.frameMessages)).toBeGreaterThanOrEqual(4);
    await expect(page.locator('#result')).toBeHidden();
    await page.locator('#stop').click();
  });
}

test('wrong-location candidate IDs cannot create a result and retained controls work by keyboard', async ({ page }) => {
  await fakeCamera(page);
  await page.addInitScript(() => { window.testCandidates = [{ id: 'f'.repeat(24), score: 0.99 }]; });
  await page.goto('/');
  const preview = page.getByRole('button', { name: 'Expand camera preview' });
  await preview.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Shrink camera preview' })).toBeFocused();
  await page.keyboard.press('Space');
  await expect(preview).toBeFocused();
  const menu = page.getByLabel('Location and settings');
  await menu.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#location')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).toBeFocused();
  await page.locator('#start').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#status')).toContainText('Retrying');
  await expect(page.locator('#result')).toBeHidden();
  await page.locator('#stop').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#start')).toBeVisible();
});


test('all saved venues are selectable and an unprepared venue clears scanning', async ({ page }) => {
  await fakeCamera(page);
  const other = 'd'.repeat(24);
  await page.route('**/venues?*', route => route.fulfill({ json: [
    { id: location, name: 'Test museum' }, { id: other, name: 'New venue' },
  ] }));
  await page.route('**/bundle/catalog.json*', route => {
    const selected = new URL(route.request().url()).searchParams.get('location_id');
    return selected === other
      ? route.fulfill({ status: 503, json: { error: 'NO_PREPARED_REFERENCES' } })
      : route.fulfill({ json: catalog });
  });
  await page.goto('/');
  await expect(page.locator('#start')).toBeEnabled();
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Painting 1');
  await page.locator('#menu summary').click();
  await page.locator('#location').selectOption(other);
  await expect(page.locator('#status')).toContainText('no prepared photo references');
  await expect(page.locator('#start')).toBeDisabled();
  await expect(page.locator('#result')).toBeHidden();
  expect(await page.evaluate(() => window.tracksStopped)).toBe(1);
  await page.locator('#menu summary').click();
  await expect(page.locator('#location')).toBeEnabled();
  await page.locator('#location').selectOption(location);
  await expect(page.locator('#start')).toBeEnabled();
});

test('full venue catalog recognizes an object beyond the old twelve-object limit', async ({ page }) => {
  await fakeCamera(page);
  const full = structuredClone(catalog);
  full.objects = Array.from({length: 113}, (_, index) => ({
    id: index.toString(16).padStart(24, '0'), location_id: location,
    name: index === 112 ? 'Santa train' : `Object ${index}`, sources: [],
  }));
  await page.route('**/bundle/catalog.json*', route => route.fulfill({json: full}));
  await page.goto('/');
  await expect(page.locator('#start')).toBeEnabled();
  await page.evaluate(id => { window.testCandidate = id; }, full.objects.at(-1).id);
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Santa train');
  await page.locator('#stop').click();
});

test('busy responses wait and recover without consuming connection retries', async ({ page }) => {
  await fakeCamera(page);
  let requests = 0;
  await page.route('**/recognize?*', async route => {
    requests++;
    if (requests <= 2 || requests === 7) return route.fulfill({
      status: 503, json: {error: 'RECOGNITION_UNAVAILABLE'},
    });
    if (requests <= 6) return route.fulfill({
      status: 429, headers: {'Retry-After': '1'}, json: {error: 'RECOGNITION_BUSY'},
    });
    return route.fulfill({json: {
      version: 'camera-server-v1', location_id: location,
      reference_version: catalog.reference_version, automatic_identification: false,
      candidates: [{id: ids[0], score: 0.9}], inferenceMs: 10,
    }});
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#status')).toHaveText('Checking the object…');
  await expect(page.locator('#stop')).toBeVisible();
  await expect(page.locator('#object-name')).toHaveText('Painting 1', {timeout: 15000});
  expect(requests).toBeGreaterThan(7);
  await page.locator('#stop').click();
});

test('stop cancels a busy response retry', async ({ page }) => {
  await fakeCamera(page);
  let requests = 0;
  await page.route('**/recognize?*', route => {
    requests++;
    return route.fulfill({status: 429, headers: {'Retry-After': '1'}, json: {error: 'RECOGNITION_BUSY'}});
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#status')).toHaveText('Checking the object…');
  await page.locator('#stop').click();
  await page.waitForTimeout(1500);
  expect(requests).toBe(1);
  await expect(page.locator('#status')).toHaveText('Camera stopped.');
});

test('a detailed object is acquired despite small hand movement during server checks', async ({page}) => {
  await fakeCamera(page);
  await page.addInitScript(() => {
    window.handShift = 0;
    CanvasRenderingContext2D.prototype.drawImage = function () {
      const size = this.canvas.width, pixels = this.createImageData(size, size);
      for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
          const sx = x - window.handShift;
          const inside = sx >= 35 && sx < 189 && y >= 35 && y < 189;
          const value = inside ? (((sx >> 2) + (y >> 2)) % 2 ? 250 : 90) : 20;
          const i = (y * size + x) * 4;
          pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value;
          pixels.data[i + 3] = 255;
        }
      }
      this.putImageData(pixels, 0, 0);
    };
  });
  const santaCatalog = structuredClone(catalog);
  santaCatalog.objects[0].name = 'Santa train';
  santaCatalog.objects[0].type = 'sculpture';
  await page.route('**/bundle/catalog.json*', route => route.fulfill({json: santaCatalog}));
  let requests = 0;
  await page.route('**/recognize?*', async route => {
    requests++;
    await page.evaluate(() => { window.handShift = window.handShift === 0 ? 6 : 0; });
    await page.waitForTimeout(120);
    await route.fulfill({json: {
      version: 'camera-server-v1', location_id: location,
      reference_version: catalog.reference_version, automatic_identification: false,
      candidates: [{id: ids[0], score: 0.9}], inferenceMs: 100,
    }});
  });
  await page.goto('/');
  await page.locator('#start').click();
  await expect(page.locator('#object-name')).toHaveText('Santa train', {timeout: 5000});
  expect(requests).toBeGreaterThanOrEqual(3);
  await expect(page.locator('#status')).toContainText('Possible match');
  await page.locator('#stop').click();
});
