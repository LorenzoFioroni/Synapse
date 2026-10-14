// Compiles src/**/*.ts to js/ with esbuild: one output file per source file, so the browser loads
// the same module graph as the sources (including lazy import()s) and paths to vendor/ stay valid.
// Source maps let the browser's developer tools show the TypeScript.
//
//   node build.mjs           build once
//   node build.mjs --watch   rebuild on every change
//
// The server (backend/frontend.py) runs this itself when js/ is older than the sources.
import { rmSync, writeFileSync } from 'node:fs';
import { context } from 'esbuild';

const OUT = 'js';
const STAMP = `${OUT}/.build-stamp`;   // its time tells the server when js/ was last built
const watch = process.argv.includes('--watch');

rmSync(OUT, { recursive: true, force: true });   // no stale files from deleted sources
const ctx = await context({
  entryPoints: ['src/**/*.ts'],
  outdir: OUT,
  outbase: 'src',
  format: 'esm',
  target: 'es2022',
  sourcemap: true,
  logLevel: 'warning',
  plugins: [{
    name: 'stamp',
    setup(build) {
      build.onEnd((result) => { if (!result.errors.length) writeFileSync(STAMP, new Date().toISOString()); });
    },
  }],
});
if (watch) {
  await ctx.watch();
  console.log('Watching src/ for changes (Ctrl+C to stop)…');
} else {
  await ctx.rebuild();
  await ctx.dispose();
}
