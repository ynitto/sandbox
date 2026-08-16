'use strict';

// agent-knowledge — quiet 運転の返信下書き承認キュー（moltbook-use の
// moltbook_drafts.py を呼ぶ）。記憶 3 層 + moltbook の集計そのものは agent-audit
// （既存 feature）の呼び出しをそのまま読む——同じ判断の根拠を 2 か所に置かない
// （計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md
// §3.4・C7）。ここが固有に持つのは承認キューだけ。
//
// list/approve/discard は軽い読み取り・ファイル移動だけで agent-audit の collect
// のように長時間化しないので、同期実行（exec.shInWsl）で足りる（非同期版を別途
// 持たない）。
const exec = require('../../routines/main/exec');

function draftsSettings(cfg) {
  const k = (cfg && cfg.agentKnowledge) || {};
  return {
    command: String(k.draftsCommand || '').trim(),
    distro: String(k.distro || '').trim(),
    draftsDir: String(k.draftsDir || '').trim(),
  };
}

function buildScript(settings, subArgs) {
  const args = [];
  if (settings.draftsDir) args.push('--drafts-dir', settings.draftsDir);
  args.push(...subArgs);
  const quoted = args.map((value) => exec.shellQuote(value)).join(' ');
  return `${settings.command} ${quoted}`;
}

function failureMessage(r, fallback) {
  const errText = r.error && r.error.message ? r.error.message : r.error;
  return r.stderr || errText || `${fallback}（exit ${r.status}）`;
}

// list --json の stdout から JSON 配列を取り出す（agent-audit の parseJsonArray と同じ理由）。
function parseJsonArray(stdout) {
  const s = String(stdout || '');
  const start = s.indexOf('[');
  const end = s.lastIndexOf(']');
  if (start < 0 || end <= start) {
    throw new Error('moltbook_drafts の応答に JSON 配列がありません');
  }
  return JSON.parse(s.slice(start, end + 1));
}

// 未設定（moltbook-use 未導入）は空の一覧として返す——エラーにしない。承認キューが
// 出ないだけで、記憶 3 層 + moltbook の集計（agent-audit 側）は独立に動く。
function listDrafts(cfg, runShell = exec.shInWsl) {
  const settings = draftsSettings(cfg);
  if (!settings.command) return { configured: false, drafts: [] };
  const r = runShell(buildScript(settings, ['list', '--json']), 30000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, '下書きの一覧を取得できませんでした'));
  return { configured: true, drafts: parseJsonArray(r.stdout) };
}

function approveDraft(cfg, file, runShell = exec.shInWsl) {
  const settings = draftsSettings(cfg);
  if (!settings.command) throw new Error('moltbook-use が設定されていません（全体設定 → 知識）');
  const r = runShell(buildScript(settings, ['approve', '--file', String(file || '')]),
    30000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, '承認できませんでした'));
  return { ok: true };
}

function discardDraft(cfg, file, reason, runShell = exec.shInWsl) {
  const settings = draftsSettings(cfg);
  if (!settings.command) throw new Error('moltbook-use が設定されていません（全体設定 → 知識）');
  const sub = ['discard', '--file', String(file || '')];
  if (reason) sub.push('--reason', String(reason));
  const r = runShell(buildScript(settings, sub), 30000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, '却下できませんでした'));
  return { ok: true };
}

module.exports = { draftsSettings, buildScript, parseJsonArray, listDrafts, approveDraft, discardDraft };
