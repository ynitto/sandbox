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
  ['statemachine-maker/src/renderer/styles.css', 'statemachine/styles.css'],
  ['statemachine-maker/src/renderer/flow.js', 'statemachine/flow.js'],
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
  const automationSource = path.join(ROOT, 'node_modules', 'statemachine-maker', 'src', 'renderer', 'renderer.js');
  if (!fs.existsSync(automationSource)) throw new Error('vendor: statemachine-maker の renderer.js が無い');
  const bridge = `\nconst automationBridge = (() => {\n  if (window.api && window.api.automation) return window.api.automation;\n  try { return window.parent && window.parent.api && window.parent.api.automation; } catch { return null; }\n})();\nif (!automationBridge) throw new Error('自動化機能の接続を初期化できません');\n`;
  // 先に maker 側の API 参照を変換し、その後にブリッジを挿入する。
  // 順序を逆にすると、ブリッジ自身の window.api まで置換される。
  const automationRenderer = fs.readFileSync(automationSource, 'utf8')
    .replace(/\bapi\./g, 'automationBridge.')
    .replace("'use strict';", `'use strict';${bridge}`);
  const automationOut = path.join(OUT, 'statemachine', 'renderer.js');
  fs.mkdirSync(path.dirname(automationOut), { recursive: true });
  fs.writeFileSync(automationOut, automationRenderer);
  for (const lang of HLJS_EXTRA) {
    const from = `@highlightjs/cdn-assets/languages/${lang}.min.js`;
    if (fs.existsSync(path.join(ROOT, 'node_modules', from))) copy(from, path.join(OUT, 'hljs', `${lang}.min.js`));
  }
  fs.writeFileSync(path.join(OUT, 'README.md'), 'npm install（scripts/vendor.js）が生成する。手で編集しない。\n');
  console.log(`vendor: ${FILES.length + 1} files → ${path.relative(ROOT, OUT)}`);
}

if (require.main === module) main();
module.exports = { FILES, HLJS_EXTRA, OUT };
