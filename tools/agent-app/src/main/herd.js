'use strict';

// `herd` — ローカル実行系（agent-herd の一族）を 1 語で指す**仮想エージェント**。
//
// agent-dashboard の実行レベルでは、ローカルを使う段に `herd` と書けば、用途ごとの実測
// （qualifications）が aider / ollama とモデルを決める（herd-family.js）。agent-app の
// 会話・タスク・ワークフローには「用途」の軸が無い——依頼はぜんぶ「作業（act）」か
// 「質問（Ask）」で、実測の台帳も読まない。それでも `herd` を選べるようにするのは、
// 設定する人が aider と ollama を選び分けなくてよいようにするためで、選び分けの規則を
// ここに 1 つだけ置く。
//
// **一族は定義から機械的に導く。** `agents/<name>.json` の `command[0]`（対話起動なら
// `interactive.command[0]`）が `agent-herd` の定義が一族である。`agents/herd.json` は
// 作らない（dashboard・agentcore と同じ規則。作ると一族の判定と衝突する）。
//
// **一族の中で違うのは「編集・実装を誰がやるか」の 1 点だけ**（agent-herd `defs` の
// 役割行と同じ読み方）。それ以外の用途（計画・評価・抽出・検証…）は定義の `variants` が
// どちらを入口にしても同じ profile（ollama-json 等）へ振り替える。
//
//   aider  … 渡したファイルを直す編集役（自分では探索しない。single-shot）
//   ollama … 自分で調べて実行するツールループ（bash。Ask では読み取り専用）
//
// なので選び分けは**依頼の形**で決める:
//   ask   … 読み取り専用（Ask）                       → ollama（readonly が enforced。--think on）
//   edit  … 作業フォルダの中のファイルを添えた作業依頼   → aider（添えたファイルを直す）
//   work  … ファイルを添えない作業依頼                  → ollama（自分で探して直す）
//   task  … タスク（ステートマシン）・ワークフローの実行  → aider（dashboard の work 用途と同じ既定）
//   plan  … AI 支援（計画。読み取り専用）               → ollama（variants が ollama-json へ回す）
// 候補が使えなければ一族の中で次の候補へ倒す（一族の外へは倒さない。ADR-3）。
// 解決結果（どれになったか）はメッセージに `family: 'herd'` と実際の `cli` で残す。

const HERD = 'herd';
const ENTRYPOINT = 'agent-herd';

const PREFERENCE = {
  ask: ['ollama', 'aider'],
  edit: ['aider', 'ollama'],
  work: ['ollama', 'aider'],
  task: ['aider', 'ollama'],
  plan: ['ollama', 'aider'],
};
const PURPOSES = Object.keys(PREFERENCE);

const REASON = {
  ask: '読み取り専用の依頼は、自分で調べて答えるツールループ',
  edit: '作業フォルダのファイルを添えた依頼は、そのファイルを直す編集役',
  work: 'ファイルを添えない作業依頼は、自分で探して直すツールループ',
  task: 'タスク・ワークフローの実行は編集役を入口にする（他の用途は定義の variants が振り替える）',
  plan: '計画・相談は読み取り専用のツールループ',
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
// それは参考資料で、aider が直す対象ではない）。
function hasWorkFiles(attachments) {
  return (Array.isArray(attachments) ? attachments : []).some((a) => a && typeof a === 'object' && a.rel);
}

// 一族の中から用途に合う定義を選ぶ。使える（available）ものが無ければ理由を言って断る。
function resolve(purpose, entries) {
  const kind = PURPOSES.includes(purpose) ? purpose : 'work';
  const family = members(entries);
  if (!family.length) throw new Error(`${HERD} を使うには agent-herd 一族の定義（aider / ollama）が必要です`);
  const order = PREFERENCE[kind];
  const ranked = [
    ...order.map((name) => family.find((m) => m.name === name)).filter(Boolean),
    ...family.filter((m) => !order.includes(m.name)),
  ];
  const usable = ranked.find((m) => m.available) || null;
  if (!usable) {
    throw new Error(`${HERD} の一族（${family.map((m) => m.name).join(' / ')}）がこの実行環境で利用できません`
      + '（tools/agent-tools/install.sh で agent-herd を PATH に通してください）');
  }
  return { cli: usable.name, purpose: kind, reason: REASON[kind], fallback: usable.name !== ranked[0].name };
}

// 名前が `herd` ならその用途で解決し、そうでなければそのまま返す。
function resolveName(name, purpose, entries) {
  return isHerd(name) ? resolve(purpose, entries).cli : String(name || '');
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

module.exports = { HERD, ENTRYPOINT, PREFERENCE, PURPOSES, isHerd, isMember, members, purposeOf, hasWorkFiles, resolve, resolveName, listEntry, withVirtualName };
