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

// 実行の記録タブ。左に作業、右にその作業の記録（この画面からの実行 + ログ）。
// 見出しは領域ヘッダーとタブが担うので、ペインは中身から始める。
function renderRoutineRuns() {
  const pane = $('tab-routine-runs');
  if (!pane) return;
  const folder = selectedProjectFolder();
  const entries = coworkItemsForFolder(folder);
  const selected = state.coworkHistory ? state.coworkHistory.id : '';
  if (!entries.length) {
    pane.innerHTML = `<section class="routine-page">
      <div class="empty compact">まだ作業がありません。「作業」タブから追加すると、動かした記録をここで読めます。</div>
    </section>`;
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
  updateRoutineAgentSendMeta();
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
  updateCoworkWorkFields();
  $('dlg-cowork-work').showModal();
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
  let item;
  if (discovered) {
    item = {
      ...existing,
      name,
      schedule: $('cw-schedule').value.trim(),
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
