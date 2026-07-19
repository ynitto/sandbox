'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// kiro-loop 端末（Phase A: capture-pane 視聴）
// ---------------------------------------------------------------------------

function stripAnsi(s) {
  return String(s || '')
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '')
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, '')
    .replace(/\r/g, '');
}

function setKiroLoopDialogVisible(show) {
  const dialog = $('dlg-kiro-loop');
  if (!dialog) return;
  if (show && !dialog.open) dialog.showModal();
  if (!show && dialog.open) dialog.close();
}

function setupKiroLoopDialog() {
  const dialog = $('dlg-kiro-loop');
  if (!dialog) return;
  $('btn-kiro-loop-refresh').addEventListener('click', () => {
    const term = state.kiroLoopTerm;
    if (term) openKiroLoopTerminal({ id: term.id, repo: term.repo, name: term.name, force: true });
  });
  $('btn-kiro-loop-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    stopKiroLoopCapturePoll();
    kiroLoopCancelWait();
    state.kiroLoopTerm = null;
  });
}

function stopKiroLoopCapturePoll() {
  if (state.kiroLoopTimer) {
    clearInterval(state.kiroLoopTimer);
    state.kiroLoopTimer = null;
  }
}

function kiroLoopCaptureSec() {
  const n = Number(state.config && state.config.kiroLoop && state.config.kiroLoop.captureSec);
  return Number.isFinite(n) && n > 0 ? n : 2;
}

function startKiroLoopCapturePoll() {
  stopKiroLoopCapturePoll();
  if (!state.kiroLoopTerm || !state.kiroLoopTerm.target) return;
  const tick = async () => {
    const dialog = $('dlg-kiro-loop');
    if (!dialog || !dialog.open || !state.kiroLoopTerm || !state.kiroLoopTerm.target) return;
    if (!api.kiroLoopCapture) return;
    const target = state.kiroLoopTerm.target;
    const repo = state.kiroLoopTerm.repo;
    const res = await api.kiroLoopCapture({ target, lines: 200, repo }).catch((err) => ({ ok: false, error: err.message, text: '' }));
    if (!state.kiroLoopTerm || state.kiroLoopTerm.target !== target) return;
    state.kiroLoopTerm.text = res && res.text != null ? res.text : '';
    state.kiroLoopTerm.error = res && res.ok === false ? (res.error || 'capture に失敗') : '';
    state.kiroLoopTerm.at = Date.now();
    const pre = $('kiro-loop-capture');
    const meta = $('kiro-loop-term-meta');
    if (pre) {
      const next = stripAnsi(state.kiroLoopTerm.text);
      if (pre.textContent !== next) {
        const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
        pre.textContent = next;
        if (stick) pre.scrollTop = pre.scrollHeight;
      }
    }
    if (meta) {
      meta.textContent = state.kiroLoopTerm.error
        ? state.kiroLoopTerm.error
        : `${new Date(state.kiroLoopTerm.at).toLocaleTimeString('ja-JP')} 時点 ／ エージェントの画面をそのまま映しています（ここには入力できません）`;
      meta.classList.toggle('sync-error', !!state.kiroLoopTerm.error);
    }
    // 構造化状態（最終実行時刻・alive/busy）は capture より低頻度で追従する
    state.kiroLoopTerm.stateTick = (state.kiroLoopTerm.stateTick || 0) + 1;
    if (state.kiroLoopTerm.stateTick % 5 === 1) refreshKiroLoopState();
  };
  tick();
  state.kiroLoopTimer = setInterval(tick, kiroLoopCaptureSec() * 1000);
}

function kiroLoopRoutineSession(items, routineName) {
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

async function openKiroLoopTerminal({ id, repo, name, force = false } = {}) {
  if (!api.kiroLoopListSessions) {
    toast('このビルドでは実行状況の表示に対応していません');
    return;
  }
  kiroLoopCancelWait();
  stopKiroLoopCapturePoll();
  const repoKey = coworkPathKey(repo);
  const cached = state.kiroLoopCache.get(repoKey) || null;
  const cacheFresh = !!state.kiroLoopCache.peek(repoKey);
  if (repoKey && id) state.coworkSelections[repoKey] = String(id);
  const previous = state.kiroLoopTerm
    && coworkPathKey(state.kiroLoopTerm.repo) === repoKey
    && String(state.kiroLoopTerm.id || '') === String(id || '')
    ? state.kiroLoopTerm
    : null;
  const cachedItems = cached ? cached.items || [] : [];
  const cachedSession = kiroLoopRoutineSession(cachedItems, name);
  const cachedTarget = cachedSession ? cachedSession.target : '';
  state.kiroLoopTerm = {
    id: id || '',
    repo: repo || '',
    name: name || '',
    target: cachedTarget,
    session: (cachedSession && cachedSession.session) || '',
    items: cachedItems,
    text: previous ? previous.text || '' : '',
    summary: state.kiroLoopStateCache.get(repoKey) || null,
    send: null,
    error: cacheFresh
      ? (cachedSession ? '' : 'この定常業務に対応するエージェントは見つかりませんでした')
      : '動いているエージェントを探しています…',
    at: Date.now(),
  };
  setKiroLoopDialogVisible(true);
  renderKiroLoopTerminal();
  if (cacheFresh && !force) {
    if (state.kiroLoopTerm.target) startKiroLoopCapturePoll();
    refreshKiroLoopState();
    return;
  }
  const listed = await state.kiroLoopCache.load(repoKey, async () => {
    const result = await guard('tmux セッション', () => api.kiroLoopListSessions({ repo: repo || '' }));
    if (!result) return { items: [], error: 'エージェントの一覧を取得できませんでした' };
    return { items: result.items || [], error: result.error || '' };
  }, { force });
  if (!state.kiroLoopTerm || coworkPathKey(state.kiroLoopTerm.repo) !== repoKey || state.kiroLoopTerm.id !== (id || '')) return;
  if (!listed || (!listed.items && listed.error)) {
    state.kiroLoopTerm.error = 'エージェントの一覧を取得できませんでした';
    renderKiroLoopTerminal();
    return;
  }
  const items = listed.items || [];
  const first = kiroLoopRoutineSession(items, name);
  state.kiroLoopTerm.items = items;
  state.kiroLoopTerm.session = first ? first.session : '';
  state.kiroLoopTerm.target = first ? first.target : '';
  state.kiroLoopTerm.error = first
    ? ''
    : (listed.error || 'この定常業務に対応するエージェントは見つかりませんでした');
  renderKiroLoopTerminal();
  refreshKiroLoopState({ force: true });
  if (first) startKiroLoopCapturePoll();
}

function renderKiroLoopTerminal() {
  const ui = captureUiState();
  const el = $('kiro-loop-dialog-body');
  if (!el) return;
  const term = state.kiroLoopTerm;
  if (!term) {
    el.innerHTML = '';
    return;
  }
  const folder = selectedProjectFolder();
  const entries = coworkHasProjectConfig(state.cowork, folder) ? coworkVisibleEntries(coworkDraft(), folder) : [];
  const selected = entries.find(({ item, index }) => coworkEntryId(item, index) === String(term.id))
    || coworkSelectedEntry(entries, folder);
  const selectedId = selected ? coworkEntryId(selected.item, selected.index) : String(term.id || '');
  const selectedItem = selected ? selected.item : null;
  const selectedState = (selectedItem && selectedItem.state) || {};
  const selectedStatus = selectedState.running ? 'running' : (selectedState.status || 'unknown');
  el.innerHTML = `
    <div class="kiro-loop-term">
      <section class="kiro-loop-overview" aria-labelledby="kiro-loop-selected-title">
        <div class="kiro-loop-overview-heading">
          <div><span class="summary-kicker">選択中</span><h3 id="kiro-loop-selected-title">${esc(term.name || (selectedItem && selectedItem.name) || selectedId || '定常業務')}</h3></div>
          <span class="status-chip ${coworkStatusClass(selectedStatus)}">${esc(statusLabel(selectedStatus))}</span>
        </div>
        <div class="kiro-loop-overview-facts">
          <div><span>実行予定</span><strong>${esc((selectedItem && selectedItem.schedule) || '手動実行')}</strong></div>
          <div><span>最終確認</span><strong>${term.at ? esc(fmtAgo(new Date(term.at).toISOString())) : '未確認'}</strong></div>
          <div><span>対象</span><strong title="${esc(term.repo || '')}">${esc(coworkRepoLabel(term.repo))}</strong></div>
        </div>
        <div id="kiro-loop-state" class="kiro-loop-state">${kiroLoopStateHtml(term.summary, term.name)}</div>
        <div class="kiro-loop-primary-actions">
          ${term.name ? `<button id="btn-kiro-loop-send-periodic" class="primary" title="設定されたこの定常業務を次回予定を待たずに送ります">今すぐ実行</button>` : ''}
          <span id="kiro-loop-send-meta" class="muted">${esc((term.send && term.send.message) || '')}</span>
        </div>
      </section>
      <section class="kiro-loop-agent-panel" aria-labelledby="kiro-loop-agent-title">
        <div class="kiro-loop-term-toolbar">
          <div><span class="summary-kicker">対応するエージェント</span><h3 id="kiro-loop-agent-title">エージェントの画面と個別指示</h3></div>
          <span id="kiro-loop-term-meta" class="muted">${esc(term.error || 'エージェントの画面を表示しています')}</span>
        </div>
        <div class="kiro-loop-send">
          <input id="kiro-loop-send-text" type="text" aria-label="エージェントへの個別指示" placeholder="エージェントへの個別指示" value="${esc((term.send && term.send.text) || '')}">
          <button id="btn-kiro-loop-send">送る</button>
          <button id="btn-kiro-loop-send-cancel" class="hidden">送るのをやめる</button>
        </div>
        <pre id="kiro-loop-capture" class="kiro-loop-capture mono" data-ui-scroll-key aria-live="polite">${esc(stripAnsi(term.text || (term.error && !term.target ? '' : '…')))}</pre>
      </section>
    </div>`;
  restoreUiState(ui);
  const sendBtn = $('btn-kiro-loop-send');
  const sendText = $('kiro-loop-send-text');
  if (sendBtn && sendText) {
    sendBtn.addEventListener('click', () => kiroLoopSendPrompt(sendText.value));
    sendText.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') kiroLoopSendPrompt(sendText.value);
    });
  }
  const periodicBtn = $('btn-kiro-loop-send-periodic');
  if (periodicBtn) periodicBtn.addEventListener('click', () => kiroLoopSendPrompt(term.name));
  const cancelBtn = $('btn-kiro-loop-send-cancel');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      kiroLoopCancelWait();
      updateKiroLoopSendMeta();
    });
  }
  updateKiroLoopSendMeta();
}

// ---------------------------------------------------------------------------
// kiro-loop 構造化状態と復旧送信（Phase C）
// ---------------------------------------------------------------------------

function kiroLoopStateHtml(summary, routineName = '') {
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
    return '<p class="muted">このフォルダで自動実行しているものは見つかりません（kiro-loop が起動していないか、別のフォルダで動いています）。</p>';
  }
  return `<table class="list">
    <tr><th>予定の名前</th><th>いまの状態</th><th>最後に送った時刻</th></tr>${rows.join('')}
  </table>
  <p class="muted">「予定の名前」は設定ファイル（kiro-loop.yaml の prompts）で付けた名前です。名前を送ると、そこに書かれた本文がエージェントへ送られます。</p>`;
}

async function refreshKiroLoopState({ force = false } = {}) {
  const term = state.kiroLoopTerm;
  if (!term || !api.kiroLoopState) return;
  const repoKey = coworkPathKey(term.repo);
  const cached = state.kiroLoopStateCache.peek(repoKey);
  if (!force && cached) {
    term.summary = cached;
    const el = $('kiro-loop-state');
    if (el) el.innerHTML = kiroLoopStateHtml(term.summary, term.name);
    return;
  }
  const res = await state.kiroLoopStateCache.load(
    repoKey,
    () => api.kiroLoopState({ repo: term.repo }).catch(() => null),
    { force }
  );
  if (state.kiroLoopTerm !== term) return;
  term.summary = res && res.ok ? res : { daemons: [] };
  state.kiroLoopStateCache.set(repoKey, term.summary);
  const el = $('kiro-loop-state');
  if (el) el.innerHTML = kiroLoopStateHtml(term.summary, term.name);
}

function kiroLoopCancelWait() {
  if (state.kiroLoopSendTimer) {
    clearTimeout(state.kiroLoopSendTimer);
    state.kiroLoopSendTimer = null;
  }
  const term = state.kiroLoopTerm;
  if (term && term.send && term.send.phase === 'waiting') {
    term.send = { text: term.send.text, phase: '', message: '' };
  }
}

function updateKiroLoopSendMeta() {
  const term = state.kiroLoopTerm;
  const meta = $('kiro-loop-send-meta');
  if (!meta) return;
  const send = (term && term.send) || {};
  meta.textContent = send.message || '';
  meta.classList.toggle('sync-error', send.phase === 'error');
  const cancel = $('btn-kiro-loop-send-cancel');
  if (cancel) cancel.classList.toggle('hidden', send.phase !== 'waiting');
}

const KIRO_LOOP_SEND_RETRY_SEC = 15;
// 応答中の自動再送は上限を設ける。ペインが固まっている・スロットが解放されないままだと
// 「応答中」が永久に続き、待っているのか壊れているのか区別できなくなるため。
const KIRO_LOOP_SEND_MAX_RETRY = 8;

async function kiroLoopSendPrompt(promptText, attempt = 1) {
  const term = state.kiroLoopTerm;
  if (!term) return;
  if (!api.kiroLoopSend) {
    toast('このビルドではエージェントへの送信に対応していません');
    return;
  }
  const text = String(promptText || '').trim();
  if (!text) {
    toast('送る指示を入力してください');
    return;
  }
  kiroLoopCancelWait();
  term.send = { text, phase: 'sending', message: 'エージェントに送っています…' };
  updateKiroLoopSendMeta();
  const res = await api.kiroLoopSend({ repo: term.repo, target: term.target, prompt: text })
    .catch((err) => ({ ok: false, busy: false, error: err.message || String(err) }));
  if (state.kiroLoopTerm !== term) return;
  if (res && res.ok) {
    term.send = { text: '', phase: 'ok', message: `送りました（${new Date().toLocaleTimeString('ja-JP')}）。下の画面に応答が出ます。` };
    const input = $('kiro-loop-send-text');
    if (input) input.value = '';
    toast('エージェントに送りました', true);
    refreshKiroLoopState();
  } else if (res && res.busy && attempt >= KIRO_LOOP_SEND_MAX_RETRY) {
    // 待ち続けても空かない = ペインが固まっているか、実行枠が解放されていない
    const waited = Math.round((KIRO_LOOP_SEND_MAX_RETRY * KIRO_LOOP_SEND_RETRY_SEC) / 60);
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
      message: `エージェントが応答中です。手が空くまで待って自動で送ります（${KIRO_LOOP_SEND_RETRY_SEC} 秒ごとに再試行 ${attempt}/${KIRO_LOOP_SEND_MAX_RETRY}）。`,
    };
    state.kiroLoopSendTimer = setTimeout(() => {
      state.kiroLoopSendTimer = null;
      if (state.kiroLoopTerm === term && term.send && term.send.phase === 'waiting') {
        kiroLoopSendPrompt(text, attempt + 1);
      }
    }, KIRO_LOOP_SEND_RETRY_SEC * 1000);
  } else {
    term.send = { text, phase: 'error', message: `送れませんでした: ${(res && (res.error || res.detail)) || '原因不明'}` };
  }
  updateKiroLoopSendMeta();
}

async function openCoworkFromSettings() {
  if ($('dlg-technical-info').open) $('dlg-technical-info').close();
  await refreshCowork({ forceDiscover: true });
  if (!coworkHasProjectConfig(state.cowork, selectedProjectFolder())) {
    toast('このプロジェクトには定常業務の設定ファイルがありません');
    return;
  }
  switchTab('cowork');
  renderCowork();
  if (!coworkDraft().length) openCoworkWorkDialog(-1);
}

function openCoworkWorkDialog(index) {
  const editing = index >= 0 ? coworkDraft()[index] : null;
  const discovered = !!(editing && editing.source === 'discovered');
  const repos = coworkRepos();
  if (!discovered && !repos.length) {
    toast('先に全体設定でリポジトリ（ワークスペース）を登録してください');
    return;
  }
  state.coworkEditIndex = index;
  const item = editing || { type: 'loop', repo: (repos[0] && repos[0].dir) || '' };
  $('cowork-work-title').textContent = index >= 0 ? '作業を編集' : '作業を追加';
  // 発見項目は当該 repo を固定表示（登録済みリポジトリ一覧に無いこともある）
  let repoOpts = repos.slice();
  if (discovered && item.repo && !repoOpts.some((r) => r.dir === item.repo)) {
    repoOpts = [{ dir: item.repo, label: item.repo }, ...repoOpts];
  }
  $('cw-repo').innerHTML = repoOpts.map((r) => `<option value="${esc(r.dir)}">${esc(r.label)} — ${esc(r.dir)}</option>`).join('');
  $('cw-repo').value = item.repo || (repoOpts[0] && repoOpts[0].dir) || '';
  $('cw-repo').disabled = discovered;
  $('cw-type').value = item.type || 'loop';
  $('cw-type').disabled = discovered;
  $('cw-name').value = item.name || item.id || '';
  $('cw-schedule').value = item.schedule || item.cron || '';
  // 発見項目のスケジュールは、書き戻せる物理フィールドがあるときだけ編集可:
  //   loop → 自身の scheduleKey / state-machine → 対となる kiro-loop エントリの scheduleKey
  const pairedLoop = !!(item._src && item._src.loop);
  $('cw-schedule').disabled = !!(discovered && (
    item.type === 'loop'
      ? (item._src && item._src.scheduleKey === '')
      : (!pairedLoop || item._src.loop.scheduleKey === '')
  ));
  $('cw-workflow').value = item.workflow || item.file || '';
  $('cw-workflow').disabled = discovered;
  $('cw-description').value = item.description || '';
  $('cw-enabled').checked = item.enabled !== false;
  const enField = $('cw-enabled-field');
  // enabled は kiro-loop の物理フィールド → loop と、対エントリを持つ統合ステートマシンで編集可
  if (enField) enField.style.display = ((item.type || 'loop') === 'loop' || pairedLoop) ? '' : 'none';
  $('dlg-cowork-work').showModal();
}

function applyCoworkWorkDialog() {
  const idx = state.coworkEditIndex;
  const existing = idx >= 0 ? coworkDraft()[idx] : null;
  const discovered = !!(existing && existing.source === 'discovered');
  const type = $('cw-type').value;
  const name = $('cw-name').value.trim() || (type === 'state-machine' ? '定型業務' : '定期実行');
  let item;
  if (discovered) {
    // id/source/_src/type/repo/workflow は保持し、編集可能フィールドのみ上書き
    item = {
      ...existing,
      name,
      schedule: $('cw-schedule').value.trim(),
      description: $('cw-description').value.trim(),
      enabled: $('cw-enabled').checked,
    };
  } else {
    item = {
      id: (existing && existing.id) || name.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '') || `cowork-${Date.now()}`,
      type,
      name,
      repo: $('cw-repo').value,
      schedule: $('cw-schedule').value.trim(),
      workflow: $('cw-workflow').value.trim(),
      description: $('cw-description').value.trim(),
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
  state.kiroLoopCache.clear();
  state.kiroLoopStateCache.clear();
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
