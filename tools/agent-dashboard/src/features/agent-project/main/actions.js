'use strict';

// 人のアクション層。agent-project の公式な入力契約だけを使う:
//   1. needs/<id>.md の「## Decision Outcome」への記入 + `- [x]`
//      → ingest_feedback が取り込む（フィードバック往復の正規ルート）
//   2. inbox/<name>.json のドロップ（E4 push 型の取り込み口）
//      → ingest_inbox が backlog 化する（タスク投入の正規ルート）
//   3. commands/<name>.json のドロップ（approve / hold / pin / defer / revise）
//      → ingest_commands が CLI と同一ロジック・同一の決定記録（DR）で実行する。
//      revise は人の即時フィードバック: タスクの内容・依存（after）・優先度の修正と
//      feedback（次の act に必ず届く指示）を、ループがブロックする前に能動的に届ける。
//      ファイルだけで届くため、本体が WSL 内で稼働していても操作できる。
//      本体が稼働していないときは agent-project CLI に委譲し、CLI も使えなければ
//      指示ファイルを置いて次回起動時の取り込みに委ねる（ロジックの二重実装はしない）。
// done の確定・状態遷移そのものをこのアプリが直接書き換えることはしない
// （「done は verify のみが根拠」の不変条件を壊さない）。

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const project = require('./project');
const { agentDirCandidates } = require('../../../base/main/agent-home');

const DECISION_MARKER = '## Decision Outcome';

// ---------------------------------------------------------------------------
// 1. needs へのフィードバック（Decision Outcome 記入 + [x] 確定）
// ---------------------------------------------------------------------------

// 合成票（needs ファイル欠落）向けの最小 MADR。本体 write_needs_file / ensure_needs と同系統。
function buildNeedsStub(stub) {
  const id = String((stub && stub.id) || '').trim();
  if (!id) throw new Error('needs スタブには id が必要です');
  const kind = String((stub && stub.kind) || 'blocked');
  const title = String((stub && stub.title) || id);
  const why = String((stub && stub.why) || '要対応');
  const date = new Date().toISOString().slice(0, 10);
  const heading =
    kind === 'plan-review' ? `実行前レビュー: ${title}` : `要対応: ${title}`;
  return (
    '---\n' +
    'status: proposed\n' +
    `date: ${date}\n` +
    'decision-makers: [human]\n' +
    `task-id: ${id}\n` +
    `kind: ${kind}\n` +
    '---\n\n' +
    `# ${heading}\n\n` +
    '## Context and Problem Statement\n\n' +
    `- なぜ: ${why}\n\n` +
    `${DECISION_MARKER}\n\n` +
    '<!-- 人の決定の記入欄。方針・指示をここに書く。 -->\n' +
    '- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n'
  );
}

function submitFeedback(needsFile, feedback, stub) {
  if (!fs.existsSync(needsFile)) {
    // 合成票（status 投影）でファイルが無いときは、差し戻し／再実行の正規ルート用にスタブを起こす。
    // 承認（approve）は commands/ 経由なのでファイル不要だが、feedback は needs への [x] が契約。
    if (!stub || !stub.id) {
      throw new Error(`needs ファイルがありません（取り込み済みの可能性）: ${needsFile}`);
    }
    fs.mkdirSync(path.dirname(needsFile), { recursive: true });
    fs.writeFileSync(needsFile, buildNeedsStub(stub), 'utf8');
  }
  let text = fs.readFileSync(needsFile, 'utf8');
  if (!text.includes(DECISION_MARKER)) {
    text = `${text.replace(/\n*$/, '\n\n')}${DECISION_MARKER}\n\n`;
  }
  const fb = String(feedback || '').trim();
  if (fb) {
    // マーカー直後に記入（read_feedback はコメントとチェックボックス行を
    // 除いたマーカー以降すべてを人の記入として読む）
    text = text.replace(DECISION_MARKER, `${DECISION_MARKER}\n\n${fb}\n`);
  }
  // 確定 [x] は Decision Outcome 配下だけを触る（本文チェックリストを潰さない）
  const outcomeIdx = text.search(/^##\s+Decision Outcome\s*$/m);
  if (outcomeIdx >= 0) {
    const head = text.slice(0, outcomeIdx);
    let tail = text.slice(outcomeIdx);
    if (/^\s*-\s*\[x\]/im.test(tail)) {
      // すでに確定済み
    } else if (/-\s*\[ \]/.test(tail)) {
      tail = tail.replace(/-\s*\[ \]/, '- [x]');
    } else {
      tail = `${tail.replace(/\n*$/, '\n')}\n- [x] 確定\n`;
    }
    text = head + tail;
  } else if (/^\s*-\s*\[x\]/im.test(text)) {
    // マーカー無しのレガシー票: 既存 [x] があれば触らない
  } else if (/-\s*\[ \]/.test(text)) {
    text = text.replace(/-\s*\[ \]/, '- [x]');
  } else {
    text = `${text.replace(/\n*$/, '\n')}\n- [x] 確定\n`;
  }
  fs.writeFileSync(needsFile, text, 'utf8');
  return { file: needsFile, feedback: fb };
}

// ---------------------------------------------------------------------------
// 2. タスク投入（inbox/*.json ドロップ）
// ---------------------------------------------------------------------------

function slugify(s) {
  const t = String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9぀-ヿ一-鿿_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return t || 'task';
}

function validateCharterVersion(projectDir, value) {
  const name = String(value || '').trim();
  if (!name) return '';
  if (name === '.' || name === '..' || path.basename(name) !== name || /[\\/\x00-\x1f]/.test(name)) {
    throw new Error(`計画バージョン名が不正です: ${name}`);
  }
  const file = path.join(projectDir, 'charters', `${name}.md`);
  if (!fs.existsSync(file)) throw new Error(`計画バージョン「${name}」が見つかりません`);
  return name;
}

function enqueueToInbox(projectDir, spec) {
  const title = String(spec.title || '').trim();
  if (!title) throw new Error('タイトルは必須です');
  const clean = { title };
  const charter = validateCharterVersion(projectDir, spec.charter);
  if (charter) clean.charter = charter;
  // task.schema.json の人が書けるフィールド（system 管理の routed_by/cohort* は通さない）。
  // workspace/refs/paths はルーティング（書込先・モノレポの担当領域）の明示指定、
  // review/expect/followup は検収・偽 done 対策・派生タスクの契約。
  for (const key of ['id', 'verify', 'accept', 'verify_template', 'note', 'after', 'level', 'track',
    'why', 'desc', 'scope', 'out_of_scope', 'constraints', 'hints', 'demo',
    'workspace', 'refs', 'paths', 'review', 'expect', 'followup']) {
    const v = spec[key];
    if (v === undefined || v === null) continue;
    const s = Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean).join(', ') : String(v).trim();
    if (s !== '') clean[key] = s;
  }
  const pr = parseInt(spec.priority, 10);
  if (!isNaN(pr) && pr !== 0) clean.priority = pr;

  const inbox = path.join(projectDir, 'inbox');
  fs.mkdirSync(inbox, { recursive: true });
  const file = path.join(inbox, `viewer-${slugify(title)}-${Date.now()}.json`);
  fs.writeFileSync(file, JSON.stringify(clean, null, 2), 'utf8');
  return { file, spec: clean };
}

// ---------------------------------------------------------------------------
// 監視担当の割り当て（assignments.json）— viewer 管理のチーム運用メタデータ。
// タスク状態ファイル（backlog/*.md）には触れない: agent-project の契約の外にある
// サイドカーファイルへの書き込みなので、done の不変条件・状態遷移に影響しない。
// プロジェクトルート直下に置くため state_git 同期でチームへ共有される。
// 書きかけを同期に載せないよう .tmp に書いてから rename する。
// ---------------------------------------------------------------------------

function setTaskOwner(projectDir, id, owner) {
  const tid = String(id || '').trim();
  if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${id}`);
  const name = String(owner || '').trim().slice(0, 60);
  const file = path.join(projectDir, project.ASSIGNMENTS_FILE);
  const cur = project.readAssignments(projectDir);
  if (name) {
    cur.tasks[tid] = name;
    if (!cur.members.includes(name)) cur.members.push(name);
  } else {
    delete cur.tasks[tid];
  }
  fs.mkdirSync(projectDir, { recursive: true });
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(cur, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: tid, owner: name };
}

// ---------------------------------------------------------------------------
// レビューコメント（reviews/<task-id>/*.json）— 成果物レビューを複数メンバーで行う。
// 1 コメント = 1 ファイル（同時投稿がファイル名衝突せず state_git でマージされる）。
// viewer 管理のサイドカーなのでタスク状態・done の不変条件には触れない。
// 書きかけを watch/同期に載せないよう .tmp に書いてから rename する。
// ---------------------------------------------------------------------------

function _reviewDir(projectDir, taskId) {
  const tid = String(taskId || '').trim();
  if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${taskId}`);
  return path.join(projectDir, project.REVIEWS_DIR, tid);
}

function _commentFile(dir, commentId) {
  const cid = String(commentId || '').trim();
  if (!cid || cid !== path.basename(cid) || /[\\/]/.test(cid)) {
    throw new Error(`不正なコメント ID です: ${commentId}`);
  }
  return path.join(dir, `${cid}.json`);
}

function addReviewComment(projectDir, taskId, { author, text }) {
  const body = String(text || '').trim();
  if (!body) throw new Error('コメント本文が空です');
  const dir = _reviewDir(projectDir, taskId);
  fs.mkdirSync(dir, { recursive: true });
  const ts = new Date().toISOString();
  // ファイル名に ts を含めて時系列に並べやすくする（同ミリ秒の衝突は slug で分ける）。
  const cid = `${ts.replace(/[:.]/g, '-')}-${slugify(author || 'anon')}`;
  const rec = { author: String(author || '').trim().slice(0, 60) || '匿名', text: body, ts };
  const file = path.join(dir, `${cid}.json`);
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(rec, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: cid, comment: { id: cid, ...rec } };
}

// 監視担当者がコメントを整理（編集）する。author/ts は保持し editedTs を足す。
function editReviewComment(projectDir, taskId, commentId, text) {
  const body = String(text || '').trim();
  if (!body) throw new Error('コメント本文が空です');
  const dir = _reviewDir(projectDir, taskId);
  const file = _commentFile(dir, commentId);
  const cur = project.readReviewComments(projectDir, taskId).find((c) => c.id === String(commentId));
  if (!cur) throw new Error(`コメントが見つかりません: ${commentId}`);
  const rec = { author: cur.author, text: body, ts: cur.ts, editedTs: new Date().toISOString() };
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(rec, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: String(commentId), comment: { id: String(commentId), ...rec } };
}

async function deleteReviewComment(projectDir, taskId, commentId, trash) {
  const dir = _reviewDir(projectDir, taskId);
  const file = _commentFile(dir, commentId);
  if (!fs.existsSync(file)) throw new Error(`コメントが見つかりません: ${commentId}`);
  const via = trash ? await trash(file) : (fs.rmSync(file, { force: true }), 'delete');
  return { file, id: String(commentId), via };
}

// ---------------------------------------------------------------------------
// 3. 人の指示（approve / hold / pin / defer / revise）
// ---------------------------------------------------------------------------

const COMMAND_ACTIONS = new Set(['approve', 'hold', 'pin', 'defer', 'revise', 'reject', 'resume-run']);
// プロジェクト単位（id 不要）のライフサイクル指示。リモートの本体を git 越しに操作する口。
const LIFECYCLE_ACTIONS = new Set(['pause', 'resume', 'stop']);

// revise が受けるフィールド編集キー（agent-project の REVISE_FIELDS と同じ）。
// 値は「置換」規約: '' / '-' / 'none' はフィールド削除、未指定（undefined/null）は触らない。
// why 以降は誘導・レビュー記述（backlog.md.example 参照）。編集 UI は未対応だが、
// ペイロード契約は本体と揃えておく（AI 補助・将来の UI がそのまま通せる）。
const REVISE_KEYS = ['title', 'priority', 'verify', 'accept', 'after', 'note', 'level', 'track',
  'node', 'why', 'desc', 'scope', 'out_of_scope', 'constraints', 'hints', 'demo'];

// revise ペイロード（フィールド編集 + feedback）を commands/CLI 両経路の形へ正規化する。
// undefined/null は「触らない」の意味なので落とす（'' は削除の明示指定として残す）
function revisePayload({ fields, feedback }) {
  const out = {};
  for (const key of REVISE_KEYS) {
    const v = fields && fields[key];
    if (v !== undefined && v !== null) out[key] = String(v);
  }
  const fb = String(feedback || '').trim();
  if (fb) out.feedback = fb;
  return out;
}

// commands/<name>.json のドロップ（agent-project の ingest_commands が拾う）。
// 書きかけを watch に読ませないよう .tmp に書いてから rename する。
// replan / pause / resume / stop はプロジェクト単位（id 不要）なので id を載せない。
function dropCommand(projectDir, { action, id, reason, fields, feedback, run, charter, complete }) {
  const dir = path.join(projectDir, 'commands');
  fs.mkdirSync(dir, { recursive: true });
  const projectScoped = action === 'replan' || LIFECYCLE_ACTIONS.has(action);
  const rec = {
    command: action,
    ...(projectScoped ? {} : { id: String(id) }),
    reason: String(reason || ''),
    actor: 'agent-dashboard',
    ts: new Date().toISOString(),
    ...(action === 'revise' ? revisePayload({ fields, feedback }) : {}),
    ...(action === 'resume-run' && run ? { run: String(run) } : {}),
    ...(action === 'replan' && charter ? { charter: String(charter) } : {}),
    // 承認 = 完了か積み直しかは呼び出し側が明示する（本体は文面から推定しない）
    ...(action === 'approve' && complete ? { complete: true } : {}),
  };
  const slug = projectScoped ? 'project' : slugify(id);
  const file = path.join(dir, `viewer-${action}-${slug}-${Date.now()}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, rec };
}

// プロジェクトルートから --root を導く（1 プロジェクト = 1 ディレクトリ）
function cliScope(projectDir) {
  return { root: path.resolve(projectDir) };
}

function quote(arg) {
  const s = String(arg);
  if (/^[\w@%+=:,./-]+$/.test(s)) return s;
  return process.platform === 'win32' ? `"${s.replace(/"/g, '""')}"` : `'${s.replace(/'/g, "'\\''")}'`;
}

function findProjectConfig(...dirs) {
  // agent-project の _find_config と同じ名前を、本体／状態 worktree の候補から探す。
  // cwd 依存を避け、dashboard CLI 委譲が設定を拾えるようにする。
  // `dir`（状態ルート）と `fromStateWorktree(dir)`（本体）の両方を見る——yaml が
  // 状態 worktree 側だけにある構成でも --config を落とさない。
  const bases = [];
  const add = (d) => {
    if (!d) return;
    const resolved = path.resolve(d);
    for (const base of [
      resolved,
      ...agentDirCandidates(resolved),
      path.dirname(resolved),
      ...agentDirCandidates(path.dirname(resolved)),
    ]) {
      if (!bases.includes(base)) bases.push(base);
    }
  };
  for (const d of dirs) add(d);
  for (const base of bases) {
    for (const name of ['agent-project.yaml', 'agent-project.yml']) {
      const p = path.join(base, name);
      if (fs.existsSync(p)) return p;
    }
  }
  return null;
}

// ⚙ 設定の CLI コマンド（例 `python3 /path/to/agent-project.py`）を argv 配列へ分解する。
// クォート（"…" / '…'）で空白入りパスも表せる。
function splitCommand(command) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(String(command || '').trim()))) {
    out.push(m[1] != null ? m[1] : m[2] != null ? m[2] : m[3]);
  }
  return out;
}

// タイムアウト時にプロセスツリーごと止める。Windows の child.kill() はトップ（多くは
// シェル）しか殺さず、agent-project 本体や WSL 側の子が生き残って再実行と多重化する。
function killTree(child) {
  if (!child || child.pid == null) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else {
      try {
        process.kill(-child.pid, 'SIGTERM'); // detached 起動によるプロセスグループごと
      } catch {
        child.kill();
      }
    }
  } catch {
    try {
      child.kill();
    } catch {
      /* 既に終了 */
    }
  }
}

function runProjectCli(command, args, timeoutMs = 60000, cwd) {
  // shell:true + 文字列連結は、空白入りコマンドパスで壊れ、cmd.exe の %VAR% 展開で
  // --reason / feedback の日本語文が変質し、メタ文字がインジェクションになる。
  // argv 配列 + shell:false で渡す（PATHEXT で .exe/.cmd は解決される）。
  const tokens = splitCommand(command);
  const file = tokens[0] || 'agent-project';
  const argv = [...tokens.slice(1), ...args.map(String)];
  const cmdline = `${command} ${args.map(quote).join(' ')}`; // 表示・手動再実行用
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(file, argv, {
        shell: false,
        windowsHide: true,
        cwd: cwd || undefined,
        detached: process.platform !== 'win32', // POSIX: グループ kill を可能にする
      });
    } catch (e) {
      reject(new Error(`agent-project を起動できません（⚙ 設定の CLI コマンドを確認）: ${e.message}`));
      return;
    }
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      killTree(child);
      reject(new Error(`agent-project がタイムアウトしました: ${cmdline}`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.stdin.on('error', () => {});
    child.stdin.end();
    child.on('error', (e) => {
      clearTimeout(timer);
      reject(new Error(`agent-project を起動できません（⚙ 設定の CLI コマンドを確認）: ${e.message}`));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ output: out.trim(), command: cmdline });
      else reject(new Error(`agent-project が失敗しました (exit ${code}): ${(err || out).trim().slice(-400)}`));
    });
  });
}

// CLI 実行（approve / hold / reprioritize / revise / resume-run）。本体が稼働していないときの経路
async function runActionViaCli(cfg, { dir, action, id, reason, fields, feedback, run, complete }) {
  const command = (cfg.projects && cfg.projects.command) || 'agent-project';
  // ファイル操作は状態ルート（dir）。CLI --root は本体側（二重リダイレクト防止）。
  const root = project.fromStateWorktree(path.resolve(dir));
  const base = ['--root', root];
  const cfgPath = findProjectConfig(root, dir);
  if (cfgPath) base.push('--config', cfgPath);
  let args;
  if (action === 'approve') {
    args = ['approve', id, '--reason', reason, ...(complete ? ['--complete'] : []), ...base];
  }
  else if (action === 'reject') args = ['reject', id, '--reason', reason, ...base];
  else if (action === 'hold') args = ['hold', id, '--reason', reason, ...base];
  else if (action === 'pin') args = ['reprioritize', id, '--pin', '--reason', reason, ...base];
  else if (action === 'resume-run') args = ['resume-run', id, '--run', String(run), '--reason', reason, ...base];
  else if (action === 'revise') {
    const payload = revisePayload({ fields, feedback });
    args = ['revise', id, '--reason', reason];
    for (const [key, value] of Object.entries(payload)) args.push(`--${key}`, value);
    args.push(...base);
  } else args = ['reprioritize', id, '--defer', '--reason', reason, ...base];
  const cwd = cfgPath ? path.dirname(cfgPath) : root;
  return runProjectCli(command, args, 60000, cwd);
}

// action: approve | hold | pin | defer | revise
//   revise は fields（title/priority/verify/accept/after/note/level/track の置換）と
//   feedback（次の act に必ず届く指示）を追加で受ける。実行中（doing）のタスクは
//   本体側が現在の試行を確定せず修正内容で積み直す（早い軌道修正）。
// 経路は project.actionMode で制御する:
//   auto（既定）… 本体が稼働中（instances の heartbeat）なら commands/ ドロップ、
//                 稼働していなければ CLI、CLI も使えなければドロップにフォールバック
//   file        … 常に commands/ ドロップ（WSL 内の本体・CLI 無し環境向け）
//   cli         … 常に CLI（従来の挙動）
async function runAction(cfg, { dir, action, id, reason, fields, feedback, run, complete }) {
  if (!COMMAND_ACTIONS.has(action)) throw new Error(`不明なアクション: ${action}`);
  const why = String(reason || '').trim() || 'agent-dashboard から操作';
  const mode = (cfg.projects && cfg.projects.actionMode) || 'auto';
  if (action === 'revise' && Object.keys(revisePayload({ fields, feedback })).length === 0) {
    throw new Error('revise には変更フィールドかフィードバックの指定が必要です');
  }
  if (action === 'resume-run' && !String(run || '').trim()) {
    throw new Error('resume-run には再開する run-id の指定が必要です');
  }

  // Windows ビュアーが WSL UNC パスを開いているときは、Windows 側の agent-project CLI は
  // WSL 内の本体と別世界なので、auto でもファイルドロップを優先する。
  const wslUnc = process.platform === 'win32' && /^\\\\wsl(?:\$|\.localhost)\\/i.test(String(dir || ''));
  if (mode === 'file' || wslUnc || (mode !== 'cli' && project.isProjectRunning(dir))) {
    const { file } = dropCommand(dir, { action, id, reason: why, fields, feedback, run });
    return {
      output: `${action} ${id}: 指示ファイルを投入しました（稼働中の agent-project が取り込みます）`,
      file,
      via: 'file',
    };
  }
  try {
    const res = await runActionViaCli(cfg, { dir, action, id, reason: why, fields, feedback, run });
    return { ...res, via: 'cli' };
  } catch (err) {
    if (mode === 'cli') throw err;
    // CLI が無い/失敗 → ファイルドロップに退避（次回の agent-project 起動時に取り込まれる）
    const { file } = dropCommand(dir, { action, id, reason: why, fields, feedback, run });
    return {
      output:
        `${action} ${id}: CLI を実行できないため指示ファイルを置きました` +
        `（次回の agent-project 起動時に取り込まれます）`,
      file,
      via: 'file-fallback',
      cliError: err.message,
    };
  }
}

// charter からのバックログ再分解を要求する（エラー回復用の一発の口。プロジェクト単位＝id 無し）。
// 本体は次パスで charter を分解し直す。冪等照合は「done 以外」（処理中＋却下済み）と行う＝
// 処理中タスクの二重投入や却下済みの復活はせず、done と類似のタスクだけやり直しとして再作成される。
// 経路は runAction と同じ auto/file/cli 契約。file は commands/replan ドロップ、cli は
// `agent-project replan --reason ...`。稼働中はドロップ・停止中は CLI・CLI 不可はドロップ退避。
async function requestReplan(cfg, { dir, reason, charter }) {
  const why = String(reason || '').trim() || 'agent-dashboard から再分解を要求';
  const charterName = validateCharterVersion(dir, charter);
  const mode = (cfg.projects && cfg.projects.actionMode) || 'auto';

  const wslUnc = process.platform === 'win32' && /^\\\\wsl(?:\$|\.localhost)\\/i.test(String(dir || ''));
  if (mode === 'file' || wslUnc || (mode !== 'cli' && project.isProjectRunning(dir))) {
    const { file } = dropCommand(dir, { action: 'replan', reason: why, charter: charterName });
    return {
      output: 'charter からの再分解を要求しました（稼働中の agent-project が次パスで取り込みます）',
      file,
      via: 'file',
    };
  }
  try {
    const command = (cfg.projects && cfg.projects.command) || 'agent-project';
    const root = project.fromStateWorktree(path.resolve(dir));
    const args = ['replan', '--reason', why, '--root', root];
    if (charterName) args.push('--charter', charterName);
    const cfgPath = findProjectConfig(root, dir);
    if (cfgPath) args.push('--config', cfgPath);
    const cwd = cfgPath ? path.dirname(cfgPath) : root;
    const res = await runProjectCli(command, args, 60000, cwd);
    return { ...res, via: 'cli' };
  } catch (err) {
    if (mode === 'cli') throw err;
    const { file } = dropCommand(dir, { action: 'replan', reason: why, charter: charterName });
    return {
      output:
        'CLI を実行できないため再分解の要求ファイルを置きました' +
        '（次回の agent-project 起動時に取り込まれます）',
      file,
      via: 'file-fallback',
      cliError: err.message,
    };
  }
}

// プロジェクト単位のライフサイクル操作（pause / resume / stop）。
// 常に commands/ ドロップ（＋git push）で届ける — リモート本体（WSL・別ホスト）の watch が
// 同期間隔内に取り込む契約（agent-project の ingest_commands）。CLI は使わない
// （stop の CLI は同一ホスト限定で、この口の主用途はリモート操作のため）。
const LIFECYCLE_LABELS = { pause: '一時停止', resume: '再開', stop: '停止' };

function requestLifecycle(cfg, { dir, action, reason }) {
  if (!LIFECYCLE_ACTIONS.has(action)) throw new Error(`不明なライフサイクル操作: ${action}`);
  const why = String(reason || '').trim() || 'agent-dashboard から操作';
  const { file } = dropCommand(dir, { action, reason: why });
  return {
    output:
      `${LIFECYCLE_LABELS[action]}を要求しました` +
      '（稼働中の agent-project が同期間隔内に取り込みます）',
    file,
    via: 'file',
  };
}

// 本体（agent-project）の起動。stop/pause と違い、停止中の本体は commands/ を読めないため
// ファイルドロップでは届かない — この PC の CLI で `agent-project start --root <dir>` を実行する
// （start は常駐を detach して即座に戻る）。本体が別マシンの構成では、この PC で起動すると
// 「この PC が実行役」になる（クレームにより同一タスクの二重実行は起きないが、エージェント
// CLI の有無等は環境依存）。その判断は呼び出し側（renderer の確認ダイアログ）が人に委ねる。
async function startProject(cfg, { dir }) {
  const command = (cfg.projects && cfg.projects.command) || 'agent-project';
  const root = project.fromStateWorktree(path.resolve(dir));
  // runAction / requestReplan と同じガード: Windows ビュアーが WSL UNC を開いているとき、
  // Windows 側 CLI で start すると UNC/Linux パスの --root で失敗するか、最悪 WSL 内の
  // 本体とは別に Windows 側で二重起動する。停止中の本体はファイルドロップでは起こせない
  // ため、人が WSL 内で打つべきコマンドを返して手動起動に委ねる。
  const unc = process.platform === 'win32' &&
    String(root || '').replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (unc) {
    const linuxRoot = (unc[1] || '/').replace(/\\/g, '/') || '/';
    const err = new Error(
      'WSL 内のプロジェクトは Windows 側の CLI からは起動できません。WSL のターミナルで起動してください。'
    );
    err.manualCommand = `${command} start --root ${linuxRoot}`;
    throw err;
  }
  const cfgPath = findProjectConfig(root, dir);
  const args = ['start', '--root', root];
  if (cfgPath) args.push('--config', cfgPath);
  const cwd = cfgPath ? path.dirname(cfgPath) : root;
  try {
    const res = await runProjectCli(command, args, 120000, cwd);
    return { ...res, via: 'cli' };
  } catch (err) {
    // CLI が無い/失敗 → 人が本体マシンで打つべきコマンドをそのまま返す（コピーして実行できる）
    err.manualCommand = `${command} ${args.map(quote).join(' ')}`;
    throw err;
  }
}

module.exports = {
  submitFeedback,
  buildNeedsStub,
  enqueueToInbox,
  setTaskOwner,
  addReviewComment,
  editReviewComment,
  deleteReviewComment,
  validateCharterVersion,
  dropCommand,
  runAction,
  requestReplan,
  requestLifecycle,
  startProject,
  findProjectConfig,
  splitCommand,
  runProjectCli,
  DECISION_MARKER,
};
