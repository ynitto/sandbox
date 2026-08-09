'use strict';

// 復旧送信: agent-loop send を WSL で実行する。生の send-keys は使わず CLI に依頼する
// （busy 判定・スロット取得・プロンプト名解決を CLI 側に任せ、dashboard は書き手にならない）。

const exec = require('./exec');

// busy 拒否（ペイン処理中 / 同時実行上限）のメッセージ検知。
// UI はこれを「処理中につき送信待機」に変換する。
function isBusyMessage(text) {
  const s = String(text || '');
  return s.includes('現在処理中です') || s.includes('同時実行数が上限');
}

// repo（ワークスペース）を cwd にして実行する — プロンプト名 → 定期プロンプト本文の
// 解決は agent-loop send が cwd の設定ファイルから行うため。
function sendPrompt({ repo, target, prompt } = {}) {
  const p = String(prompt || '').trim();
  if (!p) return { ok: false, sent: false, busy: false, error: 'プロンプトが空です' };
  const cwd = exec.toWslCwd(repo || '');
  const distro = exec.wslDistro(repo || '');
  const t = String(target || '').trim();
  const parts = [];
  if (cwd) parts.push(`cd ${exec.shellQuote(cwd)} || exit 1;`);
  parts.push('command -v agent-loop >/dev/null || { echo "agent-loop が PATH に見つかりません" >&2; exit 127; };');
  parts.push(`agent-loop send ${t ? `-s ${exec.shellQuote(t)} ` : ''}${exec.shellQuote(p)}`);
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
      : (r.stderr || r.error || 'agent-loop send に失敗しました')),
  };
}

// ---------------------------------------------------------------------------
// メッセージキュー投函（M3）: agent-loop msg で受信ボックス（~/.kiro/agents/<name>/inbox）
// へ積む。投函は CLI 依頼で、生の send-keys もファイル直書きもしない。send と違い
// busy の概念は無い——受信側の InboxWatcher が手すきになった順に処理する（待機は正常）。
// ---------------------------------------------------------------------------

function queueMessage({ repo, agent, subject, body } = {}) {
  const a = String(agent || '').trim();
  const b = String(body || '').trim();
  if (!a) return { ok: false, queued: false, error: '宛先エージェントを選択してください' };
  if (!b) return { ok: false, queued: false, error: '本文が空です' };
  const cwd = exec.toWslCwd(repo || '');
  const distro = exec.wslDistro(repo || '');
  const s = String(subject || '').trim();
  const parts = [];
  if (cwd) parts.push(`cd ${exec.shellQuote(cwd)} || exit 1;`);
  parts.push('command -v agent-loop >/dev/null || { echo "agent-loop が PATH に見つかりません" >&2; exit 127; };');
  // 本文は argv 1 要素で渡す（複数語に割ると CLI 側が空白 join で復元する分の揺れが出る）。
  // CLI 仕様: 本文が実在ファイルのパスと一致するとファイル内容が本文になる。
  parts.push(`agent-loop msg --to ${exec.shellQuote(a)} --from agent-dashboard `
    + `${s ? `--subject ${exec.shellQuote(s)} ` : ''}${exec.shellQuote(b)}`);
  const r = exec.shInWsl(parts.join(' '), 30000, distro);
  const detail = `${r.stdout}\n${r.stderr}`.trim();
  return {
    ok: r.ok,
    queued: r.ok,
    status: r.status,
    detail: detail.slice(0, 2000),
    error: r.ok ? '' : (r.stderr || r.error || 'agent-loop msg に失敗しました'),
  };
}

// 受信ボックスの一覧スクリプト（読み取り専用——契約のキュー dir を読むだけで書かない）。
// tmux.js の readLoopStates と同じく WSL 側の $HOME を経由する（inbox は ~/.kiro 配下）。
// 出力はチャンク区切り \036 + 種別 1 文字: A=エージェント名 / M=メッセージ JSON / P=処理済み件数。
function inboxListScript() {
  return 'agents_dir="$HOME"/.kiro/agents; '
    + 'for d in "$agents_dir"/*/; do [ -d "$d" ] || continue; '
    + 'name=$(basename "$d"); case "$name" in .*) continue;; esac; '
    + 'printf "\\036A %s\\n" "$name"; '
    + 'for f in "$d"inbox/*.json; do [ -f "$f" ] || continue; printf "\\036M "; cat "$f"; done; '
    + 'printf "\\036P %s\\n" "$(ls "$d"inbox/.processed/*.json 2>/dev/null | wc -l)"; '
    + 'done 2>/dev/null || true';
}

function parseInboxOutput(stdout) {
  const agents = [];
  let current = null;
  for (const chunk of String(stdout || '').split('\u001e')) {
    const t = chunk.trim();
    if (!t) continue;
    const kind = t[0];
    const rest = t.slice(1).trim();
    if (kind === 'A') {
      current = { name: rest, pending: [], processed: 0 };
      agents.push(current);
    } else if (kind === 'M' && current) {
      try {
        const m = JSON.parse(rest);
        current.pending.push({
          id: String(m.id || ''),
          from: String(m.from || ''),
          subject: String(m.subject || ''),
          createdAt: Number(m.created_at) || 0,
          body: String(m.body || '').slice(0, 500),
        });
      } catch { /* 壊れた・書きかけのメッセージはスキップ */ }
    } else if (kind === 'P' && current) {
      current.processed = parseInt(rest, 10) || 0;
    }
  }
  for (const a of agents) a.pending.sort((x, y) => x.createdAt - y.createdAt);   // 投函順 = 処理順
  return agents;
}

function listQueue({ repo, agent } = {}) {
  const distro = exec.wslDistro(repo || '');
  const r = exec.shInWsl(inboxListScript(), 15000, distro);
  if (!r.ok && !r.stdout) {
    return { ok: false, agents: [], error: r.stderr || r.error || '受信ボックスを読めませんでした' };
  }
  const agents = parseInboxOutput(r.stdout);
  const wanted = String(agent || '').trim();
  return { ok: true, agents: wanted ? agents.filter((x) => x.name === wanted) : agents, error: '' };
}

module.exports = { sendPrompt, isBusyMessage, queueMessage, listQueue, parseInboxOutput, inboxListScript };
