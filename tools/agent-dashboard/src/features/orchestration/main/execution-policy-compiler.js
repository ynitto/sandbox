'use strict';

// Management-plane compiler: profiles + measured qualifications -> agent-control v2.
// Engines never read profiles/qualifications and never re-score this output.

const purposeOperations = require('./purpose-operations');

const STRATEGIES = ['balanced', 'economy', 'quality'];

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function candidateId(candidate) {
  return `${String(candidate.agent_cli || '')}\u0000${String(candidate.model || '')}`;
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

// 処理種別は qualification の中ではなく **`qualifications` マップの鍵**である
// （契約上 `operation_class` を持つのは evaluation_profile の側）。用途別の順位表を
// 焼くにはこの鍵が要るので、索引を作る時点で `operation_class` として畳み込む。
// 鍵が読めないときだけ evaluation_profile の宣言へ落ちる。
function operationClassOf(document, key, qualification) {
  if (typeof key === 'string' && key.trim()) return key.trim();
  const profiles = isObject(document) && isObject(document.evaluation_profiles)
    ? document.evaluation_profiles : {};
  const profile = profiles[qualification.evaluation_profile_id];
  return isObject(profile) && typeof profile.operation_class === 'string'
    ? profile.operation_class.trim() : '';
}

function qualificationIndex(document, nowMs) {
  const index = new Map();
  for (const candidate of (document && Array.isArray(document.candidates) ? document.candidates : [])) {
    if (!isObject(candidate)) continue;
    const usable = [];
    for (const [key, qualification] of Object.entries(candidate.qualifications || {})) {
      if (!isObject(qualification) || !['qualified', 'trial'].includes(qualification.status)) continue;
      const expires = Date.parse(qualification.valid_until || '');
      if (!Number.isFinite(expires) || (Number.isFinite(nowMs) && expires < nowMs)) continue;
      if (typeof qualification.qualification_id !== 'string' || !qualification.qualification_id) continue;
      usable.push({ ...qualification, operation_class: operationClassOf(document, key, qualification) });
    }
    index.set(candidateId(candidate), { candidate, qualifications: usable });
  }
  return index;
}

function minMetric(values) {
  const present = values.map(finite).filter((value) => value !== null);
  return present.length ? Math.min(...present) : null;
}

function maxMetric(values) {
  const present = values.map(finite).filter((value) => value !== null);
  return present.length ? Math.max(...present) : null;
}

function metrics(record) {
  const qualifications = record.qualifications;
  const economics = isObject(record.candidate.economics) ? record.candidate.economics : {};
  const latency = isObject(record.candidate.latency) ? record.candidate.latency : {};
  return {
    criticalRisk: minMetric(qualifications.map((q) => q.critical_failure_risk)),
    successLower: maxMetric(qualifications.map((q) => q.success_rate_lower_bound)),
    quota: finite(economics.expected_quota_consumption),
    cost: finite(economics.estimated_cost),
    p50: minMetric([latency.p50_seconds, ...qualifications.map((q) => q.p50_seconds)]),
    calls: minMetric(qualifications.map((q) => q.expected_calls_with_failures)),
    evaluatedAt: Math.max(...qualifications.map((q) => Date.parse(q.last_evaluated_at || ''))
      .filter(Number.isFinite), -Infinity),
    samples: maxMetric(qualifications.map((q) => q.samples)),
  };
}

function ascMissingLast(a, b) {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a - b;
}

function descMissingLast(a, b) {
  return ascMissingLast(b, a);
}

function compareMetrics(strategy, left, right) {
  const a = left.metrics;
  const b = right.metrics;
  const comparators = strategy === 'economy'
    ? [
      () => ascMissingLast(a.cost, b.cost),
      () => ascMissingLast(a.quota, b.quota),
      () => ascMissingLast(a.calls, b.calls),
      () => ascMissingLast(a.p50, b.p50),
      () => descMissingLast(a.successLower, b.successLower),
    ]
    : strategy === 'quality'
      ? [
        () => ascMissingLast(a.criticalRisk, b.criticalRisk),
        () => descMissingLast(a.successLower, b.successLower),
        () => descMissingLast(
          Number.isFinite(a.evaluatedAt) ? a.evaluatedAt : null,
          Number.isFinite(b.evaluatedAt) ? b.evaluatedAt : null
        ),
        () => descMissingLast(a.samples, b.samples),
        () => ascMissingLast(a.cost, b.cost),
      ]
      : [
        () => ascMissingLast(a.criticalRisk, b.criticalRisk),
        () => descMissingLast(a.successLower, b.successLower),
        () => ascMissingLast(a.quota, b.quota),
        () => ascMissingLast(a.cost, b.cost),
        () => ascMissingLast(a.p50, b.p50),
      ];
  for (const compare of comparators) {
    const result = compare();
    if (result) return result;
  }
  return left.profileOrder - right.profileOrder
    || candidateId(left.candidate).localeCompare(candidateId(right.candidate));
}

function candidatesWithinCeiling(tiers, ceiling) {
  const ceilingSpec = isObject(tiers && tiers[ceiling]) ? tiers[ceiling] : null;
  if (!ceilingSpec || !Number.isFinite(Number(ceilingSpec.order))) return [];
  const ceilingOrder = Number(ceilingSpec.order);
  const rows = [];
  let profileOrder = 0;
  const orderedTiers = Object.entries(tiers || {})
    .filter(([, spec]) => isObject(spec) && Number.isFinite(Number(spec.order)))
    .sort((a, b) => Number(a[1].order) - Number(b[1].order));
  for (const [, spec] of orderedTiers) {
    if (Number(spec.order) > ceilingOrder) continue;
    for (const candidate of (Array.isArray(spec.candidates) ? spec.candidates : [])) {
      rows.push({ candidate, profileOrder: profileOrder++ });
    }
  }
  const seen = new Set();
  return rows.filter(({ candidate }) => {
    // `herd` は管理面の入力ラベルであって候補ではない。展開は呼び出し側
    // （profiles.expandedProfiles）の仕事で、ここまで残っていたら**落とす**——
    // 候補として通すと `(herd, ...)` が selection_policy に載り、エンジンが
    // load_cli("herd") で落ちる。
    if (isObject(candidate) && String(candidate.agent_cli || '').trim().toLowerCase() === 'herd') {
      return false;
    }
    if (!isObject(candidate) || !candidate.agent_cli || !candidate.model) return false;
    const id = candidateId(candidate);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

// その候補が、要求された処理種別のどれかを裏付けているか。
//
// 値は **OR**（どれか 1 つでも qualified / trial なら候補になれる）。`blocked` /
// `unknown` は qualificationIndex の時点で落ちているので、ここへは来ない。
function qualificationsForOperations(record, operations) {
  const wanted = new Set(operations);
  return record.qualifications.filter((qualification) =>
    wanted.has(String(qualification.operation_class || '')));
}

// 用途ごとの順位表。カタログに無い用途は **null** を返し、呼び出し側は
// workload 共通の candidates へフォールバックする（実測が無い用途を一斉に park させない）。
function rankedForOperations(rows, evidence, strategy, operations) {
  const ranked = [];
  for (const row of rows) {
    const record = evidence.get(candidateId(row.candidate));
    if (!record) continue;
    const matched = qualificationsForOperations(record, operations);
    if (!matched.length) continue;
    const scoped = { ...record, qualifications: matched };
    ranked.push({ ...row, record: scoped, metrics: metrics(scoped) });
  }
  ranked.sort((left, right) => compareMetrics(strategy, left, right));
  return ranked;
}

function policyCandidate(row, index) {
  return {
    agent_cli: row.candidate.agent_cli,
    model: row.candidate.model,
    qualification_refs: row.record.qualifications
      .map((qualification) => qualification.qualification_id).sort(),
    rank: index + 1,
    // trial 裏付けしか無い候補は明記する。Resolver は自動選択から除外し、
    // Execution Envelope の trial 明示承認 run でだけ選択を許す（設計 §5.2。
    // 無印のまま出すと通常 run で trial が走り「明示承認 run 限定」が破れる）。
    ...(row.record.qualifications.some((q) => q.status === 'qualified')
      ? {} : { status: 'trial' }),
  };
}

function compileSelectionPolicy({ strategy = 'balanced', tiers = {}, tierCeiling,
  qualifications = {}, nowMs } = {}) {
  if (!STRATEGIES.includes(strategy)) throw new Error(`strategy が不正です: ${strategy}`);
  const qualificationDoc = isObject(qualifications) ? qualifications : {};
  const evidence = qualificationIndex(qualificationDoc, nowMs);
  const rankRows = candidatesWithinCeiling(tiers, tierCeiling);
  const ranked = [];
  for (const row of rankRows) {
    const record = evidence.get(candidateId(row.candidate));
    if (!record || !record.qualifications.length) continue;
    ranked.push({ ...row, record, metrics: metrics(record) });
  }
  ranked.sort((left, right) => compareMetrics(strategy, left, right));

  // 用途別の順位表（additive）。実測は最初から operation_class の次元を持っているので、
  // それを捨てずに焼く。Resolver は purpose_or_role でここを引き、無ければ candidates
  // へ落ちる。selection_policy_errors は未知キーを弾かないので、この追加で古い読み手が
  // 壊れることはない（contract version は据え置き）。
  // 候補プールそのものが空（実行レベル未定義・tier に候補なし）なら宣言ごと出さない。
  // 20 件の空宣言を出すと「park すべき用途」と「そもそも候補が無い端末」が混ざる。
  const byPurpose = {};
  for (const purpose of rankRows.length ? purposeOperations.knownPurposes() : []) {
    const operations = purposeOperations.operationsFor(purpose);
    if (!operations) continue;
    const rows = rankedForOperations(rankRows, evidence, strategy, operations);
    // **候補ゼロでも書く。** 空を落とすと「カタログに無い（＝管理面が意見を持たない）」と
    // 「カタログにあるが裏付けを持つ候補が 1 つも無い」が区別できず、後者が
    // workload 共通の candidates へフォールバックしてしまう——planner のように
    // 局所不成立が実測で確定している用途が、抽出の実績しかない候補へ黙って流れる。
    // それは本変更が消そうとしているバグそのものである。空は park の宣言として残し、
    // 何が足りないか（operations）を Resolver が言えるようにする。
    byPurpose[purpose] = { operations, candidates: rows.map(policyCandidate) };
  }

  return {
    strategy,
    ranking_formula_version: 1,
    qualification_revision: Number(qualificationDoc.revision || 0),
    retry_limit: 1,
    no_candidate: 'park',
    candidates: ranked.map(policyCandidate),
    ...(Object.keys(byPurpose).length ? { by_purpose: byPurpose } : {}),
  };
}

module.exports = { STRATEGIES, compileSelectionPolicy };
