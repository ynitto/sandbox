'use strict';

// 人のアクション層。kiro-projects の公式な入力契約だけを使う:
//   1. needs/<id>.md の「## Decision Outcome」への記入 + `- [x]`
//      → ingest_feedback が取り込む（フィードバック往復の正規ルート）
//   2. inbox/<name>.json のドロップ（E4 push 型の取り込み口）
//      → ingest_inbox が backlog 化する（タスク投入の正規ルート）
//   3. commands/<name>.json のドロップ（approve / hold / pin / defer / revise）
//      → ingest_commands が CLI と同一ロジック・同一の決定記録（DR）で実行する。
//      revise は人の即時フィードバック: タスクの内容・依存（after）・優先度の修正と
//      feedback（次の act に必ず届く指示）を、ループがブロックする前に能動的に届ける。
//      ファイルだけで届くため、本体が WSL 内で稼働していても操作できる。
//      本体が稼働していないときは kiro-projects CLI に委譲し、CLI も使えなければ
//      指示ファイルを置いて次回起動時の取り込みに委ねる（ロジックの二重実装はしない）。
// done の確定・状態遷移そのものをこのアプリが直接書き換えることはしない
// （「done は verify のみが根拠」の不変条件を壊さない）。

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const kiro = require('./kiro');

const DECISION_MARKER = '## Decision Outcome';

// ---------------------------------------------------------------------------
// 1. needs へのフィードバック（Decision Outcome 記入 + [x] 確定）
// ---------------------------------------------------------------------------

function submitFeedback(needsFile, feedback) {
  if (!fs.existsSync(needsFile)) {
    throw new Error(`needs ファイルがありません（取り込み済みの可能性）: ${needsFile}`);
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
  if (/^\s*-\s*\[x\]/im.test(text)) {
    // すでに確定済み
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

function enqueueToInbox(projectDir, spec) {
  const title = String(spec.title || '').trim();
  if (!title) throw new Error('タイトルは必須です');
  const clean = { title };
  for (const key of ['id', 'verify', 'accept', 'verify_template', 'note', 'after', 'level', 'track']) {
    const v = spec[key];
    if (v !== undefined && v !== null && String(v).trim() !== '') clean[key] = String(v).trim();
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
// 3. 人の指示（approve / hold / pin / defer / revise）
// ---------------------------------------------------------------------------

const COMMAND_ACTIONS = new Set(['approve', 'hold', 'pin', 'defer', 'revise']);

// revise が受けるフィールド編集キー（kiro-projects の REVISE_FIELDS と同じ）。
// 値は「置換」規約: '' / '-' / 'none' はフィールド削除、未指定（undefined/null）は触らない。
const REVISE_KEYS = ['title', 'priority', 'verify', 'accept', 'after', 'note', 'level', 'track'];

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

// commands/<name>.json のドロップ（kiro-projects の ingest_commands が拾う）。
// 書きかけを watch に読ませないよう .tmp に書いてから rename する。
function dropCommand(projectDir, { action, id, reason, fields, feedback }) {
  const dir = path.join(projectDir, 'commands');
  fs.mkdirSync(dir, { recursive: true });
  const rec = {
    command: action,
    id: String(id),
    reason: String(reason || ''),
    actor: 'kiro-projects-viewer',
    ts: new Date().toISOString(),
    ...(action === 'revise' ? revisePayload({ fields, feedback }) : {}),
  };
  const file = path.join(dir, `viewer-${action}-${slugify(id)}-${Date.now()}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, rec };
}

// プロジェクトディレクトリ <root>/projects/<name> から --root / --project を導く
function cliScope(projectDir) {
  const dir = path.resolve(projectDir);
  const parent = path.dirname(dir);
  if (path.basename(parent) !== 'projects') {
    throw new Error(
      '旧フラット構成のプロジェクトでは CLI 操作を組み立てられません。' +
        'kiro-projects CLI を直接実行してください'
    );
  }
  return { root: path.dirname(parent), project: path.basename(dir) };
}

function quote(arg) {
  const s = String(arg);
  if (/^[\w@%+=:,./-]+$/.test(s)) return s;
  return process.platform === 'win32' ? `"${s.replace(/"/g, '""')}"` : `'${s.replace(/'/g, "'\\''")}'`;
}

function runKiroCli(command, args, timeoutMs = 60000) {
  const cmdline = `${command} ${args.map(quote).join(' ')}`;
  return new Promise((resolve, reject) => {
    const child = spawn(cmdline, { shell: true });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`kiro-projects がタイムアウトしました: ${cmdline}`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(timer);
      reject(new Error(`kiro-projects を起動できません（⚙ 設定の CLI コマンドを確認）: ${e.message}`));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ output: out.trim(), command: cmdline });
      else reject(new Error(`kiro-projects が失敗しました (exit ${code}): ${(err || out).trim().slice(-400)}`));
    });
  });
}

// CLI 実行（approve / hold / reprioritize / revise）。本体が稼働していないときの経路
async function runActionViaCli(cfg, { dir, action, id, reason, fields, feedback }) {
  const command = (cfg.kiro && cfg.kiro.command) || 'kiro-projects';
  const { root, project } = cliScope(dir);
  const base = ['--root', root, '--project', project];
  let args;
  if (action === 'approve') args = ['approve', id, '--reason', reason, ...base];
  else if (action === 'hold') args = ['hold', id, '--reason', reason, ...base];
  else if (action === 'pin') args = ['reprioritize', id, '--pin', '--reason', reason, ...base];
  else if (action === 'revise') {
    const payload = revisePayload({ fields, feedback });
    args = ['revise', id, '--reason', reason];
    for (const [key, value] of Object.entries(payload)) args.push(`--${key}`, value);
    args.push(...base);
  } else args = ['reprioritize', id, '--defer', '--reason', reason, ...base];
  return runKiroCli(command, args);
}

// action: approve | hold | pin | defer | revise
//   revise は fields（title/priority/verify/accept/after/note/level/track の置換）と
//   feedback（次の act に必ず届く指示）を追加で受ける。実行中（doing）のタスクは
//   本体側が現在の試行を確定せず修正内容で積み直す（早い軌道修正）。
// 経路は kiro.actionMode で制御する:
//   auto（既定）… 本体が稼働中（instances の heartbeat）なら commands/ ドロップ、
//                 稼働していなければ CLI、CLI も使えなければドロップにフォールバック
//   file        … 常に commands/ ドロップ（WSL 内の本体・CLI 無し環境向け）
//   cli         … 常に CLI（従来の挙動）
async function runAction(cfg, { dir, action, id, reason, fields, feedback }) {
  if (!COMMAND_ACTIONS.has(action)) throw new Error(`不明なアクション: ${action}`);
  const why = String(reason || '').trim() || 'kiro-projects-viewer から操作';
  const mode = (cfg.kiro && cfg.kiro.actionMode) || 'auto';
  if (action === 'revise' && Object.keys(revisePayload({ fields, feedback })).length === 0) {
    throw new Error('revise には変更フィールドかフィードバックの指定が必要です');
  }

  if (mode === 'file' || (mode !== 'cli' && kiro.isProjectRunning(dir))) {
    const { file } = dropCommand(dir, { action, id, reason: why, fields, feedback });
    return {
      output: `${action} ${id}: 指示ファイルを投入しました（稼働中の kiro-projects が取り込みます）`,
      file,
      via: 'file',
    };
  }
  try {
    const res = await runActionViaCli(cfg, { dir, action, id, reason: why, fields, feedback });
    return { ...res, via: 'cli' };
  } catch (err) {
    if (mode === 'cli') throw err;
    // CLI が無い/失敗 → ファイルドロップに退避（次回の kiro-projects 起動時に取り込まれる）
    const { file } = dropCommand(dir, { action, id, reason: why, fields, feedback });
    return {
      output:
        `${action} ${id}: CLI を実行できないため指示ファイルを置きました` +
        `（次回の kiro-projects 起動時に取り込まれます）`,
      file,
      via: 'file-fallback',
      cliError: err.message,
    };
  }
}

module.exports = { submitFeedback, enqueueToInbox, dropCommand, runAction, DECISION_MARKER };
