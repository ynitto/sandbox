'use strict';

// 復旧送信: kiro-loop send を WSL で実行する。生の send-keys は使わず CLI に依頼する
// （busy 判定・スロット取得・プロンプト名解決を CLI 側に任せ、dashboard は書き手にならない）。

const exec = require('./exec');

// busy 拒否（ペイン処理中 / 同時実行上限）のメッセージ検知。
// UI はこれを「処理中につき送信待機」に変換する。
function isBusyMessage(text) {
  const s = String(text || '');
  return s.includes('現在処理中です') || s.includes('同時実行数が上限');
}

// repo（ワークスペース）を cwd にして実行する — プロンプト名 → 定期プロンプト本文の
// 解決は kiro-loop send が cwd の設定ファイルから行うため。
function sendPrompt({ repo, target, prompt } = {}) {
  const p = String(prompt || '').trim();
  if (!p) return { ok: false, sent: false, busy: false, error: 'プロンプトが空です' };
  const cwd = exec.toWslCwd(repo || '');
  const distro = exec.wslDistro(repo || '');
  const t = String(target || '').trim();
  const parts = [];
  if (cwd) parts.push(`cd ${exec.shellQuote(cwd)} || exit 1;`);
  parts.push('bin=$(command -v kiro-loop || command -v agent-loop) || { echo "kiro-loop が PATH に見つかりません" >&2; exit 127; };');
  parts.push(`"$bin" send ${t ? `-s ${exec.shellQuote(t)} ` : ''}${exec.shellQuote(p)}`);
  const r = exec.shInWsl(parts.join(' '), 30000, distro);
  const sent = r.ok;
  const detail = `${r.stdout}\n${r.stderr}`.trim();
  const busy = !sent && isBusyMessage(detail);
  return {
    ok: sent,
    sent,
    busy,
    status: r.status,
    detail: detail.slice(0, 2000),
    error: sent ? '' : (busy
      ? 'ペインが処理中のため送信できませんでした'
      : (r.stderr || r.error || 'kiro-loop send に失敗しました')),
  };
}

module.exports = { sendPrompt, isBusyMessage };
