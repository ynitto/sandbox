'use strict';

// kiro-projects のプロジェクトデータ（<root>/projects/<name>/ 配下）を
// 読み取り専用で解析するデータ層。書式の正典は
// tools/kiro-projects/backlog.md.example / charter.md.example と
// docs/designs/kiro-projects-design.md §3。パース規則は kiro-projects.py の
// HEAD_RE / FIELD_RE / parse_charter / parse_policy に合わせている。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { readToolConfig } = require('./toolconfig');

// kiro-projects.py と同じ正規表現
const HEAD_RE = /^##\s+(\S+?):\s*(.*)$/;
const FIELD_RE = /^-\s+(\w+):\s*(.*)$/;
const POLICY_RE = /^(deny|pin|defer|offload|gate|protect|route):\s*(.+)$/;
const DR_HEAD_RE = /^##\s+(DR-\d+)\s+(\S+)\s+actor:\s*(.*)$/;

const TASK_STATUSES = ['inbox', 'draft', 'ready', 'doing', 'done', 'blocked', 'review'];

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function readJson(file) {
  const raw = readText(file);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

function statMtime(file) {
  try {
    return fs.statSync(file).mtimeMs;
  } catch {
    return 0;
  }
}

function stripBackticks(s) {
  const t = String(s || '').trim();
  return t.startsWith('`') && t.endsWith('`') && t.length >= 2 ? t.slice(1, -1) : t;
}

// ---------------------------------------------------------------------------
// タスク（backlog/<id>.md・archive/<id>.md）
// ---------------------------------------------------------------------------

function parseTask(text, tid) {
  const task = {
    id: tid,
    title: tid,
    status: 'inbox',
    source: 'human',
    priority: 0,
    verify: '',
    retries: 0,
    extra: {},
  };
  let seenHead = false;
  for (const line of String(text || '').split('\n')) {
    const h = line.match(HEAD_RE);
    if (h && !seenHead) {
      seenHead = true;
      task.title = h[2].trim() || tid;
      continue;
    }
    const f = line.match(FIELD_RE);
    if (!f) continue;
    const [, key, valRaw] = f;
    const val = valRaw.trim();
    switch (key) {
      case 'status':
        if (TASK_STATUSES.includes(val)) task.status = val;
        break;
      case 'source':
        task.source = val;
        break;
      case 'priority':
        task.priority = parseInt(val, 10) || 0;
        break;
      case 'verify':
        task.verify = stripBackticks(val);
        break;
      case 'retries':
        task.retries = parseInt(val, 10) || 0;
        break;
      default:
        // after / accept / level / track / review / note / cost などは保持
        if (task.extra[key] === undefined) task.extra[key] = val;
        else task.extra[key] += `\n${val}`;
    }
  }
  return task;
}

function listTasks(dir) {
  const tasks = [];
  for (const f of safeList(dir)) {
    if (!f.endsWith('.md')) continue;
    const file = path.join(dir, f);
    const text = readText(file);
    if (text === null) continue;
    const task = parseTask(text, f.replace(/\.md$/, ''));
    task.mtime = statMtime(file);
    task.file = file;
    tasks.push(task);
  }
  return tasks;
}

// ---------------------------------------------------------------------------
// charter.md
// ---------------------------------------------------------------------------

function parseCharter(text) {
  if (!text) return null;
  const charter = { name: '', sections: {} };
  let current = null;
  for (const line of text.split('\n')) {
    const title = line.match(/^#\s+Charter:\s*(.+)$/);
    if (title) {
      charter.name = title[1].trim();
      continue;
    }
    const sec = line.match(/^##\s+(\w+)\s*$/);
    if (sec) {
      current = sec[1].toLowerCase();
      charter.sections[current] = [];
      continue;
    }
    if (current) charter.sections[current].push(line);
  }
  const out = { name: charter.name, raw: text };
  for (const [key, lines] of Object.entries(charter.sections)) {
    // コメント行を落として本文だけにする
    const body = lines.filter((l) => !l.trim().startsWith('#')).join('\n').trim();
    out[key] = body;
  }
  // acceptance は行ごとの一覧にもする（達成状況の表示用）
  if (out.acceptance) {
    out.acceptanceItems = out.acceptance
      .split('\n')
      .map((l) => l.replace(/^-\s*/, '').trim())
      .filter(Boolean);
  }
  return out;
}

// ---------------------------------------------------------------------------
// policy.md / decisions/ / needs/
// ---------------------------------------------------------------------------

function parsePolicy(text) {
  const rules = [];
  for (const line of String(text || '').split('\n')) {
    const m = line.trim().match(POLICY_RE);
    if (m) rules.push({ kind: m[1], value: m[2].split('#')[0].trim() });
  }
  return rules;
}

function parseDecisions(text, id) {
  const records = [];
  let cur = null;
  for (const line of String(text || '').split('\n')) {
    const h = line.match(DR_HEAD_RE);
    if (h) {
      cur = { taskId: id, dr: h[1], date: h[2], actor: h[3].trim(), fields: {}, learn: '' };
      records.push(cur);
      continue;
    }
    if (!cur) continue;
    const f = line.match(/^-\s+(\w+)\s*:\s*(.*)$/);
    if (f) {
      if (f[1] === 'learn') cur.learn = f[2].trim();
      else cur.fields[f[1]] = f[2].trim();
    }
  }
  return records;
}

// needs/<id>.md — MADR frontmatter 付き Markdown
function parseNeeds(text, id) {
  const need = { id, kind: '', date: '', status: '', title: '', body: '', decided: false };
  const s = String(text || '');
  const fm = s.match(/^---\n([\s\S]*?)\n---\n?/);
  let body = s;
  if (fm) {
    body = s.slice(fm[0].length);
    for (const line of fm[1].split('\n')) {
      const kv = line.match(/^([\w-]+):\s*(.*)$/);
      if (!kv) continue;
      const key = kv[1];
      const val = kv[2].trim();
      if (key === 'kind') need.kind = val;
      else if (key === 'date') need.date = val;
      else if (key === 'status') need.status = val;
      else if (key === 'task-id') need.taskId = val;
    }
  }
  const title = body.match(/^#\s+(.+)$/m);
  if (title) need.title = title[1].trim();
  need.decided = /-\s*\[x\]/i.test(body);
  need.body = body.trim();
  return need;
}

function listMdDir(dir, parser) {
  const out = [];
  for (const f of safeList(dir)) {
    if (!f.endsWith('.md')) continue;
    const file = path.join(dir, f);
    const text = readText(file);
    if (text === null) continue;
    const item = parser(text, f.replace(/\.md$/, ''));
    item.mtime = statMtime(file);
    item.file = file;
    out.push(item);
  }
  return out;
}

// ---------------------------------------------------------------------------
// journal / run-log / DELIVERY
// ---------------------------------------------------------------------------

function tailLines(file, limit) {
  const raw = readText(file);
  if (raw === null) return [];
  const lines = raw.split('\n').filter((l) => l.trim());
  return lines.slice(-limit);
}

function readRunLog(file, limit = 100) {
  const raw = readText(file);
  if (raw === null) return [];
  const out = [];
  for (const line of raw.split('\n')) {
    const s = line.trim();
    if (!s) continue;
    try {
      const rec = JSON.parse(s);
      if (rec && typeof rec === 'object') out.push(rec);
    } catch {
      /* 壊れた行は無視 */
    }
  }
  return out.slice(-limit);
}

// DELIVERY.md のテーブル行（| id | タイトル | 検収 | 成果参照 | 完了 |）
function readDelivery(file, limit = 100) {
  const raw = readText(file);
  if (raw === null) return [];
  const rows = [];
  for (const line of raw.split('\n')) {
    const s = line.trim();
    if (!s.startsWith('|')) continue;
    const cells = s.split('|').map((c) => c.trim());
    // 先頭と末尾は空文字。ヘッダ・罫線行は除外
    const inner = cells.slice(1, -1);
    if (inner.length < 3) continue;
    if (/^[-: ]+$/.test(inner[0]) || inner[0] === 'id') continue;
    rows.push(inner);
  }
  return rows.slice(-limit);
}

// ---------------------------------------------------------------------------
// プロジェクト発見・スナップショット
// ---------------------------------------------------------------------------

function globalDir() {
  return path.join(os.homedir(), '.kiro-projects');
}

// ~/.kiro-projects/instances/*.json — 稼働発見レコード
function listInstances() {
  const dir = path.join(globalDir(), 'instances');
  const out = [];
  const now = Date.now() / 1000;
  for (const f of safeList(dir)) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(dir, f));
    if (!rec || typeof rec !== 'object') continue;
    const ttl = Number(rec.ttl || 0);
    const hb = Number(rec.heartbeat || 0);
    rec.fresh = !ttl || !hb ? true : now - hb <= ttl * 3;
    out.push(rec);
  }
  return out;
}

function isProjectDir(dir) {
  return (
    fs.existsSync(path.join(dir, 'backlog')) ||
    fs.existsSync(path.join(dir, 'charter.md')) ||
    fs.existsSync(path.join(dir, 'journal.md')) ||
    fs.existsSync(path.join(dir, 'needs')) ||
    fs.existsSync(path.join(dir, 'archive'))
  );
}

// コンテナ（--root 相当のディレクトリ）からプロジェクト一覧を得る。
// 標準は <root>/projects/<name>/、projects/ が無い旧フラット構成は
// root 自体を 1 プロジェクトとして扱う。
function listProjectsIn(root) {
  const projectsDir = path.join(root, 'projects');
  const out = [];
  if (fs.existsSync(projectsDir)) {
    for (const name of safeList(projectsDir)) {
      const dir = path.join(projectsDir, name);
      try {
        if (!fs.statSync(dir).isDirectory()) continue;
      } catch {
        continue;
      }
      out.push({ name, dir });
    }
  } else if (isProjectDir(root)) {
    out.push({ name: path.basename(root), dir: root, flat: true });
  }
  return out;
}

// 設定 roots ＋ instances 自動発見からコンテナ→プロジェクトのツリーを作る
function discover(cfg) {
  const roots = new Map(); // resolved root -> {root, source}
  for (const r of (cfg.kiro && cfg.kiro.roots) || []) {
    if (!r) continue;
    const resolved = path.resolve(String(r).replace(/^~(?=$|\/|\\)/, os.homedir()));
    roots.set(resolved, { root: resolved, source: 'config' });
  }
  const instances = cfg.kiro && cfg.kiro.autoDiscover === false ? [] : listInstances();
  for (const inst of instances) {
    const c = inst.container || inst.root;
    if (!c || inst.sentinel) continue;
    const resolved = path.resolve(String(c));
    if (!roots.has(resolved)) roots.set(resolved, { root: resolved, source: 'instance' });
  }

  const runningKeys = new Set(
    instances
      .filter((i) => i.fresh && !i.sentinel)
      .map((i) => `${path.resolve(String(i.container || i.root || ''))}::${i.project || ''}`)
  );

  const containers = [];
  for (const { root, source } of roots.values()) {
    const projects = listProjectsIn(root).map(({ name, dir, flat }) => {
      const tasks = listTasks(path.join(dir, 'backlog'));
      const byStatus = {};
      for (const t of tasks) byStatus[t.status] = (byStatus[t.status] || 0) + 1;
      const needs = safeList(path.join(dir, 'needs')).filter((f) => f.endsWith('.md')).length;
      return {
        name,
        dir,
        flat: !!flat,
        hasCharter: fs.existsSync(path.join(dir, 'charter.md')),
        backlogCount: tasks.length,
        byStatus,
        needsCount: needs,
        running: runningKeys.has(`${root}::${name}`),
      };
    });
    containers.push({ root, source, exists: fs.existsSync(root), projects });
  }
  return { containers, instances };
}

// ---------------------------------------------------------------------------
// kiro-flow バスの発見
// ---------------------------------------------------------------------------

// kiro-projects の既定は per-project の <project>/bus だが、--bus / 設定 `bus:` の
// 共有バス構成では別の場所になる。CLI に聞かず、ファイルの存在だけで候補を順に当たる:
//   1. <project>/bus（既定の per-project バス）
//   2. <container>/bus（共有バスをコンテナ直下に置く運用）
//   3. ⚙ 設定 kiro.flowBus（明示指定）
//   4. kiro-projects 設定ファイル（<workdir>/.kiro → ~/.kiro）の bus:
//      （相対パスは kiro-projects の workdir 相当＝コンテナの親で解決する）
// runs/ を持つ最初の候補を採用。どれにも無ければ既定の 1 を返す（hasBus=false）。
function resolveBusDir(projectDir, cfg) {
  const candidates = [];
  const push = (dir, source) => {
    if (!dir) return;
    const resolved = path.resolve(String(dir).replace(/^~(?=$|\/|\\)/, os.homedir()));
    if (!candidates.some((c) => c.dir === resolved)) candidates.push({ dir: resolved, source });
  };

  push(path.join(projectDir, 'bus'), 'project');
  const parent = path.dirname(path.resolve(projectDir));
  const container = path.basename(parent) === 'projects' ? path.dirname(parent) : null;
  if (container) push(path.join(container, 'bus'), 'container');
  if (cfg && cfg.kiro && cfg.kiro.flowBus) push(cfg.kiro.flowBus, 'config');

  // kiro-projects 設定ファイルの bus:（コンテナの親 = workdir 相当の .kiro を優先）
  const kiroDirs = container ? [path.join(path.dirname(container), '.kiro')] : [];
  const toolCfg = readToolConfig('kiro-projects', kiroDirs);
  if (toolCfg && toolCfg.values.bus) {
    const raw = String(toolCfg.values.bus);
    const base = container ? path.dirname(container) : path.dirname(projectDir);
    push(path.isAbsolute(raw) ? raw : path.join(base, raw), 'kiro-projects.yaml');
  }

  for (const c of candidates) {
    if (fs.existsSync(path.join(c.dir, 'runs'))) {
      return { busDir: c.dir, hasBus: true, source: c.source, candidates };
    }
  }
  return { busDir: candidates[0].dir, hasBus: false, source: 'project', candidates };
}

// 1 プロジェクトの完全なスナップショット
function readProject(dir, cfg) {
  const backlog = listTasks(path.join(dir, 'backlog'));
  const archive = listTasks(path.join(dir, 'archive'));
  const needs = listMdDir(path.join(dir, 'needs'), parseNeeds);
  const decisionsAll = [];
  for (const f of safeList(path.join(dir, 'decisions'))) {
    if (!f.endsWith('.md')) continue;
    const text = readText(path.join(dir, 'decisions', f));
    if (text === null) continue;
    decisionsAll.push(...parseDecisions(text, f.replace(/\.md$/, '')));
  }
  decisionsAll.sort((a, b) => String(b.date).localeCompare(String(a.date)));

  // 実行中クレーム（claims/<id>.lock）
  const claims = safeList(path.join(dir, 'claims'))
    .filter((f) => f.endsWith('.lock'))
    .map((f) => f.replace(/\.lock$/, ''));

  const autonomy = [];
  for (const f of safeList(path.join(dir, 'autonomy'))) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(dir, 'autonomy', f));
    if (rec) autonomy.push(rec);
  }

  const byStatus = {};
  for (const t of backlog) byStatus[t.status] = (byStatus[t.status] || 0) + 1;

  // inbox/ に置かれて取り込み待ちのファイル（次サイクルで backlog 化される）
  const inboxFiles = safeList(path.join(dir, 'inbox')).filter((f) =>
    /\.(json|md|markdown|txt)$/i.test(f)
  );

  const bus = resolveBusDir(dir, cfg);

  return {
    dir,
    inboxFiles,
    name: path.basename(dir),
    charter: parseCharter(readText(path.join(dir, 'charter.md'))),
    policy: parsePolicy(readText(path.join(dir, 'policy.md'))),
    backlog,
    archive,
    byStatus,
    claims,
    needs,
    decisions: decisionsAll.slice(0, 100),
    journal: tailLines(path.join(dir, 'journal.md'), 200),
    runLog: readRunLog(path.join(dir, 'run-log.jsonl')),
    delivery: readDelivery(path.join(dir, 'DELIVERY.md')),
    projectState: readJson(path.join(dir, 'project.json')),
    repos: readJson(path.join(dir, 'repos.json')),
    autonomy,
    busDir: bus.busDir,
    hasBus: bus.hasBus,
    busSource: bus.source,
    busCandidates: bus.candidates,
  };
}

module.exports = {
  parseTask,
  parseCharter,
  parsePolicy,
  parseNeeds,
  parseDecisions,
  listInstances,
  discover,
  readProject,
  resolveBusDir,
};
