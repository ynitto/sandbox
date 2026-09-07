'use strict';

// `herd` — ローカル実行系（agent-herd の一族）を 1 語で指す**仮想エージェント**。
//
// agent-dashboard の実行レベルでは、ローカルを使う段に `herd` と書けば、用途ごとの実測
// （qualifications）が aider / ollama とモデルを決める（herd-family.js）。agent-app には
// その用途の軸も実測の台帳も無い。それでも `herd` を選べるようにするのは、設定する人が
// aider と ollama を選び分けなくてよいようにするためである。
//
// **一族は定義から機械的に導く。** `agents/<name>.json` の `command[0]`（対話起動なら
// `interactive.command[0]`）が `agent-herd` の定義が一族である。`agents/herd.json` は
// 作らない（dashboard・agentcore と同じ規則。作ると一族の判定と衝突する）。
//
// **agent-app は aider と ollama を選ばない。入口は agent-herd の 1 つで、用途は
// agent-herd 自身の作法で伝える。**
//
//   会話 … 一族の共通 TUI（agent-herd の既定バックエンド = ollama の定義）を **1 本**開き、
//          用途はスラッシュの実行形（agentcore/slashroute の種別 B）で本文の先頭に書く:
//            Ask（読み取り専用）                       → `/find`（読み取り専用の道具で調べる）
//            実行・作業フォルダの中のファイルを添付   → `/edit`（編集ハーネスへ回す。どの
//                                                       エージェントで直すかは宣言側が決める）
//            実行・添付なし                            → そのまま（ツールループ）
//          ターンごとに CLI を入れ替えないので、tmux セッションと文脈はそのまま続く。
//   タスク … `agent-herd harness statemachine` の `--agent-cli` を**渡さない**（agent-herd の
//          既定と宣言に任せる）。AI 支援（`agent-herd --purpose plan`）も `--agent` を渡さない。
//   ワークフロー … agent-flow は `--agent-cli` を要求し、省くとホスト設定（kiro 等）へ落ちる
//          ので、harness の既定と同じ aider を渡す（用途別の振り替えは定義の variants）。
//
// 解決結果（どれになったか）はメッセージに `family: 'herd'` と実際の `cli` で残す。

const HERD = 'herd';
const ENTRYPOINT = 'agent-herd';
// agent-herd のトップレベル入口（引数なし＝共通 TUI）の既定バックエンド（herdcli.DEFAULT_CHAT_CLI）。
const CHAT_BACKEND = 'ollama';
// `agent-herd harness` の `--agent-cli` 既定。agent-flow へ渡す名前もこれに揃える。
const HARNESS_DEFAULT = 'aider';

// 会話の用途 → 本文の先頭に置くスラッシュ行（'' はそのまま）。
const SLASH = { ask: '/find', edit: '/edit', work: '' };
const REASON = {
  ask: '読み取り専用の依頼は、共通 TUI に /find（読み取り専用の道具）で送る',
  edit: '作業フォルダのファイルを添えた依頼は、共通 TUI に /edit（編集ハーネス）で送る',
  work: 'ファイルを添えない作業依頼は、共通 TUI のツールループにそのまま送る',
};

function isHerd(name) {
  return String(name || '').trim().toLowerCase() === HERD;
}

// 一覧の 1 行（agentCli.list の形: { name, command, available, interactive, … }）が一族か。
function isMember(entry) {
  return !!entry && String(entry.command || '').trim() === ENTRYPOINT && !entry.virtual;
}

function members(entries) {
  return (Array.isArray(entries) ? entries : []).filter(isMember);
}

// 会話の起動条件から用途を決める。
//   readonly  … Ask
//   workFiles … 作業フォルダの中のファイル（{ rel }）を添えているか
function purposeOf({ readonly = false, workFiles = false } = {}) {
  if (readonly) return 'ask';
  return workFiles ? 'edit' : 'work';
}

// 添付の並びに作業フォルダの中のファイルがあるか（userData へ写した添付 { id } は数えない——
// それは参考資料で、編集ハーネスが直す対象ではない）。
function hasWorkFiles(attachments) {
  return (Array.isArray(attachments) ? attachments : []).some((a) => a && typeof a === 'object' && a.rel);
}

// 会話: 共通 TUI を開く定義（agent-herd の既定バックエンド。無ければ一族の他の定義——
// どれも同じ共通 TUI を持つ）と、用途を表すスラッシュ行。
function resolveChat(purpose, entries) {
  const kind = Object.hasOwn(SLASH, purpose) ? purpose : 'work';
  const family = members(entries);
  if (!family.length) throw new Error(`${HERD} を使うには agent-herd 一族の定義（aider / ollama）が必要です`);
  const usable = family.filter((m) => m.available);
  if (!usable.length) {
    throw new Error(`${HERD} の一族（${family.map((m) => m.name).join(' / ')}）がこの実行環境で利用できません`
      + '（tools/agent-tools/install.sh で agent-herd を PATH に通してください）');
  }
  const picked = usable.find((m) => m.name === CHAT_BACKEND) || usable[0];
  return { cli: picked.name, purpose: kind, slash: SLASH[kind], reason: REASON[kind] };
}

// スラッシュ行を本文の先頭に置く（slashroute は「本文の先頭から連続する /name 行」を読む）。
function withSlash(slash, prompt) {
  const line = String(slash || '').trim();
  return line ? `${line}\n${String(prompt || '')}` : String(prompt || '');
}

// タスク・ワークフロー・AI 支援: agent-herd へ渡す名前。'' は「渡さない（既定に任せる）」。
//   task … `agent-herd harness statemachine`（--agent-cli を省く）
//   plan … `agent-herd --purpose plan`（--agent を省く）
//   flow … agent-flow の `--agent-cli`（省けないので harness の既定と同じ aider）
function resolveAutomation(purpose) {
  if (purpose === 'flow') return { agent: HARNESS_DEFAULT, reason: 'agent-flow は --agent-cli を要求するので harness の既定（aider）を渡す' };
  return { agent: '', reason: 'agent-herd の既定と宣言に任せる（--agent-cli を渡さない）' };
}

// 一覧へ足す仮想の 1 行。一族の定義が 1 つも無ければ null（出さない）。
function listEntry(entries) {
  const family = members(entries);
  if (!family.length) return null;
  return {
    name: HERD,
    command: ENTRYPOINT,
    available: family.some((m) => m.available),
    readonly: family.every((m) => m.readonly === 'enforced') ? 'enforced' : 'best-effort',
    session: 'replay',
    interactive: family.some((m) => m.interactive),
    virtual: true,
    members: family.map((m) => m.name),
  };
}

// 名前の並び（agent-herd defs の出力など）に、一族が居れば `herd` を足す。
function withVirtualName(names, entries) {
  const list = (Array.isArray(names) ? names : []).map((n) => String(n || '').trim()).filter(Boolean);
  const family = members(entries).map((m) => m.name).filter((n) => list.includes(n));
  if (!family.length || list.includes(HERD)) return list;
  return [...list, HERD];
}

module.exports = {
  HERD, ENTRYPOINT, CHAT_BACKEND, HARNESS_DEFAULT, SLASH,
  isHerd, isMember, members, purposeOf, hasWorkFiles, resolveChat, withSlash, resolveAutomation, listEntry, withVirtualName,
};
