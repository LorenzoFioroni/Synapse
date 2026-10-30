// Execute as a page script: DevTools evaluation can bypass the page's eval restriction.
(async () => {
  let wasmAllowed = false;
  try {
    await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
    wasmAllowed = true;
  } catch { /* A missing wasm-unsafe-eval must fail the check. */ }
  window.injected = false;
  const inline = document.createElement('script');
  inline.textContent = 'window.injected = true';
  document.body.append(inline);
  const image = document.createElement('img');
  image.setAttribute('onerror', 'window.injected = true');
  document.body.append(image);
  image.dispatchEvent(new Event('error'));
  let evalBlocked = false;
  try { new Function('window.injected = true')(); } catch (e) { evalBlocked = e instanceof EvalError; }
  const external = document.createElement('script');
  external.src = location.origin.replace('127.0.0.1', 'localhost') + '/js/themeHint.js';
  document.head.append(external);
  const object = document.createElement('object');
  object.data = '/assets/favicon.svg';
  document.body.append(object);
  const workerUrl = URL.createObjectURL(new Blob(['postMessage(1)'], {type: 'text/javascript'}));
  try { new Worker(workerUrl); } catch { /* Blob workers must be refused by worker-src. */ }
  const originalBase = document.baseURI;
  const base = document.createElement('base');
  base.href = 'https://evil.example/';
  document.head.append(base);
  await new Promise(resolve => setTimeout(resolve, 250));
  URL.revokeObjectURL(workerUrl);
  window.cspProbes = {injected: window.injected, evalBlocked, wasmAllowed,
    baseUnchanged: document.baseURI === originalBase, violations: window.cspViolations};
})();
