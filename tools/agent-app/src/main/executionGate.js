'use strict';

function normalizedLimit(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(1, Math.min(8, Math.floor(number))) : 2;
}

function createGate() {
  const active = new Set();
  return {
    acquire(id, value) {
      const limit = normalizedLimit(value);
      const key = String(id || '');
      if (active.has(key)) {
        const error = new Error('この会話はすでに応答中です');
        error.code = 'TURN_RUNNING';
        throw error;
      }
      if (active.size >= limit) {
        const error = new Error(`${active.size}件実行中で、同時実行上限 ${limit} 件に達しています`);
        error.code = 'CONCURRENCY_LIMIT';
        error.detail = { active: active.size, limit };
        throw error;
      }
      active.add(key);
      return { active: active.size, limit };
    },
    release(id, value) {
      active.delete(String(id || ''));
      return { active: active.size, limit: normalizedLimit(value) };
    },
    snapshot(value) {
      return { active: active.size, limit: normalizedLimit(value), ids: [...active] };
    },
  };
}

module.exports = { createGate, normalizedLimit };
