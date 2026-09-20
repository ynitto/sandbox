'use strict';
(function expose(global) {
  const WORKLOAD_LABEL = { chat: '会話', task: 'タスク', workflow: 'ワークフロー', project: 'プロジェクト', routine: 'タスク', shared: '共有の依頼', evaluation: '評価', audit: '記録の収集・分析', judge: '振り分け・判定', other: 'その他' };
  function site(cli) {
    return ['herd', 'ollama', 'aider'].includes(cli) ? 'local'
      : ['claude', 'codex', 'copilot', 'kiro'].includes(cli) ? 'cloud' : 'other';
  }
  function breakdown(rows, by) {
    const groups = new Map();
    for (const row of rows || []) {
      let group = row.group;
      if (!group || ['(なし)', '(session)'].includes(group) || (by === 'agent_cli' && site(group) === 'other')) group = 'other';
      if (by === 'workload' && group === 'flow') group = 'workflow';
      if (by === 'workload' && group === 'routine') group = 'task';
      if (!groups.has(group)) groups.set(group, { group, runs: 0, measured_in: 0, measured_out: 0, estimated_tokens: 0, unmeasured_runs: 0 });
      const total = groups.get(group);
      for (const key of ['runs', 'measured_in', 'measured_out', 'estimated_tokens', 'unmeasured_runs']) total[key] += Number(row[key]) || 0;
    }
    return [...groups.values()].sort((a, b) => Number(a.group === 'other') - Number(b.group === 'other'));
  }
  const api = { WORKLOAD_LABEL, site, breakdown };
  if (typeof module !== 'undefined') module.exports = api;
  else global.UsagePresentation = api;
}(typeof window === 'undefined' ? globalThis : window));
