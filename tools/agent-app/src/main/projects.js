'use strict';

// プロジェクト = 役割を付けたリポジトリの束と、その知識の置き場。
//
// 定義はナレッジリポジトリの `projects/<フォルダ>/project.yaml` に 1 枚だけ置き、全 PC で共有する。
// 書くのは人が編集画面で保存したときだけで、実行の状態・履歴・ログは持たない（大きくならない）。
// 読むのは起動時・プロジェクトを選んだとき・編集画面を開いたときだけで、そのときファイルの時刻を
// 見て変わった分だけ読み直す。監視も定期的な fetch もしない（他の PC の変更は利用者の pull で届く）。
//
// リポジトリは git の URL、origin がない場合は localId で識別する。端末の絶対パスは定義へ書かず config.json の
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
const FILES_DIR = 'files';                // 画面から足したファイルの置き場（projects/<フォルダ>/files/）
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_RULES_BYTES = 4 * 1024;         // 最初の依頼にそのまま差し込む rules.md の上限
const MAX_PREFERENCES_BYTES = 2 * 1024;   // 同じく preferences.md（進め方の好み）の上限
const MAX_INDEX_BYTES = 2 * 1024;         // 索引 README.md は先頭のこの分だけ差し込む
const ROLES = ['main', 'work', 'reference'];
const ROLE_LABEL = { main: '主', work: '作業', reference: '参照' };
const KINDS = {
  note: { label: 'メモ', dir: 'notes' },
  decision: { label: '決めたこと', dir: 'decisions' },
  rule: { label: '守ること', file: 'rules.md' },
  preference: { label: '進め方の好み', file: 'preferences.md' },
};

const text = (value, max = MAX_TEXT) => String(value == null ? '' : value).trim().slice(0, max);

// フォルダ名。人が付けた名前から、どの OS でもフォルダにできる形を作る（日本語はそのまま）。
const { folderName } = require('../shared/projectPath');

function globs(value) {
  const list = Array.isArray(value) ? value : String(value || '').split(/[,\s]+/);
  return [...new Set(list.map((item) => text(item, 200)).filter(Boolean))].slice(0, 20);
}

// 既定は作業用から選ぶ。参照専用を作業用へ変更しない。
function normalize(raw) {
  const source = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const seen = new Set();
  const repos = [];
  for (const item of Array.isArray(source.repos) ? source.repos : []) {
    if (!item || typeof item !== 'object') continue;
    const url = text(item.url, 500);
    const localId = /^[a-zA-Z0-9-]{1,80}$/.test(item.localId || '') ? item.localId : '';
    const reference = url ? normalizeUrl(url) : localId ? `local:${localId}` : '';
    if (!reference || seen.has(reference)) continue;
    seen.add(reference);
    const repo = { ...(url ? { url } : { localId, label: text(item.label, 200) || 'ローカルフォルダ' }),
      role: ROLES.includes(item.role) ? item.role : 'reference', desc: text(item.desc, 300) };
    const owns = globs(item.owns);
    if (owns.length) repo.owns = owns;
    repos.push(repo);
    if (repos.length >= MAX_REPOS) break;
  }
  let main = repos.findIndex((repo) => repo.role === 'main');
  for (let i = 0; i < repos.length; i += 1) if (repos[i].role === 'main' && i !== main) repos[i].role = 'work';
  if (main < 0) {
    main = repos.findIndex((repo) => repo.role === 'work');
    if (main >= 0) repos[main].role = 'main';
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

// ナレッジの中身（定義と索引以外のファイル）を新しい順に。ホームを開いたときに 1 回だけ読む。
//   → { recent: [{ rel, name, mtime }]（limit 件）, total }。rel はナレッジリポジトリからの相対
function knowledgeFiles(kb, folder, limit = 5) {
  const root = path.join(kb, DIR, folder);
  const found = [];
  const walk = (dir, depth) => {
    let names;
    try { names = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const entry of names) {
      if (entry.name.startsWith('.')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { if (depth < 3) walk(full, depth + 1); continue; }
      if (!entry.isFile() || (dir === root && [FILE, 'README.md'].includes(entry.name))) continue;   // 定義と索引は別の口で開く
      try { found.push({ rel: path.relative(kb, full).split(path.sep).join('/'), name: path.relative(root, full).split(path.sep).join('/'), mtime: fs.statSync(full).mtimeMs }); }
      catch { /* 消えた */ }
      if (found.length >= 2000) return;
    }
  };
  walk(root, 0);
  found.sort((a, b) => b.mtime - a.mtime);
  return { recent: found.slice(0, limit), total: found.length };
}

// 画面から足すファイルの置き場（同じ名前があれば -2, -3 … を付ける）。kb からの相対を返す
function fileTarget(kb, folder, name) {
  const base = path.basename(String(name || '')).replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').replace(/^\.+/, '') || 'file';
  const ext = path.extname(base);
  const stem = base.slice(0, base.length - ext.length) || 'file';
  for (let n = 1; n < 1000; n += 1) {
    const rel = path.posix.join(DIR, folder, FILES_DIR, n === 1 ? base : `${stem}-${n}${ext}`);
    if (!fs.existsSync(path.join(kb, rel))) return rel;
  }
  throw new Error('同じ名前のファイルが多すぎます');
}

// 最初の依頼にそのまま差し込む小さいファイル（無い・大きすぎるなら ''。大きいものは読む指示だけにする）
function smallText(kb, folder, name, max) {
  const file = path.join(kb, DIR, folder, name);
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > max) return '';
    return fs.readFileSync(file, 'utf8').trim();
  } catch { return ''; }
}

function rulesText(kb, folder) { return smallText(kb, folder, 'rules.md', MAX_RULES_BYTES); }
function preferencesText(kb, folder) { return smallText(kb, folder, KINDS.preference.file, MAX_PREFERENCES_BYTES); }

// 索引 README.md の先頭（行の切れ目で切る）。何がどこにあるかを毎回渡し、必要なノートは自分で開かせる。
//   → { text, more }（more … 続きがある）
function indexText(kb, folder) {
  let raw;
  try { raw = fs.readFileSync(path.join(kb, DIR, folder, 'README.md')); } catch { return { text: '', more: false }; }
  if (raw.length <= MAX_INDEX_BYTES) return { text: raw.toString('utf8').trim(), more: false };
  const head = raw.subarray(0, MAX_INDEX_BYTES).toString('utf8');
  const cut = head.lastIndexOf('\n');
  return { text: (cut > 0 ? head.slice(0, cut) : head).replace(/\uFFFD+$/, '').trim(), more: true };
}

// 定義のリポジトリに、この PC のフォルダを当てる。
//   repoPaths … config.json の { 正規化した URL → フォルダ }
function resolve(project, repoPaths = {}) {
  return normalize(project).repos.map((repo) => ({
    ...repo, label: repo.url ? repoLabel(repo.url) : repo.label, path: String((repoPaths || {})[repo.url ? normalizeUrl(repo.url) : `local:${repo.localId}`] || ''),
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
//   rules    … rules.md の中身（rulesText。'' なら差し込まない）
//   index    … 索引 README.md の先頭（indexText。{ text, more }）
//   preferences … preferences.md の中身（preferencesText）
//   branch   … いまの会話の作業ブランチ（作業フォルダで動くときだけ。ほかのリポジトリでも同じ名前を使わせる）
function contextBlock({ project, folder, resolved = [], current = '', kbHost = '', rules = '', index = null, preferences = '', branch = '' }) {
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
    if (branch && others.some((repo) => repo.role !== 'reference')) {
      lines.push(`作業のリポジトリを変更するときは、先にそのリポジトリでブランチ ${branch} を作って（あれば切り替えて）から書いてください。`
        + 'main へ直接コミットせず、push と PR は頼まれたときだけにしてください。');
    }
  }
  if (kbHost) {
    const dir = `${kbHost.replace(/\/+$/, '')}/${DIR}/${folder}`;
    const indexed = index && index.text;
    const unread = [indexed ? '' : 'README.md', rules ? '' : 'rules.md'].filter(Boolean);
    lines.push('', `ナレッジ: ${dir}/（索引は README.md、常に守ることは rules.md、進め方の好みは preferences.md）と、全プロジェクト共通の ${kbHost.replace(/\/+$/, '')}/${SHARED_DIR}/。`,
      unread.length ? `作業を始める前に、${unread.join(' と ')} があれば読んでください。` : '索引から、この依頼に関わるノートを開いてから作業を始めてください。',
      'あとの作業でも役に立つことが出たら、回答の最後に「ナレッジに残す候補」として 1〜3 行で挙げてください（自分では書かない）。'
        + '候補にするのは、決めたこと、やめた案や消した機能とその理由、変える前に確認が要る相手、利用者の進め方の好み（報告の頻度や細かさ、作業の分け方）です。');
    if (indexed) lines.push('', '### 索引（README.md の先頭）', index.text, ...(index.more ? ['（続きは README.md）'] : []));
    if (rules) lines.push('', '### 守ること（rules.md）', rules);
    if (preferences) lines.push('', '### 進め方の好み（preferences.md）', preferences);
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
    `- ${root} の中の変更だけを git でコミットし、そのまま push する（ほかのリポジトリには触らない）`,
    '- 書いたファイルのパスを最後に 1 行で答える',
  ];
  return lines.join('\n');
}

module.exports = {
  DIR, FILE, SHARED_DIR, FILES_DIR, MAX_BYTES, MAX_FILE_BYTES, MAX_REPOS, ROLES, ROLE_LABEL, KINDS,
  folderName, normalize, parse, serialize, normalizeUrl, repoLabel, keyOf, splitKey, projectFile,
  list, read, write, resolve, chooseRepo, contextBlock, knowledgePrompt, globRegex,
  knowledgeFiles, fileTarget, rulesText, preferencesText, indexText,
};
