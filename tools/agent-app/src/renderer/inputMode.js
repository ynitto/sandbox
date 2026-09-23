'use strict';

(function initInputMode(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.InputMode = api;
})(typeof window !== 'undefined' ? window : globalThis, () => {
  function create() {
    return { mode: 'message', lastEscapeAt: 0 };
  }

  // タスクの見本記録も同じ入力欄のタブとして扱う。
  function reduce(state, event) {
    if (event.type === 'terminal-focus') return { ...state, mode: 'terminal', lastEscapeAt: 0 };
    if (event.type === 'message-focus') return { ...state, mode: 'message', lastEscapeAt: 0 };
    if (event.type === 'share-focus') return { ...state, mode: 'share', lastEscapeAt: 0 };
    if (event.type === 'record-focus') return { ...state, mode: 'record', lastEscapeAt: 0 };
    return state;
  }

  function handleEscape(state, now = Date.now()) {
    if (state.mode !== 'terminal') return { state, forward: false };
    if (state.lastEscapeAt && now - state.lastEscapeAt <= 600) {
      return { state: { ...state, mode: 'message', lastEscapeAt: 0 }, forward: false };
    }
    return { state: { ...state, lastEscapeAt: now }, forward: true };
  }

  return { create, reduce, handleEscape };
});
