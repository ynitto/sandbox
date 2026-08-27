'use strict';

// おすすめ構成（agent-recommendation）— 点検・差分・適用。
//
// **dashboard は根拠面を書かない。** 適格性（qualifications.json）の writer は
// agent-audit だけで、ここが持つのは**起動口**である（2026-08-23 提案 §2.5 が
// 「入れるなら起動口だけ」と残していた案）。推奨そのものの生成も eval 側
// （`recommend.py`）の 1 実装で、ここは読むだけ。
//
// 適用の順序は実装が持つ。人が「実行方針から触ると保存できない」順序の罠
// （execution-policy.save は候補未設定の段があると保存を拒否する）を踏まないよう、
//   1. profiles.save（tiers。枠は選択で埋めてから）
//   2. executionPolicy.save（mode。1 のあとなので弾かれない）
//   3. control.saveControl（concurrency）
//   4. agent-audit seed（適格性。**起動するだけ**）
//   5. profiles.apply（selection_policy のコンパイル）
// の順で固定する。
//
// 設計: docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md §3.4

const fs = require('fs');
const path = require('path');

const control = require('./control');
const profiles = require('./profiles');
const executionPolicy = require('./execution-policy');
const qualifications = require('./qualifications');
const agents = require('./agents');
const herdFamily = require('./herd-family');
const { agentHomeSubdir, sharedStateReadPath } = require('../../../base/main/agent-home');

const RECOMMENDATION_FILE = 'recommendation.json';
const TIER_LABELS = { basic: '単純作業', small: '軽量', medium: '標準', large: '高性能' };

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

// 推奨は制御面ではないので `~/.agents/` 直下（agents/*.json と同じ配布経路）。
function resolveFile(cfg) {
  const explicit = String(((cfg && cfg.orchestration) || {}).recommendationFile || '').trim();
  if (explicit) return explicit;
  return path.join(agentHomeSubdir('.'), RECOMMENDATION_FILE);
}

function load(cfg) {
  const resolved = resolveFile(cfg);
  const file = sharedStateReadPath(path.dirname(resolved), path.basename(resolved));
  const document = readJson(file);
  if (!isPlainObject(document) || document.version !== 1 || !isPlainObject(document.tiers)) {
    return { exists: false, file, document: null };
  }
  return { exists: true, file, document };
}

// --- 点検 -------------------------------------------------------------------

// 適用の前に人へ見せる前提条件。**足りないものを黙って埋めない**——モデルの取得も
// 定義の再配布も、ここではやらずに「何をすればよいか」だけを言う。
function preflight(cfg, document) {
  const rows = [];
  const requires = isPlainObject(document.requires) ? document.requires : {};

  // (1) 一族の定義がこの端末で解決できるか。
  const members = herdFamily.members(cfg);
  const wanted = ((document.herd || {}).members || []);
  const missing = wanted.filter((name) => !members.includes(name));
  rows.push({
    id: 'agent-defs',
    ok: !missing.length,
    label: 'エージェント定義',
    detail: missing.length
      ? `${missing.join(' / ')} が見つかりません`
      : `${members.join(' / ') || '（なし）'}`,
    remedy: missing.length ? 'bash tools/agent-tools/install.sh を実行してください' : '',
  });

  // (2) 実測を持つ候補が 1 つでもあるか（無いと herd を展開できない）。
  const expansion = ((document.herd || {}).expansion || []).filter((row) => row && row.usable);
  rows.push({
    id: 'evidence',
    ok: expansion.length > 0,
    label: '実測の裏付け',
    detail: expansion.length
      ? `${expansion.length} 候補`
      : '一族の候補に裏付けがありません（herd を展開できません）',
    remedy: expansion.length ? '' : '評価 archive から推奨を作り直してください',
  });

  // (3) モデルの取得。**dashboard は ollama を叩かない**（読み取り専用の面なので、
  //     取得状況の確認と pull は人の手に残す）。何が要るかだけを出す。
  const models = Array.isArray(requires.models) ? requires.models : [];
  rows.push({
    id: 'models',
    ok: true,
    label: '必要なモデル',
    detail: models.join(' / ') || '（なし）',
    remedy: models.length ? `ollama pull ${models.join(' ')}` : '',
    advisory: true,
  });

  // (4) クラウド枠を埋められるか（検出できた CLI）。
  const cloud = cloudChoices(cfg);
  rows.push({
    id: 'cloud',
    ok: cloud.length > 0,
    label: 'クラウド CLI',
    detail: cloud.length ? cloud.join(' / ') : '検出できませんでした',
    remedy: cloud.length ? '' : '標準・高性能の枠は空のままになります（昇格先が無くなります）',
    advisory: true,
  });
  return rows;
}

// 枠へ入れられる候補（一族の外＝クラウド側の定義名）。**値は推奨が決めない**ので、
// ここは選択肢を並べるだけ。
function cloudChoices(cfg) {
  const family = new Set(herdFamily.members(cfg));
  const inventory = agents.list(cfg);
  const names = new Set(inventory.builtins);
  for (const dropin of inventory.dropins) {
    if (dropin.shadowed || (dropin.errors || []).length) continue;
    names.add(dropin.name);
  }
  for (const name of family) names.delete(name);
  for (const name of agents.variantTargetNames(cfg)) names.delete(name);
  names.delete(herdFamily.HERD);
  return [...names].sort();
}

// --- 差分 -------------------------------------------------------------------

function candidateText(candidates) {
  if (!Array.isArray(candidates) || !candidates.length) return '（未設定）';
  return candidates
    .filter(isPlainObject)
    .map((c) => `${c.agent_cli || '?'}${c.model ? `:${c.model}` : ''}`)
    .join(' / ');
}

// 現在 → 推奨。**充足済みの枠を差分に出さない**——枠は「人が選ぶ場所」であって
// 推奨値ではないので、正しく設定済みの端末に毎回「変更あり」が出るのは誤りである。
function diff(cfg, document, slotChoices = {}) {
  const current = profiles.load(cfg);
  const currentControl = control.loadControl(control.resolveControlDir(cfg));
  const currentQualifications = qualifications.load(cfg);
  const rows = [];

  for (const [tier, spec] of Object.entries(document.tiers)) {
    const have = (current.tiers || {})[tier] || {};
    const haveText = candidateText(have.candidates);
    const slots = Array.isArray(spec.slots) ? spec.slots : [];
    if (slots.length) {
      const chosen = slotChoices[tier];
      const filled = chosen || (have.candidates || []).length;
      rows.push({
        key: `tier:${tier}`,
        label: `実行レベル ${TIER_LABELS[tier] || tier}`,
        from: haveText,
        to: chosen ? candidateText([chosen]) : (filled ? haveText : '（枠が空のまま）'),
        changed: Boolean(chosen) && candidateText([chosen]) !== haveText,
        slot: slots.map((s) => s.requires).join('・'),
      });
      continue;
    }
    const toText = candidateText(spec.candidates);
    rows.push({
      key: `tier:${tier}`,
      label: `実行レベル ${TIER_LABELS[tier] || tier}`,
      from: haveText,
      to: toText,
      changed: haveText !== toText,
    });
  }

  const haveMode = ((current.executionPolicy || {}).mode) || '';
  const wantMode = (document.execution_policy || {}).mode || 'auto';
  rows.push({
    key: 'policy',
    label: '実行方針',
    from: haveMode || '（未設定）',
    to: wantMode,
    changed: haveMode !== wantMode,
  });

  const wantConcurrency = (((document.control || {}).workloads || {}).flow || {}).concurrency;
  if (isPlainObject(wantConcurrency)) {
    const haveConcurrency = ((currentControl.workloads || {}).flow || {}).concurrency || {};
    const same = Object.entries(wantConcurrency)
      .every(([key, value]) => haveConcurrency[key] === value);
    rows.push({
      key: 'concurrency',
      label: '同時実行数',
      from: Object.keys(haveConcurrency).length
        ? JSON.stringify(haveConcurrency) : '（未設定）',
      to: JSON.stringify(wantConcurrency),
      changed: !same,
    });
  }

  const wantRevision = Number((document.qualifications || {}).revision || 0);
  const haveRevision = currentQualifications.exists ? Number(currentQualifications.revision) : null;
  rows.push({
    key: 'qualifications',
    label: '適格性',
    from: haveRevision === null ? '（未設定）' : `revision ${haveRevision}`,
    to: `revision ${wantRevision}（${((document.qualifications || {}).candidates || []).length} 候補）`,
    changed: haveRevision !== wantRevision,
  });
  return rows;
}

// --- 適用 -------------------------------------------------------------------

// 枠の選択を候補行へ変換する。`{ medium: { agent_cli, model } }` の形で受ける。
function tiersFrom(document, slotChoices = {}) {
  const tiers = {};
  for (const [tier, spec] of Object.entries(document.tiers)) {
    const entry = {
      order: Number(spec.order),
      label: spec.label || TIER_LABELS[tier] || tier,
      candidates: Array.isArray(spec.candidates) ? spec.candidates.slice() : [],
    };
    const slots = Array.isArray(spec.slots) ? spec.slots : [];
    if (slots.length) {
      const chosen = slotChoices[tier];
      if (isPlainObject(chosen) && String(chosen.agent_cli || '').trim()) {
        entry.candidates = [{
          agent_cli: String(chosen.agent_cli).trim(),
          ...(String(chosen.model || '').trim() ? { model: String(chosen.model).trim() } : {}),
        }];
      }
    }
    tiers[tier] = entry;
  }
  return tiers;
}

// 方針が使う段に候補が 1 件も無いと execution-policy.save が拒否する。空の枠を
// 残したまま適用しようとしたときに、**保存の途中で落ちる**のではなく先に言う。
function unfilledPolicyTiers(tiers, mode) {
  const built = executionPolicy.build(mode || 'auto', {});
  const names = new Set([
    built.profiles.policy.no_cap_tier,
    ...built.profiles.policy.steps.map((step) => step.tier),
  ].filter(Boolean));
  return [...names].filter((name) => !tiers[name] || !tiers[name].candidates.length);
}

function apply(cfg, { slotChoices = {}, seed = true, runSeed } = {}) {
  const loaded = load(cfg);
  const result = { ok: false, steps: [], errors: [] };
  if (!loaded.exists) {
    result.errors.push({ step: 'load', message: `おすすめ構成が読めません: ${loaded.file}` });
    return result;
  }
  const document = loaded.document;
  const tiers = tiersFrom(document, slotChoices);
  const mode = (document.execution_policy || {}).mode || 'auto';

  const unfilled = unfilledPolicyTiers(tiers, mode);
  if (unfilled.length) {
    result.errors.push({
      step: 'slots',
      message: `候補が空の実行レベルがあります: ${unfilled.map((t) => TIER_LABELS[t] || t).join(', ')}`
        + '（クラウド枠を選んでください）',
    });
    return result;
  }

  try {
    profiles.save(cfg, { tiers });
    result.steps.push({ step: 'tiers', ok: true });
  } catch (error) {
    result.errors.push({ step: 'tiers', message: error.message || String(error) });
    return result;
  }

  const policyResult = executionPolicy.save(cfg, { mode });
  result.steps.push({ step: 'policy', ok: policyResult.ok });
  if (!policyResult.ok) {
    result.errors.push(...(policyResult.errors || []).map((e) => ({ step: 'policy', ...e })));
    return result;
  }

  const concurrency = (((document.control || {}).workloads || {}).flow || {}).concurrency;
  if (isPlainObject(concurrency)) {
    try {
      control.saveControl(cfg, { workloads: { flow: { concurrency } } });
      result.steps.push({ step: 'concurrency', ok: true });
    } catch (error) {
      result.errors.push({ step: 'concurrency', message: error.message || String(error) });
    }
  }

  if (seed) {
    // **dashboard は qualifications.json を書かない。** agent-audit を起こすだけ。
    const seedResult = (runSeed || defaultRunSeed)(cfg, loaded.file);
    result.steps.push({ step: 'seed', ok: Boolean(seedResult && seedResult.ok), detail: seedResult });
    if (!seedResult || !seedResult.ok) {
      result.errors.push({
        step: 'seed',
        message: (seedResult && seedResult.error)
          || '適格性の設定に失敗しました（agent-audit の設定を確認してください）',
      });
    }
  }

  try {
    result.apply = profiles.apply(cfg, { force: true });
    result.steps.push({ step: 'compile', ok: true });
  } catch (error) {
    result.errors.push({ step: 'compile', message: error.message || String(error) });
  }

  result.ok = !result.errors.length;
  return result;
}

// agent-audit の起動は audit 制御面の 1 実装に任せる（コマンド解決・WSL 経路を複製しない）。
function defaultRunSeed(cfg, file) {
  const audit = require('../../agent-audit/main/audit');
  return audit.seed(cfg, file);
}

module.exports = {
  RECOMMENDATION_FILE,
  TIER_LABELS,
  resolveFile,
  load,
  preflight,
  cloudChoices,
  diff,
  tiersFrom,
  unfilledPolicyTiers,
  apply,
};
