'use strict';

// プロジェクト = 役割を付けたリポジトリの束と、その知識の置き場。
//
// 定義はナレッジリポジトリの `projects/<フォルダ>/project.yaml` に 1 枚だけ置き、全 PC で共有する。
// 書くのは人が編集画面で保存したときだけで、実行の状態・履歴・ログは持たない（大きくならない）。
// 読むのは起動時・プロジェクトを選んだとき・編集画面を開いたときだけで、そのときファイルの時刻を
// 見て変わった分だけ読み直す。監視も定期的な fetch もしない（他の PC の変更は利用者の pull で届く）。
//
// リポジトリは git の URL で書く（PC に依存しない）。この PC のフォルダとの対応は config.json の
// repoPaths が持つ。ここはファイルと文字列だけを扱い、git やホストのシェルには触らない（ipc が持つ）。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

const DIR = 'projects';
const FILE = 'project.yaml';
const SHARED_DIR = 'shared';
const MAX_BYTES = 16 * 1024;
const MAX_REPOS = 20;
const MAX_TEXT = 2000;
const ROLES = ['main', 'work', 'reference'];
const ROLE_LABEL = { main: '主', work: '作業', reference: '参照' };
const KINDS = {
  note: { label: 'メモ', dir: 'notes' },
  decision: { label: '決めたこと', dir: 'decisions' },
  rule: { label: '守ること', file: 'rules.md' },
};

const text = (value, max = MAX_TEXT) => String(value == null ? '' : value).trim().slice(0, max);

// フォルダ名。人が付けた名前から、どの OS でもフォルダにできる形を作る（日本語はそのまま）。
function folderName(name) {
  const cleaned = String(name || '').trim()
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/^\.+/, '')
    .slice(0, 60)
    .replace(/[-.]+$/, '');
  return cleaned || 'project';
}

function globs(value) {
  const list = Array.isArray(value) ? value : String(value || '').split(/[,\s]+/);
  return [...new Set(list.map((item) => text(item, 200)).filter(Boolean))].slice(0, 20);
}

// 定義の形をそろえる。主は 1 つだけ（無ければ最初の作業リポジトリ、それも無ければ最初の 1 つ）。
function normalize(raw) {
  const source = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const seen = new Set();
  const repos = [];
  for (const item of Array.isArray(source.repos) ? source.repos : []) {
    if (!item || typeof item !== 'object') continue;
    const url = text(item.url, 500);
    if (!url || seen.has(normalizeUrl(url))) continue;
    seen.add(normalizeUrl(url));
    const repo = { url, role: ROLES.includes(item.role) ? item.role : 'reference', desc: text(item.desc, 300) };
    const owns = globs(item.owns);
    if (owns.length) repo.owns = owns;
    repos.push(repo);
    if (repos.length >= MAX_REPOS) break;
  }
  let main = repos.findIndex((repo) => repo.role === 'main');
  for (let i = 0; i < repos.length; i += 1) if (repos[i].role === 'main' && i !== main) repos[i].role = 'work';
  if (main < 0 && repos.length) {
    main = Math.max(0, repos.findIndex((repo) => repo.role === 'work'));
    repos[main].role = 'main';
  }
  return {
    version: 1,
    name: text(source.name, 80) || 'プロジェクト',
    repos,
    instructions: text(source.instructions),
  };
}

function parse(raw) {
  if (Buffer.byteLength(String(raw || ''), 'utf8') > MAX_BYTES) throw new Error(`${FILE} が大きすぎます（${MAX_BYTES / 1024} KB まで）`);
  const data = YAML.parse(String(raw || '')) || {};
  if (typeof data !== 'object' || Array.isArray(data)) throw new Error(`${FILE} の形が違います`);
  if (data.version != null && Number(data.version) !== 1) throw new Error(`${FILE} の version ${data.version} は読めません`);
  return normalize(data);
}

function serialize(project) {
  const p = normalize(project);
  const body = { version: 1, name: p.name, repos: p.repos };
  if (p.instructions) body.instructions = p.instructions;
  const out = `# agent-app のプロジェクト定義。画面の「プロジェクト」から編集する\n${YAML.stringify(body, { lineWidth: 0 })}`;
  if (Buffer.byteLength(out, 'utf8') > MAX_BYTES) throw new Error(`${FILE} が大きすぎます（${MAX_BYTES / 1024} KB まで）`);
  return out;
}

// URL の比べ方。scp 形式（git@host:a/b.git）と https を同じものとして扱い、資格情報と .git を落とす。
function normalizeUrl(url) {
  let s = String(url || '').trim();
  if (!s) return '';
  const scp = /^[^@\s/]+@([^:\s/]+):(.+)$/.exec(s);
  if (scp) s = `${scp[1]}/${scp[2]}`;
  else s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').replace(/^[^@/]*@/, '');
  s = s.replace(/^([^/]+):\d+\//, '$1/');
  return s.replace(/\/+$/, '').replace(/\.git$/i, '').replace(/^([^/]+)/, (host) => host.toLowerCase());
}

// URL の末尾（画面に出す短い名前）
function repoLabel(url) {
  const tail = normalizeUrl(url).split('/').filter(Boolean).pop();
  return tail || String(url || '');
}

function keyOf(kb, folder) { return `${kb}#${folder}`; }
function splitKey(key) {
  const s = String(key || '');
  const at = s.lastIndexOf('#');
  if (at <= 0) return null;
  const folder = s.slice(at + 1);
  if (!folder || folder !== path.basename(folder) || folder.startsWith('.')) return null;
  return { kb: s.slice(0, at), folder };
}

function projectFile(kb, folder) { return path.join(kb, DIR, folder, FILE); }

// ファイル時刻と大きさが同じなら前回読んだものを返す（読み直しは中身が変わったときだけ）
const cache = new Map();
function readFile(file) {
  const st = fs.statSync(file);
  const hit = cache.get(file);
  if (hit && hit.mtimeMs === st.mtimeMs && hit.size === st.size) return hit.project;
  if (st.size > MAX_BYTES) throw new Error(`${FILE} が大きすぎます（${MAX_BYTES / 1024} KB まで）`);
  const project = parse(fs.readFileSync(file, 'utf8'));
  cache.set(file, { mtimeMs: st.mtimeMs, size: st.size, project });
  return project;
}

// ナレッジリポジトリにあるプロジェクトの一覧。読めない定義は error を付けて並べる（黙って消さない）。
function list(kbDirs) {
  const out = [];
  for (const kb of [...new Set((kbDirs || []).map(String).filter(Boolean))]) {
    let names = [];
    try { names = fs.readdirSync(path.join(kb, DIR), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name); }
    catch { continue; }
    for (const folder of names.sort()) {
      const file = projectFile(kb, folder);
      if (!fs.existsSync(file)) continue;
      try { out.push({ key: keyOf(kb, folder), kb, folder, project: readFile(file), error: '' }); }
      catch (error) { out.push({ key: keyOf(kb, folder), kb, folder, project: null, error: error.message }); }
    }
  }
  return out;
}

function read(key) {
  const parts = splitKey(key);
  if (!parts) throw new Error('プロジェクトが見つかりません');
  const file = projectFile(parts.kb, parts.folder);
  if (!fs.existsSync(file)) throw new Error('プロジェクトが見つかりません（定義ファイルが無い）');
  return { key: keyOf(parts.kb, parts.folder), ...parts, project: readFile(file), error: '' };
}

// 定義を書く。新しいプロジェクトならフォルダと索引の雛形も作る。書いたファイル（kb からの相対）を返す。
function write(kb, folder, project) {
  const dir = path.join(kb, DIR, folder);
  fs.mkdirSync(dir, { recursive: true });
  const files = [];
  const file = path.join(dir, FILE);
  const body = serialize(project);
  const temp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(temp, body, 'utf8');
  fs.renameSync(temp, file);
  cache.delete(file);
  files.push(path.posix.join(DIR, folder, FILE));
  const readme = path.join(dir, 'README.md');
  if (!fs.existsSync(readme)) {
    fs.writeFileSync(readme, `# ${normalize(project).name}\n\n## 索引\n\n`, 'utf8');
    files.push(path.posix.join(DIR, folder, 'README.md'));
  }
  return files;
}

// 定義のリポジトリに、この PC のフォルダを当てる。
//   repoPaths … config.json の { 正規化した URL → フォルダ }
function resolve(project, repoPaths = {}) {
  return normalize(project).repos.map((repo) => ({
    ...repo, label: repoLabel(repo.url), path: String((repoPaths || {})[normalizeUrl(repo.url)] || ''),
  }));
}

function globRegex(glob) {
  let re = '';
  const g = String(glob || '').replace(/^\.?\//, '');
  for (let i = 0; i < g.length; i += 1) {
    const c = g[i];
    if (c === '*') {
      if (g[i + 1] === '*') { re += '.*'; i += 1; if (g[i + 1] === '/') i += 1; }
      else re += '[^/]*';
    } else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${re}(?:/.*)?$`);
}

// 依頼を始めるリポジトリ。決まるのは次の 2 つだけで、どちらも一意に当たったときに限る:
//   1. 依頼に書かれたパスが、書いてよいリポジトリの owns に当たる
//   2. 依頼が、書いてよいリポジトリの名前（URL の末尾）をそのまま含む
// それ以外（当たらない・複数に当たる）は主。参照リポジトリでは始めない（読むだけなので）。
function chooseRepo(resolved, request) {
  const writable = (resolved || []).filter((repo) => repo.path && repo.role !== 'reference');
  const main = writable.find((repo) => repo.role === 'main') || writable[0] || null;
  const body = String(request || '');
  const paths = [...body.matchAll(/(?:^|[\s`'"(（「])((?:\.\/)?[\w.-]+(?:\/[\w.-]+)+\/?)/g)].map((m) => m[1].replace(/^\.\//, ''));
  const byOwns = writable.filter((repo) => (repo.owns || []).some((glob) => paths.some((p) => globRegex(glob).test(p))));
  if (byOwns.length === 1) return { repo: byOwns[0], reason: 'owns' };
  const words = body.toLowerCase();
  const byName = writable.filter((repo) => {
    const name = repo.label.toLowerCase();
    return name.length >= 3 && new RegExp(`(^|[^\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^\\w-]|$)`).test(words);
  });
  if (byName.length === 1) return { repo: byName[0], reason: 'name' };
  return { repo: main, reason: 'main' };
}

// 最初の依頼に添える「プロジェクト」の節。パスはホスト側（CLI から見える形）で渡す。
//   resolved … resolve() の結果に hostPath を足したもの
//   current  … 今回のカレントディレクトリ（登録フォルダ）
//   kbHost   … ナレッジリポジトリのホスト側パス
function contextBlock({ project, folder, resolved = [], current = '', kbHost = '' }) {
  const p = normalize(project);
  const here = resolved.find((repo) => repo.path && repo.path === current);
  const lines = ['## プロジェクト',
    `この会話はプロジェクト「${p.name}」の作業です。カレントディレクトリは ${here ? `${here.label}（${ROLE_LABEL[here.role]}）` : 'このプロジェクトのリポジトリ'}です。`];
  const others = resolved.filter((repo) => repo !== here);
  if (others.length) {
    lines.push('', 'ほかのリポジトリ:');
    for (const repo of others) {
      const where = repo.hostPath || repo.path || 'この PC に未設定';
      lines.push(`- ${repo.label}（${ROLE_LABEL[repo.role]}）: ${where}${repo.desc ? ` — ${repo.desc}` : ''}`);
    }
    if (others.some((repo) => repo.role === 'reference')) lines.push('参照のリポジトリは読むだけにし、変更しないでください。');
  }
  if (kbHost) {
    const dir = `${kbHost.replace(/\/+$/, '')}/${DIR}/${folder}`;
    lines.push('', `ナレッジ: ${dir}/（索引は README.md、常に守ることは rules.md）と、全プロジェクト共通の ${kbHost.replace(/\/+$/, '')}/${SHARED_DIR}/。`,
      '作業を始める前に、README.md と rules.md があれば読んでください。');
  }
  if (p.instructions) lines.push('', p.instructions);
  return lines.join('\n');
}

// 「ナレッジに保存」で会話へ送る指示。書くのはエージェントで、ここは置き場を決めるだけ。
//   scope … 'project'（このプロジェクト）| 'shared'（全プロジェクト共通）
//   kind  … note | decision | rule
function knowledgePrompt({ kbHost, folder, scope = 'project', kind = 'note', date = new Date() }) {
  const k = KINDS[kind] || KINDS.note;
  const root = kbHost.replace(/\/+$/, '');
  const base = scope === 'shared' ? `${root}/${SHARED_DIR}` : `${root}/${DIR}/${folder}`;
  const day = new Date(date).toISOString().slice(0, 10);
  const target = k.file ? `${base}/${k.file}` : `${base}/${k.dir}/${day}-<内容を表す短い英小文字の名前>.md`;
  const lines = [
    `この会話で分かったことを、ナレッジ（${k.label}）として保存してください。`,
    '',
    k.file ? `- ${target} に、箇条書きで追記する（既にある内容と重複させない）`
      : `- ${target} に 1 ファイルで書く（見出し 1 つ・要点・根拠。会話の経過は書かない）`,
    `- ${base}/README.md の「## 索引」に、書いたファイルへのリンクを 1 行足す（無ければ作る）`,
    `- ${root} の中の変更だけを git でコミットする（push はしない。ほかのリポジトリには触らない）`,
    '- 書いたファイルのパスを最後に 1 行で答える',
  ];
  return lines.join('\n');
}

module.exports = {
  DIR, FILE, SHARED_DIR, MAX_BYTES, MAX_REPOS, ROLES, ROLE_LABEL, KINDS,
  folderName, normalize, parse, serialize, normalizeUrl, repoLabel, keyOf, splitKey, projectFile,
  list, read, write, resolve, chooseRepo, contextBlock, knowledgePrompt, globRegex,
};
