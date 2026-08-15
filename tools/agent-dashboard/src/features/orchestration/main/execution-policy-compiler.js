'use strict';

// Management-plane compiler: profiles + measured qualifications -> agent-control v2.
// Engines never read profiles/qualifications and never re-score this output.

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

function qualificationIndex(document, nowMs) {
  const index = new Map();
  for (const candidate of (document && Array.isArray(document.candidates) ? document.candidates : [])) {
    if (!isObject(candidate)) continue;
    const usable = [];
    for (const qualification of Object.values(candidate.qualifications || {})) {
      if (!isObject(qualification) || !['qualified', 'trial'].includes(qualification.status)) continue;
      const expires = Date.parse(qualification.valid_until || '');
      if (!Number.isFinite(expires) || (Number.isFinite(nowMs) && expires < nowMs)) continue;
      if (typeof qualification.qualification_id !== 'string' || !qualification.qualification_id) continue;
      usable.push(qualification);
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
    if (!isObject(candidate) || !candidate.agent_cli || !candidate.model) return false;
    const id = candidateId(candidate);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function compileSelectionPolicy({ strategy = 'balanced', tiers = {}, tierCeiling,
  qualifications = {}, nowMs } = {}) {
  if (!STRATEGIES.includes(strategy)) throw new Error(`strategy が不正です: ${strategy}`);
  const qualificationDoc = isObject(qualifications) ? qualifications : {};
  const evidence = qualificationIndex(qualificationDoc, nowMs);
  const ranked = [];
  for (const row of candidatesWithinCeiling(tiers, tierCeiling)) {
    const record = evidence.get(candidateId(row.candidate));
    if (!record || !record.qualifications.length) continue;
    ranked.push({ ...row, record, metrics: metrics(record) });
  }
  ranked.sort((left, right) => compareMetrics(strategy, left, right));
  return {
    strategy,
    ranking_formula_version: 1,
    qualification_revision: Number(qualificationDoc.revision || 0),
    retry_limit: 1,
    no_candidate: 'park',
    candidates: ranked.map((row, index) => ({
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
    })),
  };
}

module.exports = { STRATEGIES, compileSelectionPolicy };
