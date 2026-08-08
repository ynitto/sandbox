'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('participation', {
      render: feature.render,
      refresh: feature.refresh,
      // 領域ナビの出し分けと件数バッジ。募集も引き受けた仕事も無い端末では領域ごと出さない
      // （開いても空の画面へ誘導しない）。判定はこの feature が持つ——core は
      // 何が「募集」なのかを知らないままでいられる。
      //
      // **引き受けたものが 1 件でもあれば、募集が尽きても領域は残す。** 最後の 1 件を
      // 引き受けた瞬間に領域ごと消えると、いま起こしたばかりの仕事の結果を追えなくなる。
      available: () => feature.candidatesFromState().length > 0 || feature.joinedRecords().length > 0,
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
      <p class="muted">プロジェクト作業・ミッション・よその端末からの依頼に、この端末から参加できます。</p>
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-area="participation">参加できる仕事を見る</button>
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
    portalCardHtml, joinedRecords, statusHtml, renderStatus, deliveryLabel,
  };
});
