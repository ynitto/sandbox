'use strict';

// 人のアクション層。kiro-projects の公式な入力契約だけを使う:
//   1. needs/<id>.md の「## Decision Outcome」への記入 + `- [x]`
//      → ingest_feedback が取り込む（フィードバック往復の正規ルート）
//   2. inbox/<name>.json のドロップ（E4 push 型の取り込み口）
//      → ingest_inbox が backlog 化する（タスク投入の正規ルート）
//   3. kiro-projects CLI（approve / hold / reprioritize）
//      → 決定記録（DR）を残す人の操作はロジックを二重実装せず CLI に委譲する
// done の確定・状態遷移そのものをこのアプリが直接書き換えることはしない
// （「done は verify のみが根拠」の不変条件を壊さない）。

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

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
// 3. kiro-projects CLI（approve / hold / reprioritize）
// ---------------------------------------------------------------------------

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

// action: approve | hold | pin | defer
async function runAction(cfg, { dir, action, id, reason }) {
  const command = (cfg.kiro && cfg.kiro.command) || 'kiro-projects';
  const { root, project } = cliScope(dir);
  const why = String(reason || '').trim() || 'kiro-projects-viewer から操作';
  const base = ['--root', root, '--project', project];
  let args;
  if (action === 'approve') args = ['approve', id, '--reason', why, ...base];
  else if (action === 'hold') args = ['hold', id, '--reason', why, ...base];
  else if (action === 'pin') args = ['reprioritize', id, '--pin', '--reason', why, ...base];
  else if (action === 'defer') args = ['reprioritize', id, '--defer', '--reason', why, ...base];
  else throw new Error(`不明なアクション: ${action}`);
  return runKiroCli(command, args);
}

module.exports = { submitFeedback, enqueueToInbox, runAction, DECISION_MARKER };
