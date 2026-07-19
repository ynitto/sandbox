'use strict';

(function expose(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ParticipationModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const TERMINAL_FLOW = new Set(['done', 'failed', 'canceled']);

  function flowCandidates(runs, context = {}) {
    const out = [];
    for (const run of runs || []) {
      if (!run || TERMINAL_FLOW.has(String(run.status || ''))) continue;
      const available = Object.values(run.nodes || {}).filter((node) => node.state === 'pending').length;
      if (!available) continue;
      out.push({
        key: `flow:${run.runId}`,
        workload: 'flow',
        title: run.taskId || run.runId,
        goal: run.request || '',
        context: context.projectName || '',
        available,
        busDir: context.busDir || '',
        projectDir: context.projectDir || '',
        runId: run.runId,
      });
    }
    return out;
  }

  function amigosCandidates(overview) {
    const out = [];
    const terminal = new Set(['done', 'failed', 'cancelled']);
    for (const mission of (overview && overview.missions) || []) {
      if (!mission || terminal.has(String(mission.phase || '')) || !mission.home) continue;
      for (const role of mission.roles || []) {
        if (!role || role.node) continue;
        out.push({
          key: `amigos:${mission.id}:${role.id}`,
          workload: 'amigos',
          title: role.displayName || role.title || role.id,
          goal: role.responsibility || mission.goal || '',
          context: mission.title || mission.id,
          home: mission.home,
          missionId: mission.id,
          roleId: role.id,
          actionLabel: mission.assignmentPolicy === 'owner-picks' ? '参加を申し込む' : '参加する',
        });
      }
    }
    return out;
  }

  return { flowCandidates, amigosCandidates };
});
