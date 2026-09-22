// Keep runtime and model requests same-origin. Never use a CDN during a visit.
import { copyFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const destination = resolve('.local/live-camera/runtime');
await mkdir(destination, { recursive: true });
for (const name of [
  'ort.wasm.min.mjs',
  'ort-wasm-simd-threaded.mjs',
  'ort-wasm-simd-threaded.wasm',
]) {
  await copyFile(resolve('node_modules/onnxruntime-web/dist', name), resolve(destination, name));
}
await copyFile(resolve('app/modules/live_camera/static/ORT_LICENSE'), resolve(destination, 'LICENSE'));
await copyFile(resolve('app/modules/live_camera/static/MODEL_LICENSE'), resolve('.local/live-camera/MODEL_LICENSE'));
console.log('Prepared local ONNX Runtime Web assets.');
