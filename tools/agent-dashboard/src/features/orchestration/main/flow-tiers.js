'use strict';

// agent-flow の機能（ノード kind）・役割（role）ごとの「実行可能な実行レベル（tier）」カタログ。
//
// 実行レベルの標準語彙は basic / small / medium / large（agent-profiles 契約。UI 表示は
// 単純作業 / 軽量 / 標準 / 高性能）。tier は「同程度の複雑さを扱える候補の集合」であって
// 料金の軸ではない（設計: docs/plans/2026-08-11-agent-dashboard-unified-execution-policy-design.md）。
// このカタログは「その複雑さの仕事を、そのレベルへ任せてよいか」を管理面の 1 か所で宣言する。
//
// 不変条件はこれまでどおり: エンジン（agent-flow）はこのカタログも profiles.json も読まない。
// 適格性は dashboard が plan を組む時点（固定 tier の検証と auto tier の決定）で適用し、
// エンジンへは従来の plan 語彙（node.tier / node.agent）だけで運ぶ。
//
// 設計: docs/plans/2026-08-12-agent-flow-tier-eligibility-strategy-design.md

const TIER_ORDER = ['basic', 'small', 'medium', 'large'];

// UI 表示名（統一実行方針設計の対訳。エラーメッセージも UI 語彙で出す）。
const TIER_LABELS = { basic: '単純作業', small: '軽量', medium: '標準', large: '高性能' };

// 機能（ノード kind）ごとの実行可能レベル。宣言は「下限」（TIER_ORDER 上の連続区間）で持つ
// ——上限を切る機能は無い（高性能で単純作業を実行しても品質は落ちない。選ばれにくいのは
// 実行方針＝予算側の仕事）。
//
// basic は「短い一手順で完結する」仕事だけに任せる（統一実行方針設計の合意）。JSON 契約で
// 一動作の kind（classify / filter / extract / map）が該当する。次は basic に任せない:
//   - 成果物を作る kind（work / generate）: 依頼の文脈全体を運ぶ必要がある
//   - 複数成果を突き合わせる kind（synthesize / judge）: 横断比較の判断が要る
//   - フローの形を決める kind（split）: 計画に相当する
//   - 検証（verify）: 誤判定が後続のリトライ・完了判定を直接壊す
//   - 資料読解（retrieve): 根拠を実際に読む read 系ツールの運用が要る
//   - 集約（reduce）: 分割結果すべてを文脈に載せる
const KIND_MIN_TIER = {
  work: 'small',
  generate: 'small',
  classify: 'basic',
  synthesize: 'medium',
  verify: 'small',
  filter: 'basic',
  judge: 'medium',
  reduce: 'small',
  split: 'medium',
  map: 'basic',
  extract: 'basic',
  retrieve: 'small',
};

// 役割（flow の工程外呼び出し）ごとの実行可能レベル。planner / evaluator は run 全体の形と
// 継続を決めるため標準以上。worker / verify は対応する kind と同じ下限（カタログの読み手が
// kind と role のどちらから引いても矛盾しないように、ここにも宣言する）。
const ROLE_MIN_TIER = {
  planner: 'medium',
  evaluator: 'medium',
  worker: 'small',
  verify: 'small',
};

// 実測済みの用途限定候補。モデル名だけで汎用化せず、実行契約を持つ CLI と組で宣言する。
// 12b は文章レビューでは適格だがコード worker では停止性が悪いため、verify だけに載せる。
const ROLE_CANDIDATES = {
  verify: [{ agent_cli: 'ollama-verify', model: 'gemma4:12b', tier: 'small' }],
};

const SEED_TIER_CANDIDATES = {
  basic: [
    { agent_cli: 'aider', model: 'gemma4:e4b' },
    { agent_cli: 'ollama', model: 'gemma4:e4b' },
  ],
  small: [{ agent_cli: 'ollama-verify', model: 'gemma4:12b' }],
};

function cloneCandidates(candidates) {
  return (candidates || []).map((candidate) => ({ ...candidate }));
}

function seedTierCandidates() {
  return Object.fromEntries(Object.entries(SEED_TIER_CANDIDATES)
    .map(([tier, candidates]) => [tier, cloneCandidates(candidates)]));
}

// オプションとして拡張する振る舞いによる下限の引き上げ。オプションは機能の複雑さを一段上げる:
//   - classify の route（分類後に専門工程を追加）: 分類結果がフローの形を決める制御になる
//     ——単純作業（basic）のラベル付けには任せない。
//   - verify の retry（未完了なら修正工程を追加して再検証）: 判定がリトライ予算と再作業を
//     直接駆動する——誤 fail は再作業を焼き、誤 pass はループを終わらせる。標準以上。
const CONTINUATION_MIN_TIER = {
  route: 'small',
  retry: 'medium',
};

function tierIndex(tier) {
  return TIER_ORDER.indexOf(String(tier || ''));
}

function isKnownTier(tier) {
  return tierIndex(tier) >= 0;
}

function tiersFrom(minTier) {
  const start = tierIndex(minTier);
  return start >= 0 ? TIER_ORDER.slice(start) : TIER_ORDER.slice();
}

// 機能＋オプションの実行可能レベル一覧（TIER_ORDER 順）。未知の kind は制限しない
// （エンジン側で kind が増えても、plan 生成を黙って壊さない加算的契約）。
function allowedTiers(kind, node) {
  const mins = [KIND_MIN_TIER[String(kind || '')]];
  const continuation = node && node.continuation ? String(node.continuation) : '';
  if (continuation && CONTINUATION_MIN_TIER[continuation]) {
    mins.push(CONTINUATION_MIN_TIER[continuation]);
  }
  const min = mins.filter(Boolean).sort((a, b) => tierIndex(b) - tierIndex(a))[0];
  return min ? tiersFrom(min) : TIER_ORDER.slice();
}

// UI・IPC へ渡すカタログ。kind ごとの実行可能レベルと、オプションが宣言する下限を並べる。
function catalog() {
  const kinds = {};
  for (const [kind, min] of Object.entries(KIND_MIN_TIER)) {
    kinds[kind] = {
      tiers: tiersFrom(min),
      options: {
        ...(kind === 'classify' ? { route: CONTINUATION_MIN_TIER.route } : {}),
        ...(kind === 'verify' ? { retry: CONTINUATION_MIN_TIER.retry } : {}),
      },
    };
  }
  const roles = {};
  for (const [role, min] of Object.entries(ROLE_MIN_TIER)) {
    roles[role] = {
      tiers: tiersFrom(min),
      ...(ROLE_CANDIDATES[role] ? { candidates: cloneCandidates(ROLE_CANDIDATES[role]) } : {}),
    };
  }
  return { order: TIER_ORDER.slice(), kinds, roles };
}

// 実行方針が上限未設定時に使う既定レベル（execution-policy.js のプリセットと同じ値。
// control に flow の段がまだ無い端末でも、方針から決定できるようにする）。
const MODE_DEFAULT_TIER = { auto: 'medium', saving: 'small', quality: 'large' };

function modeDefaultTier(mode, custom) {
  if (mode === 'custom') {
    const normal = custom && custom.normalTier;
    return isKnownTier(normal) ? String(normal) : 'medium';
  }
  return MODE_DEFAULT_TIER[String(mode || '')] || 'medium';
}

// allowed（実行可能レベル）へ inherit（実行方針が今選んでいる段）を丸める。
// 範囲外は端へ、（理論上の）飛び穴は方針の性格で埋める——節約は下へ、それ以外は上へ。
function clampTier(allowed, inherit, mode) {
  if (!allowed.length) return null;
  const idx = tierIndex(inherit);
  const first = tierIndex(allowed[0]);
  const last = tierIndex(allowed[allowed.length - 1]);
  if (idx < 0 || idx <= first) return allowed[0];
  if (idx >= last) return allowed[allowed.length - 1];
  if (allowed.includes(String(inherit))) return String(inherit);
  const lower = allowed.filter((t) => tierIndex(t) < idx).pop();
  const higher = allowed.find((t) => tierIndex(t) > idx);
  return mode === 'saving' ? (lower || higher) : (higher || lower);
}

// auto tier ノードの決定（純関数）。
//   - 方針が選びうる段（policyTiers＝steps + no_cap_tier）がすべて実行可能なら「継承」
//     ——plan へは tier を書かず、実行時の agent-control 追従（予算による切替）を保つ。
//   - 1 つでも実行可能範囲の外にある段が選ばれうるなら、今の段（controlTier、無ければ
//     方針の既定）を実行可能範囲へ丸めて固定する。実行時に不適格な段へ黙って落ちる経路を
//     残さないための固定で、固定 tier ノードと同じ扱い（枯渇時は降格せず待機）になる。
// 標準語彙の外の段名（独自 tier 宣言）は適格性の対象外として継承のまま通す（後方互換）。
function decideAutoTier({ kind, node, mode, custom, policyTiers, controlTier }) {
  const allowed = allowedTiers(kind, node);
  const policy = (Array.isArray(policyTiers) ? policyTiers : [])
    .map(String).filter(Boolean);
  if (policy.length && policy.every((t) => !isKnownTier(t) || allowed.includes(t))) {
    return { inherit: true, allowed };
  }
  const inherit = isKnownTier(controlTier) ? String(controlTier) : modeDefaultTier(mode, custom);
  const tier = clampTier(allowed, inherit, mode);
  return {
    inherit: !policy.length && allowed.includes(inherit) && tier === inherit,
    tier,
    allowed,
    reason: `strategy=${mode || 'auto'} inherit=${inherit} allowed=${allowed[0]}..${allowed[allowed.length - 1]}`,
  };
}

module.exports = {
  TIER_ORDER,
  TIER_LABELS,
  KIND_MIN_TIER,
  ROLE_MIN_TIER,
  CONTINUATION_MIN_TIER,
  ROLE_CANDIDATES,
  allowedTiers,
  isKnownTier,
  catalog,
  seedTierCandidates,
  clampTier,
  decideAutoTier,
};
