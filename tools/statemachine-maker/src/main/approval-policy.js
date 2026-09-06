'use strict';

const SAFE = new Set(['open', 'read', 'navigate', 'search', 'snapshot', 'fill-draft', 'inspect', 'wait']);
const IMPORTANT = new Set(['submit', 'save', 'update', 'delete', 'purchase', 'publish', 'message', 'permission']);

function classify(operation = {}) {
  const action = String(operation.action || '').trim().toLowerCase();
  if (SAFE.has(action)) return { level: 'safe', action, reason: '外部の状態を変更しません' };
  const reason = IMPORTANT.has(action)
    ? '外部の状態を変更する可能性があります'
    : '影響を安全に判定できません';
  return { level: 'approval-required', action: action || 'unknown', reason };
}

function createApproval(value = {}) {
  const operation = classify(value);
  if (operation.level !== 'approval-required') throw new Error('安全な操作に承認は必要ありません');
  return {
    id: String(value.id || '').trim(),
    trialId: String(value.trialId || '').trim(),
    action: operation.action,
    target: String(value.target || '').trim(),
    effect: String(value.effect || '').trim(),
    state: 'pending',
  };
}

function decide(value = {}, decision = '') {
  if (value.state !== 'pending') throw new Error('この承認依頼には回答済みです');
  if (!['approved', 'rejected'].includes(decision)) throw new Error('承認結果が不正です');
  return { ...value, state: decision };
}

function consume(value = {}, operation = {}) {
  if (value.state === 'consumed') throw new Error('承認は一度だけ使えます');
  if (value.state !== 'approved') throw new Error('操作は承認されていません');
  const expected = [value.trialId, value.action, value.target];
  const actual = [operation.trialId, operation.action, operation.target].map((item) => String(item || '').trim().toLowerCase());
  if (expected.map((item) => String(item || '').trim().toLowerCase()).some((item, index) => item !== actual[index])) {
    throw new Error('承認した試行・操作・対象と一致しません');
  }
  return { ...value, state: 'consumed' };
}

module.exports = { classify, createApproval, decide, consume };
