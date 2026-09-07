'use strict';

// 登録したリポジトリのフォルダツリーとファイル本文（読むだけ。書き込みは持たない）。
// パスは Node の fs でそのまま読む。Windows では \\wsl$\… も C:\… も fs が読めるので、
// ここでは WSL 表記への変換をしない（変換が要るのは tmux と git だけ）。
//
// **すべて非同期で読む。** main プロセスの同期 I/O は IPC 全体（端末ミラーの capture-pane
// も含む）を止める。Windows で \\wsl$\ 越しに読むと stat 1 回が数 ms〜数十 ms かかり、
// 数百件のフォルダを同期で stat すると画面が目に見えて固まった。readdir と stat は
// 並列に撃ち（上限付き）、名前検索はフォルダ全体を歩いた索引を少しの間だけ覚えて、
// 1 文字打つごとにツリーを歩き直さない。

const fs = require('fs');
const path = require('path');

const fsp = fs.promises;

const MAX_TEXT = 2 * 1024 * 1024;          // これより大きいテキストは先頭だけ
const MAX_IMAGE = 8 * 1024 * 1024;
// ツリーと検索から外すフォルダ。`.worktrees` は作業フォルダの置き場で、中身はリポジトリの
// もう 1 つの写し——出すと本体のツリーに入れ子の複製が並び、名前検索も worktree の数だけ
// 同じファイルを返す。中を見たいときは上の「見るフォルダ」で作業フォルダを選ぶ。
const SKIP_DIRS = new Set(['.git', '.worktrees']);
// 名前検索が潜らないフォルダ（どの深さでも）。生成物・依存の置き場で、中身は数万件に
// なりやすく、名前で探したい物がまず無い。ツリーでは普通に開ける（潜らないのは検索だけ）。
const SEARCH_SKIP_DIRS = new Set([
  '.git', '.worktrees', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', '.mypy_cache', '.pytest_cache',
  '.cache', '.gradle', '.idea', '.vs', '.next', '.nuxt', 'dist', 'build', 'target', 'coverage',
]);
const SEARCH_MAX_DEPTH = 12;
const INDEX_MAX_ENTRIES = 100000;          // 索引に載せる最大件数（超えたら truncated）
const INDEX_BUILD_MS = 10000;              // 索引作りに使う最長時間（超えたら truncated）
const INDEX_TTL_MS = 60000;                // 索引を覚えておく時間（「更新」で捨てられる）
const IO_CONCURRENCY = 16;                 // 同時に撃つ stat / readdir の数

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

function insideOf(root, target) {
  return target === root || target.startsWith(root + path.sep);
}

// 登録フォルダの外へ出ないよう、相対パスを実体で検査する（同期版。添付の検査など、
// I/O が 1〜2 回で済む所だけが使う）。
function resolveInside(repo, rel) {
  const root = fs.realpathSync(repo);
  const target = path.resolve(root, String(rel || '').replace(/^[\\/]+/, ''));
  if (!insideOf(root, target)) throw new Error('リポジトリの外は読めません');
  let real;
  try { real = fs.realpathSync(target); } catch { throw new Error('ファイルが見つかりません'); }
  if (!insideOf(root, real)) throw new Error('リポジトリの外へのリンクは読めません');
  return { root, target, rel: path.relative(root, target).split(path.sep).join('/') };
}

// 同じ検査の非同期版。ツリー・本文・検索はこちらを使う（realpath も UNC 越しでは往復が要る）。
async function resolveInsideAsync(repo, rel) {
  const root = await fsp.realpath(repo);
  const target = path.resolve(root, String(rel || '').replace(/^[\\/]+/, ''));
  if (!insideOf(root, target)) throw new Error('リポジトリの外は読めません');
  let real;
  try { real = await fsp.realpath(target); } catch { throw new Error('ファイルが見つかりません'); }
  if (!insideOf(root, real)) throw new Error('リポジトリの外へのリンクは読めません');
  return { root, target, rel: path.relative(root, target).split(path.sep).join('/') };
}

// 配列の各要素に非同期関数を、同時 limit 件まで並列に当てる（順序は保つ）。
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next;
      next += 1;
      out[i] = await fn(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

function sortEntries(out) {
  out.sort((a, b) => (a.type === 'dir') === (b.type === 'dir') ? a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }) : (a.type === 'dir' ? -1 : 1));
  return out;
}

// 1 階層分。ディレクトリ先・名前順（大文字小文字を無視）。.git は出さない。
async function listDir(repo, rel = '') {
  const { target, rel: cleanRel } = await resolveInsideAsync(repo, rel);
  const entries = await fsp.readdir(target, { withFileTypes: true });
  const kept = entries.filter((e) => !(SKIP_DIRS.has(e.name) && !cleanRel));   // 外すのはツリーの根だけ（下の同名は出す）
  const out = await mapLimit(kept, IO_CONCURRENCY, async (e) => {
    let type = e.isDirectory() ? 'dir' : e.isSymbolicLink() ? 'link' : 'file';
    let size = 0;
    if (type === 'link') {
      try { const st = await fsp.stat(path.join(target, e.name)); type = st.isDirectory() ? 'dir' : 'file'; size = st.size; } catch { type = 'file'; /* 切れたリンク */ }
    } else if (type === 'file') {
      try { size = (await fsp.stat(path.join(target, e.name))).size; } catch { /* 消えた */ }
    }
    return { name: e.name, type, size, rel: cleanRel ? `${cleanRel}/${e.name}` : e.name, language: type === 'file' ? languageOf(e.name) : '' };
  });
  return { rel: cleanRel, entries: sortEntries(out) };
}

function looksBinary(buf) {
  const n = Math.min(buf.length, 8192);
  for (let i = 0; i < n; i += 1) if (buf[i] === 0) return true;
  return false;
}

// ファイル本文。kind: text | image | binary。大きいテキストは切って truncated を立てる。
async function readFile(repo, rel) {
  const { target, rel: cleanRel } = await resolveInsideAsync(repo, rel);
  const st = await fsp.stat(target);
  if (st.isDirectory()) throw new Error('フォルダです');
  const ext = path.extname(target).toLowerCase();
  const base = { rel: cleanRel, name: path.basename(target), size: st.size, mtime: st.mtime.toISOString(), language: languageOf(target) };
  if (IMAGE_MIME[ext]) {
    if (st.size > MAX_IMAGE) return { ...base, kind: 'binary', reason: '画像が大きすぎる' };
    const buf = await fsp.readFile(target);
    return { ...base, kind: 'image', dataUrl: `data:${IMAGE_MIME[ext]};base64,${buf.toString('base64')}` };
  }
  const fd = await fsp.open(target, 'r');
  let buf;
  try {
    const len = Math.min(st.size, MAX_TEXT);
    buf = Buffer.alloc(len);
    const { bytesRead } = await fd.read(buf, 0, len, 0);
    if (bytesRead < len) buf = buf.subarray(0, bytesRead);
  } finally { await fd.close(); }
  if (looksBinary(buf)) return { ...base, kind: 'binary', reason: 'バイナリ' };
  const text = buf.toString('utf8');
  return { ...base, kind: 'text', text, truncated: st.size > MAX_TEXT, lines: text.split('\n').length };
}

// ---- 名前検索 -------------------------------------------------------------------

// フォルダ全体の索引（root の実体パス → { at, entries, truncated }）。作っている最中の
// Promise も同じ表に置き、同時に来た検索が二重に歩かないようにする。
const indexes = new Map();

// 幅優先で歩く。浅い階層から順に載るので、件数や時間で切っても「近い所」は必ず載る
// （深さ優先だと node_modules 相当の 1 本に潜っている間に打ち切られる）。
async function buildIndex(root, { now = Date.now, maxEntries = INDEX_MAX_ENTRIES, budgetMs = INDEX_BUILD_MS } = {}) {
  const startedAt = now();
  const entries = [];
  let truncated = false;
  let level = [{ dir: root, rel: '', depth: 0 }];
  while (level.length && !truncated) {
    const nextLevel = [];
    await mapLimit(level, IO_CONCURRENCY, async ({ dir, rel, depth }) => {
      if (truncated) return;
      let items;
      try { items = await fsp.readdir(dir, { withFileTypes: true }); } catch { return; }
      for (const e of items) {
        const isDir = e.isDirectory();
        if (isDir && SEARCH_SKIP_DIRS.has(e.name)) continue;
        if (entries.length >= maxEntries) { truncated = true; return; }
        const r = rel ? `${rel}/${e.name}` : e.name;
        entries.push({ rel: r, name: e.name, type: isDir ? 'dir' : 'file', language: isDir ? '' : languageOf(e.name) });
        if (isDir && depth + 1 <= SEARCH_MAX_DEPTH) nextLevel.push({ dir: path.join(dir, e.name), rel: r, depth: depth + 1 });
      }
      if (now() - startedAt > budgetMs) truncated = true;
    });
    level = nextLevel;
  }
  return { at: now(), entries, truncated };
}

async function indexFor(root, { refresh = false } = {}) {
  const hit = indexes.get(root);
  if (hit && !refresh) {
    if (hit.promise) return hit.promise;
    if (Date.now() - hit.at < INDEX_TTL_MS) return hit;
  }
  const promise = buildIndex(root).then((built) => { indexes.set(root, built); return built; }, (err) => { indexes.delete(root); throw err; });
  indexes.set(root, { promise });
  return promise;
}

// 索引の中を名前で探す（純関数。順序は 前方一致 → 部分一致、それぞれ浅い順）。
// query に `/` があればパス全体で探す（`src/ind` のように場所を絞れる）。
function searchIndex(entries, query, limit) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return [];
  const byPath = q.includes('/');
  const prefix = [];
  const contains = [];
  for (const e of entries) {
    const hay = (byPath ? e.rel : e.name).toLowerCase();
    if (hay.startsWith(q)) prefix.push(e);
    else if (hay.includes(q)) contains.push(e);
    if (prefix.length >= limit) break;
  }
  return [...prefix, ...contains].slice(0, limit).map(({ rel, type, language }) => ({ rel, type, language }));
}

// 名前で探す（ツリーの絞り込み用）。最大 limit 件。索引を使い回すので 2 回目からは歩かない。
//   refresh … 索引を捨てて作り直す（ツリーの「更新」）
// 返り値: { hits, truncated（索引が途中で打ち切られた）, indexed（索引の件数） }
async function find(repo, query, limit = 200, { refresh = false } = {}) {
  const q = String(query || '').trim().toLowerCase();
  const { root } = await resolveInsideAsync(repo, '');
  if (!q && !refresh) return { hits: [], truncated: false, indexed: 0 };
  const index = await indexFor(root, { refresh });
  return { hits: searchIndex(index.entries, q, limit), truncated: index.truncated, indexed: index.entries.length };
}

function forgetIndex(repo) {
  if (repo == null) { indexes.clear(); return; }
  let root = String(repo);
  try { root = fs.realpathSync(repo); } catch { /* 登録のままの表記で消す */ }
  indexes.delete(root);
}

module.exports = {
  languageOf, listDir, readFile, find, resolveInside, resolveInsideAsync, buildIndex, searchIndex, forgetIndex, mapLimit,
  MAX_TEXT, EXT_LANG, NAME_LANG, SKIP_DIRS, SEARCH_SKIP_DIRS, SEARCH_MAX_DEPTH, INDEX_MAX_ENTRIES, INDEX_TTL_MS,
};
