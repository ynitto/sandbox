'use strict';

// kiro-project のプロジェクトデータ（プロジェクトルート直下）を
// 読み取り専用で解析するデータ層。書式の正典は
// tools/kiro-project/backlog.md.example / charter.md.example と
// docs/designs/kiro-project-design.md §3。パース規則は kiro-project.py の
// HEAD_RE / FIELD_RE / parse_charter / parse_policy に合わせている。
// 登録パス 1 件 = 1 プロジェクトルート（1 プロジェクト = 1 ディレクトリ = 1 プロセス）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { readToolConfig } = require('./toolconfig');

// kiro-project.py と同じ正規表現
const HEAD_RE = /^##\s+(\S+?):\s*(.*)$/;
const FIELD_RE = /^-\s+(\w+):\s*(.*)$/;
const POLICY_RE = /^(deny|pin|defer|offload|gate|protect|route):\s*(.+)$/;
const DR_HEAD_RE = /^##\s+(DR-\d+)\s+(\S+)\s+actor:\s*(.*)$/;

// offloaded: 非ブロッキング委譲（act_async）で kiro-flow daemon へ submit 済み・結果待ち。
//   flow_run（run-id）を extra に持ち、フロータブの該当 run へ辿れる。
// proposed: 実行前レビュー待ち（承認されるまで実行しない）／rejected: 却下済み（archive に退避）
const TASK_STATUSES = ['inbox', 'draft', 'proposed', 'ready', 'doing', 'done', 'blocked', 'review', 'offloaded', 'rejected'];

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
  // マスター憲章（`## master` セクション付き）: プロジェクト全体の普遍的な前提。
  // kiro-project はこれを分解せず、計画バージョン（charters/<name>.md）へ継承する。
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

// 「差分」に並ぶファイルのうち、kiro-project / kiro-flow が実行のたびに書く内部記録
// （bus/ の run ログ・claims・needs・journal・project.json 等）は人の判断材料にならない。
// これらだけが並んだカードは「変更あり」に見えて中身が無く、実際に何が変わったのか読み取れない。
const _INTERNAL_DIFF_RE = new RegExp(
  '(^|/)(bus|claims|needs|decisions|commands|inbox|archive|flow-archive)/'
    + '|(^|/)(journal\\.md|project\\.json|repos\\.json|run-log\\.jsonl|status\\.json|DELIVERY\\.md)$'
);

// 判断材料の「差分:」に続くインデント付きリストを、成果物と内部記録に分ける。
function _splitDiff(detail) {
  const s = String(detail || '');
  const head = s.match(/^-\s*差分\s*[:：].*$/m);
  const out = { artifacts: [], internal: [], truncated: 0, hasDiff: Boolean(head) };
  if (!head) return out;
  const rest = s.slice(s.indexOf(head[0]) + head[0].length).split('\n');
  for (const raw of rest) {
    if (!raw.trim()) continue;               // 見出し行の残り・空行は読み飛ばす
    if (!/^\s+-\s/.test(raw)) break;         // インデントが切れたら差分リストの終わり
    const item = raw.trim().replace(/^-\s*/, '').trim();
    const more = item.match(/^…\s*他\s*(\d+)\s*件/);
    if (more) {
      out.truncated = Number(more[1]);
      continue;
    }
    (_INTERNAL_DIFF_RE.test(item) ? out.internal : out.artifacts).push(item);
  }
  return out;
}

// 失敗理由（verify の生出力）を人が読める一文にする。よくある形だけを解釈し、当てはまらな
// ければ空を返す（生のテキストは常に残すので、要約できないときも情報は失われない）。
function _summarizeFailure(why, detail) {
  const verify = (String(detail || '').match(/^-\s*検証\s*[:：]\s*(.*)$/m) || [])[1] || '';
  const raw = `${why || ''} ${verify}`;
  if (!raw.trim()) return '';
  const notFound = (raw.match(/(?:file or directory not found|No such file or directory)[:\s]+([^\s)）]+)/) || [])[1];
  const failed = (raw.match(/(\d+)\s+failed/) || [])[1];
  const cmdMissing = (raw.match(/([\w./-]+):\s*command not found/) || [])[1];
  const exit = (raw.match(/exit=(\d+)/) || [])[1];
  // run_verify が特定した「失敗した工程」（&& 連鎖の途中で沈黙して落ちた工程のトレース）
  const step = (raw.match(/失敗した工程:\s*`([^`]+)`/) || [])[1];
  const passed = (raw.match(/(\d+)\s+passed/) || [])[1];

  if (step) {
    return `検証コマンドの工程「${step}」で失敗しました（それより前の工程は成功しています）。`;
  }
  if (cmdMissing) return `検証コマンド「${cmdMissing}」がこの環境に見つかりません。`;
  if (notFound) {
    return `検証コマンドが「${notFound}」を見つけられませんでした。`
      + '実行ディレクトリ（下の「所在」）から見て、そのパスが存在しない可能性があります。';
  }
  if (failed) return `テストが ${failed} 件失敗しました。`;
  if (/no tests ran/i.test(raw)) return 'テストが 1 件も実行されませんでした（対象が見つからないか、条件に一致しません）。';
  if (exit && passed && Number(exit) !== 0) {
    // 「テストは通っているのに exit≠0」: && 連鎖の後段（grep・外部チェック等）が沈黙して
    // 失敗した古い形式の記録。どこが落ちたかは記録に無いが、少なくとも「テストの失敗では
    // ない」ことを言う（テスト成功の出力だけを見せられて混乱するのが一番まずい）。
    return `テストは ${passed} 件成功していますが、検証コマンドの後段の工程（grep や外部チェックなど）が失敗しています（終了コード ${exit}）。`;
  }
  if (exit) return `検証コマンドが失敗しました（終了コード ${exit}）。`;
  return '';
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
    diff: null, // { artifacts, internal, truncated, hasDiff } — 成果物と内部記録に分けた差分
    risk: '', // 検収票のリスクダイジェスト総合値（low/med/high）。バッジ表示用
  };
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
      else if (key === 'risk') need.risk = val;
    }
  }
  const title = body.match(/^#\s+(.+)$/m);
  if (title) need.title = title[1].trim();
  need.decided = /-\s*\[x\]/i.test(body);
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
  need.evidenceThin = _evidenceThin(need.detail);
  if (need.evidenceThin) need.detail = _stripThinEvidence(need.detail);
  // 差分を「成果物」と「内部記録（bus/ の run ログ等）」に分ける。実行のたびに書かれる内部
  // ファイルだけが並ぶと「14 ファイル変更」に見えて中身が無い。成果物が 0 なら痩せた扱いにする。
  need.diff = _splitDiff(need.detail);
  if (need.diff.hasDiff && need.diff.artifacts.length === 0 && need.diff.internal.length > 0) {
    need.evidenceThin = true;
  }
  need.failureSummary = _summarizeFailure(need.why, need.detail);
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
  return path.join(os.homedir(), '.kiro-project');
}

// ~/.kiro-project/instances/*.json — 稼働発見レコード（root = プロジェクトルート）
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

// プロジェクトの「登録」を実体に即して直接消す（discover が発見する経路をそのまま辿るだけで、
// 除外リストのような別レイヤーは作らない。ファイル・ディレクトリ本体には一切触れない）:
//   - config.kiro.roots に直接登録（source: 'config'）→ その要素を roots から取り除く
//   - ~/.kiro-project/instances/*.json 経由の自動発見（source: 'instance'）→ 該当レコードの
//     ファイルを削除する（稼働中プロセスが生きていれば次のハートビートで自然に書き直されるが、
//     それはそのプロセス自身が再登録するのであって、ビュアー側の設定ではない）
//   - 親フォルダ登録（scan）配下で見つかった子は、個別の登録が無い（親フォルダの登録そのものが
//     「設定」）ため対象外＝呼び出し側でエラーにする
// 戻り値: { removedFrom: 'roots', roots: string[] } | { removedFrom: 'instance', file: string }
//        | { removedFrom: null }
function removeProjectRegistration(cfg, dir) {
  const resolved = path.resolve(dir);
  const rootsList = (cfg.kiro && cfg.kiro.roots) || [];
  const idx = rootsList.findIndex(
    (r) => path.resolve(String(r).replace(/^~(?=$|\/|\\)/, os.homedir())) === resolved
  );
  if (idx !== -1) {
    const nextRoots = rootsList.slice();
    nextRoots.splice(idx, 1);
    return { removedFrom: 'roots', roots: nextRoots };
  }
  const idir = path.join(globalDir(), 'instances');
  for (const f of safeList(idir)) {
    if (!f.endsWith('.json')) continue;
    const file = path.join(idir, f);
    const rec = readJson(file);
    if (rec && rec.root && path.resolve(String(rec.root)) === resolved) {
      fs.unlinkSync(file);
      return { removedFrom: 'instance', file };
    }
  }
  return { removedFrom: null };
}

// <root>/status.json — 生存信号（kiro-project.py の write_status が書く。paused も載る）。
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

const _norm = (p) => {
  try {
    return path.resolve(String(p || '')).toLowerCase();
  } catch {
    return '';
  }
};

// プロジェクトの kiro-projects の稼働判定。判定根拠と経過時間も返す（UI 表示用）:
//   'instances'   … 同一ホストの instances（heartbeat 鮮度）から確定判定（従来どおり。CLI 不要）
//   'status-sync' … リモート本体（state_git 越し）は同期されてきた status.json の新しさで近似判定
//                    （同期遅延ぶんの誤差を許容する。running:false でも「最終確認 N 分前」は分かる）
//   'none'        … 判定材料が無い（instances も status.json も無い）
// WSL 内の本体が登録する root_windows（\\wsl.localhost\...）にも一致させる
// （Windows のビュアーから WSL 内の稼働を発見するため）。
function projectLiveness(dir) {
  const target = _norm(dir);
  const status = readStatus(dir);
  const paused = !!(status && status.paused);
  if (target) {
    for (const inst of listInstances()) {
      if (!inst.fresh) continue;
      // レコードの root は「リダイレクト前の素の root」（本体の設計）。状態を worktree へ
      // 逃がしている構成では、viewer の登録パスは実体（<repo>-kiro-state/.kiro-project）へ
      // 正規化されるため root とは一致しない。実効パス（backlog の親 = 実書き込み先）でも
      // 照合しないと、稼働中なのに instances を取りこぼして status.json の鮮度判定へ落ち、
      // 長い作業（LLM 実行）中に「本体が停止中」と誤表示する（実際に起きた）。
      const candidates = [
        inst.root,
        inst.root_windows,
        inst.backlog ? path.dirname(String(inst.backlog)) : '',
      ];
      if (candidates.some((r) => r && _norm(r) === target)) {
        return { running: true, via: 'instances', ageSec: 0, paused };
      }
    }
  }
  if (status) {
    // 同一ホストが書いた status.json なら「別マシン」ではない。instances の生存窓（ttl×3＝
    // 既定 270 秒）は本体が長いタスク（LLM 実行）に入ると心拍が飛ばずに切れるが、status.json
    // の窓（既定 600 秒）はまだ生きている、という時間帯がある。そこで status-sync に落とすと
    // ローカル稼働を「別マシンで稼働中」と誤表示していた（サイドバーの `~`、概要の「稼働中
    // （別マシン）」）。host が取れないときは判定材料が無いので従来どおり同期扱いにする。
    const sameHost = String(status.host || '') === os.hostname();
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

// プロジェクトのマニフェスト = kiro-project 設定ファイル。本体の _find_config と同じく
// ルート直下 → .kiro/ の順で見る（1 root = 1 プロジェクトの発見マーカーを兼ねる）。
const TOOL_CONFIG_NAMES = ['kiro-project.yaml', 'kiro-project.yml', 'kiro-project.json'];

function hasProjectManifest(dir) {
  return TOOL_CONFIG_NAMES.some(
    (n) => fs.existsSync(path.join(dir, n)) || fs.existsSync(path.join(dir, '.kiro', n))
  );
}

// ワークスペース（このビュアーに登録するフォルダ）から、kiro-project が状態を書く
// **プロジェクトルート**を解決する。
//
//   ワークスペース  … .kiro/kiro-project.yaml を持つ開発フォルダ。kiro-project CLI を起動する場所
//                    （CLI から見た cwd）。人が普段開いているフォルダ＝登録するのはこれ。
//   プロジェクトルート … 設定の `root:` が指す状態の置き場（例 <ws>/.kiro-project）。backlog /
//                    needs / charter / bus はすべてこの直下。CLI の --root・instances の root と同じ。
//
// 設定の探索順は本体の _find_config と同じ（<ws>/ → <ws>/.kiro/）。`root:` が無ければワークスペース
// 自身がプロジェクトルート＝状態フォルダを直接登録する従来の使い方（instances 由来の自動発見も
// root を直接指すのでこの経路に乗る）。
// ~/.kiro のグローバル設定にある `root:` は使わない: それを採るとすべてのワークスペースが同じ
// 状態フォルダを指してしまう（本体は 1 プロセス 1 プロジェクトなので困らないが、ビュアーは
// 複数プロジェクトを同時に扱う）。
function resolveProjectRoot(workspaceDir) {
  const ws = path.resolve(String(workspaceDir || ''));
  if (!ws) return ws;
  const cfg = readToolConfig('kiro-project', [ws, path.join(ws, '.kiro')]);
  const fromWorkspace =
    cfg && cfg.file && path.resolve(cfg.file).startsWith(ws + path.sep);
  const raw = fromWorkspace && cfg.values ? cfg.values.root : null;
  const branch =
    (fromWorkspace && cfg.values && cfg.values.state_branch) || DEFAULT_STATE_BRANCH;
  if (!raw) return toStateWorktree(ws, branch);
  const r = String(raw).replace(/^~(?=$|\/|\\)/, os.homedir());
  const root = path.isAbsolute(r) ? path.resolve(r) : path.resolve(ws, r);
  return toStateWorktree(root, branch);
}

const DEFAULT_STATE_BRANCH = 'kiro-state';

// 状態の実体は「状態 worktree」にある。kiro-project は root（例 <repo>/.kiro-project）の読み書きを
// <repo>-<state_branch>/.kiro-project へ逃がすので、本体側に残る .kiro-project は **main に載る
// バックアップ**であって実体ではない（significant だけが載り、bus＝run の進捗は載らない）。
//
// 本体側を開くと 3 つ壊れる:
//   ・読み  … 古いバックアップを見る。実行中の run が一切見えない（bus が無い）
//   ・書き  … 指示・タスク編集が本体へ落ち、人の作業ツリーを汚す
//   ・git   … gitAutoPush が main へ commit/push してしまう（main はバックアップ専用にしたい）
// 実体へ正規化してから開く。worktree が無ければ（kiro-project 未起動・非 git）そのまま返す。
function toStateWorktree(root, branch) {
  const repo = gitToplevelOf(root);
  if (!repo) return root;                                    // git 管理外 → 本体がそのまま実体
  if (path.basename(repo).endsWith(`-${branch}`)) return root;  // 既に状態 worktree の中にいる
  const wt = path.join(path.dirname(repo), `${path.basename(repo)}-${branch}`);
  const candidate = path.join(wt, path.relative(repo, root));
  return isProjectDir(candidate) ? candidate : root;         // 未作成なら本体のまま（従来動作）
}

// root を含む git 作業ツリーのトップ（git 管理外なら null）。worktree の .git は *ファイル* なので
// ディレクトリ存在チェックでは判定できない。git に聞く。
function gitToplevelOf(dir) {
  try {
    const r = require('child_process').spawnSync(
      'git', ['-C', dir, 'rev-parse', '--show-toplevel'],
      { encoding: 'utf8', timeout: 10000 }
    );
    const out = String(r.stdout || '').trim();
    return r.status === 0 && out ? out : null;
  } catch {
    return null;
  }
}

function isProjectDir(dir) {
  return (
    hasProjectManifest(dir) ||
    fs.existsSync(path.join(dir, 'backlog')) ||
    fs.existsSync(path.join(dir, 'charter.md')) ||
    fs.existsSync(path.join(dir, 'journal.md')) ||
    fs.existsSync(path.join(dir, 'needs')) ||
    fs.existsSync(path.join(dir, 'archive'))
  );
}

// 登録ルートがプロジェクトそのものでないとき、配下からプロジェクト
// （kiro-project.yaml マニフェスト、または charter.md / backlog/ 等のマーカーを持つ
// ディレクトリ）を探す。1 root = 1 プロジェクトなので、プロジェクトと判定した
// ディレクトリの配下はそれ以上掘らない。プロジェクト内部の既知ディレクトリと
// 隠しディレクトリはスキップして走査を軽く保つ。
const SCAN_SKIP = new Set([
  'node_modules', 'bus', 'work', 'archive', 'flow-archive', 'backlog', 'needs', 'decisions',
  'commands', 'inbox', 'claims', 'autonomy', 'charters', 'runs', 'dist', 'release',
]);

function scanForProjects(rootDir, maxDepth) {
  const found = [];
  const walk = (dir, depth) => {
    for (const name of safeList(dir)) {
      if (name.startsWith('.') || SCAN_SKIP.has(name)) continue;
      const child = path.join(dir, name);
      let st;
      try {
        st = fs.statSync(child);
      } catch {
        continue;
      }
      if (!st.isDirectory()) continue;
      if (isProjectDir(child)) {
        found.push(child);
        continue;
      }
      if (depth < maxDepth) walk(child, depth + 1);
    }
  };
  walk(rootDir, 1);
  return found.sort();
}

// 設定 roots ＋ instances 自動発見からプロジェクト一覧を作る。
// 登録パス 1 件 = 1 ワークスペース（.kiro/kiro-project.yaml を持つ開発フォルダ。状態フォルダを
// 直接登録する従来の使い方や、instances 由来の自動発見＝プロジェクトルート直指定も
// resolveProjectRoot が「設定が無ければ自分自身」に倒すのでそのまま乗る）。
// 登録パスがワークスペースでもプロジェクトでもない場合は「束ねる親フォルダ」とみなし、
// 配下（既定 2 階層・設定 kiro.scanDepth）から kiro-project.yaml 等を自動発見して
// 見つかったものをそれぞれ 1 件として追加する。
function discover(cfg) {
  const roots = new Map(); // resolved root -> {root, source}
  const scanDepth = Math.max(1, Number((cfg.kiro && cfg.kiro.scanDepth) || 2));
  for (const r of (cfg.kiro && cfg.kiro.roots) || []) {
    if (!r) continue;
    const resolved = path.resolve(String(r).replace(/^~(?=$|\/|\\)/, os.homedir()));
    if (fs.existsSync(resolved) && !isProjectDir(resolved)) {
      const children = scanForProjects(resolved, scanDepth);
      if (children.length) {
        for (const d of children) {
          if (!roots.has(d)) roots.set(d, { root: d, source: 'scan' });
        }
        continue;
      }
    }
    roots.set(resolved, { root: resolved, source: 'config' });
  }
  const instances = cfg.kiro && cfg.kiro.autoDiscover === false ? [] : listInstances();
  for (const inst of instances) {
    if (!inst.root) continue;
    const resolved = path.resolve(String(inst.root));
    if (!roots.has(resolved)) roots.set(resolved, { root: resolved, source: 'instance' });
  }

  const projects = [];
  const seenDirs = new Set();                     // 実体（状態の置き場）で重複排除する
  for (const { root, source } of roots.values()) {
    const workspace = root;                       // 登録パス（＝選択の識別子。config.roots と一致）
    const dir = resolveProjectRoot(workspace);    // 状態の置き場（backlog/needs/charter はこの下）
    // 本体（<repo>/.kiro-project）と状態 worktree（<repo>-kiro-state/.kiro-project）は
    // どちらも登録・スキャンで挙がるが、正規化すると同じ実体を指す。両方を並べると同じ run が
    // 二重に見え、どちらを操作したのか分からなくなる。実体で畳む。
    const key = path.resolve(dir);
    if (seenDirs.has(key)) continue;
    seenDirs.add(key);
    const tasks = listTasks(path.join(dir, 'backlog'));
    const byStatus = {};
    for (const t of tasks) byStatus[t.status] = (byStatus[t.status] || 0) + 1;
    const needs = safeList(path.join(dir, 'needs')).filter((f) => f.endsWith('.md')).length;
    // instances（同一ホスト・確定）を先に見て、無ければ status.json（リモート・同期経由の推定）
    // にフォールバックする（projectLiveness が両方を見る）。突き合わせは本体が記録する
    // root＝プロジェクトルートで行う。
    const liveness = projectLiveness(dir);
    // 表示名: charter.md の `# Charter: <name>` があればそれを一覧にも出す（既定はワークスペース名。
    // charter を編集するだけでサイドバーに任意の名前を出せる。charter.md はサイドバーからも既存の
    // 「✎ charter.md」で編集できるため、ここでは discover 側の表示だけ揃える）。
    const charterFile = path.join(dir, 'charter.md');
    const hasCharterFile = fs.existsSync(charterFile);
    const hasCharter =
      hasCharterFile || safeList(path.join(dir, 'charters')).some((f) => f.endsWith('.md'));
    const charterName = hasCharterFile ? (parseCharter(readText(charterFile)) || {}).name || '' : '';
    projects.push({
      name: path.basename(workspace),
      charterName,
      dir: workspace,        // 選択・登録解除はワークスペース基準（readProject の入力もこれ）
      root: dir,             // プロジェクトルート（状態の置き場。readProject が操作の基準にする）
      source,
      exists: fs.existsSync(workspace),
      isProject: isProjectDir(workspace),
      hasCharter,
      backlogCount: tasks.length,
      byStatus,
      needsCount: needs,
      running: liveness.running,
      paused: liveness.paused,
      liveness,
    });
  }
  return { projects, instances };
}

// ---------------------------------------------------------------------------
// kiro-flow バスの発見
// ---------------------------------------------------------------------------

// kiro-project の既定は <root>/bus だが、--bus / 設定 `bus:` の明示バス構成では別の場所になる。
// CLI に聞かず、ファイルの存在だけで候補を順に当たる:
//   1. <root>/bus（既定のバス）
//   2. ⚙ 設定 kiro.flowBusByProject[<name>]（プロジェクト単位のバス写像。pure-remote で
//      kiro-flow の鏡写し先 clone を割り当てる場合）
//   3. ⚙ 設定 kiro.flowBus（単一の明示指定）
//   4. kiro-project 設定ファイル（ワークスペースの .kiro → ~/.kiro）の bus:
//      （相対パスはプロジェクトルート基準で解決する＝本体の `--bus` と同じ規則）
// runs/ を持つ最初の候補を採用。どれにも無ければ既定の 1 を返す（hasBus=false）。
function resolveBusDir(projectDir, workspaceDir, cfg) {
  const workspace = path.resolve(String(workspaceDir || projectDir || ''));
  const candidates = [];
  const push = (dir, source) => {
    if (!dir) return;
    const resolved = path.resolve(String(dir).replace(/^~(?=$|\/|\\)/, os.homedir()));
    if (!candidates.some((c) => c.dir === resolved)) candidates.push({ dir: resolved, source });
  };

  push(path.join(projectDir, 'bus'), 'project');
  // pure-remote（clone だけ・ローカル daemon 無し）では <root>/bus に runs/ が無いため、
  // ここで割り当てた <clone>/kiro-flow が採用される。
  // 写像のキーは従来どおりプロジェクトルート名。ワークスペース名でも引ける（登録が
  // ワークスペースになったので、人はそちらの名前で書くほうが自然）。
  const names = [path.basename(path.resolve(projectDir)), path.basename(workspace)];
  const byProject = cfg && cfg.kiro && cfg.kiro.flowBusByProject;
  if (byProject && typeof byProject === 'object') {
    const hit = names.find((n) => byProject[n]);
    if (hit) push(byProject[hit], 'config-per-project');
  }
  if (cfg && cfg.kiro && cfg.kiro.flowBus) push(cfg.kiro.flowBus, 'config');

  // kiro-project 設定ファイルの bus:（設定は本体と同じくワークスペースから探す）
  const toolCfg = readToolConfig('kiro-project', [workspace, path.join(workspace, '.kiro')]);
  if (toolCfg && toolCfg.values.bus) {
    const raw = String(toolCfg.values.bus);
    push(path.isAbsolute(raw) ? raw : path.join(projectDir, raw), 'kiro-project.yaml');
  }

  for (const c of candidates) {
    if (fs.existsSync(path.join(c.dir, 'runs'))) {
      return { busDir: c.dir, hasBus: true, source: c.source, candidates };
    }
  }
  return { busDir: candidates[0].dir, hasBus: false, source: 'project', candidates };
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
    let isDir = false;
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
  // バージョンの identity は **ファイル名**（v2 など）。kiro-project 側の `charter:` タグ・
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

  return {
    dir,                                  // プロジェクトルート（状態の置き場。操作の基準）
    workspace,                            // ワークスペース（登録フォルダ。設定 .kiro/ の在り処）
    // 表示名はワークスペース名。状態フォルダ（.kiro-project 等）の技術的な名前を出さない。
    name: path.basename(workspace),
    inboxFiles,
    replanPending,
    charter: parseCharter(readText(path.join(dir, 'charter.md'))),
    charters,
    policy: parsePolicy(readText(path.join(dir, 'policy.md'))),
    backlog,
    archive,
    byStatus,
    claims,
    needs,
    specs,
    // プロジェクトルール（rules.md）: 人が書く恒常ルール＋効いた learn の自動昇格。
    // 全タスクの act / plan / verify 合成へ常時注入される（本体 §6.6）。無ければ null。
    rules: readText(path.join(dir, 'rules.md')),
    decisions: decisionsAll.slice(0, 100),
    journal: tailLines(path.join(dir, 'journal.md'), 200),
    runLog: readRunLog(path.join(dir, 'run-log.jsonl')),
    delivery: readDelivery(path.join(dir, 'DELIVERY.md')),
    projectState: readJson(path.join(dir, 'project.json')),
    repos: readJson(path.join(dir, 'repos.json')),
    autonomy,
    liveness: projectLiveness(dir),
    busDir: bus.busDir,
    hasBus: bus.hasBus,
    busSource: bus.source,
    busCandidates: bus.candidates,
  };
}

module.exports = {
  dependentsOf,
  parseTask,
  parseCharter,
  parsePolicy,
  parseNeeds,
  parseDecisions,
  listInstances,
  removeProjectRegistration,
  isProjectRunning,
  replanRequestPending,
  readStatus,
  projectLiveness,
  discover,
  scanForProjects,
  readProject,
  resolveProjectRoot,
  resolveBusDir,
};
