'use strict';

// `herd` — ローカル実行系を 1 語で指すための候補ラベル。
//
// **なぜ要るか。** 実行レベルの構成で人が打っていたのは `aider` / `ollama` と
// `gemma4:e4b` / `gemma4:12b` の組み合わせだが、正しい組み合わせは用途ごとに違う
// （抽出は e4b・レビューは 12b・コード編集は aider の e4b）。1 つ書かせると
// どれかの用途で必ず外れる。用途ごとの正解を知っているのは実測
// （agent-candidate-qualifications）なので、人は「ローカルを使うか」だけを言い、
// 具体の (agent_cli, model) は Compiler が実測から埋める。
//
// **一族は機械的に導ける。** `agents/<name>.json` の `command[0]` が `agent-herd` の
// 定義（aider / ollama / opencode）が一族である。`herd.json` を作る必要も、定義へ
// family フィールドを足す必要も無い。クラウド CLI はこの入口を通らない
// （agent-herd 設計 §1）ので、自動的に一族から外れる。
//
// **台帳と格付けの中では区別が残る。** `qualifications` の鍵は `(agent_cli, model)` で、
// aider と ollama の差は用途の次元ではなくハーネスの次元（single-shot ＋ 限定 4 ツール
// 契約 / tool-loop ＋ bash 無制限）である。`(herd, gemma4:e4b)` へ畳むと aider の
// コード編集 9/9 と ollama のテキスト抽出 6/6 が同じ候補に混ざるので畳まない。
// `herd` は**管理面の入力ラベル**であって、記録のキーではない。
//
// 設計: docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md §3.5

const agents = require('./agents');

// 実行レベルの候補として書ける一族の名前。
const HERD = 'herd';

// 一族の入口（`agents/<name>.json` の command[0]）。
const HERD_ENTRYPOINT = 'agent-herd';

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// 候補の同一性キー。区切りは制御文字を**明示して**書く——ここが 2 か所でずれると
// 重複が畳めず、同じ候補が 2 度並んだ順位表になる（実際に一度そうなった）。
function candidateKey(agentCli, model) {
  return `${String(agentCli || '').trim().toLowerCase()}\u0000${String(model || '').trim()}`;
}

function isHerdCandidate(candidate) {
  return isPlainObject(candidate)
    && String(candidate.agent_cli || '').trim().toLowerCase() === HERD;
}

// この端末で解決できる一族の定義名（探索順・先勝ち・検証エラーのある定義は除く）。
function members(cfg) {
  const found = [];
  for (const dropin of agents.list(cfg).dropins) {
    if (dropin.shadowed || (dropin.errors || []).length) continue;
    const spec = dropin.spec;
    if (!isPlainObject(spec) || !Array.isArray(spec.command) || !spec.command.length) continue;
    if (String(spec.command[0] || '').trim() !== HERD_ENTRYPOINT) continue;
    found.push(String(dropin.name || '').trim().toLowerCase());
  }
  return [...new Set(found.filter(Boolean))].sort();
}

// 実測が知っている `(agent_cli, model)` のうち、一族に属するものを宣言順で返す。
//
// **モデルは実測が決める。** 候補行がモデルを書いていればその 1 つに縛り、
// 空なら一族のどのモデルでも候補になれる（どれが選ばれるかは用途別の順位表が決める）。
function expandCandidate(candidate, { memberNames, qualifications }) {
  const pinned = String((candidate && candidate.model) || '').trim();
  const allowed = new Set(memberNames);
  const rows = [];
  const seen = new Set();
  const known = isPlainObject(qualifications) && Array.isArray(qualifications.candidates)
    ? qualifications.candidates : [];
  for (const entry of known) {
    if (!isPlainObject(entry)) continue;
    const cli = String(entry.agent_cli || '').trim().toLowerCase();
    const model = String(entry.model || '').trim();
    if (!allowed.has(cli) || !model) continue;
    if (pinned && model !== pinned) continue;
    const id = candidateKey(cli, model);
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push({ agent_cli: cli, model });
  }
  return rows;
}

// tiers の中の `herd` 行を実測由来の具体候補へ置き換える（純関数）。
//
// 展開できない（実測がまだ無い・一族の定義が入っていない）ときは **その行を落とす**。
// 推測で 1 つ選ぶと「設定したのと違うものが動く」を新しく作ることになる——呼び出し側は
// 候補ゼロとして扱い、理由を人へ見せる。
function expandTiers(tiers, { memberNames, qualifications }) {
  if (!isPlainObject(tiers)) return { tiers: {}, expanded: false, unresolved: [] };
  const out = {};
  const unresolved = [];
  let expanded = false;
  for (const [name, spec] of Object.entries(tiers)) {
    if (!isPlainObject(spec)) continue;
    const candidates = [];
    const seen = new Set();
    for (const candidate of (Array.isArray(spec.candidates) ? spec.candidates : [])) {
      if (!isHerdCandidate(candidate)) {
        // 具体名の行も同じ台帳で畳む——herd の展開結果と重なると、同じ候補が
        // 2 度並んだ順位表になる。
        const id = isPlainObject(candidate)
          ? candidateKey(candidate.agent_cli, candidate.model)
          : null;
        if (id !== null) {
          if (seen.has(id)) continue;
          seen.add(id);
        }
        candidates.push(candidate);
        continue;
      }
      expanded = true;
      const rows = expandCandidate(candidate, { memberNames, qualifications });
      if (!rows.length) unresolved.push({ tier: name, ...candidate });
      for (const row of rows) {
        const id = candidateKey(row.agent_cli, row.model);
        if (seen.has(id)) continue;
        seen.add(id);
        candidates.push(row);
      }
    }
    out[name] = { ...spec, candidates };
  }
  return { tiers: out, expanded, unresolved };
}

// 実行レベルの候補として書いてよい `agent_cli` の許可リスト。
//
// **禁止リストではなく許可リストにする。** 変種先を名指しで弾く形（旧
// `variantTargetNames`）は、profile 統一で対象名の実ファイルが消えた瞬間に
// 空集合を返して黙って無効になっていた。「実在する定義か `herd`」だけを通せば、
// 綴りの変化で封じが外れることはない。
function allowedAgentNames(cfg) {
  const names = new Set([HERD]);
  const inventory = agents.list(cfg);
  for (const name of inventory.builtins) names.add(String(name).trim().toLowerCase());
  for (const dropin of inventory.dropins) {
    if (dropin.shadowed || (dropin.errors || []).length) continue;
    names.add(String(dropin.name || '').trim().toLowerCase());
  }
  return names;
}

module.exports = {
  HERD,
  candidateKey,
  HERD_ENTRYPOINT,
  isHerdCandidate,
  members,
  expandCandidate,
  expandTiers,
  allowedAgentNames,
};
