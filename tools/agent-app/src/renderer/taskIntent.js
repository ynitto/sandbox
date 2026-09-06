'use strict';

(function exposeTaskIntent(global) {
  function create(input) {
    const source = input && typeof input === 'object' ? input : {};
    const message = source.message && typeof source.message === 'object' ? source.message : {};
    const root = String(source.root || '').trim();
    const purpose = message.role === 'user' ? String(message.text || '').trim() : '';
    if (!root) throw new Error('リポジトリを選んでください');
    if (!purpose) throw new Error('タスクにする依頼を選んでください');
    const execution = source.execution && typeof source.execution === 'object' ? source.execution : {};
    return {
      version: 1,
      id: String(source.id || '').trim(),
      root,
      purpose,
      attachments: (Array.isArray(message.attachments) ? message.attachments : []).map((item) => ({
        name: String(item && item.name || ''),
        size: Number(item && item.size) || 0,
      })).filter((item) => item.name),
      agent: String(execution.agent || ''),
      model: String(execution.model || ''),
    };
  }

  function consume(intent, state) {
    const current = state && typeof state === 'object' ? state : {};
    const consumedId = String(current.consumedId || '');
    if (!intent || !intent.id || intent.root !== current.root) return { accepted: false, reason: 'root', consumedId };
    if (intent.id === consumedId) return { accepted: false, reason: 'consumed', consumedId };
    return { accepted: true, intent, consumedId: intent.id };
  }

  const taskIntent = { create, consume };
  if (typeof window === 'undefined') module.exports = taskIntent;
  else global.TaskIntent = taskIntent;
}(typeof window === 'undefined' ? globalThis : window));
