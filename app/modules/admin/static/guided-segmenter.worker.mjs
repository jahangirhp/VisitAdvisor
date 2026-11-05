// A selected-object model on sampled still frames, never an identity/pose tracker.
// All model/runtime requests are served by this application after local setup.
// Protocol: init{id}->ready{id,model}; segment{id,image:ImageBitmap,point:{x,y}}
// ->result{id,width,height,mask:ArrayBuffer,bbox:[x1,y1,x2,y2],confidence,
// foregroundFraction}. Coordinates normalized; mask bytes are 0/255 at input size.
// Transfer image ownership to this worker (max1M pixels); it always closes it.
// dispose{id}->closed{id} releases the task and worker; failures return error{id,message}.
const ASSET_BASE = '/admin/capture-assets/';
// The pinned SDK has automatic timing telemetry. Block it before importing the
// SDK, and permit only our local asset URLs. No frame, metric or token goes out.
const localFetch = self.fetch.bind(self);
self.fetch = (input, options) => {
  const url = new URL(input instanceof Request ? input.url : input, self.location.href);
  if (url.origin !== self.location.origin || !url.pathname.startsWith(ASSET_BASE)) {
    return Promise.reject(new TypeError('Only local capture assets are allowed.'));
  }
  return localFetch(input, options);
};
const MODEL = Object.freeze({
  name: 'mediapipe-magic-touch-legacy-float32-1',
  sha256: 'e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25',
});
const MAX_PIXELS = 1024 * 1024;
let segmenter = null;
let busy = false;

export function seedIsForeground(mask, width, height, point) {
  // A boundary tap can miss by a few pixels; a distant unrelated island cannot.
  const radius = Math.min(6, Math.max(1, Math.round(Math.min(width, height) * 0.008)));
  const x = Math.min(width - 1, Math.floor(point.x * width));
  const y = Math.min(height - 1, Math.floor(point.y * height));
  for (let row = Math.max(0, y - radius); row <= Math.min(height - 1, y + radius); row += 1) {
    for (let col = Math.max(0, x - radius); col <= Math.min(width - 1, x + radius); col += 1) {
      if (mask[row * width + col]) return true;
    }
  }
  return false;
}

async function initialize() {
  if (segmenter) return;
  if (typeof OffscreenCanvas === 'undefined' || !globalThis.crypto?.subtle) {
    throw new Error('unsupported');
  }
  const { FilesetResolver, InteractiveSegmenterLegacy } = await import(
    `${ASSET_BASE}vision_bundle.mjs`
  );
  const response = await fetch(`${ASSET_BASE}magic_touch.tflite`);
  if (!response.ok) throw new Error('assets');
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== 6227884) throw new Error('assets');
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    (value) => value.toString(16).padStart(2, '0')).join('');
  if (hash !== MODEL.sha256) throw new Error('assets');
  // Module WASM loader is required inside a module worker; no importScripts/CDN.
  const fileset = await FilesetResolver.forVisionTasks(`${ASSET_BASE}wasm`, true);
  // Legacy MagicTouch is selected after the v2 model took 5–8s on this machine;
  // the pinned legacy CPU model measured ~115ms warm on the same input.
  segmenter = await InteractiveSegmenterLegacy.createFromOptions(fileset, {
    baseOptions: { modelAssetBuffer: new Uint8Array(bytes), delegate: 'CPU' },
    canvas: new OffscreenCanvas(1, 1),
    outputConfidenceMasks: true,
    outputCategoryMask: false,
  });
}

function segment(image, point) {
  if (!segmenter) throw new Error('uninitialized');
  if (!image || !Number.isInteger(image.width) || !Number.isInteger(image.height)
    || image.width < 32 || image.height < 32 || image.width * image.height > MAX_PIXELS
    || !point || !Number.isFinite(point.x) || !Number.isFinite(point.y)
    || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1) {
    throw new Error('input');
  }
  let output = null;
  // Read borrowed native masks within the synchronous callback, copy only the
  // binary derivative, and release every result before accepting another frame.
  segmenter.segment(image, { keypoint: point }, (result) => {
    try {
      const mask = result.confidenceMasks?.[0];
      if (!mask) throw new Error('mask');
      output = readMask(mask, image, point);
    } finally {
      result.close();
    }
  });
  if (!output) throw new Error('mask');
  return output;
}

function readMask(result, image, point) {
  const width = result.width;
  const height = result.height;
  const probabilities = result.getAsFloat32Array();
  if (width !== image.width || height !== image.height
    || probabilities.length !== width * height) throw new Error('mask');
  const binary = new Uint8Array(probabilities.length);
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  let sum = 0;
  let count = 0;
  for (let i = 0; i < probabilities.length; i += 1) {
    const value = probabilities[i];
    if (!Number.isFinite(value)) throw new Error('mask');
    if (value < 0.5) continue;
    binary[i] = 255;
    const x = i % width;
    const y = Math.floor(i / width);
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
    sum += value;
    count += 1;
  }
  if (!count || !seedIsForeground(binary, width, height, point)) {
    throw new Error('unrelated-mask');
  }
  return {
    width, height, mask: binary.buffer,
    bbox: [minX / width, minY / height, (maxX + 1) / width, (maxY + 1) / height],
    // Mean mask probability; not a calibrated identity/occlusion confidence.
    confidence: sum / count,
    foregroundFraction: count / binary.length,
  };
}

self.addEventListener('message', async ({ data }) => {
  const { type, id, image, point } = data || {};
  if (busy) {
    image?.close?.();
    self.postMessage({ type: 'error', id, message: 'Segmentation is already running.' });
    return;
  }
  busy = true;
  try {
    if (type === 'init') {
      await initialize();
      self.postMessage({ type: 'ready', id, model: MODEL });
    } else if (type === 'segment') {
      const result = segment(image, point);
      self.postMessage({ type: 'result', id, ...result }, [result.mask]);
    } else if (type === 'dispose') {
      segmenter?.close();
      segmenter = null;
      self.postMessage({ type: 'closed', id });
      self.close();
    } else {
      self.postMessage({ type: 'error', id, message: 'Unsupported capture operation.' });
    }
  } catch {
    // Do not expose WASM internals, resource paths or runtime diagnostics to users.
    self.postMessage({
      type: 'error', id,
      message: type === 'init'
        ? 'The local segmentation model could not start. Use photo upload instead.'
        : 'The target could not be isolated. Tap it again or use photo upload.',
    });
  } finally {
    image?.close?.();
    busy = false;
  }
});
