'use strict';

// 用途（purpose / role / ノード kind）→ その用途を任せてよいと言える処理種別
// （`operation_class`）のカタログ。
//
// **なぜ管理面に置くか。** 実測（agent-candidate-qualifications）は最初から
// `候補 = (agent_cli, model)` × `operation_class` の形を持っている。ところが
// Execution Policy Compiler はその次元を捨てて workload ごとに 1 本の順位表を焼いて
// いたため、Resolver は `remaining[0]`——**workload ごとに 1 位が全ノードを取る**——に
// なっていた。抽出の実績しかない候補がレビューにも 1 位で選ばれる、という形である。
//
// 直し方は「用途の軸を GUI へ足すこと」ではない。用途の軸は既に
// `flow-tiers.js` の `KIND_MIN_TIER`（kind → 最低段）として管理面にあり、GUI に
// 出していないだけである。ここへ「その用途に要る処理種別」を 1 表足せば、
// 利用者からは見えないまま用途別の候補選択ができる。
//
// **不変条件は変わらない**: エンジンはこのカタログも profiles.json も qualifications も
// 読まない。Compiler がここを引いて `selection_policy.by_purpose` へ焼き、エンジンは
// 自分が既に持っている用途名（`purpose_or_role`）でそれを引くだけである。
//
// 設計: docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md §3.5

// --- 対応表 ---------------------------------------------------------------
//
// 値は **OR** である（どれか 1 つでも qualified / trial なら、その用途の候補になれる）。
// AND にすると、同じ仕事を別の切り口で測った 2 つの実測が互いを打ち消してしまう。
//
// **裏付けの無い用途はここへ書かない。** 書かなければ従来どおり workload 共通の
// `candidates` へフォールバックする（＝この変更前と同じ挙動）。実測が無い用途を
// 巻き込んで一斉に park させないための既定であり、カタログが埋まった用途から順に
// 効く opt-in の展開になる。
const PURPOSE_OPERATIONS = {
  // --- 成果物を作る（コード worker 圏）---
  // 局所修正として測った 2 種のどちらかが立てば worker として使える。
  // `multi-artifact-contract-change` と `code-worker` は **裏付けではなく禁止側**の
  // 記録（blocked）なので、ここには書かない——blocked は Compiler が自動選択から
  // 外すのであって、要求条件として並べるものではない。
  work: ['single-symbol-edit', 'existing-test-repair'],
  generate: ['single-symbol-edit'],

  // --- 判断・検証（レビュー圏）---
  // 誤判定が後続のリトライと完了判定を直接壊すので、レビューとして測った実績を要求する。
  verify: ['bounded-review'],
  judge: ['bounded-review'],
  review: ['bounded-review'],
  adjudicate: ['bounded-review'],

  // --- 読んで拾う（抽出圏）---
  extract: ['extract'],
  retrieve: ['extract'],
  classify: ['extract', 'bounded-analysis'],
  map: ['extract', 'bounded-analysis'],

  // --- 分析・選別 ---
  evaluator: ['bounded-analysis'],
  filter: ['bounded-analysis'],
  prioritize: ['bounded-analysis'],
  route: ['bounded-analysis'],
  assess: ['bounded-analysis'],

  // --- まとめる ---
  reduce: ['constrained-summary'],
  synthesize: ['constrained-summary'],

  // --- 形を決める ---
  // planner は B1 の planner_eval で局所不成立（鎖 2/3・fan-out 3/3・列挙 1/3・単一 0/3）。
  // ローカル候補が持たない処理種別を要求することで、**構成ではなく実測で**クラウドへ
  // 上がる。専用の基準線が引けたらこの値を差し替える。
  planner: ['planner'],
  plan: ['planner'],
  split: ['bounded-proposal'],
};

// 用途名は大小・前後空白を無視して引く（呼び出し側の綴りは kind / role / purpose と
// 出どころが混ざるため）。
function operationsFor(purpose) {
  const key = String(purpose || '').trim().toLowerCase();
  if (!key) return null;
  const operations = PURPOSE_OPERATIONS[key];
  return Array.isArray(operations) && operations.length ? operations.slice() : null;
}

// カタログが扱う用途名の一覧（Compiler が by_purpose を焼く対象）。
function knownPurposes() {
  return Object.keys(PURPOSE_OPERATIONS).slice().sort();
}

module.exports = { PURPOSE_OPERATIONS, operationsFor, knownPurposes };
