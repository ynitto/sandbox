'use strict';

// ワークフローを AI と作る・変える会話。**タスク（taskTeaching.js）と同じ形**で、ワークフロー画面の
// 中に tmux の端末ミラー（`.terminal-stage`）と入力欄（`.composer-shell`）を埋め込む。会話の実体は
// agent-app の会話基盤（kind: 'workflow' の会話）で、AI は会話の中で `.agents/workflows/<保存名>.json`
// を直接書く。画面はその 1 ファイルを読んで「候補の工程」を出す（ワークベンチ側が描く）。
//
// 置き場は共有ワークベンチ（Shadow DOM）の `<slot name="flow-teaching">`。ここは光の DOM 側
// （index.html の #flow-teaching）を描き、どのワークフローの会話を出すかはワークベンチが
// `statemachine:flow-teaching-view` で教えてくれる。
(function initFlowTeaching() {
  const $ = (id) => document.getElementById(id);
  const PHASE_LABEL = { starting: '起動中', ready: '待機', busy: '応答中', attention: '確認待ち', dead: '終了', gone: 'セッション消失' };
  const TERMINAL_KEYS = { Escape: '\x1b', Tab: '\t', Enter: '\r', Newline: '\n', Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D', 'C-c': '\x03' };

  const state = {
    deps: null, visible: false, creating: false, onCreate: null, repo: '', workflowId: '', existing: false, context: '',
    session: null, availableSession: null, phase: null, running: false, pending: false, shareWait: false,
    token: 0, input: null, autoStart: null,
  };

  function term() { return window.FlowTerm; }
  function error(message) { state.deps.notice(message, 'error'); }

  function status(kind = '', text = '', ttl = 0) {
    const node = $('flow-teach-input-status');
    node.className = `input-status ${kind}`.trim();
    node.textContent = text;
    clearTimeout(status.timer);
    if (ttl) status.timer = setTimeout(() => status(), ttl);
  }

  function readExecutionInputs() {
    return { agent: $('flow-teach-agent').value, model: $('flow-teach-model').value.trim(), autoApprove: $('flow-teach-permission').value === 'auto' };
  }

  function populateExecutionInputs(values = null) {
    const select = $('flow-teach-agent');
    const names = state.deps.agentNames();
    const wanted = (values && values.agent) || select.value || state.deps.executionDefaults().agent;
    if (names.join('\n') !== [...select.options].map((option) => option.value).join('\n')) {
      select.replaceChildren();
      for (const name of names) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        select.append(option);
      }
    }
    if (names.includes(wanted)) select.value = wanted;
    if (values && values.model != null) $('flow-teach-model').value = values.model;
    if (values && values.autoApprove != null && !$('flow-teach-permission').dataset.pinned) {
      $('flow-teach-permission').value = values.autoApprove ? 'auto' : 'confirm';
    }
  }

  // ---- 画面 ------------------------------------------------------------------------

  function renderShell() {
    $('flow-teaching').hidden = !state.visible;
    if (!state.visible) return;
    const sess = state.session;
    const ph = state.phase;
    populateExecutionInputs();
    $('flow-teach-settings-summary').textContent = state.deps.executionLabel(readExecutionInputs());
    $('flow-teach-launch').hidden = !!sess;
    $('flow-teach-heading').hidden = !state.existing;
    $('flow-teach-create').hidden = !state.creating;
    $('flow-teach-placeholder').hidden = state.creating;
    $('flow-teach-settings-title').textContent = state.creating ? '今回の作成設定' : '今回の編集設定';
    $('flow-teach-start').textContent = state.creating ? '作成開始' : sess ? '編集中' : '編集開始';
    $('flow-teach-start').disabled = state.pending || !!sess;
    $('flow-teach-status').textContent = state.pending ? 'AI との会話を開いています…' : '';
    $('flow-teach-terminal').hidden = !sess;
    $('flow-teach-composer').hidden = !sess;
    if (!sess) return;
    $('flow-teach-term-agent').textContent = [sess.cli, sess.model].filter(Boolean).join(' · ');
    $('flow-teach-phase').hidden = !ph;
    if (ph) {
      $('flow-teach-phase').textContent = PHASE_LABEL[ph.phase] || ph.phase;
      $('flow-teach-phase').className = `phase ${ph.phase}`;
      $('flow-teach-phase').title = ph.detail || '';
    }
    $('flow-teach-term-name').textContent = ph && ph.name ? `tmux -L agent-app attach -t ${ph.name}` : '';
    $('flow-teach-restart').hidden = !(ph && (ph.phase === 'dead' || ph.phase === 'gone'));
    $('flow-teach-stop').hidden = !state.running;
    $('flow-teach-send').disabled = state.pending;
    setInputMode(state.input ? state.input.mode : 'message', { focus: false });
  }

  // 入力先は会話画面と同じ 3 つ（メッセージ / 端末操作 / 共有に依頼）。
  function setInputMode(mode, { focus = true } = {}) {
    const alive = !!state.session && !['dead', 'gone'].includes((state.phase || {}).phase);
    const shareReady = state.deps.shareEnabled();
    let next = mode === 'terminal' || mode === 'share' ? mode : 'message';
    if (next === 'terminal' && !alive) next = 'message';
    if (next === 'share' && !(shareReady && state.session)) next = 'message';
    state.input = InputMode.reduce(state.input || InputMode.create(), { type: `${next}-focus` });
    for (const [id, name] of [['flow-teach-mode-message', 'message'], ['flow-teach-mode-terminal', 'terminal'], ['flow-teach-mode-share', 'share']]) {
      $(id).setAttribute('aria-pressed', String(next === name));
      $(id).classList.toggle('on', next === name);
    }
    $('flow-teach-mode-terminal').disabled = !alive;
    $('flow-teach-mode-share').hidden = !(shareReady && state.session);
    $('flow-teach-message-input').hidden = next === 'terminal';
    $('flow-teach-terminal-keys').hidden = next !== 'terminal';
    $('flow-teach-composer-toolbar').hidden = next === 'terminal';
    $('flow-teach-prompt').placeholder = next === 'share' ? '参加者の AI に依頼する' : '質問への回答、変更したいこと、具体例';
    term().setInputEnabled(next === 'terminal');
    if (focus) { if (next === 'terminal') term().focus(); else $('flow-teach-prompt').focus(); }
  }

  // ---- 会話（tmux）を開く -------------------------------------------------------------

  async function loadView() {
    const token = (state.token += 1);
    state.session = null;
    state.availableSession = null;
    state.phase = null;
    state.running = false;
    state.pending = false;
    term().detach();
    renderShell();
    if (!state.repo || !state.workflowId) return;
    let view;
    try { view = await api.automation.flowTeachSession(state.repo, state.workflowId); } catch (err) { error(err.message); return; }
    if (token !== state.token) return;
    state.availableSession = view.session || null;
    if (view.session) populateExecutionInputs({ agent: view.session.cli, model: view.session.model, autoApprove: !!view.session.autoApprove });
    renderShell();
    const autoStart = state.autoStart;
    if (autoStart && autoStart.repo === state.repo && autoStart.workflowId === state.workflowId) {
      state.autoStart = null;
      await start(token, autoStart.options);
    }
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
      await term().attach(session.id, $('flow-teach-term-host'));
      requestAnimationFrame(() => term().refit());
    } catch (err) {
      if (token === state.token) error(err.message);
    }
  }

  // 設定を確かめてボタンを押した後にだけ tmux を開く（タスクと同じ順で、先に端末を見せる）。
  async function start(token = state.token, preferredOptions = null) {
    state.pending = true;
    renderShell();
    try {
      const options = preferredOptions || state.deps.executionOptions(readExecutionInputs());
      if (state.availableSession && !state.session) {
        await attach(state.availableSession, token);
        if (token !== state.token) return;
      }
      const view = await api.automation.flowTeachStart({ repo: state.repo, workflowId: state.workflowId, context: state.context, ...options });
      if (token !== state.token) return;
      state.pending = false;
      state.availableSession = null;
      if (view.session) {
        state.running = state.running || !!view.started;
        if (!state.session || state.session.id !== view.session.id || term().current() !== view.session.id) await attach(view.session, token);
        else state.session = view.session;
      }
      state.deps.reloadWorkflows();
    } catch (err) {
      if (token !== state.token) return;
      state.pending = false;
      error(err.message);
    }
    if (token === state.token) renderShell();
  }

  // ---- 依頼の送信 ---------------------------------------------------------------------

  async function send() {
    const text = $('flow-teach-prompt').value.trim();
    const sess = state.session;
    if (!text || !sess) return;
    const shared = state.input && state.input.mode === 'share';
    state.pending = true;
    status('pending', shared ? '受付済み・共有の列へ' : `受付済み・${sess.cli}を準備中`);
    renderShell();
    try {
      let res;
      if (shared) {
        res = await api.send(sess.id, text, { policy: 'shared', cli: '', model: '', readonly: true, skillMode: 'off', skills: [], attachments: [] });
        state.running = true;
        state.shareWait = true;
      } else if (state.running) res = await api.termSubmit(sess.id, text);
      else {
        const autoApprove = $('flow-teach-permission').value === 'auto';
        res = await api.send(sess.id, text, {
          policy: sess.policy, cli: sess.cli, model: sess.model, readonly: false, autoApprove,
          skillMode: 'off', skills: [], attachments: [],
        });
        sess.autoApprove = autoApprove;
        state.running = true;
      }
      $('flow-teach-prompt').value = '';
      if (!shared && (res.restarted || term().current() !== sess.id)) await attach(await api.readSession(sess.id));
      if (res.warning) state.deps.notice(res.warning);
      const at = new Date(res.acceptedAt || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      status('success', shared ? `✓ 共有の列に並べた ${at}` : `✓ ${sess.cli}へ送信済み ${at}`, 4000);
    } catch (err) {
      error(err.message);
      status('error', '送信失敗・入力内容を保持しました');
    } finally {
      state.pending = false;
      renderShell();
    }
  }

  async function submitCreate() {
    if (state.pending) return;
    const purpose = $('flow-teach-purpose').value.trim();
    const errorNode = $('flow-teach-create-error');
    errorNode.hidden = !!purpose;
    if (!purpose) {
      errorNode.textContent = '実現したいことを入力してください';
      $('flow-teach-purpose').focus();
      return;
    }
    state.pending = true;
    renderShell();
    try {
      await state.onCreate({ purpose, options: state.deps.executionOptions(readExecutionInputs()) });
    } catch (err) {
      errorNode.textContent = err.message;
      errorNode.hidden = false;
    } finally {
      state.pending = false;
      renderShell();
    }
  }

  // ---- 外から ------------------------------------------------------------------------

  function sameView(a, b) {
    return !!a && !!b && a.root === b.root && a.workflowId === b.workflowId && !!a.existing === !!b.existing;
  }

  // ワークベンチからの「いまこのワークフローの会話を出している」。null なら隠す。
  function show(detail) {
    if (!detail || detail.hidden || (!detail.workflowId && !detail.creating)) {
      state.visible = false;
      state.creating = false;
      state.token += 1;
      term().detach();
      renderShell();
      return;
    }
    const enteringCreate = !!detail.creating && (!state.creating || state.repo !== detail.root);
    state.creating = !!detail.creating;
    state.onCreate = detail.onCreate || null;
    if (enteringCreate) {
      delete $('flow-teach-permission').dataset.pinned;
      populateExecutionInputs(state.deps.executionDefaults());
      $('flow-teach-purpose').value = '';
      $('flow-teach-create-error').hidden = true;
    }
    const next = { root: detail.root, workflowId: detail.workflowId, existing: !!detail.existing };
    const changed = !sameView(next, { root: state.repo, workflowId: state.workflowId, existing: state.existing });
    state.repo = next.root;
    state.workflowId = next.workflowId;
    state.existing = next.existing;
    state.context = detail.context || '';
    state.visible = true;
    if (changed || (!state.session && !state.creating) || enteringCreate) loadView().catch((err) => error(err.message));
    else renderShell();
  }

  // 新しいワークフロー: 目的を書いて AI と作り始める（ワークベンチの「AIに相談する」から）。
  async function create({ root, purpose, options = null }) {
    const chosen = options || state.deps.executionOptions({});
    const view = await api.automation.flowTeachPrepare({ repo: root, purpose, ...chosen });
    state.autoStart = { repo: root, workflowId: view.workflowId, options: chosen };
    return view.workflowId;
  }

  function onTermPhase(p) {
    if (!state.session || p.id !== state.session.id) return;
    state.phase = { phase: p.phase, detail: p.detail, name: p.name };
    renderShell();
  }

  function onTurnStarted(p) {
    if (!state.session || p.id !== state.session.id) return;
    state.running = true;
    renderShell();
  }

  async function onTurnDone({ id }) {
    if (!state.session || id !== state.session.id) return;
    state.running = false;
    if (state.shareWait) {
      state.shareWait = false;
      setInputMode('message', { focus: false });
      attach(await api.readSession(id)).catch(() => {});
    }
    // AI が書いた定義を候補として取り込み、ワークベンチの「候補の工程」を描き直す
    try { await api.automation.flowTeachAdopt(state.repo, state.workflowId); } catch { /* まだ書かれていない・直す途中 */ }
    try { await state.deps.reloadWorkflows(); } catch { /* 一覧の読み直しに失敗しても会話は続く */ }
    renderShell();
  }

  // 引き受けた人の端末を、この会話の端末ミラーへそのまま描く（キーは送れない）
  function onShareScreen(p) {
    if (!p || !state.visible || !state.session || p.sessionId !== state.session.id) return;
    if (term().current() !== p.id) term().attachRemote(p.id, $('flow-teach-term-host'));
    term().applyScreen({ id: p.id, text: p.text || '' });
  }

  function init(deps) {
    state.deps = deps;
    $('flow-teach-start').onclick = () => state.creating ? submitCreate() : start();
    $('flow-teach-purpose').oninput = () => { $('flow-teach-create-error').hidden = true; };
    $('flow-teach-send').onclick = () => send();
    $('flow-teach-stop').onclick = () => state.session && api.stop(state.session.id).catch((err) => error(err.message));
    $('flow-teach-restart').onclick = () => state.session && attach(state.session).catch((err) => error(err.message));
    $('flow-teach-mode-message').onclick = () => setInputMode('message');
    $('flow-teach-mode-terminal').onclick = () => setInputMode('terminal');
    $('flow-teach-mode-share').onclick = () => setInputMode('share');
    $('flow-teach-permission').onchange = () => { $('flow-teach-permission').dataset.pinned = '1'; renderShell(); };
    $('flow-teach-agent').onchange = () => renderShell();
    $('flow-teach-model').oninput = () => renderShell();
    $('flow-teach-prompt').addEventListener('focus', () => { if (state.input && state.input.mode === 'terminal') setInputMode('message', { focus: false }); });
    $('flow-teach-prompt').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    });
    for (const button of document.querySelectorAll('[data-flow-teach-key]')) {
      button.onclick = () => {
        setInputMode('terminal', { focus: false });
        term().sendKey(TERMINAL_KEYS[button.dataset.flowTeachKey] || '');
        term().focus();
      };
    }
    term().configure({
      onFocus: () => setInputMode('terminal', { focus: false }),
      onError: (err) => error(err.message),
      onEscape: () => {
        const result = InputMode.handleEscape(state.input || InputMode.create());
        state.input = result.state;
        if (!result.forward) setInputMode('message');
        return result.forward;
      },
    });
  }

  window.FlowTeaching = { init, show, create, onTermPhase, onTurnStarted, onTurnDone, onShareScreen, state };
}());
