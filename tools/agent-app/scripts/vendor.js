'use strict';

// 画面で使う外部ライブラリの配布物を node_modules から src/renderer/vendor/ へ写す。
// index.html は CSP（script-src 'self'）の下で file:// から読むので、CDN は使えず
// node_modules を直接参照する相対パスも壊れやすい。npm install のあと（postinstall）に走る。

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'src', 'renderer', 'vendor');

// [node_modules 内のパス, vendor/ 内の名前]
const FILES = [
  ['@xterm/xterm/lib/xterm.js', 'xterm.js'],
  ['@xterm/xterm/css/xterm.css', 'xterm.css'],
  ['@xterm/addon-fit/lib/addon-fit.js', 'addon-fit.js'],
  ['@highlightjs/cdn-assets/highlight.min.js', 'highlight.min.js'],
  ['@highlightjs/cdn-assets/styles/github.min.css', 'highlight-github.min.css'],
  ['marked/lib/marked.umd.js', 'marked.umd.js'],
  ['dompurify/dist/purify.min.js', 'purify.min.js'],
  ['mermaid/dist/mermaid.min.js', 'mermaid.min.js'],
  ['diff2html/bundles/js/diff2html-ui.min.js', 'diff2html-ui.min.js'],
  ['diff2html/bundles/css/diff2html.min.css', 'diff2html.min.css'],
];

// highlight.js の追加言語（highlight.min.js の同梱セットに無いもの）
const HLJS_EXTRA = ['dockerfile', 'powershell', 'vim', 'nginx', 'x86asm', 'protobuf', 'elixir', 'erlang', 'haskell', 'julia', 'ocaml', 'fortran', 'verilog', 'vhdl'];

function copy(from, to) {
  const src = path.join(ROOT, 'node_modules', from);
  if (!fs.existsSync(src)) throw new Error(`vendor: ${from} が無い（npm install が済んでいるか）`);
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.copyFileSync(src, to);
}

function main() {
  fs.rmSync(OUT, { recursive: true, force: true });
  for (const [from, name] of FILES) copy(from, path.join(OUT, name));
  for (const lang of HLJS_EXTRA) {
    const from = `@highlightjs/cdn-assets/languages/${lang}.min.js`;
    if (fs.existsSync(path.join(ROOT, 'node_modules', from))) copy(from, path.join(OUT, 'hljs', `${lang}.min.js`));
  }
  fs.writeFileSync(path.join(OUT, 'README.md'), 'npm install（scripts/vendor.js）が生成する。手で編集しない。\n');
  console.log(`vendor: ${FILES.length} files → ${path.relative(ROOT, OUT)}`);
}

if (require.main === module) main();
module.exports = { FILES, HLJS_EXTRA, OUT };
