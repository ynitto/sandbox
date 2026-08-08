'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('participation', { render: feature.render, refresh: feature.refresh });
  }
  // ホーム（ポータル）へのサマリーカード。募集が無ければ '' を返してカードごと出さない
  // （参加タブの「候補が無ければ隠す」と同じ判断を 1 か所＝この feature が持つ）。
  if (typeof root.registerPortalCard === 'function') {
    root.registerPortalCard('participation', { order: 40, html: feature.portalCardHtml });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, (root) => {
  const statuses = {};
  let currentCandidates = [];
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const escHtml = (value) => String(value == null ? '' : value)
    .replace(/[&<>"']/g, (char) => ESC[char]);

  function participationHtml(candidates, statuses) {
    if (!(candidates || []).length) {
      return '<div class="participation-empty"><strong>現在参加できる仕事はありません</strong><p>新しい募集が見つかると、ここに表示されます。</p></div>';
    }
    return `<div class="participation-grid">${candidates.map((candidate) => {
      const status = (statuses || {})[candidate.key] || {};
      const type = candidate.workload === 'amigos'
        ? 'ミッション'
        : candidate.workload === 'board' ? 'よその端末からの依頼' : 'プロジェクト作業';
      const detail = candidate.workload === 'flow' && candidate.available > 1
        ? `実行できる作業 ${candidate.available} 件`
        : candidate.context || '';
      // 引き受けられない端末では**理由を添えて押させない**。押せるのに何も起きない
      // 状態を作らないのが、この画面に板を載せる条件だった。
      const joined = status.joined || candidate.joined;
      const blocked = !!candidate.disabled;
      const label = status.busy
        ? '参加しています…'
        : joined ? '参加を依頼済み' : candidate.actionLabel || '参加する';
      const feedback = status.error
        ? `<p class="participation-feedback is-error" role="alert">${escHtml(status.error)}</p>`
        : status.message
        ? `<p class="participation-feedback" role="status">${escHtml(status.message)}</p>`
        : blocked
        ? `<p class="participation-feedback" role="status">${escHtml(candidate.reason)}</p>`
        : '';
      return `<article class="participation-card">
        <div class="participation-card-heading">
          <span class="participation-type">${type}</span>
          <h3>${escHtml(candidate.title)}</h3>
        </div>
        ${candidate.goal ? `<p class="participation-goal">${escHtml(candidate.goal)}</p>` : ''}
        ${detail ? `<p class="participation-context">${escHtml(detail)}</p>` : ''}
        <div class="participation-card-action">
          <button type="button" class="primary-inline participation-join"
            data-participation-key="${escHtml(candidate.key)}"${status.busy || joined || blocked ? ' disabled' : ''}>${escHtml(label)}</button>
        </div>
        ${feedback}
      </article>`;
    }).join('')}</div>`;
  }

  async function joinCandidate(candidate, api) {
    if (candidate.workload === 'board') {
      // 板へ bid を直接書かず、この端末の常駐体へ「引き受ける」意思を渡す。
      // 入札の名義と lease は常駐体に一元化する——二重落札を防ぐ規則を UI に複製しない。
      await api.delegationNodeCommand({
        action: 'board-bid', id: candidate.id, boardRepo: candidate.boardRepo,
        reason: 'agent-dashboard の参加画面から引き受け',
      });
      return { message: '引き受けを依頼しました。落札すると「実行」に現れます。' };
    }
    if (candidate.workload === 'flow') {
      await api.participationFlowJoin({
        busDir: candidate.busDir,
        projectDir: candidate.projectDir,
        runId: candidate.runId,
      });
      return { message: '参加を開始しました。進行状況は「実行」で確認できます。' };
    }
    await api.amigosClaim(candidate.home, candidate.missionId, candidate.roleId);
    return {
      message: candidate.actionLabel === '参加を申し込む'
        ? '参加を申し込みました。決定されるとミッションに反映されます。'
        : '参加を依頼しました。ミッションへの反映をお待ちください。',
    };
  }

  function refresh() {}

  function candidatesFromState() {
    const appState = typeof state !== 'undefined' ? state : (root.state || {});
    const model = root.ParticipationModel;
    if (!model) return [];
    const project = appState.project || {};
    const projectNameNode = root.document && root.document.getElementById('project-name');
    const projectName = project.name || project.charterName
      || (projectNameNode && projectNameNode.textContent) || '';
    const flow = project.busDir
      ? model.flowCandidates(appState.flowRuns || [], {
          busDir: project.busDir,
          projectDir: project.workspace || project.dir || appState.selectedDir || '',
          projectName,
        })
      : [];
    const board = model.boardCandidates(appState.boardViews || [], {
      board: appState.boardStatus || null,
      commands: appState.boardCommands || {},
    });
    return [...flow, ...model.amigosCandidates(appState.amigos), ...board];
  }

  function setVisibility(button, pane, visible) {
    for (const element of [button, pane]) {
      element.hidden = !visible;
      element.classList.toggle('hidden', !visible);
    }
  }

  function wire(pane) {
    for (const button of pane.querySelectorAll('.participation-join')) {
      button.addEventListener('click', async () => {
        const candidate = currentCandidates.find((item) => item.key === button.dataset.participationKey);
        if (!candidate) return;
        statuses[candidate.key] = { busy: true };
        render();
        try {
          const result = await joinCandidate(candidate, root.api);
          statuses[candidate.key] = { joined: true, message: result.message };
        } catch (error) {
          statuses[candidate.key] = {
            error: error && error.message ? error.message : String(error),
          };
        }
        render();
      });
    }
  }

  // ホーム（ポータル）のカード。募集が無いときは '' で不参加（カードを出さない）。
  function portalCardHtml() {
    const count = candidatesFromState().length;
    if (!count) return '';
    return `<div class="portal-card-heading">
        <span class="summary-kicker">この端末で手伝う</span>
        <h3>参加できる仕事</h3>
      </div>
      <p class="portal-card-count"><strong>${count}</strong> 件の募集があります</p>
      <p class="muted">プロジェクト作業・ミッション・よその端末からの依頼に、この端末から参加できます。</p>
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-tab="participation">参加できる仕事を見る</button>
      </div>`;
  }

  function render() {
    if (!root.document) return;
    const pane = root.document.getElementById('tab-participation');
    const button = root.document.getElementById('tab-btn-participation');
    if (!pane || !button) return;
    currentCandidates = candidatesFromState();
    const keys = new Set(currentCandidates.map((candidate) => candidate.key));
    for (const key of Object.keys(statuses)) {
      if (!keys.has(key)) delete statuses[key];
    }
    const active = button.classList.contains('active');
    const visible = currentCandidates.length > 0 || active;
    setVisibility(button, pane, visible);
    if (!visible) {
      pane.innerHTML = '';
      return;
    }
    pane.innerHTML = `<section class="participation-page" aria-labelledby="participation-title">
      <header class="participation-header">
        <div>
          <span class="summary-kicker">この端末で手伝う</span>
          <h2 id="participation-title">参加できる仕事</h2>
          <p>自動実行を有効にしている端末は自動で参加します。必要なときだけ手動で参加してください。</p>
        </div>
      </header>
      ${participationHtml(currentCandidates, statuses)}
    </section>`;
    wire(pane);
  }

  return {
    participationHtml, joinCandidate, candidatesFromState, refresh, render, escHtml,
    portalCardHtml,
  };
});
