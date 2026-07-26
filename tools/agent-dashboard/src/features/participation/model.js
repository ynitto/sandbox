'use strict';

(function expose(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ParticipationModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  // 'canceled' は語彙統一（W0-9）前に書かれた meta.json の旧綴り。読み取りだけ受け入れる。
  const TERMINAL_FLOW = new Set(['done', 'failed', 'cancelled', 'canceled']);

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

  // 委譲公示板（agent-board）の公示 → 参加候補（S8-3 の手動入札）。
  //
  // **手動入札は「自己抑制の上書き」**である。自動入札は担当リポジトリ・タグの照合で自分を
  // 抑えるので、条件を満たす公示は放っておいても拾われる。人が押す意味があるのは条件を
  // 満たさないが引き受けたいとき（手元にクローンが無い・タグ宣言漏れ）と owner-picks の応募。
  //
  // 押しても実行できない状態を作らないため、可否は `engine/status.json` の `board` ブロック
  // だけを根拠にする（dashboard が host.yaml と agent-flow の設定を自前で読み解くと、
  // 宣言の解釈が 2 実装になる）。
  function boardReason(board) {
    if (!board || !board.configured) {
      return 'この端末は委譲公示板に参加していません（常駐体の設定で board を宣言してください）';
    }
    if (!board.lastTick) {
      return '常駐体（agent-project serve）が動いていないため、指示を届けられません';
    }
    if (!(board.intakeProjects || []).length) {
      return 'この端末はまだ板の仕事を実行できません（プロジェクトを 1 つも持たない端末の'
        + '落札実行は未対応です）';
    }
    return '';
  }

  function boardCandidates(views, context = {}) {
    const board = context.board || null;
    const commands = context.commands || {};
    const reason = boardReason(board);
    const mine = new Set(((board && board.myBids) || []).map(String));
    const out = [];
    for (const view of views || []) {
      if (!view || view.target !== 'board') continue;
      if (view.phase !== 'open') continue;      // 終端・実行中の公示は候補にしない
      const key = `board:${view.id}`;
      const sent = commands[view.id];
      out.push({
        key,
        workload: 'board',
        title: view.title || view.id,
        goal: view.goal || '',
        context: view.workload === 'amigos' ? 'ミッションの依頼' : 'プロジェクト作業の依頼',
        id: view.id,
        boardRepo: view.boardRepo || '',
        actionLabel: view.bids_open ? '引き受けを申し込む' : '引き受ける',
        // 既に入札済み / 指示を投函済みなら押させない（二重投函は板の側で冪等だが、
        // 画面が「まだ何もしていない」ように見えるのを避ける）。
        joined: mine.has(String(view.id)) || (sent && sent.state !== 'error') || false,
        disabled: !!reason,
        reason,
      });
    }
    return out;
  }

  return { flowCandidates, amigosCandidates, boardCandidates, boardReason };
});
