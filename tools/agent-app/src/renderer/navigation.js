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

  function taskItems(snapshot, definitions, teaching) {
    const tasks = snapshot && Array.isArray(snapshot.tasks) ? snapshot.tasks : [];
    const runtime = snapshot && Array.isArray(snapshot.machines) ? snapshot.machines : [];
    const base = tasks.length ? tasks : runtime.length ? runtime : (Array.isArray(definitions) ? definitions : []).map((task) => ({
      ...task, parameters: [], schedule: null, history: [],
    }));
    const machineOf = (task) => String(task.machine || String(task.id || '').replace(/^machine:/, ''));
    const machines = new Set(base.map(machineOf));
    const sessions = (Array.isArray(teaching) ? teaching : []).filter((item) => item && item.machine);
    // 定義があるタスクは実行できるまま。AIとの変更が進んでいれば、その進み具合（change）だけを添える。
    const changes = new Map(sessions
      .filter((item) => machines.has(String(item.machine)) && item.status && item.status !== 'ready')
      .map((item) => [String(item.machine), item.status]));
    const published = changes.size ? base.map((task) => (changes.has(machineOf(task)) ? { ...task, change: changes.get(machineOf(task)) } : task)) : base;
    // 定義がまだ無い下書きだけを、教示中の項目として一覧に足す。
    const drafts = sessions
      .filter((item) => !machines.has(String(item.machine)))
      .map((item) => ({
        id: `machine:${item.machine}`,
        machine: item.machine,
        name: item.title || item.machine,
        teachingStatus: item.status || 'draft',
        teaching: true,
      }));
    return drafts.length ? [...published, ...drafts] : published;
  }

  const navigation = { AREAS, normalizeArea, areaInfo, taskItems };
  if (typeof window === 'undefined') module.exports = navigation;
  else window.AgentNavigation = navigation;
}());
