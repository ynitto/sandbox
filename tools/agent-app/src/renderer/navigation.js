'use strict';

// アプリ全体の領域名と旧設定の読み替えを一か所に置く。
// Node からも読み込める純粋なモジュールにし、保存値の移行を画面なしで検証する。
(function exposeNavigation() {
  const AREAS = {
    conversation: { label: '会話', createLabel: '新しい会話', listId: 'sessions' },
    tasks: { label: 'タスク', createLabel: '新しいタスク', listId: 'tasks' },
    workflows: { label: 'ワークフロー', createLabel: '新しいワークフロー', listId: 'workflows' },
  };

  function normalizeArea(value) {
    if (value === 'tasks' || value === 'automation') return 'tasks';
    if (value === 'workflows') return 'workflows';
    return 'conversation';
  }

  function areaInfo(value) {
    return AREAS[normalizeArea(value)];
  }

  function taskItems(snapshot, definitions) {
    const runtime = snapshot && Array.isArray(snapshot.machines) ? snapshot.machines : [];
    if (runtime.length) return runtime;
    return (Array.isArray(definitions) ? definitions : []).map((task) => ({
      ...task, parameters: [], schedule: null, history: [],
    }));
  }

  const navigation = { AREAS, normalizeArea, areaInfo, taskItems };
  if (typeof window === 'undefined') module.exports = navigation;
  else window.AgentNavigation = navigation;
}());
