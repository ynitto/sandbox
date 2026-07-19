'use strict';

// 委譲契約のエンジン非依存コア（封筒の検証・id 採番・共通値の正規化）。
// 契約の正典は schemas/delegation.schema.json、設計は
// docs/plans/2026-07-19-delegation-contract-design.md。
//
// ここは「1 契約」の本体 — バス・claim プロトコルは統一せず、この封筒だけを
// エンジン別アダプタ（amigos-adapter / flow-adapter）がネイティブ形式へ変換する。
// dashboard はバスへ直接書かない（amigos: commands ドロップ / flow: inbox ドロップ）。

const OPS = new Set(['post', 'award', 'accept', 'reject', 'cancel']);
const WORKLOADS = new Set(['flow', 'amigos']);
const ASSIGNMENTS = new Set(['first-come', 'owner-picks']);
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

function hex(n) {
  // Math.random は暗号強度不要（衝突回避の add-on。冪等キーの主体は時刻部分）
  let s = '';
  for (let i = 0; i < n; i += 1) s += Math.floor(Math.random() * 16).toString(16);
  return s;
}

// dg-<YYYYMMDDHHMMSS>-<hex4>。冪等キー（同一 id の再投函は同一公示）。
// 呼び出し側が Date を渡せるのはテストのため（実行時は現在時刻）。
function newDelegationId(now) {
  const d = now instanceof Date ? now : new Date();
  const p = (v, w) => String(v).padStart(w, '0');
  const stamp =
    p(d.getUTCFullYear(), 4) + p(d.getUTCMonth() + 1, 2) + p(d.getUTCDate(), 2) +
    p(d.getUTCHours(), 2) + p(d.getUTCMinutes(), 2) + p(d.getUTCSeconds(), 2);
  return `dg-${stamp}-${hex(4)}`;
}

function isObj(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

// 封筒を検証して正規化した複製を返す（不正は Error）。エンジンの非対称は
// ここで fail-fast する（設計 D4/D5: flow×owner-picks・flow の award/accept/reject を拒否）。
function validateEnvelope(env) {
  if (!isObj(env)) throw new Error('委譲封筒がオブジェクトではありません');
  const op = env.op;
  if (!OPS.has(op)) throw new Error(`不正な op です: ${op}`);
  if (Number(env.version) !== 1) throw new Error('version は 1 である必要があります');
  const id = String(env.id || '');
  if (!ID_RE.test(id)) {
    throw new Error(`不正な id です（[A-Za-z0-9_-]{1,64}）: ${env.id}`);
  }
  const workload = String(env.workload || '');
  if (!WORKLOADS.has(workload)) throw new Error(`不正な workload です: ${env.workload}`);

  const out = { op, version: 1, id, workload };

  if (op === 'post') {
    const goal = String(env.goal || '').trim();
    if (!goal) throw new Error('post には goal が必要です');
    out.goal = goal;
    out.title = String(env.title || '').trim();
    out.design = typeof env.design === 'string' ? env.design : '';
    out.workspace = isObj(env.workspace) ? env.workspace : null;
    out.references = Array.isArray(env.references) ? env.references.filter(isObj) : [];

    const policy = isObj(env.policy) ? env.policy : {};
    const assignment = policy.assignment == null ? 'first-come' : String(policy.assignment);
    if (!ASSIGNMENTS.has(assignment)) {
      throw new Error(`不正な policy.assignment です: ${policy.assignment}`);
    }
    // D4: flow は first-come のみ。owner-picks は黙って落とさず投函前に拒否する。
    if (workload === 'flow' && assignment === 'owner-picks') {
      throw new Error(
        'flow は owner-picks（応募→選定）に未対応です。first-come で公示してください'
      );
    }
    out.policy = {
      assignment,
      staffing: policy.staffing == null ? 'self-staff' : String(policy.staffing),
      staffing_timeout_sec:
        policy.staffing_timeout_sec == null ? 600 : Number(policy.staffing_timeout_sec),
    };
    out.acceptance = env.acceptance == null ? 'manual' : String(env.acceptance);
    const budget = isObj(env.budget) ? env.budget : {};
    out.budget = {
      execution_minutes: Number(budget.execution_minutes) || 0,
      per_unit_turns: budget.per_unit_turns == null ? 30 : Number(budget.per_unit_turns),
    };
    out.deadline = env.deadline ? String(env.deadline) : '';
    out.priority = env.priority == null ? 'normal' : String(env.priority);
    out.requested_by = String(env.requested_by || 'dashboard');
    out.requested_at = env.requested_at ? String(env.requested_at) : '';
    out.engine = isObj(env.engine) ? env.engine : {};

    // workload=amigos は役割ミッション表が必須（mission.schema.json の roles と同形）。
    if (workload === 'amigos') {
      const roles = out.engine.amigos && out.engine.amigos.roles;
      if (!Array.isArray(roles) || !roles.length) {
        throw new Error('workload=amigos の post には engine.amigos.roles（役割表）が必要です');
      }
    }
    return out;
  }

  if (op === 'award') {
    // D5: award（owner-picks の落札確定）は v1 では amigos のみ。
    if (workload !== 'amigos') throw new Error('award は v1 では amigos のみ対応です');
    out.unit = String(env.unit || '');
    out.node = String(env.node || '');
    if (!out.unit || !out.node) throw new Error('award には unit（ロール）と node が必要です');
    return out;
  }

  if (op === 'accept' || op === 'reject') {
    if (workload !== 'amigos') throw new Error(`${op} は v1 では amigos のみ対応です`);
    if (op === 'reject') {
      const feedback = String(env.feedback || '').trim();
      if (!feedback) throw new Error('reject には feedback（差し戻し理由）が必要です');
      out.feedback = feedback;
    }
    return out;
  }

  // cancel は両エンジン対応（D6/D8）
  out.reason = env.reason ? String(env.reason) : '';
  return out;
}

// 部分ペイロード（renderer から）に op / version / id / 出自を補って封筒化してから検証する。
function buildEnvelope(op, payload, now) {
  const p = isObj(payload) ? payload : {};
  const env = { ...p, op, version: 1 };
  if (!env.id) env.id = newDelegationId(now);
  if (op === 'post' && !env.requested_at) {
    const d = now instanceof Date ? now : new Date();
    env.requested_at = d.toISOString().replace(/\.\d+Z$/, 'Z');
  }
  return validateEnvelope(env);
}

module.exports = { validateEnvelope, buildEnvelope, newDelegationId, ID_RE };
