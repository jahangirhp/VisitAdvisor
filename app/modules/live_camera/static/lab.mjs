import { drawFrame, encodeFrame } from './capture.mjs';

const $ = id => document.getElementById(id);
let catalog;
let previewURL;
let busy = false;

async function loadCatalog() {
  $('retry').hidden = true;
  $('photo').disabled = true;
  $('status').textContent = 'Preparing recognition and checking current references…';
  try {
    const response = await fetch('./bundle/catalog.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('References are unavailable. Retry shortly.');
    catalog = await response.json();
    if (catalog.automatic_identification !== false || !Array.isArray(catalog.objects)) {
      throw new Error('Unsupported recognition configuration.');
    }
    const size = catalog.policy.frame_size || 224;
    if (![224, 512].includes(size)) throw new Error('Unsupported camera frame size.');
    $('frame').width = $('frame').height = size;
    $('supported').textContent = `${catalog.location.name}: ${catalog.objects.map(o => o.name).join(', ')}.`;
    $('status').textContent = 'Ready. Choose a photo to compare.';
    $('photo').disabled = false;
  } catch (error) {
    $('status').textContent = error.message;
    $('retry').hidden = false;
  }
}

async function recognize(file) {
  if (!file || busy || !catalog) return;
  $('result').hidden = true;
  $('previews').hidden = true;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    $('status').textContent = 'Choose a JPEG, PNG or WebP no larger than 5 MiB.';
    return;
  }
  busy = true;
  $('photo').disabled = true;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    if (bitmap.width * bitmap.height > 16_000_000) throw new Error('Choose a photo with at most 16 megapixels.');
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = URL.createObjectURL(file);
    $('original').src = previewURL;
    const canvas = $('frame');
    drawFrame(canvas.getContext('2d'), bitmap, bitmap.width, bitmap.height, canvas.width);
    const blob = await encodeFrame(canvas);
    if (!blob || blob.size > 256 * 1024) throw new Error('The camera crop is too large. Choose a simpler photo.');
    $('previews').hidden = false;
    $('status').textContent = 'Comparing the photo with the supported reference views…';
    const params = new URLSearchParams({ location_id: catalog.location.id, reference_version: catalog.reference_version });
    const start = performance.now();
    const response = await fetch(`./recognize?${params}`, {
      method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob,
    });
    const elapsed = performance.now() - start;
    if (response.status === 429) throw new Error('Recognition is busy. Wait a moment and choose the photo again.');
    if (response.status === 409) {
      catalog = null;
      $('retry').hidden = false;
      throw new Error('References changed. Reload references before testing again.');
    }
    if (!response.ok) throw new Error('Recognition could not finish. Retry shortly.');
    const result = await response.json();
    if (result.reference_version !== catalog.reference_version || result.location_id !== catalog.location.id || result.automatic_identification !== false) {
      throw new Error('Recognition returned an inconsistent reference version. Reload references.');
    }
    $('candidates').replaceChildren();
    for (const candidate of result.candidates || []) {
      const object = catalog.objects.find(o => o.id === candidate.id);
      if (!object || !Number.isFinite(candidate.score)) continue;
      const item = document.createElement('li');
      item.textContent = object.name;
      const detail = document.createElement('span');
      detail.textContent = `Similarity ${candidate.score.toFixed(3)} · ${candidate.reference_views ?? 'unknown'} reference images · ${candidate.coverage_ready ? 'minimum view coverage met' : 'view coverage incomplete'}`;
      item.append(detail);
      $('candidates').append(item);
    }
    const allowed = result.suggestion_allowed === true;
    $('decision').textContent = allowed ? 'Possible match — confirmation required' : 'Suggestion withheld';
    $('timing').textContent = `Request ${Math.round(elapsed)} ms · server inference ${Math.round(result.inferenceMs || 0)} ms`;
    $('reason').textContent = allowed ? 'The leading candidate passed the fitted preview thresholds. This is not automatic identification.' : 'The system needs stronger evidence, better reference coverage or fitted calibration. The ranked candidates below the thresholds are diagnostic results.';
    $('result').hidden = false;
    $('status').textContent = 'Comparison complete. Choose another photo to test.';
  } catch (error) {
    $('status').textContent = error.message;
  } finally {
    bitmap?.close();
    busy = false;
    $('photo').disabled = !catalog;
    $('photo').value = '';
  }
}

$('retry').addEventListener('click', loadCatalog);
$('photo').addEventListener('change', event => recognize(event.target.files[0]));
await loadCatalog();
