'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('participation', {
      render: feature.render,
      refresh: feature.refresh,
      // **左メニューには常に出す。** 以前は募集が 0 件のとき領域ごと隠していたが、
      // 出たり消えたりするメニューは「さっきあった項目が無い」と探させるだけで、
      // 募集が無いこと自体も人が知りたい情報である（画面がそれを一言で言う）。
      available: () => true,
      badge: () => {
        const n = feature.candidatesFromState().length;
        return n ? { text: String(n), title: `参加できる仕事 ${n} 件` } : null;
      },
    });
    root.registerFeatureTab('participation-status', { render: feature.renderStatus });
  }
  // ホーム（ポータル）へのサマリーカード。募集が無ければ '' を返してカードごと出さない
  // （参加タブの「候補が無ければ隠す」と同じ判断を 1 か所＝この feature が持つ）。
  if (typeof root.registerPortalCard === 'function') {
    root.registerPortalCard('participation', { order: 40, html: feature.portalCardHtml });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, (root) => {
  const statuses = {};
  let currentCandidates = [];
  let allProjectFlowCandidates = null;
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const escHtml = (value) => String(value == null ? '' : value)
    .replace(/[&<>"']/g, (char) => ESC[char]);

  // カードは見出し＋短い説明＋参加先＋ボタン。説明の全文は描画するがカード内で高さを
  // 抑えるため、Markdown の構造を保ったまま先頭数行だけを見比べられる。
  function candidateMeta(candidate) {
    if (candidate.workload === 'flow') {
      const n = Number(candidate.available || 0);
      return [candidate.context, n ? `未着手の工程 ${n} 件` : ''].filter(Boolean).join(' ・ ');
    }
    return candidate.context || '';
  }

  function participationHtml(candidates, statuses, formatProse = root.proseHtml) {
    if (!(candidates || []).length) {
      return '<div class="participation-empty"><strong>現在参加できる仕事はありません</strong><p>新しい募集が見つかると、ここに表示されます。</p></div>';
    }
    return `<div class="participation-grid">${candidates.map((candidate) => {
      const status = (statuses || {})[candidate.key] || {};
      const type = candidate.workload === 'amigos'
        ? 'ミッション'
        : candidate.workload === 'board' ? 'よその端末からの依頼' : 'プロジェクト作業';
      const meta = candidateMeta(candidate);
      const goal = String(candidate.goal || '').trim();
      const description = goal
        ? (typeof formatProse === 'function'
            ? formatProse(goal)
            : `<div class="md"><p>${escHtml(goal)}</p></div>`)
        : '';
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
        ${description ? `<div class="participation-description">${description}</div>` : ''}
        ${meta ? `<p class="participation-context">${escHtml(meta)}</p>` : ''}
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

  async function loadFlowCandidates(projects, api, model) {
    const results = await Promise.allSettled((projects || [])
      .filter((project) => project && project.exists !== false && project.isProject)
      .map(async (summary) => {
        const project = await api.readProject(summary.dir);
        if (!project || !project.busDir) return [];
        const flow = await api.flowRuns(project.dir, project.busDir);
        const scope = encodeURIComponent(project.busDir);
        return model.flowCandidates((flow && flow.runs) || [], {
          busDir: project.busDir,
          projectDir: project.workspace || project.dir || summary.dir,
          projectName: summary.charterName || summary.name || project.name || '',
        }).map((candidate) => ({ ...candidate, key: `${candidate.key}@${scope}` }));
      }));
    const candidates = results.flatMap((result) => result.status === 'fulfilled' ? result.value : []);
    return [...new Map(candidates.map((candidate) => [candidate.key, candidate])).values()];
  }

  async function refresh() {
    const appState = typeof state !== 'undefined' ? state : (root.state || {});
    const model = root.ParticipationModel;
    if (!model || !root.api || !root.api.readProject || !root.api.flowRuns) return;
    allProjectFlowCandidates = await loadFlowCandidates(
      (appState.discovery && appState.discovery.projects) || [], root.api, model
    );
  }

  function candidatesFromState() {
    const appState = typeof state !== 'undefined' ? state : (root.state || {});
    const model = root.ParticipationModel;
    if (!model) return [];
    const project = appState.project || {};
    const projectNameNode = root.document && root.document.getElementById('project-name');
    const projectName = project.name || project.charterName
      || (projectNameNode && projectNameNode.textContent) || '';
    const selectedProjectFlow = project.busDir
      ? model.flowCandidates(appState.flowRuns || [], {
          busDir: project.busDir,
          projectDir: project.workspace || project.dir || appState.selectedDir || '',
          projectName,
        })
      : [];
    const flow = allProjectFlowCandidates === null ? selectedProjectFlow : allProjectFlowCandidates;
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

  // この端末が引き受けた（引き受けを依頼した）仕事。**新しい取得経路は作らない**——
  // 板の公示（state.boardViews）・投函した指示の受理状況（state.boardCommands）・
  // 板が返す自分の入札（board.myBids）という、参加画面が既に読んでいるものだけで組む。
  function joinedRecords() {
    const appState = typeof state !== 'undefined' ? state : (root.state || {});
    const board = appState.boardStatus || null;
    const commands = appState.boardCommands || {};
    const mine = new Set(((board && board.myBids) || []).map(String));
    const out = [];
    for (const view of appState.boardViews || []) {
      if (!view || view.target !== 'board') continue;
      const sent = commands[view.id];
      if (!mine.has(String(view.id)) && !sent) continue;
      out.push({
        id: view.id,
        title: view.title || view.id,
        goal: view.goal || '',
        phase: view.phase || '',
        kind: view.workload === 'amigos' ? 'ミッションの依頼' : 'プロジェクト作業の依頼',
        awarded: mine.has(String(view.id)),
        sent: sent || null,
      });
    }
    return out;
  }

  // 指示の届き方（送信済み → 受理済み → 失敗）。押しても何も起きない、を可視化する。
  function deliveryLabel(record) {
    if (record.sent && record.sent.state === 'error') {
      return { text: `指示が通りませんでした: ${record.sent.error || ''}`, tone: 'is-error' };
    }
    if (record.awarded) return { text: 'この端末が引き受けています', tone: '' };
    if (record.sent && record.sent.state === 'pending') return { text: '引き受けを依頼済み（受理待ち）', tone: '' };
    if (record.sent && record.sent.state === 'done') return { text: '引き受けの依頼は受理されました', tone: '' };
    return { text: '', tone: '' };
  }

  const PHASE_LABELS = {
    open: '募集中', bidding: '入札中', awarded: '担当が決まりました',
    running: '実行中', done: '完了', failed: '失敗', cancelled: '中止',
  };

  // 「参加の状況」タブ。参加したあと何が起きたかを、参加したのと同じ領域で追えるようにする。
  function statusHtml(records, statuses) {
    const session = Object.entries(statuses || {})
      .filter(([, s]) => s && (s.joined || s.error))
      .map(([key, s]) => ({ key, ...s }));
    if (!records.length && !session.length) {
      return '<div class="participation-empty"><strong>まだ引き受けた仕事はありません</strong>'
        + '<p>「参加できる仕事」から引き受けると、ここで結果を追えます。</p></div>';
    }
    const rows = records.map((r) => {
      const delivery = deliveryLabel(r);
      return `<article class="participation-card">
        <div class="participation-card-heading">
          <span class="participation-type">${escHtml(r.kind)}</span>
          <h3>${escHtml(r.title)}</h3>
        </div>
        ${r.goal ? `<p class="participation-goal">${escHtml(r.goal)}</p>` : ''}
        <p class="participation-context">状態: ${escHtml(PHASE_LABELS[r.phase] || r.phase || '不明')}</p>
        ${delivery.text
          ? `<p class="participation-feedback ${delivery.tone}" role="status">${escHtml(delivery.text)}</p>`
          : ''}
      </article>`;
    }).join('');
    const sessionRows = session.map((s) => `<li class="${s.error ? 'is-error' : ''}">
      ${escHtml(s.error || s.message || '')}</li>`).join('');
    return `${records.length ? `<div class="participation-grid">${rows}</div>` : ''}
      ${session.length
        ? `<section class="participation-session">
            <h3>この画面から起こした参加</h3>
            <ul class="participation-session-list">${sessionRows}</ul>
          </section>`
        : ''}`;
  }

  function renderStatus() {
    if (!root.document) return;
    const pane = root.document.getElementById('tab-participation-status');
    if (!pane) return;
    pane.innerHTML = `<section class="participation-page" aria-labelledby="participation-status-title">
      <header class="area-page-header">
        <h2 id="participation-status-title">参加の状況</h2>
        <p class="muted">この端末が引き受けた仕事と、送った引き受けの依頼が届いたかを確認します。</p>
      </header>
      ${statusHtml(joinedRecords(), statuses)}
    </section>`;
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
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-area="participation">開く</button>
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
    // 募集が 0 件でもタブは出す（領域と同じ判断）。**空であることも画面が言う**——
    // タブごと消えると、募集が無いのか自分が見落としたのかを人が確かめられない。
    setVisibility(button, pane, true);
    pane.innerHTML = `<section class="participation-page" aria-labelledby="participation-title">
      <header class="participation-header">
        <div>
          <span class="summary-kicker">この端末で手伝う</span>
          <h2 id="participation-title">参加できる仕事</h2>
          <p>自動実行を有効にしている端末は自動で参加します。必要なときだけ手動で参加してください。</p>
        </div>
      </header>
      <details class="participation-criteria" data-ui-key="participation-criteria">
        <summary>ここに何が出るか</summary>
        <ul>
          <li><strong>プロジェクト作業</strong>: 選択中のプロジェクトの実行のうち、まだ誰も取っていない工程が残っているもの。
            実行中の run も出ます——動いていること自体は「この端末が手伝える工程がある」ことと矛盾しません。</li>
          <li><strong>ミッション</strong>: 担当端末が決まっていない役割。</li>
          <li><strong>よその端末からの依頼</strong>: 委譲公示板で募集中の依頼。</li>
        </ul>
      </details>
      ${participationHtml(currentCandidates, statuses)}
    </section>`;
    wire(pane);
  }

  return {
    participationHtml, joinCandidate, loadFlowCandidates, candidatesFromState, refresh, render, escHtml,
    portalCardHtml, joinedRecords, statusHtml, renderStatus, deliveryLabel,
  };
});
