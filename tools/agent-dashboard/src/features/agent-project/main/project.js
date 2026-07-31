'use strict';

// agent-project のプロジェクトデータ（プロジェクトルート直下）を
// 読み取り専用で解析するデータ層。書式の正典は
// tools/agent-project/backlog.md.example / charter.md.example と
// docs/designs/agent-project-design.md §3。パース規則は agent-project.py の
// HEAD_RE / FIELD_RE / parse_charter / parse_policy に合わせている。
// 登録パス 1 件 = 1 プロジェクトルート（1 プロジェクト = 1 ディレクトリ = 1 プロセス）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { readToolConfig } = require('./toolconfig');
const { reposFileName } = require('./authoring');
const { agentDirCandidates } = require('../../../base/main/agent-home');

// agent-project.py と同じ正規表現
const HEAD_RE = /^##\s+(\S+?):\s*(.*)$/;
const FIELD_RE = /^-\s+(\w+):\s*(.*)$/;
const POLICY_RE = /^(deny|pin|defer|offload|gate|protect|route):\s*(.+)$/;
const DR_HEAD_RE = /^##\s+(DR-\d+)\s+(\S+)\s+actor:\s*(.*)$/;

// offloaded: 委譲公示板へ公示済み・請負側の実行結果待ち。
//   flow_run（run-id）を extra に持ち、フロータブの該当 run へ辿れる。
// proposed: 実行前レビュー待ち（承認されるまで実行しない）／rejected: 却下済み（archive に退避）
const TASK_STATUSES = ['inbox', 'draft', 'proposed', 'ready', 'doing', 'done', 'blocked', 'review', 'offloaded', 'rejected'];

function readText(file) {
  try {
    // CRLF は読み時に正規化する。行末 \r が残ると `$` アンカーの HEAD_RE / FIELD_RE /
    // frontmatter 正規表現が全て外れ、status が既定の inbox に落ちる等、Windows/WSL 間で
    // 同期・編集された md がサイレントに誤読される。
    return fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
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
  for (const line of String(text || '').replace(/\r\n/g, '\n').split('\n')) {
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

// tid に依存する（extra.after に tid を含む）タスクの推移閉包（影響範囲の一覧提示用）。
function dependentsOf(tasks, tid) {
  const deps = (t) =>
    String((t.extra && t.extra.after) || '')
      .split(/[\s,]+/)
      .filter(Boolean);
  const out = [];
  const seen = new Set([tid]);
  let frontier = new Set([tid]);
  while (frontier.size) {
    const next = new Set();
    for (const t of tasks) {
      if (seen.has(t.id)) continue;
      if (deps(t).some((d) => frontier.has(d))) {
        out.push(t);
        seen.add(t.id);
        next.add(t.id);
      }
    }
    frontier = next;
  }
  return out;
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
// 監視担当（assignments.json）— viewer 管理のチーム運用メタデータ
//   タスクの「実作業の分担（エージェント）」とは別軸の、人の監視・検収の分担。
//   agent-project の契約ファイルではない（本体は読まない）ため、書いても
//   done の不変条件・状態遷移には一切影響しない。プロジェクトルート直下に
//   置くので state_git 同期（ドット始まり・flow-archive/claims 以外は全て対象）で
//   チームに共有される。書式:
//     { "members": ["alice", "bob"], "tasks": { "<task-id>": "alice" } }
// ---------------------------------------------------------------------------

const ASSIGNMENTS_FILE = 'assignments.json';

function readAssignments(dir) {
  const raw = readJson(path.join(dir, ASSIGNMENTS_FILE));
  const out = { members: [], tasks: {} };
  if (raw && typeof raw === 'object') {
    if (Array.isArray(raw.members)) {
      out.members = raw.members.map((m) => String(m).trim()).filter(Boolean);
    }
    if (raw.tasks && typeof raw.tasks === 'object') {
      for (const [id, name] of Object.entries(raw.tasks)) {
        const v = String(name == null ? '' : name).trim();
        if (v) out.tasks[String(id)] = v;
      }
    }
  }
  return out;
}

// タスクの実効担当: assignments.json（viewer の割り当て）＞ backlog md の `- owner:`
// （未知キーは本体が保持する契約なので、手書き・inbox 経由の owner も生きる）。
function effectiveOwner(assignments, task) {
  return (
    (assignments.tasks && assignments.tasks[task.id]) ||
    String((task.extra && task.extra.owner) || '').trim() ||
    ''
  );
}

// ---------------------------------------------------------------------------
// レビューコメント（reviews/<task-id>/*.json）— viewer 管理のチーム運用メタデータ。
//   成果物レビューを複数メンバーで行うための、タスク（成果物）単位のコメント束。
//   agent-project の契約ファイルではない（本体は読まない）＝ done の不変条件に影響しない。
//   1 コメント = 1 ファイル。複数メンバーが別 PC から同時にコメントしてもファイル名が
//   別なので state_git 同期で自然にマージされる（全体 JSON の last-write-wins を避ける。
//   バスの runs/ と同じ流儀）。書式: { author, text, ts, editedTs? }。
// ---------------------------------------------------------------------------

const REVIEWS_DIR = 'reviews';

function readReviewComments(dir, taskId) {
  const tid = String(taskId || '').trim();
  if (!tid || tid !== path.basename(tid)) return [];
  const cdir = path.join(dir, REVIEWS_DIR, tid);
  const out = [];
  for (const f of safeList(cdir)) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(cdir, f));
    if (!rec || typeof rec !== 'object') continue;
    const text = String(rec.text || '').trim();
    if (!text) continue;
    out.push({
      id: f.replace(/\.json$/, ''),
      author: String(rec.author || '').trim() || '匿名',
      text,
      ts: String(rec.ts || ''),
      editedTs: rec.editedTs ? String(rec.editedTs) : '',
    });
  }
  // 投稿時刻の昇順（会話として読める順）。ts 欠落はファイル名末尾で代替。
  out.sort((a, b) => String(a.ts || a.id).localeCompare(String(b.ts || b.id)));
  return out;
}

// ---------------------------------------------------------------------------
// charter.md
// ---------------------------------------------------------------------------

function parseCharter(text) {
  if (!text) return null;
  const charter = { name: '', sections: {} };
  let current = null;
  // 見出しの規則は本体（agent-project の _CHARTER_NAME_RE / _CHARTER_SECTION_RE）と
  // authoring.js parseCharterDoc に合わせる: タイトルは `# Charter|憲章`（コロン任意）、
  // セクションは `## <英字キー>` に後続テキストがあってもよい（例 `## goal（目標）`）。
  for (const line of String(text).replace(/\r\n/g, '\n').split('\n')) {
    const title = line.match(/^#\s+(?:Charter|憲章)\s*[:：]?\s*(.+?)\s*$/);
    if (title) {
      if (!charter.name) charter.name = title[1].trim(); // 最初の宣言を採用（本体の search と同じ）
      continue;
    }
    const sec = line.match(/^##\s+([A-Za-z]+)\b/);
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
  // マスター憲章（`## master` セクション付き）: プロジェクト全体の普遍的な前提。
  // agent-project はこれを分解せず、計画バージョン（charters/<name>.md）へ継承する。
  // セクション本文（コメントのみ＝空）に上書きされないよう、セクション展開の後で立てる。
  out.master = Object.prototype.hasOwnProperty.call(charter.sections, 'master');
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

// 墓標（tombstones.md）— 却下したタスクを再分解で作り直させないための記録。本体の
// load_tombstones と同じ規則で読む（1 行 1 墓標・`::` 区切り・4 番目が charter= タグ）。
// 画面に出すのは「消したものを戻せる」ようにするため: 却下（reject）は墓標を残すので、
// 一覧と解除（revive）が無いと、画面から消したタスクは画面からは二度と入れ直せない。
function parseTombstones(text) {
  const out = [];
  for (const line of String(text || '').replace(/\r\n/g, '\n').split('\n')) {
    const m = line.match(/^\s*-\s+(.+?)\s*$/);
    if (!m || line.trimStart().startsWith('-->')) continue;
    const parts = m[1].split('::').map((x) => x.trim());
    const title = parts[0] || '';
    if (!title) continue;
    const tag = (parts[3] || '').startsWith('charter=') ? parts[3].slice('charter='.length).trim() : '';
    out.push({ title, reason: parts[1] || '', date: parts[2] || '', charter: tag });
  }
  return out;
}

function parseDecisions(text, id) {
  const records = [];
  let cur = null;
  for (const line of String(text || '').replace(/\r\n/g, '\n').split('\n')) {
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

// needs/<id>.md — MADR frontmatter 付き Markdown。
// 表示用に「なぜ / 状態 / 概況」の要点と、判断材料（残りのセクション）を構造化して返す。
// ファイル編集用の足場（## Decision Outcome・チェックボックス・HTML コメントのヒント）は
// ビュアーの操作ボタンが代替するため detail からは除く（原文は body に保持）。
// 判断材料（evidence）が実質的に空か。stub 実行や無変更のとき、evidence は「成果物: (参照なし)」
// 「所在: <内部パス>」「差分: 変更なし」だけになり、人には内部パスの羅列に見えて分かりにくい。
// 成果物の参照がプレースホルダ（参照なし/変更なし）で、かつファイル差分（差分: N ファイル）も
// 無ければ「痩せた evidence」と判定する。実 executor（PR/MR リンク・コミット・差分あり）は false。
function _evidenceThin(detail) {
  const s = String(detail || '');
  const deliverable = s.match(/^-\s*成果物\s*[:：]\s*(.+)$/m);
  if (!deliverable) return false; // 成果物行が無い＝別種の判断材料（タスク定義等）は対象外
  const v = deliverable[1].trim();
  const placeholder = v === '(参照なし)' || v === '(変更なし)';
  const hasFileDiff = /^-\s*差分\s*[:：]\s*\d+\s*ファイル/m.test(s);
  return placeholder && !hasFileDiff;
}

// 「差分」に並ぶファイルのうち、agent-project / agent-flow が実行のたびに書く内部記録
// （bus/ の run ログ・claims・needs・journal・project.json 等）は人の判断材料にならない。
// これらだけが並んだカードは「変更あり」に見えて中身が無く、実際に何が変わったのか読み取れない。
const _INTERNAL_DIFF_RE = new RegExp(
  '(^|/)\\.agent-project/'
    + '|(^|/)(bus|claims|needs|decisions|commands|inbox|archive|flow-archive)/'
    + '|(^|/)(journal\\.md|project\\.json|repos\\.json|run-log\\.jsonl|status\\.json|DELIVERY\\.md)$'
);

function _isInternalDiffFile(file) {
  return _INTERNAL_DIFF_RE.test(String(file || '').replace(/\\/g, '/'));
}

// 判断材料の差分リスト（レガシー「差分: N ファイル」と現行「変更ファイル（N 件）:」）を
// 成果物と内部記録に分ける。複数リポジトリ節があっても全リストを集める。
function _splitDiff(detail) {
  const s = String(detail || '');
  const out = { artifacts: [], internal: [], truncated: 0, hasDiff: false };
  const headRe = /^-\s*(?:変更ファイル(?:（\d+\s*件）)?|差分)\s*[:：].*$/gm;
  let m;
  while ((m = headRe.exec(s)) !== null) {
    out.hasDiff = true;
    const rest = s.slice(m.index + m[0].length).split('\n');
    for (const raw of rest) {
      if (!raw.trim()) continue;
      if (!/^\s+-\s/.test(raw)) break;
      const item = raw.trim().replace(/^-\s*/, '').trim();
      const more = item.match(/^…\s*他\s*(\d+)\s*件/);
      if (more) {
        out.truncated += Number(more[1]);
        continue;
      }
      (_isInternalDiffFile(item) ? out.internal : out.artifacts).push(item);
    }
  }
  return out;
}

// frontmatter / 判断材料から GitLab MR URL を拾う（複数可）。
function _extractMrUrls(...sources) {
  const seen = new Set();
  const out = [];
  const re = /https?:\/\/[^\s)）\]>"']+\/-\/merge_requests\/\d+/g;
  for (const src of sources) {
    const text = typeof src === 'string' ? src : JSON.stringify(src || '');
    for (const u of text.match(re) || []) {
      if (!seen.has(u)) {
        seen.add(u);
        out.push(u);
      }
    }
  }
  return out;
}

// 検証レポートの要約（S5）。人が検収で読むのは「コマンド」ではなく **基準と証跡**。
// 壊れていれば null（＝要約を出さない）。表示できないことより、誤った要約を出す方が悪い。
function _parseVerification(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  let data;
  try {
    data = JSON.parse(s);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || !Array.isArray(data.criteria)) return null;
  const criteria = data.criteria
    .filter((c) => c && typeof c === 'object')
    .map((c, i) => {
      const ev = (c.evidence && typeof c.evidence === 'object') ? c.evidence : {};
      return {
        id: Number(c.id) || i + 1,
        text: String(c.text || ''),
        verdict: ['pass', 'fail', 'unverifiable'].includes(String(c.verdict))
          ? String(c.verdict) : 'fail',   // 読めない判定は fail（フェイルクローズ）
        evidence: {
          commands: (Array.isArray(ev.commands) ? ev.commands : []).map(String),
          output: String(ev.output || ''),
          files: (Array.isArray(ev.files) ? ev.files : []).map(String),
        },
        note: String(c.note || ''),
      };
    });
  if (!criteria.length) return null;
  return {
    criteria,
    report: String(data.report || ''),
    pass: criteria.filter((c) => c.verdict === 'pass').length,
  };
}

function _parseDeliveryJson(raw) {
  const s = String(raw || '').trim();
  if (!s) return [];
  try {
    const v = JSON.parse(s);
    return Array.isArray(v) ? v.filter((e) => e && typeof e === 'object') : [];
  } catch {
    return [];
  }
}

// 判断材料 markdown から delivery エントリを復元（旧票・frontmatter 無し向けフォールバック）。
function _deliveryFromDetail(detail) {
  const s = String(detail || '');
  const entries = [];
  const sections = s.split(/^###\s+リポジトリ:\s*/m).slice(1);
  const pushFiles = (text, entry) => {
    // 行末まで（\s は改行も含むので使わない）: 「変更ファイル（N 件）:」の次行からリストを取る
    const head = text.match(/^-\s*変更ファイル(?:（(\d+)\s*件）)?[^\S\n]*[:：][^\S\n]*.*$/m);
    if (!head) return;
    const totalHint = head[1] ? Number(head[1]) : 0;
    const rest = text.slice(text.indexOf(head[0]) + head[0].length).split('\n');
    const files = [];
    let truncated = 0;
    for (const raw of rest) {
      if (!raw.trim()) continue;
      if (!/^\s+-\s/.test(raw)) break;
      const item = raw.trim().replace(/^-\s*/, '').trim();
      const more = item.match(/^…\s*他\s*(\d+)\s*件/);
      if (more) {
        truncated = Number(more[1]);
        continue;
      }
      files.push(item);
    }
    entry.files = files;
    entry.files_total = totalHint || files.length + truncated;
  };
  if (sections.length) {
    for (const chunk of sections) {
      const title = (chunk.match(/^([^\n（]+)(?:（([^）]+)）)?/) || [])[1] || 'repo';
      const roleHint = (chunk.match(/^([^\n（]+)(?:（([^）]+)）)?/) || [])[2] || '';
      const role = /参照/.test(roleHint) ? 'reference' : 'write';
      const entry = {
        name: title.trim(),
        role,
        url: ((chunk.match(/^-\s*参照\s*[:：]\s*(.*)$/m) || [])[1] || '').trim(),
        path: ((chunk.match(/^-\s*所在\s*[:：]\s*(.*)$/m) || [])[1] || '').trim(),
        base: ((chunk.match(/base\s+`([^`]+)`/) || [])[1] || '').trim(),
        branch: ((chunk.match(/ブランチ\s+`([^`]+)`|ブランチ指定\s*[:：]\s*`([^`]+)`/) || []).slice(1).find(Boolean) || '').trim(),
        ref: '',
        files: [],
        files_total: 0,
        diff_cmd: ((chunk.match(/^-\s*差分を見る\s*[:：]\s*`([^`]+)`/m) || [])[1] || '').trim(),
        mr_url: ((chunk.match(/^-\s*MR\s*[:：]\s*(\S+)/m) || [])[1] || '')
          .replace(/（.*$/, '')
          .trim(),
      };
      pushFiles(chunk, entry);
      entries.push(entry);
    }
    return entries;
  }
  // 単一リポジトリ形式（見出し無し）
  const branch = ((s.match(/ブランチ\s+`([^`]+)`/) || [])[1] || '').trim();
  const base = ((s.match(/base\s+`([^`]+)`/) || [])[1] || '').trim();
  const pathLoc = ((s.match(/^-\s*所在\s*[:：]\s*(.*)$/m) || [])[1] || '').trim();
  const diffCmd = ((s.match(/^-\s*差分を見る\s*[:：]\s*`([^`]+)`/m) || [])[1] || '').trim();
  const mr = ((s.match(/^-\s*MR\s*[:：]\s*(\S+)/m) || [])[1] || '').trim();
  const entry = {
    name: 'write',
    role: 'write',
    url: '',
    path: pathLoc.replace(/\s*\/\s*ブランチ.*$/, '').trim(),
    base,
    branch,
    ref: '',
    files: [],
    files_total: 0,
    diff_cmd: diffCmd,
    mr_url: mr,
  };
  pushFiles(s, entry);
  // レガシー「差分:」リストも拾う
  if (!entry.files.length) {
    const legacy = _splitDiff(s);
    entry.files = legacy.artifacts;
    entry.files_total = legacy.artifacts.length + legacy.truncated;
  }
  // 単発実行の旧形式では「所在」が状態ディレクトリ（例: repo/.agent-project）を指す一方、
  // 変更ファイルはリポジトリルート相対（.agent-project/...）で記録されることがある。
  // そのまま連結すると .agent-project/.agent-project/... となるため、重なる末尾を戻す。
  const locationLeaf = path.basename(entry.path.replace(/[/\\]+$/, ''));
  if (
    /^\.agent-project$/.test(locationLeaf)
    || (locationLeaf && entry.files.some((file) => String(file).replace(/\\/g, '/').startsWith(`${locationLeaf}/`)))
  ) {
    entry.path = path.dirname(entry.path);
  }
  const hasDiffList = /^-\s*(?:変更ファイル(?:（\d+\s*件）)?|差分)\s*[:：]/m.test(s);
  if (entry.files.length || entry.mr_url || entry.branch || entry.diff_cmd || (entry.path && hasDiffList)) {
    entries.push(entry);
  }
  return entries;
}

function _normalizeDelivery(entries) {
  return (entries || [])
    .filter((e) => e && typeof e === 'object')
    .map((e) => {
      const sourceFiles = Array.isArray(e.files) ? e.files.map(String) : [];
      const files = sourceFiles.filter((file) => !_isInternalDiffFile(file));
      return {
        name: String(e.name || 'repo'),
        role: e.role === 'reference' ? 'reference' : 'write',
        url: String(e.url || ''),
        path: String(e.path || ''),
        base: String(e.base || ''),
        target: String(e.target || e.base || ''),
        branch: String(e.branch || ''),
        ref: String(e.ref || ''),
        files,
        // 元データの total は省略された内部ファイルを含み得る。完全一覧は検収画面で
        // git から取り直すため、ここでは実際に名前が分かる成果物だけを数える。
        files_total: files.length,
        diff_cmd: String(e.diff_cmd || ''),
        mr_url: String(e.mr_url || ''),
      };
    });
}

// agent-project が frontmatter へ書いた失敗の解釈を、そのまま表示モデルの形へ移す。
// **ここで解釈し直さない**——生データを見て判断できるのは書き手だけで、読み手が散文から
// 復元しようとすると、書き手の文言が変わった瞬間に静かに食い違う。
function _failureFromFrontmatter(fmFields) {
  const get = (k) => String(fmFields[k] || '').trim();
  const context = {
    category: get('failure-category'),
    owner: get('failure-owner'),
    command: get('failure-command'),
    workdir: get('failure-workdir'),
    exitCode: get('failure-exit'),
    target: get('failure-target'),
    resolvedTarget: get('failure-target'),
  };
  const hasContext = Object.values(context).some(Boolean);
  return {
    summary: get('failure-summary'),
    resolution: get('failure-resolution'),
    context: hasContext ? context : null,
  };
}

// verify の自由文を、画面で共通表示できる「原因・実行条件・確認手順」に正規化する。
// 特定ツール名には依存しない。解釈できない値は空のままにし、生ログを根拠として必ず残す。
function _diagnoseFailure(why, detail) {
  const verify = (String(detail || '').match(/^-\s*検証\s*[:：]\s*(.*)$/m) || [])[1] || '';
  const raw = `${why || ''} ${verify}`;
  const empty = { summary: '', resolution: '', context: null };
  if (!raw.trim()) return empty;
  // 検証まで到達していない（act が失敗して止まった）記録からは、検証の所見を作らない。
  // 失敗の理由は別にある——ここで何か言うと、その本当の理由を覆い隠す。
  if (/→\s*未実行/.test(verify)) return empty;
  const workdir = (String(detail || '').match(/^-\s*所在\s*[:：]\s*(\S+)/m) || [])[1];
  const missingEnglish = (raw.match(/(?:file or directory not found|No such file or directory)[:\s]+([^\s)）]+)/i) || [])[1];
  const missingJapanese = (raw.match(/(?:エラー\s*[:：]\s*)?[^\n]*?(?:見つかりません|存在しません)\s*[:：]\s*([^\s)）]+)/) || [])[1];
  const notFound = missingEnglish || missingJapanese;
  const failed = (raw.match(/(\d+)\s+failed/) || [])[1];
  const cmdMissing = (raw.match(/([\w./-]+):\s*command not found/) || [])[1];
  const exit = (raw.match(/exit=(\d+)/) || [])[1];
  // run_verify が特定した「失敗した工程」（&& 連鎖の途中で沈黙して落ちた工程のトレース）
  const step = (raw.match(/失敗した工程:\s*`([^`]+)`/) || [])[1];
  const passed = (raw.match(/(\d+)\s+passed/) || [])[1];
  const verifyCommand = (verify.match(/`([^`]+)`/) || [])[1] || '';
  const command = step || verifyCommand;
  const context = {
    category: '',
    owner: '',
    command,
    workdir,
    exitCode: exit || '',
    target: '',
    resolvedTarget: '',
  };

  if (cmdMissing) {
    context.category = '実行環境';
    context.owner = '検査設定・実行環境';
    return {
      summary: `検証に必要なコマンド「${cmdMissing}」が実行環境に見つかりません。`,
      resolution: `「${cmdMissing}」がインストール済みか、検証プロセスの PATH から実行できるかを確認してから再実行してください。`,
      context,
    };
  }
  if (notFound) {
    context.category = 'パス・入力';
    context.owner = '検査設定・実行環境';
    context.target = notFound;
    context.resolvedTarget = workdir && !path.isAbsolute(notFound) ? path.resolve(workdir, notFound) : notFound;
    return {
      summary: context.resolvedTarget
        ? `検証コマンドが必要なパス「${context.resolvedTarget}」を見つけられませんでした。`
        : `検証コマンドが必要なパス「${notFound}」を見つけられませんでした。`,
      resolution: '対象が実際に存在する場所を確認し、コマンドのパス指定を実行ディレクトリ基準の正しい相対パス、または絶対パスへ変更して再実行してください。',
      context,
    };
  }
  if (failed) {
    context.category = 'テスト失敗';
    context.owner = '成果物';
    return {
      summary: `テストが ${failed} 件失敗しました。`,
      resolution: '失敗したテスト名と最初のエラーを生ログで確認し、成果物を修正して同じ検証コマンドを再実行してください。',
      context,
    };
  }
  if (/no tests ran/i.test(raw)) {
    context.category = '検証対象なし';
    context.owner = '検査設定・実行環境';
    return {
      summary: 'テストが 1 件も実行されませんでした（対象が見つからないか、条件に一致しません）。',
      resolution: 'テスト対象のパス、選択条件、実行ディレクトリを確認してから再実行してください。',
      context,
    };
  }
  if (step) {
    context.category = '検証工程';
    context.owner = '要確認';
    return {
      summary: `検証コマンドの工程「${step}」で失敗しました（それより前の工程は成功しています）。`,
      resolution: '生ログの該当工程を確認し、表示された作業ディレクトリで同じコマンドを再現して原因を切り分けてください。',
      context,
    };
  }
  if (exit && passed && Number(exit) !== 0) {
    // 「テストは通っているのに exit≠0」: && 連鎖の後段（grep・外部チェック等）が沈黙して
    // 失敗した古い形式の記録。どこが落ちたかは記録に無いが、少なくとも「テストの失敗では
    // ない」ことを言う（テスト成功の出力だけを見せられて混乱するのが一番まずい）。
    context.category = '検証工程';
    context.owner = '検査設定・実行環境';
    return {
      summary: `テストは ${passed} 件成功していますが、検証コマンドの後段の工程（grep や外部チェックなど）が失敗しています（終了コード ${exit}）。`,
      resolution: 'テスト後に実行される工程を生ログで確認し、その工程を単独で再実行してください。',
      context,
    };
  }
  if (exit) {
    context.category = '不明な検証失敗';
    context.owner = '要確認';
    return {
      summary: `検証コマンドが失敗しました（終了コード ${exit}）。`,
      resolution: '実行コマンド・作業ディレクトリ・生ログを確認し、同じ条件で再現して原因を切り分けてください。',
      context,
    };
  }
  return empty;
}

// 痩せた evidence から実質情報の無い行（成果物プレースホルダ・所在・実行先・差分なし）を落とす。
// 検証（verify → PASS/FAIL）やタスク定義・goal 等の意味のある行は残す。
function _stripThinEvidence(detail) {
  const drop = [
    /^-\s*成果物\s*[:：]\s*\(?(参照なし|変更なし)\)?\s*$/,
    /^-\s*所在\s*[:：]/,
    /^-\s*実行先\s*[:：]/,
    /^-\s*差分\s*[:：]\s*baseline 以降の変更なし\s*$/,
  ];
  return String(detail || '')
    .split('\n')
    .filter((l) => !drop.some((re) => re.test(l.trim())))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function parseNeeds(text, id) {
  const need = {
    id,
    kind: '',
    date: '',
    status: '',
    title: '',
    body: '',
    decided: false,
    why: '',
    stateNote: '',
    summary: '',
    detail: '',
    evidenceThin: false, // 判断材料が実質空（stub 実行・無変更）＝内部パスだけのとき true
    failureSummary: '', // 失敗理由の要約（生の verify 出力を解釈した一文。解釈できなければ空）
    failureResolution: '', // 既知の失敗について、その場で実行できる修正方法
    failureContext: null, // { category, owner, command, workdir, exitCode, target, resolvedTarget }
    diff: null, // { artifacts, internal, truncated, hasDiff } — 成果物と内部記録に分けた差分
    risk: '', // 検収票のリスクダイジェスト総合値（low/med/high）。バッジ表示用
    mrUrl: '', // 代表 MR URL（frontmatter mr-url / 判断材料）。GitLab ならこれを開く
    mrUrls: [], // 複数リポジトリ分の MR URL
    delivery: [], // 検収サブ画面用のリポジトリ単位エントリ
    verification: null, // 検証レポートの要約（S5）: { criteria: [{id,text,verdict,evidence,note}], report, pass }
  };
  const s = String(text || '').replace(/\r\n/g, '\n');
  const fm = s.match(/^---\n([\s\S]*?)\n---\n?/);
  let body = s;
  let deliveryRaw = '';
  let verificationRaw = '';
  const fmFields = {};          // frontmatter の生キー（失敗の構造化フィールドを読むのに使う）
  if (fm) {
    body = s.slice(fm[0].length);
    for (const line of fm[1].split('\n')) {
      const kv = line.match(/^([\w-]+):\s*(.*)$/);
      if (!kv) continue;
      const key = kv[1];
      const val = kv[2].trim();
      fmFields[key] = val;
      if (key === 'kind') need.kind = val;
      else if (key === 'date') need.date = val;
      else if (key === 'status') need.status = val;
      else if (key === 'task-id') need.taskId = val;
      else if (key === 'risk') need.risk = val;
      else if (key === 'mr-url') need.mrUrl = val;
      else if (key === 'delivery') deliveryRaw = val;
      else if (key === 'verification') verificationRaw = val;
    }
  }
  const title = body.match(/^#\s+(.+)$/m);
  if (title) need.title = title[1].trim();
  need.decided = (() => {
    // 確定 [x] は Decision Outcome / 旧フィードバック欄配下だけ（本文チェックリストは対象外）。
    // agent-project の FEEDBACK_MARKERS と同じ契約（旧票が UI 上ずっと undecided に見えないように）。
    const markers = ['## Decision Outcome', '## フィードバック'];
    let best = -1;
    let markerLen = 0;
    for (const m of markers) {
      const i = body.indexOf(m);
      if (i >= 0 && (best < 0 || i < best)) {
        best = i;
        markerLen = m.length;
      }
    }
    if (best < 0) return false;
    return /-\s*\[x\]/i.test(body.slice(best + markerLen));
  })();
  need.body = body.trim();

  // 記入用の足場より前（本文）だけを対象に要点を抽出する
  const main = body.split(/^##\s+Decision Outcome\s*$/m)[0].replace(/<!--[\s\S]*?-->/g, '');
  const pick = (label) => {
    const m = main.match(new RegExp(`^-\\s*${label}\\s*[:：]\\s*(.*)$`, 'm'));
    return m ? m[1].trim() : '';
  };
  need.why = pick('なぜ');
  need.stateNote = pick('状態');
  need.summary = pick('概況');
  // 要点（なぜ/状態/概況）とタイトル・Context 見出しを除いた残り＝判断材料（タスク定義・
  // 成果物の所在・goal など）。折りたたみの「詳細」に出す。
  need.detail = main
    .split('\n')
    .filter((l) => {
      const t = l.trim();
      if (/^#\s+/.test(t)) return false; // タイトル行
      if (/^##\s+Context and Problem Statement/i.test(t)) return false;
      if (/^-\s*(なぜ|状態|概況)\s*[:：]/.test(t)) return false;
      return true;
    })
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  // stub 実行・無変更で痩せた判断材料は、内部パスだけの羅列に見えて分かりにくい。退化行を
  // 落として（実質情報を残しつつ）viewer 側で「成果情報なし」と一言添えられるよう印を付ける。
  // 失敗の解釈は agent-project（生データを持っている側）が frontmatter へ構造化して書く。
  // ここはそれを運ぶだけ。_diagnoseFailure は **その項目が無い旧記録のためのフォールバック**で、
  // 新しく書かれる票では使われない。両側が独立に散文を解釈していたのが、書き手の文言変更で
  // 読み手だけが静かに壊れる原因だった。
  const failureDiagnosis = fmFields['failure-summary']
    ? _failureFromFrontmatter(fmFields)
    : _diagnoseFailure(need.why, need.detail);
  need.failureSummary = failureDiagnosis.summary;
  need.failureResolution = failureDiagnosis.resolution;
  need.failureContext = failureDiagnosis.context;
  need.failureClass = String(fmFields['failure-class'] || '');
  need.failureChain = String(fmFields['failure-chain'] || '').split(',').filter(Boolean);
  need.failurePhase = String(fmFields['failure-phase'] || '');
  need.verifyVerdict = String(fmFields['verify-verdict'] || '');
  need.evidenceThin = _evidenceThin(need.detail);
  if (need.evidenceThin) need.detail = _stripThinEvidence(need.detail);
  // 差分を「成果物」と「内部記録（bus/ の run ログ等）」に分ける。実行のたびに書かれる内部
  // ファイルだけが並ぶと「14 ファイル変更」に見えて中身が無い。成果物が 0 なら痩せた扱いにする。
  need.diff = _splitDiff(need.detail);
  if (need.diff.hasDiff && need.diff.artifacts.length === 0 && need.diff.internal.length > 0) {
    need.evidenceThin = true;
  }
  // 検収サブ画面: frontmatter delivery を優先し、無ければ判断材料から復元する
  need.delivery = _normalizeDelivery(_parseDeliveryJson(deliveryRaw));
  need.verification = _parseVerification(verificationRaw);
  if (!need.delivery.length) need.delivery = _normalizeDelivery(_deliveryFromDetail(need.detail));
  need.mrUrls = _extractMrUrls(need.mrUrl, need.delivery.map((e) => e.mr_url).join(' '), need.detail);
  if (!need.mrUrl && need.mrUrls.length) need.mrUrl = need.mrUrls[0];
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

// commands/*.err — 本体が取り込みに失敗して退避した指示（本体 _reject_command と対）。
// 中身は {error, failed_at, command:{command,id,...}}。タスク id ごとに最新の 1 件へまとめ、
// 対応する needs カードへ「直前の指示は失敗した」根拠として渡す。承認の失敗は非同期
// （ドロップ→後で取り込み）なので送信時トーストでは伝えられず、これが無いと失敗が
// 誰にも見えないまま同じボタンが繰り返し押される。
function listCommandFailures(dir) {
  const out = {};
  const cdir = path.join(dir, 'commands');
  for (const f of safeList(cdir)) {
    if (!f.endsWith('.err')) continue;
    const rec = readJson(path.join(cdir, f));
    if (!rec || typeof rec !== 'object') continue;
    const cmd = rec.command && typeof rec.command === 'object' ? rec.command : {};
    const tid = String(cmd.id || '').trim();
    if (!tid) continue;
    const entry = {
      action: String(cmd.command || ''),
      error: String(rec.error || ''),
      failedAt: String(rec.failed_at || ''),
    };
    const prev = out[tid];
    if (!prev || String(prev.failedAt) < entry.failedAt) out[tid] = entry;
  }
  return out;
}

// commands/processed/*.json — 本体が取り込みに成功した指示の受理レシート（本体
// _write_command_receipt と対）。中身は {ok, action, id, processed_at, source}。承認の
// 取り込みは非同期（ドロップ→後で本体が処理）で、送信時トーストは「送信済み」までしか
// 言えない。成功時に元ファイルが消えるだけだと「保留中（本体未取り込み）」と「処理済み」が
// 区別できず、押しても何も起きないように見える。受理レシートを読み、送信した指示が本体に
// 届いたことをカードで示す。タスク id ごとに最新の 1 件へまとめる。
function listCommandReceipts(dir) {
  const out = {};
  const pdir = path.join(dir, 'commands', 'processed');
  for (const f of safeList(pdir)) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(pdir, f));
    if (!rec || typeof rec !== 'object' || !rec.ok) continue;
    const tid = String(rec.id || '').trim();
    if (!tid) continue;                       // プロジェクト単位（replan/pause 等）はカードに紐づかない
    const entry = {
      action: String(rec.action || ''),
      processedAt: String(rec.processed_at || ''),
      source: String(rec.source || ''),
    };
    const prev = out[tid];
    if (!prev || String(prev.processedAt) < entry.processedAt) out[tid] = entry;
  }
  return out;
}

// タスク級の票の kind（本体 needs.py の _TASK_NEEDS_KINDS と同じ集合）。これ以外
// （milestone・未知）はタスクに紐づかないので、タスクの有無で判断しない。
const TASK_NEEDS_KINDS = new Set(['plan-review', 'review', 'blocked']);

// needs/<id>.md が無い判断待ちタスク（review / blocked / proposed）を backlog status から補う。
// 本体の ensure_needs と同じ契約: needs は status の投影で、票が失われても検収・承認導線を残す。
// ここではファイルを書かず表示用だけを合成する（承認は commands/ 経由で needs ファイルが無くても届く）。
function synthesizeNeedsFromBacklog(needs, backlog, needsDir, archive) {
  const expectedKind = (status) =>
    status === 'review' ? 'review' : status === 'proposed' ? 'plan-review' : status === 'blocked' ? 'blocked' : '';
  const taskById = new Map((backlog || []).map((t) => [String(t.id), t]));
  const archivedIds = new Set((archive || []).map((t) => String(t.id)));
  const have = new Set();
  const out = [];
  for (const n of needs || []) {
    const tid = String(n.taskId || n.id || '');
    const task = taskById.get(tid);
    const expected = task ? expectedKind(String(task.status || '')) : '';
    // needs は status の投影。タスクが判断待ちを抜けた（done で archive 済み・ready/doing へ
    // 戻った）のに票ファイルだけ残ると、決着済みの判断がカードとして出続ける。投影から
    // 外れた票はここで落とす。
    if (archivedIds.has(tid) || (task && !expected)) continue;
    // 投影元のタスクがどこにも無い票（＝タスクを消した後に残った孤児）も落とす。
    // 従来は「タスクを持たない票 = charter/milestone カード」と見なして残していたが、
    // その判別は kind でしかできない: タスク級（plan-review/review/blocked）の票は
    // タスクが消えた時点で操作不能（承認も却下も対象が無い）＝出し続けても人は何もできない。
    // 本体側でも reap_orphan_needs が同じ規則でファイルを掃除する（ここは即時の表示同期）。
    if (!task && TASK_NEEDS_KINDS.has(String(n.kind || ''))) continue;
    // 古い plan-review が残ったまま task が blocked/review へ進んだ場合、存在するだけで
    // 合成を抑止せず、下で正しい種別の表示票に置き換える。
    if (expected && String(n.kind || '') !== expected) continue;
    out.push(n);
    if (n.id) have.add(String(n.id));
    if (n.taskId) have.add(String(n.taskId));
  }
  for (const t of backlog || []) {
    const st = String(t.status || '');
    if (!['review', 'blocked', 'proposed'].includes(st)) continue;
    if (have.has(String(t.id))) continue;
    const kind = expectedKind(st);
    const why =
      st === 'review'
        ? '成果物の検収待ち（承認すると完了になります）'
        : st === 'proposed'
          ? '新規タスクの実行前レビュー（承認されるまで実行しません）'
          : (t.extra && t.extra.needs_reason) ||
            `実行が止まっています（retries=${t.retries || 0}）。指示を送るか、そのまま再実行してください。`;
    out.push({
      id: t.id,
      taskId: t.id,
      kind,
      date: '',
      status: st,
      title: `${t.id} — ${t.title || ''}`.trim(),
      body: '',
      decided: false,
      why,
      stateNote: '',
      summary: '',
      detail: '',
      evidenceThin: false,
      failureSummary: '',
      diff: null,
      risk: '',
      mrUrl: '',
      mrUrls: [],
      delivery: [],
      file: path.join(needsDir, `${t.id}.md`),
      mtime: t.mtime || 0,
      synthesized: true,
    });
    have.add(String(t.id));
  }
  return out;
}

// backlog の mr_url / gate_ref を needs に補う（合成票・旧票で frontmatter が薄いとき）。
function attachDeliveryHintsFromBacklog(needs, backlog) {
  const byId = new Map();
  for (const t of backlog || []) byId.set(String(t.id), t);
  for (const n of needs || []) {
    const tid = String(n.taskId || n.id || '');
    const t = byId.get(tid);
    if (!t || !t.extra) continue;
    const candidates = [t.extra.mr_url, t.extra.gate_ref].map((x) => String(x || '').trim()).filter(Boolean);
    const mrs = _extractMrUrls(n.mrUrl, ...(n.mrUrls || []), ...candidates, n.detail || '');
    if (mrs.length) {
      n.mrUrls = mrs;
      if (!n.mrUrl) n.mrUrl = mrs[0];
    }
    if ((!n.delivery || !n.delivery.length) && n.mrUrl) {
      n.delivery = _normalizeDelivery([
        {
          name: 'MR',
          role: 'write',
          mr_url: n.mrUrl,
          branch: String(t.extra.gate_branch || ''),
          files: [],
          files_total: 0,
        },
      ]);
    }
  }
  return needs;
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
  const home = process.env.AGENT_PROJECT_HOME
    ? String(process.env.AGENT_PROJECT_HOME).replace(/^~(?=$|\/|\\)/, os.homedir())
    : path.join(os.homedir(), '.agent-project');
  return home;
}

// ---------------------------------------------------------------------------
// Windows ビュアー × WSL 本体 — パス規約の橋渡し
//
// ビュアーは \\wsl.localhost\<distro>\home\... で開き、本体は /home/... を書く。
// win32 の path.resolve('/home/...') は \home\...（または C:\home\...）に化けて
// 一致しないため、比較・発見・設定解決はすべて規約非依存キーで行う。
// ---------------------------------------------------------------------------

// POSIX 絶対パス（/home/...）。UNC（// や \\）は除外。
function _isPosixAbs(p) {
  const s = String(p || '');
  return s.startsWith('/') && !s.startsWith('//');
}

// \\wsl$\Distro\rest / \\wsl.localhost\Distro\rest（スラッシュ混在も可）
function _wslUncMatch(p) {
  const s = String(p || '').replace(/\//g, '\\');
  return s.match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
}

// POSIX 形の比較キー。実行側（WSL）が書くパスと画面側の UNC を突き合わせるためだけに使う。
// Windows ドライブを WSL から見た /mnt/<drive>/… は扱わない（設計 §4.6 で経路ごと廃止）。
function _posixKey(rest) {
  const r = String(rest || '').replace(/\\/g, '/').replace(/\/+/g, '/').replace(/\/$/, '') || '/';
  return r.toLowerCase();
}

// UNC の WSL ディストロ名（\\wsl$\<distro>\… / \\wsl.localhost\<distro>\…）。UNC でなければ ''。
function _wslDistroOf(p) {
  const m = String(p || '').replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  return m ? m[1].toLowerCase() : '';
}

// 比較用キー: WSL UNC → Linux パス、resolve 残骸の \home\... も Linux に戻す。
function _pathKey(p) {
  let s = String(p || '').trim();
  if (!s) return '';
  const unc = s.replace(/\//g, '\\');
  const m = unc.match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (m) {
    return _posixKey((m[1] || '').replace(/\\/g, '/') || '/');
  }
  // path.win32.resolve('/home/...') → '\home\...' or 'C:\home\...'
  const asBack = s.replace(/\//g, '\\');
  const drivePosix = asBack.match(/^(?:[A-Za-z]:)?\\(home|tmp|var|usr|opt|etc)\\(.*)$/i);
  if (drivePosix && !asBack.startsWith('\\\\')) {
    return (`/${drivePosix[1]}/${drivePosix[2]}`.replace(/\\/g, '/').replace(/\/+/g, '/').replace(/\/$/, '')).toLowerCase();
  }
  if (_isPosixAbs(s)) {
    return _posixKey(s);
  }
  try {
    s = path.resolve(s);
  } catch {
    /* keep */
  }
  return s.replace(/\\/g, '/').replace(/\/+/g, '/').replace(/\/$/, '').toLowerCase();
}

function pathsEqual(a, b) {
  const ka = _pathKey(a);
  const kb = _pathKey(b);
  if (!(ka && kb && ka === kb)) return false;
  // 両方が WSL UNC でディストロ名が異なるなら別実体（Ubuntu と Debian の /home/x は別物）。
  // 片方が Linux パス（ディストロ情報なし）のときは従来どおり一致を許す。
  const da = _wslDistroOf(a);
  const db = _wslDistroOf(b);
  if (da && db && da !== db) return false;
  return true;
}

// ホスト名の緩い一致（大小・DNS サフィックス差を吸収）。空は不一致。
function hostsMatch(a, b) {
  const x = String(a || '').toLowerCase();
  const y = String(b || '').toLowerCase();
  if (!x || !y) return false;
  if (x === y) return true;
  return x.split('.')[0] === y.split('.')[0];
}

// 同一マシン判定: ホスト名一致、または Windows ビュアーが WSL 本体の status を読んでいる。
function sameMachineStatus(status) {
  if (!status) return false;
  if (hostsMatch(status.host, os.hostname())) return true;
  return process.platform === 'win32' && String(status.runtime || '') === 'wsl';
}

// POSIX 絶対パスを Windows から読める WSL UNC へ（distro が取れなければそのまま）。
// distro を明示できる: 実行側の状況ファイル（engine/status.json）が持つパスは、⚙ 設定で
// 選んだディストロのものとして解決する。既定ディストロへ丸めると、別ディストロに
// プロジェクトを置いた環境で存在しないフォルダを指す。
function toViewerPath(p, distro = '') {
  const s = String(p || '');
  if (process.platform !== 'win32' || !_isPosixAbs(s)) return s;
  const name = String(distro || '').trim() || _defaultWslDistro();
  if (!name) return s;
  const rest = s.replace(/\//g, '\\');
  return `\\\\wsl.localhost\\${name}${rest}`;
}

// ⚙ 設定で選んだディストロ（engine.distro）。POSIX パスを UNC へ寄せる呼び出しは、
// **必ずこれを渡す**——渡さないと WSL の既定ディストロへ丸まり、設定と違う環境の
// 存在しないフォルダを指す。同じ設定を使う経路が食い違うと、ある画面では開けて
// 別の画面では開けない、という形で表面化する。
function viewerDistro(cfg) {
  return String((((cfg || {}).engine) || {}).distro || '').trim();
}

let _wslDistroCache = { at: 0, name: '' };
function _defaultWslDistro() {
  if (process.env.WSL_DISTRO_NAME) return process.env.WSL_DISTRO_NAME;
  const now = Date.now();
  if (now - _wslDistroCache.at < 60000) return _wslDistroCache.name;
  let name = '';
  try {
    const { spawnSync } = require('child_process');
    // --list --quiet は UTF-16LE。先頭の既定ディストロ名だけ拾う。
    const r = spawnSync('wsl.exe', ['--list', '--quiet'], {
      encoding: 'buffer', timeout: 8000, windowsHide: true,
    });
    if (r.status === 0 && r.stdout && r.stdout.length) {
      const text = r.stdout.toString('utf16le').replace(/\0/g, '');
      name = text.split(/\r?\n/).map((l) => l.trim()).find(Boolean) || '';
    }
  } catch {
    /* WSL 無し */
  }
  _wslDistroCache = { at: now, name };
  return name;
}

let _wslHomeCache = { at: 0, dirs: [] };
// WSL 既定ディストロの ~/.agent-project を Windows パスで返す（instances 共有用）。
function wslAgentProjectDirs() {
  if (process.platform !== 'win32') return [];
  const now = Date.now();
  if (now - _wslHomeCache.at < 60000) return _wslHomeCache.dirs;
  const dirs = [];
  try {
    const { spawnSync } = require('child_process');
    const r = spawnSync(
      'wsl.exe',
      ['-e', 'sh', '-lc', 'command -v wslpath >/dev/null && wslpath -w "$HOME/.agent-project"'],
      { encoding: 'utf8', timeout: 8000, windowsHide: true }
    );
    const out = String(r.stdout || '').trim().split(/\r?\n/)[0] || '';
    if (r.status === 0 && out && /^[A-Za-z]:\\|^\\\\/.test(out)) dirs.push(out);
  } catch {
    /* WSL 無し */
  }
  _wslHomeCache = { at: now, dirs };
  return dirs;
}

// instances ディレクトリ群（ローカル home + AGENT_PROJECT_REGISTRY + WSL home）。
function instanceDirs() {
  const out = [];
  const add = (d) => {
    if (!d) return;
    const resolved = String(d).replace(/^~(?=$|\/|\\)/, os.homedir());
    if (!out.some((x) => pathsEqual(x, resolved))) out.push(resolved);
  };
  add(path.join(globalDir(), 'instances'));
  const reg = process.env.AGENT_PROJECT_REGISTRY || '';
  for (const part of reg.split(path.delimiter)) {
    if (part.trim()) add(part.trim());
  }
  for (const home of wslAgentProjectDirs()) {
    add(path.join(home, 'instances'));
  }
  return out;
}

// ~/.agent-project/instances/*.json — 稼働発見レコード（root = プロジェクトルート）
function listInstances() {
  const out = [];
  const seen = new Set(); // host|pid|rootKey
  const now = Date.now() / 1000;
  for (const dir of instanceDirs()) {
    for (const f of safeList(dir)) {
      if (!f.endsWith('.json')) continue;
      const rec = readJson(path.join(dir, f));
      if (!rec || typeof rec !== 'object') continue;
      const ttl = Number(rec.ttl || 0);
      const hb = Number(rec.heartbeat || 0);
      rec.fresh = !ttl || !hb ? true : now - hb <= ttl * 3;
      const key = `${rec.host || ''}|${rec.pid || ''}|${_pathKey(rec.root || '')}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(rec);
    }
  }
  return out;
}

// <root>/status.json — 生存信号（agent-project.py の write_status が書く。paused も載る）。
// 本体が別ホストで稼働し git 同期経由でしか届かない場合、instances（同一ホストのローカル
// レジストリ）は空になる。この場合の唯一の生存根拠が、同期されてきた status.json の
// updated_iso の新しさ。fresh_after_sec は書き手（本体）が自分の同期間隔
// （state_git_interval / --status-interval）から計算した値なので、ビュアー側は単純比較
// するだけでよい。存在しない/壊れていれば null。
function readStatus(dir) {
  const rec = readJson(path.join(dir, 'status.json'));
  if (!rec || typeof rec !== 'object') return null;
  const updatedMs = Date.parse(rec.updated_iso || '');
  if (isNaN(updatedMs)) return null;
  const ageSec = (Date.now() - updatedMs) / 1000;
  const freshSec = Number(rec.fresh_after_sec) || 120;
  return { ...rec, ageSec, fresh: ageSec >= 0 && ageSec <= freshSec };
}

// ノード別生存信号 status/<node>.json（本体 write_status のノード別書き出しと対）。
// 複数 PC 分散運用で「どのノード（PC）が生きているか／実行中か」を一覧するための読み取り。
// 各ノードが別ファイルなので、同期越しでも上書き合戦にならず全ノードの状態が並ぶ。
function readNodeStatuses(dir) {
  const sdir = path.join(dir, 'status');
  const out = [];
  for (const f of safeList(sdir)) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(sdir, f));
    if (!rec || typeof rec !== 'object') continue;
    const updatedMs = Date.parse(rec.updated_iso || '');
    const ageSec = isNaN(updatedMs) ? null : (Date.now() - updatedMs) / 1000;
    const freshSec = Number(rec.fresh_after_sec) || 120;
    out.push({
      node: String(rec.node || f.replace(/\.json$/, '')),
      host: String(rec.host || ''),
      running: ageSec !== null && ageSec >= 0 && ageSec <= freshSec,
      ageSec: ageSec === null ? null : Math.round(ageSec),
      paused: !!rec.paused,
      level: rec.level,
      watch: rec.watch,
    });
  }
  return out.sort((a, b) => String(a.node).localeCompare(String(b.node)));
}

// この PC の実行エンジンが監督している子のうち、状態の置き場が dir と同じものを返す。
// 突き合わせは viewerRoot（実行側の POSIX パスを画面から開ける形に寄せたもの）を
// resolveProjectRoot で状態の置き場へ寄せてから _pathKey で行う——実行側は素の root を
// 書くが、状態を worktree へ逃がしている構成では dir が実体（<repo>-agent-state/...）に
// なるため、素の文字列比較では取りこぼす。
function engineChildFor(dir, cfg) {
  if (!dir || !cfg) return null;
  try {
    const engine = require('./engine');
    const key = _pathKey(dir);
    for (const c of engine.readStatus(cfg).children) {
      const viewer = String(c.viewerRoot || '').trim();
      if (!viewer) continue;
      if (_pathKey(resolveProjectRoot(viewer)) === key) return c;
    }
  } catch {
    return null;   // 設定が読めない等（単体テストの部分 cfg）は判定材料なしに倒す
  }
  return null;
}


// プロジェクトの稼働判定。判定根拠と経過時間も返す（UI 表示用）:
//   'engine'      … この PC の実行エンジンが監督している子の実測（children[].alive）。確定判定
//   'status-sync' … 別 PC の本体（state_git 越し）は同期されてきた status.json の新しさで近似判定
//                    （同期遅延ぶんの誤差を許容する。running:false でも「最終確認 N 分前」は分かる）
//   'none'        … 判定材料が無い
//
// 根拠を instances レジストリ（各プロセスの自己申告 + heartbeat 鮮度）から
// `engine/status.json` の `children[].alive` へ移した（実装計画 W1-9）。後者は**親が
// Popen で見た実測**なので心拍窓を持たない——レジストリの鮮度窓は長い作業（LLM 実行）中に
// 切れて status.json の推定へ落ち、稼働中を「停止中」と誤表示していた（実際に起きた）。
// child は discover が engine/status.json から取ってくる同じプロジェクトの子エントリ。
// 省略時（単体呼び出し）は engine から引き当てる。
function projectLiveness(dir, child, cfg) {
  const status = readStatus(dir);
  const paused = !!(status && status.paused);
  const c = child === undefined ? engineChildFor(dir, cfg) : child;
  if (c) {
    // この PC が監督している＝生死は実測で分かる。paused（稼働時間外）は running:false だが
    // 「壊れて止まった」ではないので、呼び出し側が別の印で出す（discover の offHours）。
    return { running: !!c.alive, via: 'engine', ageSec: 0, paused: paused || !!c.paused };
  }
  if (status) {
    // 同一ホストが書いた status.json なら「別マシン」ではない。instances の生存窓（ttl×3＝
    // 既定 270 秒）は本体が長いタスク（LLM 実行）に入ると心拍が飛ばずに切れるが、status.json
    // の窓（既定 600 秒）はまだ生きている、という時間帯がある。そこで status-sync に落とすと
    // ローカル稼働を「別マシンで稼働中」と誤表示していた（サイドバーの `~`、概要の「稼働中
    // （別マシン）」）。host が取れないときは判定材料が無いので従来どおり同期扱いにする。
    // Windows×WSL はホスト名が食い違うことがあり、runtime==='wsl' も同一マシン信号にする。
    const sameHost = sameMachineStatus(status);
    return {
      running: status.fresh,
      via: sameHost ? 'status-local' : 'status-sync',
      ageSec: Math.round(status.ageSec),
      level: status.level,
      watch: status.watch,
      paused,
    };
  }
  return { running: false, via: 'none', ageSec: null, paused: false };
}

// actions.js の指示ルーティング（commands/ ドロップ vs CLI）が使う真偽値。
// リモート稼働を status.json 経由で推定できる場合もここで true にする — CLI はほぼ確実に
// 使えない（別ホスト）ので、file-drop を優先させるのが実態に合っている。
function isProjectRunning(dir) {
  return projectLiveness(dir).running;
}

// バックログ再分解の要求が未消化か（本体の replan_request_path / consume_replan_request と対）。
// commands にドロップ済み（ingest 前）か、本体が立てた .replan.request マーカー
// （ingest 後・再分解前）のどちらかが残っていれば true。本体が再分解まで進めると両方消える。
function replanRequestPending(dir) {
  if (fs.existsSync(path.join(dir, '.replan.request'))) return true;
  const cdir = path.join(dir, 'commands');
  for (const f of safeList(cdir)) {
    if (!f.endsWith('.json')) continue;
    const rec = readJson(path.join(cdir, f));
    if (rec && String(rec.command || '').trim() === 'replan') return true;
  }
  return false;
}

// プロジェクトのマニフェスト = agent-project 設定ファイル。本体の _find_config と同じく
// ルート直下 → .agent/ の順で見る（1 root = 1 プロジェクトの発見マーカーを兼ねる）。
const TOOL_CONFIG_NAMES = ['agent-project.yaml', 'agent-project.yml', 'agent-project.json'];

function hasProjectManifest(dir) {
  return TOOL_CONFIG_NAMES.some(
    (n) => fs.existsSync(path.join(dir, n)) || agentDirCandidates(dir).some((d) => fs.existsSync(path.join(d, n)))
  );
}

// 登録フォルダから agent-project の **プロジェクトルート**（状態の置き場）を解決する。
//
// S1 以降、状態ルートは常に**状態専用リポジトリの clone**で、リダイレクトは無い。
// dashboard に登録するのもその clone なので、通常はここは恒等写像に近い。
// 残る仕事は移行途中の 2 つの形を読めるようにすることだけ:
//
//   ・成果物リポジトリを登録したまま（直下に旧ブートストラップ `state_repo:` が残っている）
//     → 隣の `<repo>-state` を開く（`resolveStateRepoRoot`）
//   ・状態が `<ws>/.agent-project` にネストしている旧レイアウト → その下を開く
//
// **状態 worktree（`<repo>-agent-state`）へのリダイレクトと自動作成は廃止した。** エンジンが
// そこへ書かなくなった以上、開くと「更新が止まった状態」を実体と信じて見せることになる
// （読みが古いだけでなく、指示・タスク編集の書き込み先まで死んだ worktree へ落ちる）。
//
// ~/.agent のグローバル設定にある値は使わない: それを採るとすべてのワークスペースが同じ
// 状態フォルダを指してしまう（本体は 1 プロセス 1 プロジェクトなので困らないが、ビュアーは
// 複数プロジェクトを同時に扱う）。
function resolveProjectRoot(workspaceDir) {
  const ws = path.resolve(String(workspaceDir || ''));
  if (!ws) return ws;
  const cfg = readToolConfig('agent-project', [ws, ...agentDirCandidates(ws)]);
  const fromWorkspace =
    cfg && cfg.file && path.resolve(cfg.file).startsWith(ws + path.sep);
  const values = fromWorkspace && cfg.values ? cfg.values : null;

  // 移行途中の互換: 成果物リポジトリを登録したままでも隣の状態 clone を開く。
  // 実体の git clone は agent-project に任せ、dashboard はパス解決だけする。
  const stateRepoRoot = resolveStateRepoRoot(ws, values);
  if (stateRepoRoot) return stateRepoRoot;

  // 旧レイアウト（状態が <ws>/.agent-project にネストしている）だけ読み替える。**登録パス自身に
  // マニフェストがあっても優先しない**——設定ファイルだけがあって実体は下、という形がまさに
  // この旧レイアウトで、先に ws を返すと backlog が 1 件も見えない空プロジェクトとして出る。
  const nestedState = path.join(ws, '.agent-project');
  if (path.basename(ws) !== '.agent-project' && hasProjectStateMarkers(nestedState)) {
    return nestedState;
  }
  return ws;
}

// git リモート URL/パスの正規化比較（本体 _same_git_remote と同型）。
function _sameGitRemote(a, b) {
  const norm = (u) => {
    let s = String(u || '').trim().replace(/\/+$/, '');
    if (s.endsWith('.git')) s = s.slice(0, -4);
    if (!s.includes('://') && !s.includes('@')) {
      // ローカルパスらしい → 絶対化（~ 展開込み）
      const expanded = s.replace(/^~(?=$|\/|\\)/, os.homedir());
      try { return path.resolve(expanded); } catch { return expanded; }
    }
    return s;
  };
  const na = norm(a);
  const nb = norm(b);
  return Boolean(na) && Boolean(nb) && na === nb;
}

function _gitRemoteOrigin(dir) {
  try {
    const r = require('child_process').spawnSync(
      'git', ['-C', dir, 'remote', 'get-url', 'origin'],
      { encoding: 'utf8', timeout: 10000, windowsHide: true }
    );
    if (r.status !== 0) return '';
    return String(r.stdout || '').trim();
  } catch {
    return '';
  }
}

// ワークスペース（成果物側）の git トップ。絶対パスは使わず prefix 深さで組み立てる
// （Windows ビュアー＋WSL の混在でも表記を壊さない）。
function gitRepoTop(dir) {
  const gp = gitShowPrefix(dir);
  if (!gp.ok) return null;
  return _repoTopPath(dir, gp.prefix) || null;
}

// 旧ブートストラップ `agent-project.yaml` の `state_repo:` から状態 clone のパスを返す
// （**移行途中の互換経路**）。S1 以降 clone 先の宣言は各 PC の host.yaml `projects[].root` で、
// dashboard はそれを読まない（登録するのは状態 clone そのもの）。ここが効くのは
// 「成果物リポジトリを登録したまま・直下に旧 yaml が残っている」形だけ。
//   ・clone 先は <成果物top の親>/<repo名>-state（旧 `state_repo_dir` は廃止した——
//     ノード固有パスを共有 yaml に書く経路そのものが S1 で無くなったため）
//
// **clone 自体は dashboard では行わない。** 通常 clone は agent-project に任せる。ここは
// パス解決だけし、未作成でもそのパスを返す（エンジン起動後に実体が現れる）。
//
// origin が state_repo と食い違う既存ディレクトリだけは使わない（旧 worktree 等を
// 誤って開かない。本体と同じ護り）。workspace 自身が既にその clone なら workspace を返す。
function resolveStateRepoRoot(workspaceDir, values) {
  if (!values) return null;
  const stateRepo = String(values.state_repo || '').trim();
  if (!stateRepo) return null;

  const ws = path.resolve(String(workspaceDir || ''));
  if (!ws) return null;

  // 登録パス自身が状態専用 clone（origin 一致）なら、そのままルート。
  if (fs.existsSync(path.join(ws, '.git')) && _sameGitRemote(_gitRemoteOrigin(ws), stateRepo)) {
    return ws;
  }

  const deliverableTop = gitRepoTop(ws) || ws;
  const dst = path.resolve(
    path.join(path.dirname(deliverableTop), `${path.basename(deliverableTop)}-state`));

  // 自分自身へ解決された場合（上の origin チェックで既に返しているが、非 git 等の保険）
  if (pathsEqual(dst, ws)) return ws;

  // 既存ディレクトリの origin が state_repo と食い違う（旧 worktree や別 repo）なら使わない。
  // 黙って誤ディレクトリを開くと移行が効かない。未作成・空フォルダはパスを返して
  // agent-project の clone を待つ（dashboard は git clone しない）。
  if (fs.existsSync(path.join(dst, '.git'))
      && !_sameGitRemote(_gitRemoteOrigin(dst), stateRepo)) {
    return null;
  }

  return dst;
}

// git 管理下か + repo トップから dir までの相対パス（区切りは常に "/"、非 git なら ok:false）。
// あえて --show-toplevel（絶対パス）ではなく --show-prefix（相対パス）を使う: 絶対パスは
// プラットフォーム／区切り規約に依存するため、WSL 内の本体（git が Linux パス /home/... を返す）と
// Windows のビュアー（\\wsl.localhost\... で読む）が混在すると、それを win32 の path.* で
// 加工した瞬間に壊れる（ドライブ相対の \home\... になる／パスが二重連結される）。相対パスの
// 深さだけを git から取り、worktree の兄弟パスは root 自身の表記から組み立てて規約を保つ。
function gitShowPrefix(dir) {
  try {
    const r = require('child_process').spawnSync(
      'git', ['-C', dir, 'rev-parse', '--show-prefix'],
      { encoding: 'utf8', timeout: 10000 }
    );
    if (r.status !== 0) return { ok: false, prefix: '' };
    // repo トップ直下なら空文字（末尾 "/" 付きの相対パス or "" が返る）
    return { ok: true, prefix: String(r.stdout || '').trim() };
  } catch {
    return { ok: false, prefix: '' };
  }
}

function _prefixDepth(prefix) {
  return String(prefix || '')
    .split('/')
    .filter(Boolean).length;
}

// p を「p 自身の区切り規約を保ったまま」分割し、末尾 depth 個（repo トップより下の相対分）を
// 切り離して { sep, head, tail } を返す。git の絶対パス出力は使わないので UNC（\\wsl.localhost\...）・
// ドライブ（C:\...）・POSIX（/home/...）のいずれでも p の表記を壊さない。UNC 先頭の \\ は
// 先頭 2 つの空要素として保持され、join でそのまま復元される。
function _splitTail(p, depth) {
  const s = String(p || '');
  const sep = s.includes('\\') ? '\\' : '/';
  const trimmed = s.replace(/[\\/]+$/, '');
  const segs = trimmed.split(/[\\/]/);
  const n = Math.max(0, Math.min(depth, segs.length));
  const tail = n > 0 ? segs.splice(segs.length - n, n) : [];
  return { sep, head: segs, tail };
}

// dir（repo トップより下でもよい）から repo トップのパス。git の絶対パス出力は使わず、
// prefix の深さぶんだけ dir 自身の表記から削る（WSL/Windows 混在でも表記を壊さない）。
function _repoTopPath(dir, prefixRel) {
  const { sep, head } = _splitTail(dir, _prefixDepth(prefixRel));
  return head.join(sep);
}


function _repairStateDeliveryPaths(entries, stateProjectDir, sourceProjectDir, prefixRel) {
  if (!sourceProjectDir || pathsEqual(stateProjectDir, sourceProjectDir)) return entries || [];
  const stateTop = _repoTopPath(stateProjectDir, prefixRel);
  const sourceTop = _repoTopPath(sourceProjectDir, prefixRel);
  return (entries || []).map((entry) =>
    entry && entry.role !== 'reference' && entry.path && pathsEqual(entry.path, stateTop)
      ? { ...entry, path: sourceTop }
      : entry
  );
}

function hasProjectStateMarkers(dir) {
  return (
    hasProjectManifest(dir) ||
    fs.existsSync(path.join(dir, 'backlog')) ||
    fs.existsSync(path.join(dir, 'charter.md')) ||
    fs.existsSync(path.join(dir, 'journal.md')) ||
    fs.existsSync(path.join(dir, 'needs')) ||
    fs.existsSync(path.join(dir, 'archive'))
  );
}

function isProjectDir(dir) {
  return hasProjectStateMarkers(dir) || hasProjectStateMarkers(path.join(dir, '.agent-project'));
}

// instances の root は状態領域を指す。既定の隠し状態フォルダなら、その親がユーザーが
// 開発時に扱うワークスペースなので、一覧の識別子と表示名は親へ戻す。
function projectWorkspaceDir(projectRoot) {
  const resolved = path.resolve(String(projectRoot || ''));
  return path.basename(resolved) === '.agent-project' ? path.dirname(resolved) : resolved;
}

// プロジェクト一覧は **実行エンジンの状況ファイル**（engine/status.json の children）から作る
// （実装計画 W2-4）。フォルダを列挙する設定・親フォルダの自動スキャン・稼働レコードからの
// 自動追加はすべて廃止した——プロジェクトを宣言する場所は実行側の host.yaml 1 か所で、
// この画面はそれを映すだけ（設計 §4.6・R10）。
// children[].root は実行側が `run --watch --root` に渡している値そのものなので、
// resolveProjectRoot（設定が無ければ自分自身に倒す）で状態の置き場へ寄せられる。
function discover(cfg) {
  const engine = require('./engine');
  const status = engine.readStatus(cfg);
  const roots = new Map(); // resolved root -> {root, source, child}
  for (const child of status.children) {
    const viewer = String(child.viewerRoot || '').trim();
    if (!viewer) continue;
    // 絶対パス（POSIX / ドライブ / UNC）はそのまま使う。UNC を path.resolve に通すと、
    // 実行ホストによっては cwd を前置されて存在しないパスに化ける。
    const resolved = _isPosixAbs(viewer) || path.isAbsolute(viewer) || viewer.startsWith('\\\\')
      ? viewer
      : path.resolve(viewer);
    if (!roots.has(resolved)) roots.set(resolved, { root: resolved, source: 'engine', child });
  }
  // 定常業務専用フォルダ（S2）。agent-project の管理外なので engine/status.json には出ない
  // が、セレクタには並べて定常業務タブを開けるようにする。**engine 由来が既に居る実体には
  // 足さない**——project エントリは backlog / charter / needs / 検収を持ち、routine エントリは
  // cowork タブしか持たないので、routine で上書きすると機能が消える。
  for (const raw of (((cfg && cfg.cowork) || {}).roots || [])) {
    const declared = String(raw || '').trim();
    if (!declared) continue;
    const expanded = declared.replace(/^~(?=$|\/|\\)/, os.homedir());
    const resolved = _isPosixAbs(expanded) ? toViewerPath(expanded)
      : path.isAbsolute(expanded) || expanded.startsWith('\\\\') ? expanded
        : path.resolve(expanded);
    if (![...roots.keys()].some((k) => pathsEqual(k, resolved))) {
      roots.set(resolved, { root: resolved, source: 'cowork', child: null });
    }
  }

  const projects = [];
  const seenDirs = new Set();                     // 実体（状態の置き場）で重複排除する
  // engine 由来を先に処理する（同じ実体に解決される cowork.roots のエントリを後から
  // 落とすため。Map は挿入順で回るので engine → cowork の順になる）。
  for (const { root, source, child } of roots.values()) {
    const workspace = root;                       // 選択の識別子（readProject の入力もこれ）
    const dir = resolveProjectRoot(workspace);    // 状態の置き場（backlog/needs/charter はこの下）
    // 本体（<repo>/.agent-project）と状態 worktree（<repo>-agent-state/.agent-project）は
    // どちらも登録・スキャンで挙がるが、正規化すると同じ実体を指す。両方を並べると同じ run が
    // 二重に見え、どちらを操作したのか分からなくなる。実体で畳む。
    const key = _pathKey(dir);
    if (seenDirs.has(key)) continue;
    seenDirs.add(key);
    const tasks = listTasks(path.join(dir, 'backlog'));
    const byStatus = {};
    for (const t of tasks) byStatus[t.status] = (byStatus[t.status] || 0) + 1;
    const needs = safeList(path.join(dir, 'needs')).filter((f) => f.endsWith('.md')).length;
    // instances（同一ホスト・確定）を先に見て、無ければ status.json（リモート・同期経由の推定）
    // にフォールバックする（projectLiveness が両方を見る）。突き合わせは本体が記録する
    // root＝プロジェクトルートで行う。
    const liveness = projectLiveness(dir, child);
    // 実行エンジンが「繰り返し失敗したので一時的に切り離した」プロジェクトは、稼働中と
    // 区別して出す（人が気づかないと、そのプロジェクトだけ永久に止まったままになる）。
    const quarantined = !!(child && child.quarantined);
    // 稼働時間外の計画停止。切り離しと違って時間が来れば自動で戻るので、別の印にする
    // （同じ「停止中」に見えると、直す必要が無いものを人が直しに行く）。
    const offHours = !!(child && child.paused);
    // 表示名: charter.md の `# Charter: <name>` があればそれを一覧にも出す（既定はワークスペース名。
    // charter を編集するだけでサイドバーに任意の名前を出せる。charter.md はサイドバーからも既存の
    // 「✎ charter.md」で編集できるため、ここでは discover 側の表示だけ揃える）。
    const charterFile = path.join(dir, 'charter.md');
    const hasCharterFile = fs.existsSync(charterFile);
    const hasCharter =
      hasCharterFile || safeList(path.join(dir, 'charters')).some((f) => f.endsWith('.md'));
    const charterName = hasCharterFile ? (parseCharter(readText(charterFile)) || {}).name || '' : '';
    projects.push({
      name: path.basename(projectWorkspaceDir(workspace)),
      charterName,
      dir: workspace,
      root: dir,             // プロジェクトルート（状態の置き場。readProject が操作の基準にする）
      source,
      // kind: project = agent-project が回すプロジェクト / routine = 定常業務専用フォルダ（S2）。
      // 表示側は既存の isProject 分岐（cowork タブのみ・既定タブ cowork）へ流す。
      kind: source === 'cowork' ? 'routine' : 'project',
      exists: fs.existsSync(workspace),
      isProject: isProjectDir(workspace) || isProjectDir(dir),
      hasCharter,
      backlogCount: tasks.length,
      byStatus,
      needsCount: needs,
      running: liveness.running,
      paused: liveness.paused,
      quarantined,
      offHours,
      liveness,
    });
  }
  return { projects, engine: status };
}

// ---------------------------------------------------------------------------
// agent-flow バスの発見
// ---------------------------------------------------------------------------

// agent-project の既定は <root>/bus だが、--bus / 設定 `bus:` の明示バス構成では別の場所になる。
// CLI に聞かず、ファイルの存在だけで候補を順に当たる:
//   優先: 明示設定（flowBusByProject / flowBus / agent-project.yaml の bus:）
//   次点: <root>/bus（既定）
// 明示設定があるのに「ローカル bus に runs がある」だけでそちらを採ると、本体が書く共有バスと
// viewer の監視先が割れ、cancel/resubmit が空振りする。設定がある候補を先に採用する。
// runs/ を持つ候補を採用。どれにも無ければ最優先候補を返す（hasBus=false）。
function resolveBusDir(projectDir, workspaceDir, cfg) {
  const workspace = path.resolve(String(workspaceDir || projectDir || ''));
  const preferred = [];
  const fallback = [];
  const push = (list, dir, source) => {
    if (!dir) return;
    let resolved = String(dir).replace(/^~(?=$|\/|\\)/, os.homedir());
    if (_isPosixAbs(resolved)) resolved = toViewerPath(resolved);
    else resolved = path.resolve(resolved);
    if (![...preferred, ...fallback].some((c) => pathsEqual(c.dir, resolved))) {
      list.push({ dir: resolved, source });
    }
  };

  push(fallback, path.join(projectDir, 'bus'), 'project');
  // agent-flow 自身の state-git が状態リポジトリへ鏡写しするバスの名前空間
  // （本体の FLOW_STATE_SUBDIR="agent-flow"）。バスをルート外に置く構成では、実行中 run の
  // ミラーはリモート clone の <clone>/agent-flow に届く。従来は flowBusByProject の手動設定が
  // 必須で、設定漏れ＝「別 PC で実行中の run が見えない」だったため自動発見する。
  push(fallback, path.join(projectDir, 'agent-flow'), 'state-mirror');
  // pure-remote（clone だけ・ローカル daemon 無し）では明示写像の <clone>/agent-flow を使う。
  const names = [path.basename(path.resolve(projectDir)), path.basename(workspace)];
  const byProject = cfg && cfg.projects && cfg.projects.flowBusByProject;
  if (byProject && typeof byProject === 'object') {
    const hit = names.find((n) => byProject[n]);
    if (hit) push(preferred, byProject[hit], 'config-per-project');
  }
  if (cfg && cfg.projects && cfg.projects.flowBus) {
    push(preferred, cfg.projects.flowBus, 'config');
  }

  const toolCfg = readToolConfig('agent-project', [workspace, ...agentDirCandidates(workspace)]);
  if (toolCfg && toolCfg.values.bus) {
    const raw = String(toolCfg.values.bus);
    push(preferred, path.isAbsolute(raw) ? raw : path.join(projectDir, raw), 'agent-project.yaml');
  }

  const ordered = [...preferred, ...fallback];
  // 明示設定（preferred）は従来どおり定義順の最初の実在を採る（人の指定が正）。
  const explicitHit = preferred.find((c) => fs.existsSync(path.join(c.dir, 'runs')));
  if (explicitHit) {
    return { busDir: explicitHit.dir, hasBus: true, source: explicitHit.source, candidates: ordered };
  }
  // 自動発見（fallback）は <root>/bus と <root>/agent-flow（鏡写し）が両方在りうる。
  // エンジン PC ではローカル bus が生きた実体、リモート clone では鏡写し側が新しいことが
  // 多い——構成を推測せず、実測の鮮度（runs 配下の最新更新時刻）で新しい方を選ぶ。
  const implicitHits = fallback.filter((c) => fs.existsSync(path.join(c.dir, 'runs')));
  if (implicitHits.length) {
    const pick = implicitHits.length === 1
      ? implicitHits[0]
      : implicitHits.slice().sort((a, b) => _busRecency(b.dir) - _busRecency(a.dir))[0];
    return { busDir: pick.dir, hasBus: true, source: pick.source, candidates: ordered };
  }
  const first = ordered[0] || { dir: path.join(projectDir, 'bus'), source: 'project' };
  return { busDir: first.dir, hasBus: false, source: first.source, candidates: ordered };
}

// バス候補の鮮度: runs/ とその直下エントリ（有界サンプル）の最新 mtime。
// run の meta/イベントはファイル置換（rename）で書かれるため run ディレクトリの mtime が動く。
function _busRecency(busDir) {
  const runsDir = path.join(busDir, 'runs');
  let latest;
  try {
    latest = fs.statSync(runsDir).mtimeMs;
  } catch {
    return 0;
  }
  for (const name of safeList(runsDir).slice(0, 50)) {
    try {
      const m = fs.statSync(path.join(runsDir, name)).mtimeMs;
      if (m > latest) latest = m;
    } catch {
      /* 列挙後に消えた run は無視 */
    }
  }
  return latest;
}

// 1 プロジェクトの完全なスナップショット。
// 入力は**ワークスペース**（登録するフォルダ）。状態は resolveProjectRoot が導く
// **プロジェクトルート**（dir）の直下から読む。返り値の `dir` はプロジェクトルートで、
// 以降の操作（approve/enqueue/reset/authoring/flow-archive）はすべてこれを基準にする。
function readProject(workspaceDir, cfg) {
  const workspace = path.resolve(String(workspaceDir || ''));
  const dir = resolveProjectRoot(workspace);
  const backlog = listTasks(path.join(dir, 'backlog'));
  const archive = listTasks(path.join(dir, 'archive'));
  // 監視担当（チーム運用）: 各タスクへ実効担当を載せ、メンバー一覧は割り当て済みの
  // 名前も合流して返す（ミーティングで新しい名前を書けばそのまま選択肢になる）。
  const assignments = readAssignments(dir);
  const memberSet = new Set(assignments.members);
  for (const t of [...backlog, ...archive]) {
    t.owner = effectiveOwner(assignments, t);
    if (t.owner) memberSet.add(t.owner);
  }
  assignments.members = [...memberSet].sort((a, b) => a.localeCompare(b, 'ja'));
  const needsDir = path.join(dir, 'needs');
  const needs = attachDeliveryHintsFromBacklog(
    synthesizeNeedsFromBacklog(listMdDir(needsDir, parseNeeds), backlog, needsDir, archive),
    backlog
  );
  // 直前の指示の失敗（commands/*.err）を該当カードへ。決着済みカードには出さない。
  const commandFailures = listCommandFailures(dir);
  // 直前の指示の受理（commands/processed/*.json）を該当カードへ＝「送信済み → 受理済み」。
  const commandReceipts = listCommandReceipts(dir);
  for (const need of needs) {
    const tid = String(need.taskId || need.id || '').trim();
    const cf = commandFailures[tid];
    if (cf && !need.decided) need.commandFailure = cf;
    const cr = commandReceipts[tid];
    if (cr && !need.decided) need.commandReceipt = cr;
  }
  // 要対応カードにも監視担当を載せる（誰がこの判断を見るかの分担を画面で示す）。
  // 併せて成果物レビューのコメント（reviews/<task-id>/）も載せる＝複数メンバーの
  // コメントを担当者が一箇所で確認・整理して承認/再実行を判断できる。
  const ownerByTask = new Map([...backlog, ...archive].map((t) => [String(t.id), t.owner || '']));
  for (const need of needs) {
    const tid = String(need.taskId || need.id || '').trim();
    need.owner = ownerByTask.get(tid) || '';
    need.comments = readReviewComments(dir, tid);
  }
  const gp = gitShowPrefix(dir);
  if (gp.ok && !pathsEqual(dir, workspace)) {
    // 状態 clone と成果物リポジトリは別物なので、needs に載った検収 diff の「所在」は
    // 状態側のパスのままだとこのビュアーから開けない。登録ワークスペース側へ読み替える。
    // （worktree 方式の逆変換 fromStateWorktree は方式ごと廃止した — S1）
    for (const need of needs) {
      need.delivery = _repairStateDeliveryPaths(need.delivery, dir, workspace, gp.prefix);
    }
  }
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

  // バックログ再分解の要求が未消化か（ボタンを「要求済み（取り込み待ち）」に変えるため）。
  // viewer がドロップした commands/*replan*.json（ingest 前）か、本体が立てた
  // .replan.request マーカー（ingest 後・再分解前）のどちらかが残っていれば pending。
  // 本体が再分解まで進めると両方消えてボタンが再び押せる状態に戻る。
  const replanPending = replanRequestPending(dir);

  // specs/<task-id>/ — spec 前段タスクの成果物（spec.md/design.md/tasks.md）。
  // needs カード（spec-review・総合検証）からワンクリックで開けるよう一覧しておく。
  const specs = [];
  for (const sub of safeList(path.join(dir, 'specs'))) {
    const sdir = path.join(dir, 'specs', sub);
    let isDir;
    try {
      isDir = fs.statSync(sdir).isDirectory();
    } catch {
      isDir = false;
    }
    if (!isDir) continue;
    const files = ['spec.md', 'design.md', 'tasks.md']
      .filter((f) => fs.existsSync(path.join(sdir, f)))
      .map((f) => ({ name: f, path: path.join(sdir, f) }));
    if (files.length) specs.push({ id: sub, files });
  }

  const bus = resolveBusDir(dir, workspace, cfg);

  // 複数 charter（charters/<name>.md = 1 バージョン）。無ければ単一 charter.md（従来）。
  // バージョンの identity は **ファイル名**（v2 など）。agent-project 側の `charter:` タグ・
  // milestone id・状態キーもファイル名基準なので、`# Charter: <title>` の宣言名がファイル名と
  // 食い違っても（前バージョンをコピーしてタイトルを直し忘れた等）ファイル名を優先する。
  // `...ch` を先に展開してから name を確定し、宣言名は title として保持する（上書き防止）。
  const charters = [];
  for (const f of safeList(path.join(dir, 'charters')).sort()) {
    if (!f.endsWith('.md')) continue;
    const ch = parseCharter(readText(path.join(dir, 'charters', f)));
    if (ch) {
      charters.push({ ...ch, name: f.replace(/\.md$/, ''), title: ch.name, file: path.join(dir, 'charters', f) });
    }
  }

  const reposFile = reposFileName(dir);

  return {
    dir,                                  // プロジェクトルート（状態の置き場。操作の基準）
    workspace,                            // ワークスペース（登録フォルダ。設定 .agent/ の在り処）
    // 表示名はワークスペース名。状態フォルダ（.agent-project 等）の技術的な名前を出さない。
    name: path.basename(workspace),
    inboxFiles,
    replanPending,
    charter: parseCharter(readText(path.join(dir, 'charter.md'))),
    charters,
    policy: parsePolicy(readText(path.join(dir, 'policy.md'))),
    backlog,
    archive,
    assignments,                          // 監視担当（viewer 管理・assignments.json）
    byStatus,
    claims,
    needs,
    specs,
    // プロジェクトルール（rules.md）: 人が書く恒常ルール＋効いた learn の自動昇格。
    // 全タスクの act / plan / verify 合成へ常時注入される（本体 §6.6）。無ければ null。
    rules: readText(path.join(dir, 'rules.md')),
    // 墓標（却下したタスクの「作り直さない」記録）。削除＝却下の取り消し導線に使う。
    tombstones: parseTombstones(readText(path.join(dir, 'tombstones.md'))),
    decisions: decisionsAll.slice(0, 100),
    journal: tailLines(path.join(dir, 'journal.md'), 200),
    runLog: readRunLog(path.join(dir, 'run-log.jsonl')),
    delivery: readDelivery(path.join(dir, 'DELIVERY.md')),
    projectState: readJson(path.join(dir, 'project.json')),
    // 実効レジストリは yaml → yml → json の優先順（本体の REPOS_FILE_NAMES と同じ）。
    // yaml/yml が正のときはパーサが無く repos は null にする（残骸の repos.json を読んで
    // 古い内容を見せない）。どのファイルが正かは reposFile で UI へ伝える。
    reposFile,
    repos: reposFile === 'repos.json' ? readJson(path.join(dir, 'repos.json')) : null,
    autonomy,
    liveness: projectLiveness(dir, undefined, cfg),
    nodes: readNodeStatuses(dir),   // 複数 PC 分散運用のノード別生存一覧（無ければ空）
    busDir: bus.busDir,
    hasBus: bus.hasBus,
    busSource: bus.source,
    busCandidates: bus.candidates,
  };
}

module.exports = {
  dependentsOf,
  parseTask,
  readAssignments,
  effectiveOwner,
  ASSIGNMENTS_FILE,
  readReviewComments,
  REVIEWS_DIR,
  parseCharter,
  parsePolicy,
  parseTombstones,
  parseNeeds,
  synthesizeNeedsFromBacklog,
  attachDeliveryHintsFromBacklog,
  listCommandFailures,
  listCommandReceipts,
  readNodeStatuses,
  _splitDiff,
  _deliveryFromDetail,
  _extractMrUrls,
  parseDecisions,
  listInstances,
  isProjectRunning,
  replanRequestPending,
  readStatus,
  projectLiveness,
  discover,
  readProject,
  resolveProjectRoot,
  resolveStateRepoRoot,
  resolveBusDir,
  _repairStateDeliveryPaths,
  _sameGitRemote,
  _pathKey,
  pathsEqual,
  hostsMatch,
  sameMachineStatus,
  toViewerPath,
  viewerDistro,
  _isPosixAbs,
};
