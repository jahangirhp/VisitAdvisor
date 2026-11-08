// WebKit's Playwright request interception omits Blob and multipart file bytes.
// Inspect the browser's submitted bodies so assertions cover the actual bytes.
export async function captureSubmittedForms(page) {
  await page.addInitScript(() => {
    const send = window.fetch.bind(window);
    window.__submittedForms = [];
    window.__submittedBlobs = [];
    window.fetch = (input, options) => {
      const path = new URL(input instanceof Request ? input.url : input, location.href).pathname;
      if (options?.body instanceof FormData) {
        window.__submittedForms.push({ path, form: options.body });
      } else if (options?.body instanceof Blob) {
        window.__submittedBlobs.push({ path, blob: options.body });
      }
      return send(input, options);
    };
  });
}

export async function submittedBlob(page, request) {
  return Buffer.from(await page.evaluate(async path => {
    const index = window.__submittedBlobs.findIndex(item => item.path === path);
    if (index < 0) throw new Error('No matching photo analysis submission');
    const { blob } = window.__submittedBlobs.splice(index, 1)[0];
    return [...new Uint8Array(await blob.arrayBuffer())];
  }, new URL(request.url()).pathname));
}

export async function submittedForm(page, request) {
  const entries = await page.evaluate(async path => {
    const index = window.__submittedForms.findIndex(item => item.path === path);
    if (index < 0) throw new Error('No matching form submission');
    const { form } = window.__submittedForms.splice(index, 1)[0];
    return Promise.all([...form].map(async ([name, value]) => [name,
      typeof value === 'string' ? value : { bytes: [...new Uint8Array(await value.arrayBuffer())],
        type: value.type, name: value.name }]));
  }, new URL(request.url()).pathname);
  const form = new FormData();
  for (const [name, value] of entries) {
    if (typeof value === 'string') form.append(name, value);
    else form.append(name, new Blob([new Uint8Array(value.bytes)], { type: value.type }), value.name);
  }
  return form;
}
