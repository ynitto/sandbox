'use strict';

// 登録したリポジトリのフォルダツリーとファイル本文（読むだけ。書き込みは持たない）。
// パスは Node の fs でそのまま読む。Windows では \\wsl$\… も C:\… も fs が読めるので、
// ここでは WSL 表記への変換をしない（変換が要るのは tmux と git だけ）。

const fs = require('fs');
const path = require('path');

const MAX_TEXT = 2 * 1024 * 1024;          // これより大きいテキストは先頭だけ
const MAX_IMAGE = 8 * 1024 * 1024;
// ツリーと検索から外すフォルダ。`.worktrees` は作業フォルダの置き場で、中身はリポジトリの
// もう 1 つの写し——出すと本体のツリーに入れ子の複製が並び、名前検索も worktree の数だけ
// 同じファイルを返す。中を見たいときは上の「見るフォルダ」で作業フォルダを選ぶ。
const SKIP_DIRS = new Set(['.git', '.worktrees']);

const IMAGE_MIME = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
  '.svg': 'image/svg+xml', '.bmp': 'image/bmp', '.ico': 'image/x-icon', '.avif': 'image/avif',
};

// 拡張子 → highlight.js の言語 ID。無いものは '' （画面側で自動判定か plaintext）。
const EXT_LANG = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript', ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'typescript',
  json: 'json', jsonc: 'json', json5: 'json', py: 'python', pyw: 'python', pyi: 'python', rb: 'ruby', rake: 'ruby', gemspec: 'ruby',
  go: 'go', rs: 'rust', java: 'java', kt: 'kotlin', kts: 'kotlin', scala: 'scala', groovy: 'groovy', gradle: 'groovy',
  c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', hxx: 'cpp', ino: 'cpp', cs: 'csharp', fs: 'fsharp', fsx: 'fsharp',
  swift: 'swift', m: 'objectivec', mm: 'objectivec', php: 'php', pl: 'perl', pm: 'perl', lua: 'lua', r: 'r', R: 'r', jl: 'julia', dart: 'dart',
  ex: 'elixir', exs: 'elixir', erl: 'erlang', hs: 'haskell', ml: 'ocaml', mli: 'ocaml', clj: 'clojure', cljs: 'clojure', lisp: 'lisp', el: 'lisp', scm: 'scheme',
  sh: 'bash', bash: 'bash', zsh: 'bash', fish: 'bash', ksh: 'bash', ps1: 'powershell', psm1: 'powershell', bat: 'dos', cmd: 'dos',
  html: 'xml', htm: 'xml', xhtml: 'xml', xml: 'xml', xsl: 'xml', xsd: 'xml', svg: 'xml', plist: 'xml', csproj: 'xml', vue: 'xml', svelte: 'xml',
  css: 'css', scss: 'scss', sass: 'scss', less: 'less', styl: 'stylus',
  md: 'markdown', markdown: 'markdown', mdx: 'markdown', yml: 'yaml', yaml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini', properties: 'properties',
  sql: 'sql', graphql: 'graphql', gql: 'graphql', proto: 'protobuf', tf: 'ini', hcl: 'ini',
  dockerfile: 'dockerfile', makefile: 'makefile', mk: 'makefile', cmake: 'cmake', nginx: 'nginx', vim: 'vim', diff: 'diff', patch: 'diff',
  tex: 'latex', bib: 'latex', txt: 'plaintext', log: 'plaintext', csv: 'plaintext', tsv: 'plaintext', env: 'bash', asm: 'x86asm', s: 'x86asm',
  v: 'verilog', sv: 'verilog', vhd: 'vhdl', vhdl: 'vhdl', f90: 'fortran', f: 'fortran', nim: 'nim', zig: 'zig', wat: 'wasm', coffee: 'coffeescript',
  ipynb: 'json', lock: 'json', nix: 'nix', elm: 'elm', ex_: 'elixir', http: 'http', ejs: 'xml', hbs: 'handlebars', mustache: 'handlebars', twig: 'twig',
};
const NAME_LANG = {
  dockerfile: 'dockerfile', makefile: 'makefile', gnumakefile: 'makefile', cmakelists: 'cmake', 'cmakelists.txt': 'cmake', rakefile: 'ruby', gemfile: 'ruby',
  'package.json': 'json', '.gitignore': 'plaintext', '.gitattributes': 'plaintext', '.editorconfig': 'ini', '.npmrc': 'ini', '.env': 'bash',
  '.bashrc': 'bash', '.zshrc': 'bash', '.profile': 'bash', '.bash_profile': 'bash', 'go.mod': 'go', 'go.sum': 'plaintext', 'cargo.lock': 'ini', 'pipfile': 'ini',
};

function languageOf(name) {
  const base = String(name || '').split(/[\\/]/).pop();
  const lower = base.toLowerCase();
  if (NAME_LANG[lower]) return NAME_LANG[lower];
  const dot = lower.lastIndexOf('.');
  if (dot < 0) return lower.startsWith('dockerfile') ? 'dockerfile' : '';
  const ext = lower.slice(dot + 1);
  if (EXT_LANG[ext]) return EXT_LANG[ext];
  // Dockerfile.dev / Makefile.inc のような「名前.拡張子」
  const stem = lower.slice(0, dot);
  if (NAME_LANG[stem]) return NAME_LANG[stem];
  return '';
}

// 登録フォルダの外へ出ないよう、相対パスを実体で検査する。
function resolveInside(repo, rel) {
  const root = fs.realpathSync(repo);
  const target = path.resolve(root, String(rel || '').replace(/^[\\/]+/, ''));
  if (target !== root && !target.startsWith(root + path.sep)) throw new Error('リポジトリの外は読めません');
  let real;
  try { real = fs.realpathSync(target); } catch { throw new Error('ファイルが見つかりません'); }
  if (real !== root && !real.startsWith(root + path.sep)) throw new Error('リポジトリの外へのリンクは読めません');
  return { root, target, rel: path.relative(root, target).split(path.sep).join('/') };
}

// 1 階層分。ディレクトリ先・名前順（大文字小文字を無視）。.git は出さない。
function listDir(repo, rel = '') {
  const { target, rel: cleanRel } = resolveInside(repo, rel);
  const entries = fs.readdirSync(target, { withFileTypes: true });
  const out = [];
  for (const e of entries) {
    if (SKIP_DIRS.has(e.name) && !cleanRel) continue;   // 外すのはツリーの根だけ（下の同名は出す）
    let type = e.isDirectory() ? 'dir' : e.isSymbolicLink() ? 'link' : 'file';
    let size = 0;
    if (type === 'link') {
      try { const st = fs.statSync(path.join(target, e.name)); type = st.isDirectory() ? 'dir' : 'file'; size = st.size; } catch { /* 切れたリンク */ }
    } else if (type === 'file') {
      try { size = fs.statSync(path.join(target, e.name)).size; } catch { /* 消えた */ }
    }
    out.push({ name: e.name, type, size, rel: cleanRel ? `${cleanRel}/${e.name}` : e.name, language: type === 'file' ? languageOf(e.name) : '' });
  }
  out.sort((a, b) => (a.type === 'dir') === (b.type === 'dir') ? a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }) : (a.type === 'dir' ? -1 : 1));
  return { rel: cleanRel, entries: out };
}

function looksBinary(buf) {
  const n = Math.min(buf.length, 8192);
  for (let i = 0; i < n; i += 1) if (buf[i] === 0) return true;
  return false;
}

// ファイル本文。kind: text | image | binary。大きいテキストは切って truncated を立てる。
function readFile(repo, rel) {
  const { target, rel: cleanRel } = resolveInside(repo, rel);
  const st = fs.statSync(target);
  if (st.isDirectory()) throw new Error('フォルダです');
  const ext = path.extname(target).toLowerCase();
  const base = { rel: cleanRel, name: path.basename(target), size: st.size, mtime: st.mtime.toISOString(), language: languageOf(target) };
  if (IMAGE_MIME[ext]) {
    if (st.size > MAX_IMAGE) return { ...base, kind: 'binary', reason: '画像が大きすぎる' };
    const buf = fs.readFileSync(target);
    return { ...base, kind: 'image', dataUrl: `data:${IMAGE_MIME[ext]};base64,${buf.toString('base64')}` };
  }
  const fd = fs.openSync(target, 'r');
  let buf;
  try {
    const len = Math.min(st.size, MAX_TEXT);
    buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, 0);
  } finally { fs.closeSync(fd); }
  if (looksBinary(buf)) return { ...base, kind: 'binary', reason: 'バイナリ' };
  const text = buf.toString('utf8');
  return { ...base, kind: 'text', text, truncated: st.size > MAX_TEXT, lines: text.split('\n').length };
}

// 名前で探す（ツリーの絞り込み用）。深さ優先で最大 limit 件。node_modules と .git は潜らない。
function find(repo, query, limit = 200) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return [];
  const { root } = resolveInside(repo, '');
  const out = [];
  const walk = (dir, rel, depth) => {
    if (out.length >= limit || depth > 12) return;
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (out.length >= limit) return;
      if ((SKIP_DIRS.has(e.name) && !rel) || e.name === '.git' || e.name === 'node_modules') continue;
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.name.toLowerCase().includes(q)) out.push({ rel: r, type: e.isDirectory() ? 'dir' : 'file', language: e.isDirectory() ? '' : languageOf(e.name) });
      if (e.isDirectory()) walk(path.join(dir, e.name), r, depth + 1);
    }
  };
  walk(root, '', 0);
  return out;
}

module.exports = { languageOf, listDir, readFile, find, resolveInside, MAX_TEXT, EXT_LANG, NAME_LANG };
