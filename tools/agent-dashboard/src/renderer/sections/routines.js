'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 定常業務領域の「実行の記録」と「定常業務の設定」
// ---------------------------------------------------------------------------
//
// 定常業務（cowork / agent-loop）は、この dashboard 自身が実行側になる
// 唯一のワークロードである（README「定常業務だけのフォルダは別」）。だから領域として
// 「動かす（作業）」「動いた結果を追う（実行の記録）」「動かし方を決める（設定）」の
// 3 つが揃う。他の領域と違い、起動の導線がこの画面にあるのはそのためで、
// **実行エンジン（agent-project serve）を起こす経路はここにも無い**（設計 §2.1 の非目標）。
//
// 設定がここにあるのは、cowork 固有の設定（roots / loopProvider / 各コマンド）が
// 「端末のすべてのワークロードに効く」全体設定ではないため。全体設定には agent-control /
// agent-instructions / 予算のような**横断して効くもの**だけを残す。

// ---------------------------------------------------------------------------
// アドホック起動（M2）: 登録済みフォルダ + 自由な指示でエージェントCLIを起動する。
// 起動経路（CLI/モデル解決・共通指示の前置・履歴）は定常業務の実行と同じで、
// 項目（作業）を登録しなくても使える。起動先は登録済みフォルダからの選択のみ。
// ---------------------------------------------------------------------------

function routineAdhocDraft() {
  if (!state.routineAdhocDraft) state.routineAdhocDraft = { root: '', prompt: '', message: '', ok: true };
  return state.routineAdhocDraft;
}

function routineAdhocPanelHtml() {
  const roots = (state.cowork && state.cowork.roots) || [];
  const draft = routineAdhocDraft();
  const options = roots.map((r) =>
    `<option value="${esc(r)}"${r === draft.root ? ' selected' : ''}>${esc(r)}</option>`).join('');
  return `<section class="global-settings-card routine-adhoc" aria-labelledby="routine-adhoc-title">
    <h3 id="routine-adhoc-title">依頼内容</h3>
    <p class="muted">フォルダと依頼内容を指定して、AIエージェントを起動します。</p>
    ${roots.length ? `
      <div class="field">
        <select id="routine-adhoc-root" aria-label="起動するフォルダ">${options}</select>
      </div>
      <div class="field">
        <textarea id="routine-adhoc-prompt" rows="3" aria-label="AIへの依頼内容" placeholder="依頼内容">${esc(draft.prompt)}</textarea>
      </div>
      <div class="row">
        <button id="btn-routine-adhoc-run" class="primary">依頼する</button>
        <span id="routine-adhoc-meta" class="muted${draft.ok ? '' : ' sync-error'}">${esc(draft.message || '')}</span>
      </div>`
    : '<p class="muted">起動先にできる登録済みフォルダがありません。「全体設定」でフォルダを登録してください。</p>'}
  </section>`;
}

function bindRoutineAdhocPanel(root) {
  const draft = routineAdhocDraft();
  const rootSel = root.querySelector('#routine-adhoc-root');
  const promptEl = root.querySelector('#routine-adhoc-prompt');
  if (rootSel) rootSel.addEventListener('change', () => { draft.root = rootSel.value; });
  if (promptEl) promptEl.addEventListener('input', () => { draft.prompt = promptEl.value; });
  const run = root.querySelector('#btn-routine-adhoc-run');
  if (run) run.addEventListener('click', () => runRoutineAdhoc());
}

async function runRoutineAdhoc() {
  if (!api.coworkRunAdhoc) {
    toast('このビルドではアドホック起動に対応していません');
    return;
  }
  const draft = routineAdhocDraft();
  const rootSel = $('routine-adhoc-root');
  const promptEl = $('routine-adhoc-prompt');
  const rootDir = rootSel ? rootSel.value : '';
  const prompt = promptEl ? promptEl.value.trim() : '';
  if (!prompt) {
    toast('エージェントへの指示を入力してください');
    if (promptEl) promptEl.focus();
    return;
  }
  const btn = $('btn-routine-adhoc-run');
  if (btn) btn.disabled = true;
  const res = await api.coworkRunAdhoc(rootDir, prompt)
    .catch((err) => ({ ok: false, error: err.message || String(err) }));
  if (btn) btn.disabled = false;
  draft.root = rootDir;
  if (res && res.ok) {
    draft.prompt = '';
    draft.ok = true;
    draft.message = `${res.message || '起動しました'}（${new Date().toLocaleTimeString('ja-JP')}）`;
    toast('エージェントCLIを起動しました', true);
  } else {
    draft.ok = false;
    draft.message = `起動できませんでした: ${(res && (res.error || res.message)) || '原因不明'}`;
  }
  renderCowork();
}

function openRoutineAdhocDialog() {
  const dialog = $('dlg-routine-adhoc');
  const body = $('routine-adhoc-dialog-body');
  if (!dialog || !body) return;
  body.innerHTML = routineAdhocPanelHtml();
  bindRoutineAdhocPanel(body);
  const close = $('btn-routine-adhoc-close');
  if (close) close.onclick = () => dialog.close();
  if (!dialog.open) dialog.showModal();
  const prompt = body.querySelector('#routine-adhoc-prompt');
  if (prompt) prompt.focus();
}

// 実行の記録タブ。上にアドホック起動、下は左に作業、右にその作業の記録
// （この画面からの実行 + ログ）。見出しは領域ヘッダーとタブが担うので、ペインは中身から始める。
function renderRoutineRuns() {
  const pane = $('tab-routine-runs');
  if (!pane) return;
  const folder = selectedProjectFolder();
  const entries = coworkItemsForFolder(folder);
  const selected = state.coworkHistory ? state.coworkHistory.id : '';
  if (!entries.length) {
    pane.innerHTML = `<section class="routine-page">
      ${routineAdhocPanelHtml()}
      <div class="empty compact">まだ作業がありません。「作業」タブから追加すると、動かした記録をここで読めます。</div>
    </section>`;
    bindRoutineAdhocPanel(pane);
    return;
  }
  // 選択 UI は作業タブと同じ部品を使う。以前はここだけ独自の行を並べていて、作業タブの
  // カード（名前 ＋ 状態 ＋ 予定 ＋ 最終実行）と形が揃わず、同じ「作業を選ぶ」操作なのに
  // 別の画面に見えていた（検索も件数も無かった）。
  const picker = coworkRoutineSelectorHtml(
    entries.map((item, index) => ({ item, index })),
    selected,
    '記録を見る作業',
    'routine-runs-picker-label',
    'routine-runs'
  );
  pane.innerHTML = `<section class="routine-page">
    ${routineAdhocPanelHtml()}
    <div class="cowork-split-view">
      <section class="cowork-list-pane">${picker}</section>
      <section class="cowork-detail-pane" id="routine-runs-body">${coworkHistoryBodyHtml(state.coworkHistory)}</section>
    </div>
  </section>`;
  bindCoworkRoutineSelector(pane, (id) => {
    const item = entries.find((x) => x.id === id);
    loadCoworkHistory(id, item ? item.name : '');
  });
  bindCoworkHistoryBody(pane);
  bindRoutineAdhocPanel(pane);
}

// 定常業務の設定タブ。全体設定にあった「定常業務」節をそのまま領域の中へ移した。
// HTML の組み立ては元の関数（globalSettingsRoutineHtml）を使い回し、配線だけこちらが持つ
// （カード自身が見出しを持つので、ペイン側の見出しは足さない）。
function renderRoutineSettings() {
  const pane = $('tab-routine-settings');
  if (!pane) return;
  pane.innerHTML = `<section class="routine-page">${globalSettingsRoutineHtml()}</section>`;
  populateSettingsFields();
  setupRoutineSettings(pane);
}

// 全体設定ページの setupGlobalSettings から、定常業務に関わる配線だけを取り出したもの。
function setupRoutineSettings(root) {
  for (const el of root.querySelectorAll('[id^="cfg-"]')) {
    el.addEventListener('input', () => {
      state.globalSettingsDirty = true;
    });
    el.addEventListener('change', () => {
      state.globalSettingsDirty = true;
    });
  }
  const save = root.querySelector('#btn-save-routine-settings');
  if (save) {
    save.addEventListener('click', () =>
      guard('定常業務設定の保存', () => saveGlobalSettingsSection('routine')));
  }
  const add = root.querySelector('#btn-settings-cowork-add-root');
  if (add) add.addEventListener('click', () => addCoworkRoot());
  for (const btn of root.querySelectorAll('[data-drop-cowork-root]')) {
    btn.addEventListener('click', () => dropCoworkRoot(btn.dataset.dropCoworkRoot));
  }
  const open = root.querySelector('#btn-settings-cowork-open');
  if (open) open.addEventListener('click', openCoworkFromSettings);
}

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// routine-agent 端末
// ---------------------------------------------------------------------------

function stripAnsi(s) {
  return String(s || '')
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '')
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, '')
    .replace(/\r/g, '');
}

function setRoutineAgentDialogVisible(show) {
  const dialog = $('dlg-routine-agent');
  if (!dialog) return;
  if (show && !dialog.open) dialog.showModal();
  if (!show && dialog.open) dialog.close();
}

function setupRoutineAgentDialog() {
  const dialog = $('dlg-routine-agent');
  if (!dialog) return;
  $('btn-routine-agent-refresh').addEventListener('click', () => {
    const term = state.routineAgentTerm;
    if (term) openRoutineAgentTerminal({ id: term.id, repo: term.repo, name: term.name, force: true });
  });
  $('btn-routine-agent-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    stopRoutineAgentCapturePoll();
    routineAgentCancelWait();
    state.routineAgentTerm = null;
  });
}

function stopRoutineAgentCapturePoll() {
  if (state.routineAgentTimer) {
    clearInterval(state.routineAgentTimer);
    state.routineAgentTimer = null;
  }
}

function routineAgentCaptureSec() {
  const n = Number(state.config && state.config.routines && state.config.routines.captureSec);
  return Number.isFinite(n) && n > 0 ? n : 2;
}

function startRoutineAgentCapturePoll() {
  stopRoutineAgentCapturePoll();
  if (!state.routineAgentTerm || !state.routineAgentTerm.target) return;
  const tick = async () => {
    const dialog = $('dlg-routine-agent');
    if (!dialog || !dialog.open || !state.routineAgentTerm || !state.routineAgentTerm.target) return;
    if (!api.routineAgentCapture) return;
    const target = state.routineAgentTerm.target;
    const repo = state.routineAgentTerm.repo;
    const res = await api.routineAgentCapture({ target, lines: 200, repo }).catch((err) => ({ ok: false, error: err.message, text: '' }));
    if (!state.routineAgentTerm || state.routineAgentTerm.target !== target) return;
    state.routineAgentTerm.text = res && res.text != null ? res.text : '';
    state.routineAgentTerm.error = res && res.ok === false ? (res.error || 'capture に失敗') : '';
    state.routineAgentTerm.at = Date.now();
    const pre = $('routine-agent-capture');
    const meta = $('routine-agent-term-meta');
    if (pre) {
      const next = stripAnsi(state.routineAgentTerm.text);
      if (pre.textContent !== next) {
        const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
        pre.textContent = next;
        if (stick) pre.scrollTop = pre.scrollHeight;
      }
    }
    if (meta) {
      meta.textContent = state.routineAgentTerm.error
        ? state.routineAgentTerm.error
        : `${new Date(state.routineAgentTerm.at).toLocaleTimeString('ja-JP')} 時点 ／ エージェントの画面をそのまま映しています（ここには入力できません）`;
      meta.classList.toggle('sync-error', !!state.routineAgentTerm.error);
    }
    // 構造化状態（最終実行時刻・alive/busy）は capture より低頻度で追従する
    state.routineAgentTerm.stateTick = (state.routineAgentTerm.stateTick || 0) + 1;
    if (state.routineAgentTerm.stateTick % 5 === 1) refreshRoutineAgentState();
  };
  tick();
  state.routineAgentTimer = setInterval(tick, routineAgentCaptureSec() * 1000);
}

function routineAgentRoutineSession(items, routineName) {
  const sessions = Array.isArray(items) ? items : [];
  const wanted = String(routineName || '').trim().toLocaleLowerCase('ja-JP');
  if (!wanted) return sessions.length === 1 ? sessions[0] : null;
  const named = sessions.filter((item) => String(item && item.name || '').trim());
  const exact = named.find((item) => String(item.name).trim().toLocaleLowerCase('ja-JP') === wanted);
  if (exact) return exact;
  const partial = named.find((item) => {
    const name = String(item.name).trim().toLocaleLowerCase('ja-JP');
    return name.includes(wanted) || wanted.includes(name);
  });
  if (partial) return partial;
  return sessions.length === 1 ? sessions[0] : null;
}

async function openRoutineAgentTerminal({ id, repo, name, force = false } = {}) {
  if (!api.routineAgentListSessions) {
    toast('このビルドでは実行状況の表示に対応していません');
    return;
  }
  routineAgentCancelWait();
  stopRoutineAgentCapturePoll();
  const repoKey = coworkPathKey(repo);
  const cached = state.routineAgentCache.get(repoKey) || null;
  const cacheFresh = !!state.routineAgentCache.peek(repoKey);
  if (repoKey && id) state.coworkSelections[repoKey] = String(id);
  const previous = state.routineAgentTerm
    && coworkPathKey(state.routineAgentTerm.repo) === repoKey
    && String(state.routineAgentTerm.id || '') === String(id || '')
    ? state.routineAgentTerm
    : null;
  const cachedItems = cached ? cached.items || [] : [];
  const cachedSession = routineAgentRoutineSession(cachedItems, name);
  const cachedTarget = cachedSession ? cachedSession.target : '';
  state.routineAgentTerm = {
    id: id || '',
    repo: repo || '',
    name: name || '',
    target: cachedTarget,
    session: (cachedSession && cachedSession.session) || '',
    items: cachedItems,
    text: previous ? previous.text || '' : '',
    summary: state.routineAgentStateCache.get(repoKey) || null,
    send: null,
    error: cacheFresh
      ? (cachedSession ? '' : 'この定常業務に対応するエージェントは見つかりませんでした')
      : '動いているエージェントを探しています…',
    at: Date.now(),
  };
  setRoutineAgentDialogVisible(true);
  renderRoutineAgentTerminal();
  if (cacheFresh && !force) {
    if (state.routineAgentTerm.target) startRoutineAgentCapturePoll();
    refreshRoutineAgentState();
    return;
  }
  const listed = await state.routineAgentCache.load(repoKey, async () => {
    const result = await guard('tmux セッション', () => api.routineAgentListSessions({ repo: repo || '' }));
    if (!result) return { items: [], error: 'エージェントの一覧を取得できませんでした' };
    return { items: result.items || [], error: result.error || '' };
  }, { force });
  if (!state.routineAgentTerm || coworkPathKey(state.routineAgentTerm.repo) !== repoKey || state.routineAgentTerm.id !== (id || '')) return;
  if (!listed || (!listed.items && listed.error)) {
    state.routineAgentTerm.error = 'エージェントの一覧を取得できませんでした';
    renderRoutineAgentTerminal();
    return;
  }
  const items = listed.items || [];
  const first = routineAgentRoutineSession(items, name);
  state.routineAgentTerm.items = items;
  state.routineAgentTerm.session = first ? first.session : '';
  state.routineAgentTerm.target = first ? first.target : '';
  state.routineAgentTerm.error = first
    ? ''
    : (listed.error || 'この定常業務に対応するエージェントは見つかりませんでした');
  renderRoutineAgentTerminal();
  refreshRoutineAgentState({ force: true });
  if (first) startRoutineAgentCapturePoll();
}

function renderRoutineAgentTerminal() {
  const ui = captureUiState();
  const el = $('routine-agent-dialog-body');
  if (!el) return;
  const term = state.routineAgentTerm;
  if (!term) {
    el.innerHTML = '';
    return;
  }
  const folder = selectedProjectFolder();
  const entries = coworkHasProjectConfig(state.cowork, folder) ? coworkVisibleEntries(coworkDraft(), folder) : [];
  const selected = entries.find(({ item, index }) => coworkEntryId(item, index) === String(term.id));
  if (!selected) {
    stopRoutineAgentCapturePoll();
    routineAgentCancelWait();
    state.routineAgentTerm = null;
    setRoutineAgentDialogVisible(false);
    el.innerHTML = '';
    return;
  }
  const selectedId = coworkEntryId(selected.item, selected.index);
  const selectedItem = selected.item;
  const selectedState = (selectedItem && selectedItem.state) || {};
  const selectedStatus = selectedState.running ? 'running' : (selectedState.status || 'unknown');
  el.innerHTML = `
    <div class="routine-agent-term">
      <section class="routine-agent-overview" aria-labelledby="routine-agent-selected-title">
        <div class="routine-agent-overview-heading">
          <div><span class="summary-kicker">選択中</span><h3 id="routine-agent-selected-title">${esc(term.name || (selectedItem && selectedItem.name) || selectedId || '定常業務')}</h3></div>
          <span class="status-chip ${coworkStatusClass(selectedStatus)}">${esc(statusLabel(selectedStatus))}</span>
        </div>
        <div class="routine-agent-overview-facts">
          <div><span>実行予定</span><strong>${esc((selectedItem && selectedItem.schedule) || '手動実行')}</strong></div>
          <div><span>最終確認</span><strong>${term.at ? esc(fmtAgo(new Date(term.at).toISOString())) : '未確認'}</strong></div>
          <div><span>対象</span><strong title="${esc(term.repo || '')}">${esc(coworkRepoLabel(term.repo))}</strong></div>
        </div>
        <div id="routine-agent-state" class="routine-agent-state">${routineAgentStateHtml(term.summary, term.name)}</div>
        <div class="routine-agent-primary-actions">
          ${term.name ? `<button id="btn-routine-agent-send-periodic" class="primary" title="設定されたこの定常業務を次回予定を待たずに送ります">今すぐ実行</button>` : ''}
          <span id="routine-agent-send-meta" class="muted">${esc((term.send && term.send.message) || '')}</span>
        </div>
      </section>
      <section class="routine-agent-panel" aria-labelledby="routine-agent-agent-title">
        <div class="routine-agent-term-toolbar">
          <div><span class="summary-kicker">対応するエージェント</span><h3 id="routine-agent-agent-title">エージェントの画面と個別指示</h3></div>
          <span id="routine-agent-term-meta" class="muted">${esc(term.error || 'エージェントの画面を表示しています')}</span>
        </div>
        <div class="routine-agent-send">
          <input id="routine-agent-send-text" type="text" aria-label="エージェントへの個別指示" placeholder="エージェントへの個別指示" value="${esc((term.send && term.send.text) || '')}">
          <button id="btn-routine-agent-send">送る</button>
          <button id="btn-routine-agent-send-cancel" class="hidden">送るのをやめる</button>
        </div>
        <pre id="routine-agent-capture" class="routine-agent-capture mono" data-ui-scroll-key aria-live="polite">${esc(stripAnsi(term.text || (term.error && !term.target ? '' : '…')))}</pre>
      </section>
      <section class="routine-agent-panel" aria-labelledby="routine-agent-queue-title">
        <div class="routine-agent-term-toolbar">
          <div><span class="summary-kicker">あとで処理する依頼</span><h3 id="routine-agent-queue-title">依頼を積む</h3></div>
          <span id="routine-agent-queue-meta" class="muted">待ち行列を読み込んでいます…</span>
        </div>
        <p class="muted">エージェントが応答中でも投函できます。積んだ依頼は手が空いた順に処理されます（待機は失敗ではありません）。</p>
        <div class="routine-agent-send">
          <select id="routine-agent-queue-agent" aria-label="宛先エージェント"></select>
          <input id="routine-agent-queue-subject" type="text" aria-label="件名" placeholder="件名（任意）">
          <input id="routine-agent-queue-body" type="text" aria-label="依頼の本文" placeholder="依頼の本文">
          <button id="btn-routine-agent-queue-post">投函</button>
        </div>
        <div id="routine-agent-queue-list"></div>
      </section>
    </div>`;
  restoreUiState(ui);
  const sendBtn = $('btn-routine-agent-send');
  const sendText = $('routine-agent-send-text');
  if (sendBtn && sendText) {
    sendBtn.addEventListener('click', () => routineAgentSendPrompt(sendText.value));
    sendText.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') routineAgentSendPrompt(sendText.value);
    });
  }
  const periodicBtn = $('btn-routine-agent-send-periodic');
  if (periodicBtn) periodicBtn.addEventListener('click', () => routineAgentSendPrompt(term.name));
  const cancelBtn = $('btn-routine-agent-send-cancel');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      routineAgentCancelWait();
      updateRoutineAgentSendMeta();
    });
  }
  const queuePost = $('btn-routine-agent-queue-post');
  if (queuePost) queuePost.addEventListener('click', () => routineAgentQueuePost());
  updateRoutineAgentSendMeta();
  refreshRoutineAgentQueue();
}

// ---------------------------------------------------------------------------
// routine-agent 構造化状態と復旧送信
// ---------------------------------------------------------------------------

function routineAgentStateHtml(summary, routineName = '') {
  const rows = [];
  const wanted = String(routineName || '').trim().toLowerCase();
  for (const d of (summary && summary.daemons) || []) {
    for (const s of d.sessions || []) {
      const sessionName = String(s.name || s.pane || '').trim().toLowerCase();
      if (wanted && sessionName !== wanted && !sessionName.includes(wanted)) continue;
      const label = !s.alive ? '止まっています' : (s.busy ? '応答中' : '待機中');
      const cls = !s.alive ? 'st-failed' : (s.busy ? 'st-running' : 'st-ready');
      const lastSent = s.lastSentAt
        ? `${fmtTime(new Date(s.lastSentAt * 1000).toISOString())}${s.lastSendOk === false ? '（送信に失敗）' : ''}`
        : 'まだありません';
      rows.push(`<tr>
        <td>${esc(s.name || s.pane)}</td>
        <td><span class="status-chip ${cls}">${label}</span></td>
        <td class="mono">${esc(lastSent)}</td>
      </tr>`);
    }
  }
  if (!summary) return '<p class="muted">状態を読み込んでいます…</p>';
  if (!rows.length) {
    return '<p class="muted">このフォルダで自動実行しているものは見つかりません（routine-agent が起動していないか、別のフォルダで動いています）。</p>';
  }
  return `<table class="list">
    <tr><th>予定の名前</th><th>いまの状態</th><th>最後に送った時刻</th></tr>${rows.join('')}
  </table>
  <p class="muted">「予定の名前」は設定ファイル（agent-loop.yaml の prompts）で付けた名前です。名前を送ると、そこに書かれた本文がエージェントへ送られます。</p>`;
}

async function refreshRoutineAgentState({ force = false } = {}) {
  const term = state.routineAgentTerm;
  if (!term || !api.routineAgentState) return;
  const repoKey = coworkPathKey(term.repo);
  const cached = state.routineAgentStateCache.peek(repoKey);
  if (!force && cached) {
    term.summary = cached;
    const el = $('routine-agent-state');
    if (el) el.innerHTML = routineAgentStateHtml(term.summary, term.name);
    return;
  }
  const res = await state.routineAgentStateCache.load(
    repoKey,
    () => api.routineAgentState({ repo: term.repo }).catch(() => null),
    { force }
  );
  if (state.routineAgentTerm !== term) return;
  term.summary = res && res.ok ? res : { daemons: [] };
  state.routineAgentStateCache.set(repoKey, term.summary);
  const el = $('routine-agent-state');
  if (el) el.innerHTML = routineAgentStateHtml(term.summary, term.name);
}

function routineAgentCancelWait() {
  if (state.routineAgentSendTimer) {
    clearTimeout(state.routineAgentSendTimer);
    state.routineAgentSendTimer = null;
  }
  const term = state.routineAgentTerm;
  if (term && term.send && term.send.phase === 'waiting') {
    term.send = { text: term.send.text, phase: '', message: '' };
  }
}

function updateRoutineAgentSendMeta() {
  const term = state.routineAgentTerm;
  const meta = $('routine-agent-send-meta');
  if (!meta) return;
  const send = (term && term.send) || {};
  meta.textContent = send.message || '';
  meta.classList.toggle('sync-error', send.phase === 'error');
  const cancel = $('btn-routine-agent-send-cancel');
  if (cancel) cancel.classList.toggle('hidden', send.phase !== 'waiting');
}

const ROUTINE_AGENT_SEND_RETRY_SEC = 15;
// 応答中の自動再送は上限を設ける。ペインが固まっている・スロットが解放されないままだと
// 「応答中」が永久に続き、待っているのか壊れているのか区別できなくなるため。
const ROUTINE_AGENT_SEND_MAX_RETRY = 8;

async function routineAgentSendPrompt(promptText, attempt = 1) {
  const term = state.routineAgentTerm;
  if (!term) return;
  if (!api.routineAgentSend) {
    toast('このビルドではエージェントへの送信に対応していません');
    return;
  }
  const text = String(promptText || '').trim();
  if (!text) {
    toast('送る指示を入力してください');
    return;
  }
  routineAgentCancelWait();
  term.send = { text, phase: 'sending', message: 'エージェントに送っています…' };
  updateRoutineAgentSendMeta();
  const res = await api.routineAgentSend({ repo: term.repo, target: term.target, prompt: text })
    .catch((err) => ({ ok: false, busy: false, error: err.message || String(err) }));
  if (state.routineAgentTerm !== term) return;
  if (res && res.ok) {
    term.send = { text: '', phase: 'ok', message: `送りました（${new Date().toLocaleTimeString('ja-JP')}）。下の画面に応答が出ます。` };
    const input = $('routine-agent-send-text');
    if (input) input.value = '';
    toast('エージェントに送りました', true);
    refreshRoutineAgentState();
  } else if (res && res.busy && attempt >= ROUTINE_AGENT_SEND_MAX_RETRY) {
    // 待ち続けても空かない = ペインが固まっているか、実行枠が解放されていない
    const waited = Math.round((ROUTINE_AGENT_SEND_MAX_RETRY * ROUTINE_AGENT_SEND_RETRY_SEC) / 60);
    term.send = {
      text,
      phase: 'error',
      message: `${waited} 分待っても応答中のままなので送信をやめました。下の画面でエージェントが止まっていないか確認してください（止まっている場合は実行枠が空かないため、他の予定も動きません）。`,
    };
  } else if (res && res.busy) {
    // busy 拒否は失敗ではなく「送信待機」— 完了を待って自動で再送する
    term.send = {
      text,
      phase: 'waiting',
      message: `エージェントが応答中です。手が空くまで待って自動で送ります（${ROUTINE_AGENT_SEND_RETRY_SEC} 秒ごとに再試行 ${attempt}/${ROUTINE_AGENT_SEND_MAX_RETRY}）。`,
    };
    state.routineAgentSendTimer = setTimeout(() => {
      state.routineAgentSendTimer = null;
      if (state.routineAgentTerm === term && term.send && term.send.phase === 'waiting') {
        routineAgentSendPrompt(text, attempt + 1);
      }
    }, ROUTINE_AGENT_SEND_RETRY_SEC * 1000);
  } else {
    term.send = { text, phase: 'error', message: `送れませんでした: ${(res && (res.error || res.detail)) || '原因不明'}` };
  }
  updateRoutineAgentSendMeta();
}

// ---------------------------------------------------------------------------
// メッセージキュー投函（M3）: agent-loop の受信ボックスへ依頼を積み、待ち行列を見せる。
// 復旧送信（routineAgentSendPrompt）と違い、応答中でも投函は受理される——busy は
// 失敗ではなく待機で、受信側が手すきになった順に処理する。
// ---------------------------------------------------------------------------

function routineAgentQueueListHtml(agents) {
  if (!agents) return '<p class="muted">待ち行列を読み込んでいます…</p>';
  if (!agents.length) {
    return '<p class="muted">投函先のエージェントがまだありません（このフォルダで agent-loop が一度も動いていないと空になります）。</p>';
  }
  const rows = [];
  for (const a of agents) {
    for (const m of a.pending) {
      rows.push(`<tr>
        <td>${esc(a.name)}</td>
        <td>${esc(m.from || '')}</td>
        <td title="${esc(m.body || '')}">${esc(m.subject || '（件名なし）')}</td>
        <td class="mono">${esc(fmtTime(m.createdAt))}</td>
        <td><span class="status-chip st-ready">待機中</span></td>
      </tr>`);
    }
  }
  const processed = agents.map((a) => `${a.name}: ${a.processed} 件`).join(' ／ ');
  return `${rows.length
    ? `<table class="list"><tr><th>宛先</th><th>差出人</th><th>件名</th><th>投函時刻</th><th>状態</th></tr>${rows.join('')}</table>`
    : '<p class="muted">待機中の依頼はありません。</p>'}
  <p class="muted">処理済み — ${esc(processed)}</p>`;
}

async function refreshRoutineAgentQueue() {
  const term = state.routineAgentTerm;
  if (!term || !api.routineAgentQueue) return;
  const res = await api.routineAgentQueue({ repo: term.repo }).catch(() => null);
  if (state.routineAgentTerm !== term) return;
  const agents = res && res.ok ? res.agents : null;
  state.routineAgentQueue = agents;
  const meta = $('routine-agent-queue-meta');
  if (meta) meta.textContent = agents ? '' : ((res && res.error) || '待ち行列を取得できませんでした');
  const sel = $('routine-agent-queue-agent');
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = (agents || [])
      .map((a) => `<option value="${esc(a.name)}">${esc(a.name)}（待機 ${a.pending.length} 件）</option>`)
      .join('');
    if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  const list = $('routine-agent-queue-list');
  if (list) list.innerHTML = routineAgentQueueListHtml(agents);
}

async function routineAgentQueuePost() {
  const term = state.routineAgentTerm;
  if (!term) return;
  if (!api.routineAgentQueueMessage) {
    toast('このビルドでは依頼の投函に対応していません');
    return;
  }
  const sel = $('routine-agent-queue-agent');
  const subjectEl = $('routine-agent-queue-subject');
  const bodyEl = $('routine-agent-queue-body');
  const agent = sel ? sel.value : '';
  const body = bodyEl ? bodyEl.value.trim() : '';
  if (!agent) {
    toast('宛先エージェントを選択してください');
    return;
  }
  if (!body) {
    toast('依頼の本文を入力してください');
    if (bodyEl) bodyEl.focus();
    return;
  }
  const btn = $('btn-routine-agent-queue-post');
  if (btn) btn.disabled = true;
  const res = await api.routineAgentQueueMessage({
    repo: term.repo, agent, subject: subjectEl ? subjectEl.value.trim() : '', body,
  }).catch((err) => ({ ok: false, error: err.message || String(err) }));
  if (btn) btn.disabled = false;
  const meta = $('routine-agent-queue-meta');
  if (res && res.ok) {
    if (bodyEl) bodyEl.value = '';
    if (subjectEl) subjectEl.value = '';
    if (meta) meta.textContent = `投函しました（${new Date().toLocaleTimeString('ja-JP')}）。手が空いた順に処理されます。`;
    toast('依頼を投函しました', true);
    refreshRoutineAgentQueue();
  } else if (meta) {
    meta.textContent = `投函できませんでした: ${(res && (res.error || res.detail)) || '原因不明'}`;
  }
}

async function openCoworkFromSettings() {
  if ($('dlg-technical-info').open) $('dlg-technical-info').close();
  await refreshCowork({ forceDiscover: true });
  state.coworkForcedOpen = true;
  updateCoworkTabVisibility();
  switchTab('cowork');
  renderCowork();
  if (!coworkVisibleEntries(coworkDraft(), selectedProjectFolder()).length) openCoworkWorkDialog(-1);
}

function openCoworkWorkDialog(index) {
  const editing = index >= 0 ? coworkDraft()[index] : null;
  const discovered = !!(editing && editing.source === 'discovered');
  const repo = selectedProjectFolder();
  if (!repo) {
    toast('プロジェクトを選択してください');
    return;
  }
  state.coworkEditIndex = index;
  const item = editing || { type: 'loop', repo };
  $('cowork-work-title').textContent = index >= 0 ? '作業を編集' : '作業を追加';
  $('cw-type').value = item.type || 'loop';
  $('cw-type').disabled = discovered;
  $('cw-name').value = item.name || item.id || '';
  $('cw-schedule').value = item.schedule || item.cron || '';
  // 発見項目のスケジュールは、書き戻せる物理フィールドがあるときだけ編集可:
  //   loop → 自身の scheduleKey / state-machine → 対となる routine-agent エントリの scheduleKey
  const pairedLoop = !!(item._src && item._src.loop);
  $('cw-schedule').disabled = !!(discovered && (
    item.type === 'loop'
      ? (item._src && item._src.scheduleKey === '')
      : (!pairedLoop || item._src.loop.scheduleKey === '')
  ));
  $('cw-prompt').value = item.prompt || '';
  $('cw-instruction').value = item.instruction || '';
  fillCoworkExecutionSelect(item);
  updateCoworkWorkFields();
  $('dlg-cowork-work').showModal();
}

// 実行エージェントの選択肢。既定（値 ''）は自動割り当てで、自動割り当てが今選ぶ具体的な
// エージェント・モデルをそのまま表示する。それ以外は全体設定の実行レベル構成に宣言された
// （実行レベル×候補）の組だけ——自由入力の組み合わせは実行資格の裏付けが無いので出さない。
function fillCoworkExecutionSelect(item) {
  const select = $('cw-execution');
  const tiers = Array.isArray(state.cowork && state.cowork.routineTiers) ? state.cowork.routineTiers : [];
  const auto = (item && item.autoExecution) || {};
  const autoLabel = `自動割り当て（現在: ${auto.agent_cli || '既定'} / ${auto.model || '既定モデル'}）`;
  select.innerHTML = [
    `<option value="">${esc(autoLabel)}</option>`,
    ...tiers.map((tier, index) =>
      `<option value="${index}">${esc(orchTierLabel(tier.id, tier.label))} — ${esc(tier.agent_cli || '既定')} / ${esc(tier.model || '既定')}</option>`),
  ].join('');
  const choice = item && item.executionChoice;
  const selected = choice ? tiers.findIndex((tier) => tier.id === choice.tier
    && String(tier.agent_cli || '') === String(choice.agent_cli || '')
    && String(tier.model || '') === String(choice.model || '')) : -1;
  select.value = selected >= 0 ? String(selected) : '';
  select.disabled = !tiers.length;
}

function coworkExecutionSelection() {
  const tiers = Array.isArray(state.cowork && state.cowork.routineTiers) ? state.cowork.routineTiers : [];
  const raw = $('cw-execution').value;
  if (raw === '') return null;
  const tier = tiers[Number(raw)];
  return tier ? { tier: tier.id, agent_cli: tier.agent_cli || '', model: tier.model || '' } : null;
}

function updateCoworkWorkFields() {
  const stateMachine = $('cw-type').value === 'state-machine';
  $('cw-prompt-field').hidden = stateMachine;
  $('cw-instruction-field').hidden = !stateMachine;
  $('cw-prompt').required = !stateMachine;
  $('cw-instruction').required = stateMachine && state.coworkEditIndex < 0;
}

async function applyCoworkWorkDialog() {
  const idx = state.coworkEditIndex;
  const existing = idx >= 0 ? coworkDraft()[idx] : null;
  const discovered = !!(existing && existing.source === 'discovered');
  const type = $('cw-type').value;
  const name = $('cw-name').value.trim();
  const prompt = $('cw-prompt').value.trim();
  const instruction = $('cw-instruction').value.trim();
  if (!name) { toast('名前を入力してください'); $('cw-name').focus(); return; }
  if (type === 'loop' && !prompt) { toast('プロンプトを入力してください'); $('cw-prompt').focus(); return; }
  if (type === 'state-machine' && !existing && !instruction) {
    toast('定型業務の手順を入力してください'); $('cw-instruction').focus(); return;
  }
  const repo = (existing && existing.repo) || selectedProjectFolder();
  const id = (existing && existing.id)
    || name.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '')
    || `cowork-${Date.now()}`;
  const machine = (existing && (existing.workflow || existing.file))
    || id.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '')
    || `routine-${Date.now()}`;
  const shouldGenerate = type === 'state-machine' && instruction
    && (!existing || instruction !== String(existing.instruction || ''));
  if (shouldGenerate) {
    const button = $('btn-cw-ok');
    button.disabled = true;
    const launched = await guard('定型業務の作成', () => api.coworkGenerateStateMachine({
      repo, name, machine, instruction,
    }));
    button.disabled = false;
    if (!launched || !launched.ok) {
      if (launched && launched.error) toast(`定型業務の作成を開始できませんでした: ${launched.error}`);
      return;
    }
  }
  const executionChoice = coworkExecutionSelection();
  let item;
  if (discovered) {
    item = {
      ...existing,
      name,
      schedule: $('cw-schedule').value.trim(),
      executionChoice,
      ...(type === 'loop' ? { prompt } : instruction ? { instruction } : {}),
    };
  } else {
    item = {
      ...(existing || {}),
      id,
      type,
      name,
      repo,
      schedule: $('cw-schedule').value.trim(),
      executionChoice,
      ...(type === 'loop' ? { prompt, instruction: '' } : { instruction, prompt: '', workflow: machine }),
      managed: true,
      source: 'config',
    };
  }
  if (idx >= 0) coworkDraft()[idx] = item;
  else coworkDraft().push(item);
  $('dlg-cowork-work').close();
  updateCoworkTabVisibility();
  renderCowork();
}

function openCoworkSaveDialog() {
  $('cw-save-branch').value = '';
  $('cw-save-create').checked = false;
  $('cw-save-push').checked = false;
  $('dlg-cowork-save').showModal();
}

async function saveCoworkDraft() {
  const payload = {
    items: coworkDraft(),
    branch: $('cw-save-branch').value.trim(),
    createBranch: $('cw-save-create').checked,
    push: $('cw-save-push').checked,
  };
  const res = await guard('作業の保存', () => api.coworkSaveWork(payload));
  if (!res) return;
  state.config = res.config;
  state.coworkDraft = null;
  state.coworkHistoryCache.clear();
  state.routineAgentCache.clear();
  state.routineAgentStateCache.clear();
  $('dlg-cowork-save').close();
  await refreshCowork({ forceDiscover: true });
  updateCoworkTabVisibility();
  renderCowork();
  const failed = (res.git || []).filter((x) => x.result && x.result.ok === false);
  const wbErrors = (res.writeback && res.writeback.errors) || [];
  const ok = failed.length === 0 && wbErrors.length === 0;
  let msg = '作業の変更を保存しました';
  if (wbErrors.length) msg = `実体ファイルの書き戻しに一部失敗: ${wbErrors[0]}`;
  else if (failed.length) msg = `保存しましたが git 操作に失敗したリポジトリがあります: ${failed[0].repo}`;
  toast(msg, ok);
}

// ---------------------------------------------------------------------------
// 手順ビルダー（定型手順）: 画面操作（ブラウザ / Windows アプリ）・コマンド実行・AI の処理
// （生成・判断）を並べて、statemachine-use の作成モードへ渡す指示文を組む。
//
// 画面は工程列（procedure）を持ち回るだけで、指示文の組み立てと検査は main
// （features/cowork/main/procedure.js）の 1 実装に任せる。作成の起動は自由文と同じ
// `cowork:generateStateMachine`（payload.procedure）を通し、入口を増やさない。
// 組んだ工程列は作業項目（procedure）に残るので、作り直しは同じ画面から始められる。
// ---------------------------------------------------------------------------

// 工程の種類。表示名は main（procedure.js の STEP_KINDS）と同じ綴りにする。
const ROUTINE_STEP_KINDS = [
  { id: 'browser', label: '画面操作（ブラウザ）', targetLabel: 'URL', targetPlaceholder: 'https://…',
    detailPlaceholder: '例: ログイン後に「申請一覧」を開き、今日の日付の行を読み取る' },
  { id: 'windows', label: '画面操作（Windows アプリ）', targetLabel: 'アプリ', targetPlaceholder: '例: 勤怠管理',
    detailPlaceholder: '例: メニュー「集計」→「月次」を開き、対象月 {{month}} を入力して「出力」を押す' },
  { id: 'command', label: 'コマンド実行', targetLabel: 'コマンド', targetPlaceholder: '例: python3 scripts/export.py --month {{month}}',
    detailPlaceholder: '補足（任意）' },
  { id: 'agent', label: 'AI の処理（生成・判断）', targetLabel: '',
    detailPlaceholder: '例: 読み取った申請内容を要約し、差し戻しが必要か判断する' },
];

function routineStepKind(id) {
  return ROUTINE_STEP_KINDS.find((k) => k.id === id) || ROUTINE_STEP_KINDS[0];
}

function emptyRoutineProcedure(repo, index = -1) {
  return {
    index, repo, name: '', machine: '', machineTouched: false,
    purpose: '', steps: [], finish: '', notes: '',
    preview: null, tools: null, message: '', ok: true, busy: false,
  };
}

function routineProcedureDraft() {
  if (!state.routineProcedure) state.routineProcedure = emptyRoutineProcedure('');
  return state.routineProcedure;
}

function routineMachineId(name) {
  return String(name || '').trim().toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 60);
}

// 判断欄の 1 行 = 「ラベル: 行き先」。行き先を省いた行は次の工程へ進む。
function parseRoutineOutcomes(textValue) {
  return String(textValue || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const m = /^(.+?)\s*(?::|=|→|->)\s*(\S+)\s*$/.exec(line);
    return m ? { label: m[1].trim(), to: m[2].trim() } : { label: line, to: 'next' };
  });
}

function routineOutcomesText(outcomes) {
  return (Array.isArray(outcomes) ? outcomes : []).map((o) => `${o.label}: ${o.to}`).join('\n');
}

// 作業項目に残した工程列から編集用の下書きを起こす。
function routineProcedureFromItem(item, index) {
  const draft = emptyRoutineProcedure(item.repo || selectedProjectFolder(), index);
  const src = item.procedure || {};
  draft.name = String(item.name || '');
  draft.machine = String(item.workflow || item.file || routineMachineId(item.name) || '');
  draft.machineTouched = true;
  draft.purpose = String(src.purpose || '');
  draft.finish = String(src.finish || '');
  draft.notes = String(src.notes || '');
  draft.steps = (Array.isArray(src.steps) ? src.steps : []).map((step) => ({
    kind: routineStepKind(step.kind).id,
    title: String(step.title || ''),
    detail: String(step.detail || ''),
    target: String(step.target || ''),
    check: String(step.check || ''),
    outcomesText: routineOutcomesText(step.outcomes),
  }));
  return draft;
}

// main へ渡す工程列。検査（分岐先・シェル記号・必須）は main が行う。
function routineProcedurePayload(draft) {
  return {
    purpose: draft.purpose,
    finish: draft.finish,
    notes: draft.notes,
    steps: draft.steps.map((step) => ({
      kind: step.kind,
      title: step.title,
      detail: step.detail,
      target: step.target,
      check: step.check,
      outcomes: parseRoutineOutcomes(step.outcomesText),
    })),
  };
}

function routineProcedureStepHtml(step, index, count) {
  const kind = routineStepKind(step.kind);
  const id = (field) => `rp-step-${index}-${field}`;
  return `<li class="routine-procedure-step" data-rp-step="${index}">
    <div class="routine-procedure-step-head">
      <div class="row"><span class="label-chip">工程 ${index + 1}</span><strong>${esc(kind.label)}</strong></div>
      <div class="row">
        <button type="button" data-rp-move="up" ${index === 0 ? 'disabled' : ''} aria-label="工程 ${index + 1} を上へ">上へ</button>
        <button type="button" data-rp-move="down" ${index === count - 1 ? 'disabled' : ''} aria-label="工程 ${index + 1} を下へ">下へ</button>
        <button type="button" data-rp-remove aria-label="工程 ${index + 1} を削除">削除</button>
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label for="${id('title')}">名前</label>
        <input id="${id('title')}" data-rp-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く">
      </div>
      ${kind.targetLabel ? `<div class="field">
        <label for="${id('target')}">${esc(kind.targetLabel)}</label>
        <input id="${id('target')}" data-rp-field="target" class="${kind.id === 'command' ? 'mono' : ''}" value="${esc(step.target)}" placeholder="${esc(kind.targetPlaceholder || '')}">
      </div>` : ''}
    </div>
    <div class="field">
      <label for="${id('detail')}">${kind.id === 'command' ? '補足' : '内容'}</label>
      <textarea id="${id('detail')}" data-rp-field="detail" rows="3" placeholder="${esc(kind.detailPlaceholder || '')}">${esc(step.detail)}</textarea>
    </div>
    ${kind.id === 'agent' ? '' : `<div class="field">
      <label for="${id('check')}">完了の確認コマンド（任意）</label>
      <input id="${id('check')}" data-rp-field="check" class="mono" value="${esc(step.check)}" placeholder="${kind.id === 'windows' ? '例: winauto wait name:=完了 --app 勤怠管理' : kind.id === 'browser' ? '例: python3 scripts/check_list.py' : '例: test -s out/report.csv'}">
      <small class="muted">終了コード 0 で通過します。パイプやリダイレクトは使えません。</small>
    </div>`}
    <div class="field">
      <label for="${id('outcomes')}">判断（任意・1 行に 1 つ）</label>
      <textarea id="${id('outcomes')}" data-rp-field="outcomesText" rows="2" class="mono" placeholder="APPROVED: next&#10;REJECTED: step:1&#10;ERROR: abort">${esc(step.outcomesText)}</textarea>
      <small class="muted">「ラベル: 行き先」。行き先は next（次の工程）/ step:番号 / done（完了）/ abort（失敗）。空欄なら OK / FAILED で次へ進みます。</small>
    </div>
  </li>`;
}

function routineProcedureToolsHtml(tools) {
  if (!tools) return '';
  if (!tools.length) return '<p class="muted">この手順に画面操作の工程はありません。</p>';
  return `<ul class="routine-procedure-tools">${tools.map((t) => `<li class="${t.ok ? 'is-ok' : 'is-ng'}">
    <span class="status-chip ${t.ok ? 'st-done' : 'st-failed'}">${t.ok ? '利用可能' : '未準備'}</span>
    <strong>${esc(t.label)}</strong><span class="muted">${esc(t.summary || '')}</span>${t.hint ? `<small class="muted">${esc(t.hint)}</small>` : ''}
  </li>`).join('')}</ul>`;
}

function routineProcedureHtml() {
  const draft = routineProcedureDraft();
  const parameters = draft.preview ? draft.preview.parameters : null;
  const addButtons = ROUTINE_STEP_KINDS.map((kind) =>
    `<button type="button" data-rp-add="${kind.id}">＋ ${esc(kind.label)}</button>`).join('');
  return `<section class="routine-procedure" aria-labelledby="routine-procedure-dialog-title">
    <p class="muted">画面操作（ブラウザ / Windows アプリ）・コマンド・AI の処理を順に並べます。定義は外部ターミナルのエージェントが作り、作成後はこの一覧に現れます。</p>
    <div class="row2">
      <div class="field">
        <label for="rp-name">名前</label>
        <input id="rp-name" data-rp-top="name" value="${esc(draft.name)}" placeholder="例: 月次の勤怠集計">
      </div>
      <div class="field">
        <label for="rp-machine">識別名</label>
        <input id="rp-machine" data-rp-top="machine" class="mono" value="${esc(draft.machine)}" placeholder="英小文字・数字・ハイフン">
        <small class="muted">定義を置くフォルダ名になります。</small>
      </div>
    </div>
    <div class="field">
      <label for="rp-purpose">目的</label>
      <textarea id="rp-purpose" data-rp-top="purpose" rows="2" placeholder="例: 毎月 1 日に勤怠システムから月次集計を出力し、異常値があれば差し戻し候補を一覧にする">${esc(draft.purpose)}</textarea>
    </div>
    <section class="routine-procedure-steps" aria-label="工程">
      <div class="cowork-section-heading"><h3>工程</h3></div>
      <div class="row routine-procedure-add">${addButtons}</div>
      ${draft.steps.length
    ? `<ol id="rp-steps" class="routine-procedure-list">${draft.steps.map((s, i) => routineProcedureStepHtml(s, i, draft.steps.length)).join('')}</ol>`
    : '<div class="empty compact">工程がありません。上のボタンから追加してください。</div>'}
    </section>
    <div class="row2">
      <div class="field">
        <label for="rp-finish">終了条件</label>
        <textarea id="rp-finish" data-rp-top="finish" rows="2" placeholder="例: 集計ファイルが出力され、差し戻し候補の一覧が出来たら完了">${esc(draft.finish)}</textarea>
      </div>
      <div class="field">
        <label for="rp-notes">注意事項</label>
        <textarea id="rp-notes" data-rp-top="notes" rows="2" placeholder="例: 申請の承認・却下は押さない（人が行う）">${esc(draft.notes)}</textarea>
      </div>
    </div>
    <div class="routine-procedure-meta">
      <div><span class="muted">入力パラメータ</span> ${parameters
    ? (parameters.length ? parameters.map((k) => `<code>{{${esc(k)}}}</code>`).join(' ') : '<span class="muted">なし</span>')
    : '<span class="muted">「指示文を確認」で検出します（本文の {{key}} が実行時の入力になります）</span>'}</div>
      <div class="row"><button type="button" id="btn-rp-tools" ${api.coworkProcedureTools ? '' : 'disabled'}>道具を確認</button>
        <span class="muted">画面操作に使う CLI がこの端末から呼べるかを診断します。</span></div>
      ${routineProcedureToolsHtml(draft.tools)}
    </div>
    ${draft.preview ? `<details class="routine-procedure-preview" open>
      <summary>作成モードへ渡す指示文</summary>
      <pre>${esc(draft.preview.instruction)}</pre>
    </details>` : ''}
    <p id="rp-message" class="${draft.ok ? 'muted' : 'cowork-item-error'}" ${draft.message ? '' : 'hidden'}>${esc(draft.message)}</p>
  </section>`;
}

function renderRoutineProcedureBody() {
  const body = $('routine-procedure-dialog-body');
  if (!body) return;
  body.innerHTML = routineProcedureHtml();
  bindRoutineProcedureBody(body);
}

function bindRoutineProcedureBody(body) {
  const draft = routineProcedureDraft();
  // 入力は下書きへ直接書く（描き直さない）。構造が変わる操作だけ描き直す。
  // 容れ物（body）は描き直しても同じ要素なので、addEventListener で積まずに差し替える。
  body.oninput = (event) => {
    const el = event.target;
    if (!el || typeof el.value !== 'string') return;
    if (el.dataset.rpTop) {
      draft[el.dataset.rpTop] = el.value;
      if (el.dataset.rpTop === 'machine') draft.machineTouched = true;
      if (el.dataset.rpTop === 'name' && !draft.machineTouched) {
        draft.machine = routineMachineId(el.value);
        const machine = body.querySelector('#rp-machine');
        if (machine) machine.value = draft.machine;
      }
      return;
    }
    const card = el.closest('[data-rp-step]');
    if (card && el.dataset.rpField) {
      const step = draft.steps[Number(card.dataset.rpStep)];
      if (step) step[el.dataset.rpField] = el.value;
    }
  };
  for (const btn of body.querySelectorAll('[data-rp-add]')) {
    btn.addEventListener('click', () => {
      draft.steps.push({ kind: btn.dataset.rpAdd, title: '', detail: '', target: '', check: '', outcomesText: '' });
      draft.preview = null;
      renderRoutineProcedureBody();
      const last = $('routine-procedure-dialog-body').querySelector(`[data-rp-step="${draft.steps.length - 1}"] input`);
      if (last) last.focus();
    });
  }
  for (const btn of body.querySelectorAll('[data-rp-move]')) {
    btn.addEventListener('click', () => {
      const index = Number(btn.closest('[data-rp-step]').dataset.rpStep);
      const to = btn.dataset.rpMove === 'up' ? index - 1 : index + 1;
      if (to < 0 || to >= draft.steps.length) return;
      const [moved] = draft.steps.splice(index, 1);
      draft.steps.splice(to, 0, moved);
      draft.preview = null;
      renderRoutineProcedureBody();
    });
  }
  for (const btn of body.querySelectorAll('[data-rp-remove]')) {
    btn.addEventListener('click', () => {
      draft.steps.splice(Number(btn.closest('[data-rp-step]').dataset.rpStep), 1);
      draft.preview = null;
      renderRoutineProcedureBody();
    });
  }
  const tools = body.querySelector('#btn-rp-tools');
  if (tools) tools.addEventListener('click', () => checkRoutineProcedureTools());
}

function setRoutineProcedureMessage(message, ok) {
  const draft = routineProcedureDraft();
  draft.message = message;
  draft.ok = ok;
  const el = $('rp-message');
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
  el.className = ok ? 'muted' : 'cowork-item-error';
}

async function checkRoutineProcedureTools() {
  const draft = routineProcedureDraft();
  if (!api.coworkProcedureTools) return;
  const kinds = [...new Set(draft.steps.map((s) => s.kind).filter((k) => k === 'browser' || k === 'windows'))];
  const btn = $('btn-rp-tools');
  if (btn) { btn.disabled = true; btn.textContent = '確認中…'; }
  const res = await guard('道具の確認', () => api.coworkProcedureTools({ repo: draft.repo, kinds }));
  draft.tools = Array.isArray(res) ? res : (draft.tools || []);
  renderRoutineProcedureBody();
}

async function previewRoutineProcedure() {
  const draft = routineProcedureDraft();
  if (!api.coworkProcedurePreview) return null;
  draft.preview = null;
  try {
    const res = await api.coworkProcedurePreview({
      name: draft.name, machine: draft.machine, procedure: routineProcedurePayload(draft),
    });
    draft.preview = res;
    draft.message = '';
    draft.ok = true;
  } catch (err) {
    draft.message = String((err && err.message) || err);
    draft.ok = false;
  }
  renderRoutineProcedureBody();
  return draft.preview;
}

// 作成を開始する。起動は自由文の手順付き作業と同じ経路で、成功したら作業項目へ工程列を残す
// （設定変更ダイアログの「定型業務の手順」には生成した指示文が映る）。反映は「変更を保存」。
async function createRoutineProcedure() {
  const draft = routineProcedureDraft();
  if (draft.busy) return;
  const name = String(draft.name || '').trim();
  if (!name) { setRoutineProcedureMessage('名前を入力してください', false); $('rp-name')?.focus(); return; }
  const machine = routineMachineId(draft.machine || name) || `routine-${Date.now()}`;
  if (!draft.steps.length) { setRoutineProcedureMessage('工程を 1 つ以上追加してください', false); return; }
  draft.busy = true;
  const create = $('btn-rp-create');
  if (create) { create.disabled = true; create.textContent = '起動中…'; }
  let launched;
  try {
    launched = await api.coworkGenerateStateMachine({
      repo: draft.repo, name, machine, procedure: routineProcedurePayload(draft),
    });
  } catch (err) {
    launched = { ok: false, error: String((err && err.message) || err) };
  }
  draft.busy = false;
  if (create) { create.disabled = false; create.textContent = '作成を開始'; }
  if (!launched || !launched.ok) {
    setRoutineProcedureMessage(`定型業務の作成を開始できませんでした: ${(launched && launched.error) || '原因不明'}`, false);
    return;
  }
  const existing = draft.index >= 0 ? coworkDraft()[draft.index] : null;
  const instruction = String(launched.instruction || '');
  const procedure = launched.procedure || routineProcedurePayload(draft);
  // 発見項目（実体ファイルが正）は名前と工程列だけ差し替え、手動項目として二重登録しない。
  const item = existing && existing.source === 'discovered'
    ? { ...existing, name, instruction, procedure }
    : {
      ...(existing || {}),
      id: (existing && existing.id) || machine,
      type: 'state-machine',
      name,
      repo: draft.repo,
      schedule: (existing && existing.schedule) || '',
      workflow: machine,
      instruction,
      procedure,
      prompt: (existing && existing.prompt) || '',
      managed: true,
      source: 'config',
    };
  if (draft.index >= 0) coworkDraft()[draft.index] = item;
  else coworkDraft().push(item);
  state.routineProcedure = null;
  const dialog = $('dlg-routine-procedure');
  if (dialog && dialog.open) dialog.close();
  toast('外部ターミナルで定型業務の作成を開始しました。作成が終わったら「変更を保存」で予定を登録できます', true);
  updateCoworkTabVisibility();
  renderCowork();
}

function openRoutineProcedureDialog(index = -1) {
  const dialog = $('dlg-routine-procedure');
  if (!dialog) return;
  const editing = index >= 0 ? coworkDraft()[index] : null;
  const repo = (editing && editing.repo) || selectedProjectFolder();
  if (!repo) {
    toast('プロジェクトを選択してください');
    return;
  }
  state.routineProcedure = editing ? routineProcedureFromItem(editing, index) : emptyRoutineProcedure(repo, -1);
  $('routine-procedure-dialog-title').textContent = editing ? '手順を組み立て直す' : '手順を組み立てる';
  renderRoutineProcedureBody();
  const close = () => { state.routineProcedure = null; if (dialog.open) dialog.close(); };
  $('btn-rp-cancel').onclick = close;
  $('btn-rp-preview').onclick = () => previewRoutineProcedure();
  $('btn-rp-create').onclick = () => createRoutineProcedure();
  dialog.oncancel = (event) => { event.preventDefault(); close(); };
  if (!dialog.open) dialog.showModal();
  const first = dialog.querySelector('#rp-name');
  if (first) first.focus();
}
