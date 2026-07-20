'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('participation', { render: feature.render, refresh: feature.refresh });
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
      const type = candidate.workload === 'amigos' ? 'ミッション' : 'プロジェクト作業';
      const detail = candidate.workload === 'flow' && candidate.available > 1
        ? `実行できる作業 ${candidate.available} 件`
        : candidate.context || '';
      const label = status.busy
        ? '参加しています…'
        : status.joined ? '参加を依頼済み' : candidate.actionLabel || '参加する';
      const feedback = status.error
        ? `<p class="participation-feedback is-error" role="alert">${escHtml(status.error)}</p>`
        : status.message
        ? `<p class="participation-feedback" role="status">${escHtml(status.message)}</p>`
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
            data-participation-key="${escHtml(candidate.key)}"${status.busy || status.joined ? ' disabled' : ''}>${escHtml(label)}</button>
        </div>
        ${feedback}
      </article>`;
    }).join('')}</div>`;
  }

  async function joinCandidate(candidate, api) {
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
    return [...flow, ...model.amigosCandidates(appState.amigos)];
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
  };
});
