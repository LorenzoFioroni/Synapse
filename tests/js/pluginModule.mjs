// Compile a contributed plugin's pure module while retaining the host's public SDK identity.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const { buildSync } = createRequire(new URL('../../frontend/package.json', import.meta.url))('esbuild');
export async function pluginModule(path) {
  const compiled = buildSync({ entryPoints: [fileURLToPath(new URL('../../plugins/' + path, import.meta.url))],
    bundle: true, write: false, format: 'esm', target: 'es2022',
    external: ['*/frontend/src/plugins/markdown.js', '*/frontend/src/plugins/task-runtime.js'] }).outputFiles[0].text;
  const code = compiled.replace(/(["'])[^"']*frontend\/src\/plugins\/(markdown|task-runtime)\.js\1/g,
    (_, quote, name) => JSON.stringify(new URL(`../../frontend/js/plugins/${name}.js`, import.meta.url).href));
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
