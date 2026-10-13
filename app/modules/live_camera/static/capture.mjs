// Shared by the live camera and the regression replay. No image storage.
export function drawFrame(context, source, width, height) {
  if (!(width > 0 && height > 0)) throw new Error('Invalid frame dimensions');
  const side = Math.min(width, height) * 224 / 256;
  context.drawImage(source, (width - side) / 2, (height - side) / 2,
    side, side, 0, 0, 224, 224);
  return context.getImageData(0, 0, 224, 224).data;
}

export function encodeFrame(canvas) {
  return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
}
