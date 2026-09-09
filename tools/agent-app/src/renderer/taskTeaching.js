'use strict';

// タスク（ステートマシン）を AI と作る・変える会話。**手動実行の画面と同じく、タスク画面の中に
// tmux の端末ミラーを埋め込む。** 会話の実体は agent-app の会話基盤（kind: 'task' の会話）で、
// CLI は会話と同じ定義・同じ起動方針で起こす。
//
// 置き場は共有ワークベンチ（Shadow DOM）の `<slot name="teaching">`。ここは光の DOM 側
// （index.html の #task-teaching）を描き、どのタスクの会話を出すかはワークベンチが
// `statemachine:teaching-view` で教えてくれる。
//
// 見本の記録（playwright-cli / winauto）は**この端末**で取る。Windows では AI が WSL の tmux に
// いて画面は Windows 側にあるので、記録の開始・終了はここから行い、できた記録の所在を
// main が WSL 表記へ直して会話へ送る（automation:teach:demonstration）。AI が見本を要るときは
// 返答に `@record …` の 1 行を書く（renderer/teachingProtocol.js）。
(function initTaskTeaching() {
  const $ = (id) => document.getElementById(id);
  const PHASE_LABEL = { starting: '起動中', ready: '待機', busy: '応答中', attention: '確認待ち', dead: '終了', gone: 'セッション消失' };
  const TERMINAL_KEYS = { Escape: '\x1b', Tab: '\t', Enter: '\r', Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D', 'C-c': '\x03' };

  const state = {
    deps: null, visible: false, repo: '', machine: '', title: '', agent: '', creating: false, editing: false, card: false, published: false,
    session: null, availableSession: null, phase: null, tools: null, running: false, pending: false, token: 0,
    input: null, record: { open: false, source: 'browser', target: '', active: false, busy: false, message: '', ok: true, request: null },
  };

  function term() { return window.TaskTerm; }

  function status(kind = '', text = '', ttl = 0) {
    const node = $('task-input-status');
    node.className = `input-status ${kind}`.trim();
    node.textContent = text;
    clearTimeout(status.timer);
    if (ttl) status.timer = setTimeout(() => status(), ttl);
  }

  function error(message) { state.deps.notice(message, 'error'); }

  function sameView(a, b) {
    return !!a && !!b && a.root === b.root && a.machine === b.machine && !!a.creating === !!b.creating
      && !!a.editing === !!b.editing && !!a.card === !!b.card && !!a.published === !!b.published;
  }

  // ---- 表示の切り替え ---------------------------------------------------------

  function renderShell() {
    const root = $('task-teaching');
    root.hidden = !state.visible;
    // カード（「AIと編集」）の中に入るときは、端末は白い面の中身なので枠と影を持たない。
    root.classList.toggle('in-card', state.visible && state.card);
    $('task-create').hidden = !(state.visible && state.creating);
    $('task-workspace').hidden = !(state.visible && !state.creating);
    if (!state.visible) return;
    if (state.creating) renderCreate();
    else renderWorkspace();
  }

  function renderCreate() {
    populateExecutionInputs('task-create');
    $('task-create-settings').textContent = state.deps.executionLabel(readExecutionInputs('task-create'));
    $('task-create-error').hidden = true;
    const purpose = $('task-purpose');
    if (!purpose.value && state.deps.takeIntent) {
      const intent = state.deps.takeIntent();
      if (intent) {
        purpose.value = intent.purpose || '';
        populateExecutionInputs('task-create', { agent: intent.agent, model: intent.model });
        $('task-create-settings').textContent = state.deps.executionLabel(readExecutionInputs('task-create'));
      }
    }
    purpose.focus();
  }

  function renderWorkspace() {
    const sess = state.session;
    const ph = state.phase;
    const hasTerminal = !!sess;
    const waitingToLaunch = !hasTerminal;
    $('task-launch').hidden = !waitingToLaunch;
    if (waitingToLaunch) {
      populateExecutionInputs('task-launch');
      $('task-launch-title').textContent = state.published ? 'AIと編集' : '下書きの編集を続ける';
      $('task-launch-start').textContent = state.availableSession ? 'tmuxで編集を続ける' : 'tmuxで編集を始める';
      $('task-launch-start').disabled = state.pending;
    }
    const note = $('task-open-note');
    note.hidden = true;
    note.textContent = '';
    $('task-terminal').hidden = !hasTerminal;
    $('task-composer').hidden = !hasTerminal;
    $('task-term-agent').textContent = sess ? [sess.cli, sess.model].filter(Boolean).join(' · ') : '';
    $('task-term-name').textContent = ph && ph.name ? `tmux -L agent-app attach -t ${ph.name}` : '';
    const phaseNode = $('task-phase');
    phaseNode.hidden = !ph;
    if (ph) { phaseNode.textContent = PHASE_LABEL[ph.phase] || ph.phase; phaseNode.className = `phase ${ph.phase}`; phaseNode.title = ph.detail || ''; }
    $('task-term-restart').hidden = !(ph && (ph.phase === 'dead' || ph.phase === 'gone'));
    $('task-stop').hidden = !(state.running || state.pending);
    $('task-send').disabled = state.pending || !hasTerminal;
    renderRecord();
    setInputMode(state.input && state.input.mode === 'terminal' ? 'terminal' : 'message', { focus: false });
  }

  function populateExecutionInputs(prefix, preferred = null) {
    const select = $(`${prefix}-agent`);
    const model = $(`${prefix}-model`);
    if (!select || !model) return;
    const defaults = state.deps.executionDefaults();
    const wanted = String((preferred && preferred.agent) || select.value || state.agent || defaults.agent || '');
    const agents = state.deps.agentNames();
    select.innerHTML = agents.length
      ? agents.map((name) => `<option value="${name.replace(/&/g, '&amp;').replace(/"/g, '&quot;')}"${name === wanted ? ' selected' : ''}>${name}</option>`).join('')
      : '<option value="">利用できるエージェントがありません</option>';
    select.disabled = !agents.length;
    if (agents.includes(wanted)) select.value = wanted;
    if (preferred && preferred.model != null) model.value = preferred.model;
    else if (!model.value) model.value = defaults.model || '';
  }

  function readExecutionInputs(prefix) {
    return { agent: $(`${prefix}-agent`).value, model: $(`${prefix}-model`).value.trim() };
  }

  function setInputMode(mode, { focus = true } = {}) {
    const alive = !!state.session && !['dead', 'gone'].includes((state.phase || {}).phase);
    const next = mode === 'terminal' && alive ? 'terminal' : 'message';
    state.input = InputMode.reduce(state.input || InputMode.create(), { type: next === 'terminal' ? 'terminal-focus' : 'message-focus' });
    $('task-mode-message').setAttribute('aria-pressed', String(next === 'message'));
    $('task-mode-terminal').setAttribute('aria-pressed', String(next === 'terminal'));
    $('task-mode-terminal').disabled = !alive;
    $('task-message-input').hidden = next !== 'message';
    $('task-terminal-keys').hidden = next !== 'terminal';
    $('task-composer-toolbar').hidden = next !== 'message';
    $('task-terminal').classList.toggle('input-terminal', next === 'terminal');
    term().setInputEnabled(next === 'terminal');
    if (focus) { if (next === 'terminal') term().focus(); else $('task-prompt').focus(); }
  }

  // ---- 会話（tmux）を開く ------------------------------------------------------

  async function loadView() {
    const token = (state.token += 1);
    state.session = null;
    state.availableSession = null;
    state.phase = null;
    state.running = false;
    term().detach();
    renderShell();
    if (!state.repo || !state.machine) return;
    let view;
    try { view = await api.automation.teachSession(state.repo, state.machine); } catch (err) { error(err.message); return; }
    if (token !== state.token) return;
    state.tools = view.tools || null;
    state.availableSession = view.session || null;
    if (view.session) populateExecutionInputs('task-launch', { agent: view.session.cli, model: view.session.model });
    renderShell();
  }

  async function attach(session, token = state.token) {
    state.session = session;
    state.running = state.deps.isRunning(session.id);
    const size = term().size();
    try {
      const r = await api.termOpen(session.id, size.cols, size.rows);
      if (token !== state.token) return;
      state.phase = { phase: r.phase, detail: r.detail, name: r.name };
      if (r.warning) state.deps.notice(r.warning);
      renderShell();
      await term().attach(session.id, $('task-term-host'));
      requestAnimationFrame(() => term().refit());
    } catch (err) {
      if (token === state.token) error(err.message);
    }
  }

  // 設定を確認してボタンを押した後にだけ tmux を開く。既存の下書きは同じセッションへ戻る。
  async function startTeaching(token = state.token) {
    state.pending = true;
    renderShell();
    try {
      const options = state.deps.executionOptions(readExecutionInputs('task-launch'));
      const view = state.availableSession
        ? { session: state.availableSession, tools: state.tools, started: false }
        : await api.automation.teachStart({ repo: state.repo, machine: state.machine, ...options });
      if (token !== state.token) return;
      state.pending = false;
      state.tools = view.tools || state.tools;
      state.availableSession = null;
      if (view.session) { state.running = state.running || !!view.started; await attach(view.session, token); }
      state.deps.reloadTasks();
    } catch (err) {
      if (token !== state.token) return;
      state.pending = false;
      error(err.message);
    }
    if (token === state.token) renderShell();
  }

  // 新しいタスク: 目的を書いて AI と作り始める。
  async function create() {
    const purpose = $('task-purpose').value.trim();
    const machine = $('task-machine').value.trim();
    const errorNode = $('task-create-error');
    if (!purpose) { errorNode.textContent = '何を自動化したいかを書いてください'; errorNode.hidden = false; return; }
    errorNode.hidden = true;
    $('task-create-start').disabled = true;
    try {
      const options = state.deps.executionOptions(readExecutionInputs('task-create'));
      const view = await api.automation.teachStart({ repo: state.repo, purpose, machine, ...options });
      $('task-purpose').value = '';
      $('task-machine').value = '';
      await state.deps.openTask(view.machine);
    } catch (err) {
      errorNode.textContent = err.message;
      errorNode.hidden = false;
    } finally {
      $('task-create-start').disabled = false;
    }
  }

  // ---- 依頼の送信 ---------------------------------------------------------------

  async function send() {
    const text = $('task-prompt').value.trim();
    const sess = state.session;
    if (!text || !sess) return;
    state.pending = true;
    status('pending', `受付済み・${sess.cli}を準備中`);
    renderShell();
    try {
      let res;
      if (state.running) res = await api.termSubmit(sess.id, text);
      else {
        res = await api.send(sess.id, text, {
          policy: sess.policy, cli: sess.cli, model: sess.model, readonly: false, autoApprove: !!sess.autoApprove,
          skillMode: 'off', skills: [], attachments: [],
        });
        state.running = true;
      }
      $('task-prompt').value = '';
      if (res.restarted || term().current() !== sess.id) await attach(await api.readSession(sess.id));
      if (res.warning) state.deps.notice(res.warning);
      const at = new Date(res.acceptedAt || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      status('success', `✓ ${sess.cli}へ送信済み ${at}`, 4000);
    } catch (err) {
      error(err.message);
      status('error', '送信失敗・入力内容を保持しました');
    } finally {
      state.pending = false;
      renderShell();
    }
  }

  async function restart() {
    const sess = state.session;
    if (!sess) return;
    const size = term().size();
    try {
      const r = await api.termRestart(sess.id, size.cols, size.rows);
      state.phase = { phase: r.phase, detail: r.detail, name: r.name };
      await term().attach(sess.id, $('task-term-host'));
      setInputMode('message');
      renderShell();
    } catch (err) { error(err.message); }
  }

  // ---- 操作の見本（この端末で記録し、所在を会話へ送る） -------------------------------

  // 足りないものだけを 1 行で言う。仕組みの説明（記録はこの端末で取る・Windows では WSL へ渡す）は
  // 画面に常駐させない——README に書いてある。
  function recordNote() {
    const tools = state.tools || {};
    if (state.record.source === 'windows') {
      if (api.platform !== 'win32') return 'Windows アプリの見本は Windows 上でだけ記録できます。';
      if (tools.windows === false) return 'winauto が見つかりません（python tools/winauto/install.py）。';
      return '';
    }
    return tools.browser === false ? 'playwright-cli が見つかりません（npm install -g @playwright/cli@latest）。' : '';
  }

  function renderRecord() {
    const rec = state.record;
    $('task-record').hidden = !rec.open;
    if (!rec.open) return;
    const windows = rec.source === 'windows';
    $('task-record-source').value = rec.source;
    $('task-record-source').disabled = rec.active;
    $('task-record-target-label').textContent = windows ? 'アプリ名' : '開始 URL';
    const target = $('task-record-target');
    target.placeholder = windows ? '例: 勤怠管理' : 'https://…';
    if (target.value !== rec.target) target.value = rec.target;
    target.disabled = rec.active;
    $('task-record-request').hidden = !rec.request;
    $('task-record-request').textContent = rec.request ? `AI が見本を求めています: ${rec.request.source === 'windows' ? 'Windows アプリ' : 'ブラウザ'}${rec.request.target ? ` ${rec.request.target}` : ''}` : '';
    $('task-record-start').hidden = rec.active;
    $('task-record-start').disabled = rec.busy || (windows && api.platform !== 'win32');
    $('task-record-stop').hidden = !rec.active;
    $('task-record-stop').disabled = rec.busy;
    $('task-record-message').textContent = rec.message;
    $('task-record-message').className = `sub ${rec.ok ? '' : 'error'}`.trim();
    $('task-record-message').hidden = !rec.message;
    const note = recordNote();
    $('task-record-note').textContent = note;
    $('task-record-note').hidden = !note;
  }

  function openRecord(request = null) {
    const rec = state.record;
    rec.open = true;
    if (request) {
      rec.request = request;
      if (!rec.active) { rec.source = request.source; rec.target = request.target || rec.target; }
    }
    renderShell();
  }

  async function startRecording() {
    const rec = state.record;
    if (rec.busy || rec.active) return;
    rec.busy = true; rec.ok = true; rec.message = '始めています…';
    renderRecord();
    try {
      await api.automation.recordingStart({
        root: state.repo, source: rec.source, ...(rec.source === 'windows' ? { app: rec.target } : { url: rec.target }),
      });
      rec.active = true;
      rec.message = rec.source === 'windows' ? '操作したあとに「終了してAIへ渡す」を押してください。' : '開いたブラウザで操作し、終わったら「終了してAIへ渡す」を押してください。';
    } catch (err) {
      rec.ok = false; rec.message = err.message;
    } finally {
      rec.busy = false;
      renderRecord();
    }
  }

  async function stopRecording() {
    const rec = state.record;
    if (rec.busy || !rec.active) return;
    rec.busy = true; rec.ok = true; rec.message = '記録を工程に整理しています…';
    renderRecord();
    try {
      const result = await api.automation.recordingStop({
        root: state.repo, source: rec.source, ...(rec.source === 'windows' ? { app: rec.target } : { url: rec.target }),
      });
      rec.active = false;
      const saved = await api.automation.teachDemonstration(state.repo, state.machine, { ...result, target: rec.target });
      rec.request = null;
      rec.message = `${saved.steps} 工程の見本を保存しました（${saved.relative}）。${saved.sent ? 'AI へ渡しました。' : 'AI との会話を開いてから、見本の場所を伝えてください。'}`;
      if (saved.sent) state.running = true;
    } catch (err) {
      rec.active = false;
      rec.ok = false; rec.message = err.message;
    } finally {
      rec.busy = false;
      renderShell();
    }
  }

  // ---- 親からの通知 ---------------------------------------------------------------

  // ワークベンチが「このタスクの会話を出す / 出さない」と言ってきた。
  function show(detail) {
    if (!detail || detail.hidden) { hide(); return; }
    const next = { root: detail.root || '', machine: detail.machine || '', agent: detail.agent || '', creating: !!detail.creating, editing: !!detail.editing, card: !!detail.card, published: !!detail.published };
    const same = state.visible && sameView(next, { root: state.repo, machine: state.machine, creating: state.creating, editing: state.editing, card: state.card, published: state.published });
    state.title = detail.title || '';
    if (same) { renderShell(); requestAnimationFrame(() => term().refit()); return; }
    state.repo = next.root;
    state.machine = next.machine;
    state.agent = next.agent;
    state.creating = next.creating;
    state.editing = next.editing;
    state.card = next.card;
    state.published = next.published;
    state.visible = true;
    state.record = { ...state.record, open: false, request: null, message: '' };
    if (state.creating) { state.token += 1; state.session = null; state.availableSession = null; term().detach(); renderShell(); return; }
    loadView();
  }

  function hide() {
    if (!state.visible) return;
    state.visible = false;
    state.token += 1;
    term().detach();
    renderShell();
  }

  function onTermPhase(p) {
    if (!state.session || p.id !== state.session.id) return;
    state.phase = { phase: p.phase, detail: p.detail, name: p.name };
    if (p.phase === 'dead' || p.phase === 'gone') { setInputMode('message', { focus: false }); status('error', 'セッション終了'); }
    renderShell();
  }

  function onTurnStarted(p) {
    if (!state.session || p.id !== state.session.id) return;
    state.running = true;
    if (p.warning) state.deps.notice(p.warning);
    renderShell();
  }

  // ターンが終わった: AI がファイルを書いたかもしれないので定義を読み直し、見本の依頼があれば案内する。
  async function onTurnDone({ id, message }) {
    if (!state.session || id !== state.session.id) return;
    state.running = false;
    const request = TeachingProtocol.parseRecordRequest(message && message.text);
    if (request) openRecord(request);
    else renderShell();
    try { await state.deps.refreshWorkbench(); } catch { /* 一覧の読み直しに失敗しても会話は続く */ }
    state.deps.reloadTasks();
  }

  function init(deps) {
    state.deps = deps;
    state.input = InputMode.create();
    term().configure({
      onFocus: () => setInputMode('terminal', { focus: false }),
      onError: () => status('error', '端末への入力に失敗しました'),
      onEscape: () => {
        const result = InputMode.handleEscape(state.input);
        state.input = result.state;
        if (!result.forward) setInputMode('message');
        return result.forward;
      },
    });
    $('task-create-start').onclick = () => create().catch((err) => error(err.message));
    $('task-launch-start').onclick = () => startTeaching().catch((err) => error(err.message));
    for (const id of ['task-create-agent', 'task-create-model']) $(id).addEventListener('change', () => {
      $('task-create-settings').textContent = state.deps.executionLabel(readExecutionInputs('task-create'));
    });
    $('task-purpose').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); create().catch((err) => error(err.message)); }
    });
    $('task-create-cancel').onclick = () => state.deps.cancelCreate();
    $('task-send').onclick = () => send();
    $('task-stop').onclick = () => { if (state.session) api.stop(state.session.id).catch((err) => error(err.message)); };
    $('task-term-restart').onclick = () => restart();
    $('task-mode-message').onclick = () => setInputMode('message');
    $('task-mode-terminal').onclick = () => setInputMode('terminal');
    $('task-prompt').addEventListener('focus', () => { if (state.input.mode !== 'message') setInputMode('message', { focus: false }); });
    $('task-prompt').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$('task-send').disabled) send(); }
    });
    for (const button of document.querySelectorAll('[data-task-key]')) {
      button.onclick = () => { setInputMode('terminal', { focus: false }); term().sendKey(TERMINAL_KEYS[button.dataset.taskKey] || ''); term().focus(); };
    }
    $('task-record-open').onclick = () => openRecord();
    $('task-record-close').onclick = () => { state.record.open = false; renderShell(); };
    $('task-record-source').onchange = () => { state.record.source = $('task-record-source').value; renderRecord(); };
    $('task-record-target').addEventListener('input', () => { state.record.target = $('task-record-target').value; });
    $('task-record-start').onclick = () => startRecording();
    $('task-record-stop').onclick = () => stopRecording();
    renderShell();
  }

  window.TaskTeaching = { init, show, hide, onTermPhase, onTurnStarted, onTurnDone, state };
})();
