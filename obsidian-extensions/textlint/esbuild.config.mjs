import esbuild from 'esbuild';
import process from 'process';
import builtins from 'builtin-modules';
import { copyFileSync, existsSync } from 'fs';

const prod = process.argv[2] === 'production';

const context = await esbuild.context({
  entryPoints: ['src/main.ts'],
  bundle: true,
  external: [
    'obsidian',
    'electron',
    '@codemirror/autocomplete',
    '@codemirror/collab',
    '@codemirror/commands',
    '@codemirror/language',
    '@codemirror/lint',
    '@codemirror/search',
    '@codemirror/state',
    '@codemirror/view',
    '@lezer/common',
    '@lezer/highlight',
    '@lezer/lr',
    ...builtins,
  ],
  format: 'cjs',
  target: 'es2018',
  logLevel: 'info',
  sourcemap: prod ? false : 'inline',
  treeShaking: true,
  outfile: 'main.js',
  minify: prod,
  plugins: [
    {
      name: 'copy-worker',
      setup(build) {
        build.onEnd(() => {
          const workerSrc = 'worker-dist/textlint-worker.js';
          if (existsSync(workerSrc)) {
            copyFileSync(workerSrc, 'textlint-worker.js');
          }
        });
      },
    },
  ],
});

if (prod) {
  await context.rebuild();
  process.exit(0);
} else {
  await context.watch();
}
