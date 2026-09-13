'use strict';

// 画面の状態は 1 か所。保存は main（store）がやり、ここは表示と操作だけ。
const state = {
  config: null,
  area: 'conversation',
  host: null,           // host:info（platform / tmux の有無）。届くまで null
  hostReady: null,      // host:info の返事を待つ Promise（送信前に待つ）
  repo: '',
  repoToken: 0,         // selectRepo のたびに進める。遅れて届いたホストの返事を捨てる印
  agents: [],
  capabilities: null,   // automation:capabilities（{ herd, agentLoop, agentFlow }）。届くまで null
  agentsLoading: false, // agents:list（ホストの PATH を引く）の返事待ち
  agentsReady: null,
  sessions: [],
  tasks: [],
  taskToken: 0,          // タスク一覧の読み込みのたびに進める。遅れて届いた実行状態を捨てる印
  selectionToken: 0,     // 項目を続けて選んだとき、古い設定保存の返事で表示状態を戻さない印
  taskStatusPending: false, // 定義は出したが、実行状態（ファイル実体の確認を伴う）はまだ重ねていない
  workflows: [],
  workflowRuns: [],
  selectedTask: '',
  selectedWorkflow: '',
  areaError: '',
  current: null,        // 開いている会話（store の中身）
  draft: false,         // 「新しい会話」を押してまだ 1 通も送っていない
  running: new Set(),   // 応答中の会話 ID
  pending: new Set(),   // 送信中（main が CLI を起動し直している間など）の会話 ID
  logs: new Map(),      // 会話 ID → 応答中に流れた行（ヘッドレス）
  tails: new Map(),     // 会話 ID → 端末の末尾（tmux）
  liveParts: new Map(), // 会話 ID → { thinking, information }（構造化された応答中イベント）
  phases: new Map(),    // 会話 ID → { phase, detail }
  changesOpen: false,
  input: InputMode.create(),
  inputStatusTimer: null,
  view: 'chat',
  diffSide: false,
  diffScope: 'worktree',   // 変更ビュー: 作業ツリー / ブランチ（分岐元から積んだコミット）
  diffText: '',
  worktrees: [],           // git worktree list の結果
  worktree: '',            // 「新しい会話」で選んでいる作業フォルダ（'' はリポジトリ本体）
  attachments: [],         // 次の依頼に付ける添付 [{ id, name, size } | { rel, name }]
  settingsSkills: [],
  settingsActions: [],
  settingsQuick: [],
  settingsAgents: [],
  turnSkillMode: 'auto',
  turnSkills: [],
  turnSkillPreview: [],
  skillPreviewTimer: null,
  pendingTaskIntent: null,
  filledPrompt: '',     // 入力欄へこちらが置いた本文（書きかけと見分けるため）
  sessionFilter: '',    // 会話一覧の絞り込み（名前の一部）
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const basename = (p) => String(p).replace(/[\\/]+$/, '').split(/[\\/]/).pop();

function notice(text, kind = '') {
  const n = $('notice');
  n.textContent = text || '';
  n.className = kind;
  n.hidden = !text;
}

const PHASE_LABEL = { starting: '起動中', ready: '待機', busy: '応答中', attention: '確認待ち', dead: '終了', gone: 'セッション消失' };
// 端末へそのまま送るキー。端末操作の仮想キー（index.html の data-terminal-key）と同じ表を使う。
const TERMINAL_KEYS = {
  Escape: '\x1b', Tab: '\t', Enter: '\r', Newline: '\n', Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D', 'C-c': '\x03',
};
const POPUP_MENU_SELECTOR = 'details.more-menu[open], details.run-settings[open]';
const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

function closePopupMenus(root, event = null) {
  const path = event && typeof event.composedPath === 'function' ? event.composedPath() : [];
  for (const menu of root.querySelectorAll(POPUP_MENU_SELECTOR)) {
    if (!event || !path.includes(menu)) menu.open = false;
  }
}

function isTmux(sess) { return !!sess && sess.transport === 'tmux'; }

function inputStatus(kind = '', text = '', ttl = 0) {
  clearTimeout(state.inputStatusTimer);
  const node = $('input-status');
  node.className = `input-status ${kind}`.trim();
  node.textContent = text;
  const shell = document.querySelector('.composer-shell');
  shell.classList.toggle('error', kind === 'error');
  if (kind === 'success') {
    shell.classList.remove('sent');
    requestAnimationFrame(() => shell.classList.add('sent'));
  }
  if (ttl) state.inputStatusTimer = setTimeout(() => inputStatus(), ttl);
}

// 入力先（メッセージ / 端末操作 / 共有に依頼）。「共有に依頼」は、この依頼を LAN の仲間の AI へ
// 回す先で、押せるのは設定 > 共有を使うと決めているときだけ。実行設定は共有の分だけに絞る。
function setInputMode(mode, { focus = true } = {}) {
  const tmuxReady = isTmux(state.current) && !['dead', 'gone'].includes((state.phases.get(state.current.id) || {}).phase);
  const shareReady = shareEnabled();
  // 共有の答えを待っている間は、入力欄は引き受けた人への「ひとこと」になる（手元の CLI は止まっていて、
  // 相手の端末にキーは送れないので、他の入力先は押せない）
  const waiting = shareWaiting();
  let next = mode === 'terminal' || mode === 'share' ? mode : 'message';
  if (waiting) next = 'share';
  if (next === 'terminal' && !tmuxReady) next = 'message';
  if (next === 'share' && !shareReady) next = 'message';
  state.input = InputMode.reduce(state.input, { type: `${next}-focus` });
  for (const [id, name] of [['input-mode-message', 'message'], ['input-mode-terminal', 'terminal'], ['input-mode-share', 'share']]) {
    $(id).setAttribute('aria-pressed', String(next === name));
    $(id).classList.toggle('on', next === name);
  }
  $('input-mode-terminal').disabled = !tmuxReady || !!waiting;
  $('input-mode-message').disabled = !!waiting;
  $('input-mode-share').hidden = !shareReady;
  $('message-input').hidden = next === 'terminal';
  $('terminal-keys').hidden = next !== 'terminal';
  document.querySelector('.composer-toolbar').hidden = next === 'terminal';
  $('chat').classList.toggle('input-terminal', next === 'terminal');
  $('prompt').placeholder = waiting ? `${waiting.node || '引き受けた人'} へ伝える`
    : (next === 'share' ? '参加者の AI に依頼する' : 'エージェントに依頼する');
  Term.setInputEnabled(next === 'terminal');
  renderRunSettingsSummary();
  if (focus) {
    if (next === 'terminal') Term.focus();
    else $('prompt').focus();
  }
}

// 今見ている作業フォルダ。会話を開いていればその会話のもの（会話ごとに固定）、
// 下書き中なら選択中のもの。'' はリポジトリ本体。
function activeWorktree() {
  return state.current ? (state.current.worktree || '') : state.worktree;
}

function worktreeLabel(name) {
  if (!name) return 'リポジトリ本体';
  const w = state.worktrees.find((x) => x.name === name);
  const branch = (w && w.branch) || (state.current && state.current.worktree === name ? state.current.branch : '');
  return branch ? `${name}（${branch}）` : name;
}

// worktree の UI を出すか。機能を切っていても、既に worktree で始めた会話を開いたときは
// 「どこで動いているか」が分かるように出す（選び直しはできない）。
function worktreeUI() {
  return !!(state.config && state.config.useWorktree) || !!(state.current && state.current.worktree);
}

// ---- 左: リポジトリと会話 ------------------------------------------------

async function addRepo() {
  const cfg = await api.addRepo();
  if (!cfg) return;
  state.config = cfg;
  await selectRepo(cfg.lastRepo);
}

function renderRepos() {
  const select = $('repo-select');
  select.replaceChildren();
  if (!state.config.repos.length) {
    const option = el('option', '', 'リポジトリを追加してください');
    option.value = '';
    select.append(option);
  }
  for (const repo of state.config.repos) {
    const option = el('option', '', basename(repo));
    option.value = repo;
    option.title = repo;
    select.append(option);
  }
  select.value = state.repo;
  select.disabled = !state.config.repos.length;
  $('repo-remove').disabled = !state.repo;
}

async function removeConversation(session) {
  if (!session || !confirm(isTmux(session) ? 'この会話を削除しますか？端末セッションも終了します。' : 'この会話を削除しますか？')) return;
  const selected = !!(state.current && state.current.id === session.id);
  const wt = session.worktree || '';
  if (selected) Term.detach();
  await api.removeSession(session.id);
  state.running.delete(session.id);
  state.pending.delete(session.id);
  state.phases.delete(session.id);
  state.liveParts.delete(session.id);
  state.logs.delete(session.id);
  state.tails.delete(session.id);
  state.sessions = await api.listSessions(state.repo);
  // 作業フォルダは会話とは別物なので、他の会話が使っていないときだけ別に聞く。
  const others = state.sessions.filter((s) => s.worktree === wt).length;
  if (wt && !others && confirm(`作業フォルダ ${wt} も削除しますか？ブランチは残ります。`)) {
    try { await api.removeWorktree(state.repo, wt, { force: false }); } catch (err) { notice(err.message, 'error'); }
    await refreshWorktrees();
  }
  if (selected) newDraft();
  else renderSessions();
}

function renderSessions() {
  const ul = $('sessions');
  ul.replaceChildren();
  const needle = state.sessionFilter.trim().toLowerCase();
  const shown = needle
    ? state.sessions.filter((s) => String(s.title || '').toLowerCase().includes(needle))
    : state.sessions;
  for (const s of shown) {
    const ph = state.phases.get(s.id);
    const cls = [state.current && s.id === state.current.id ? 'active' : '', state.running.has(s.id) ? 'running' : (ph && ph.phase === 'attention' ? 'attention' : '')];
    const li = el('li', `row-item ${cls.join(' ')}`);
    const pick = el('button', 'list-pick');
    const body = el('span', 'grow');
    body.append(el('div', '', s.title || '（無題）'));
    const where = s.worktree ? ` · ${s.branch || s.worktree}` : '';
    const status = state.running.has(s.id) ? '応答中' : (ph && ph.phase === 'attention' ? '確認待ち' : `${s.count}件`);
    body.append(el('div', 'sub', `${s.cli}${s.readonly ? ' · Ask' : ''}${where} · ${status}`));
    pick.append(body);
    // 「確認待ち」の会話は、答える場所（端末操作）まで 1 押しで行く
    const answering = !!(ph && ph.phase === 'attention');
    pick.title = answering ? `「${s.title || '無題の会話'}」を開いて端末操作で答える` : '';
    pick.onclick = () => openSession(s.id, { answer: answering });
    const remove = el('button', 'session-remove', '削除');
    remove.type = 'button';
    remove.title = `${s.title || '無題の会話'}を削除`;
    remove.setAttribute('aria-label', remove.title);
    remove.onclick = (event) => {
      event.stopPropagation();
      removeConversation(s).catch((err) => notice(err.message, 'error'));
    };
    li.append(pick, remove);
    ul.append(li);
  }
  if (!shown.length) ul.append(el('li', 'empty', needle ? '名前が合う会話はない' : (state.repo ? 'まだ会話がない' : '')));
}

// 会話の名前を変える（既定は最初の依頼の先頭。長い会話ほど見分けが付かなくなる）
async function renameConversation() {
  const cur = state.current;
  if (!cur) return;
  const next = prompt('この会話の名前', cur.title || '');
  if (next == null) return;
  const title = next.trim().slice(0, 80);
  if (!title || title === cur.title) return;
  state.current = await api.updateSession(cur.id, { title });
  state.sessions = await api.listSessions(state.repo);
  renderHeader();
  renderSessions();
}

function scheduleLabel(schedule) {
  if (!schedule) return '定期実行なし';
  if (schedule.kind === 'interval') return `${schedule.minutes}分ごと`;
  if (schedule.kind === 'daily') return `毎日 ${schedule.time}`;
  if (schedule.kind === 'weekly') {
    const days = ['日', '月', '火', '水', '木', '金', '土'];
    return `${(schedule.days || []).map((day) => days[day]).join('・')} ${schedule.time}`;
  }
  return '定期実行あり';
}

function taskId(task) { return String(task && (task.id || task.machine) || ''); }

function renderTaskItems() {
  const ul = $('tasks');
  ul.replaceChildren();
  for (const task of state.tasks) {
    const latest = (task.history || [])[0];
    // 状態語は共有ワークベンチと同じ4つ。定義があるタスクは実行結果を出し、AIとの変更が進んでいれば「変更中」を添える。
    const teachingLabels = { draft: '下書き', ready: '利用可能' };
    // 実行状態（履歴・定期実行）はagent-loopがファイル実体を確かめるぶん遅い。定義は先に
    // 出し、まだ重ねていない間は「確認中」と分かるように出す（「未実行」と混同しない）。
    const pending = state.taskStatusPending && !task.teachingStatus;
    const status = task.teachingStatus ? (teachingLabels[task.teachingStatus] || '下書き')
      : pending ? '確認中…'
        : `${latest ? (latest.ok ? '完了' : latest.escalate ? '要確認' : '失敗') : '未実行'}`;
    const id = taskId(task);
    const schedules = Array.isArray(task.schedules) ? task.schedules : (task.schedule ? [task.schedule] : []);
    const scheduleState = pending ? '確認中…'
      : schedules.length ? `${schedules.filter((item) => item.effective !== false).length}/${schedules.length}件の予定` : '予定なし';
    const kind = task.kind === 'prompt' ? 'プロンプト' : task.kind === 'hook' ? 'フック' : task.kind === 'broken' ? '要修正' : 'ステートマシン';
    const li = el('li', `row-item${id === state.selectedTask ? ' active' : ''}`);
    const pick = el('button', 'list-pick');
    const body = el('span', 'grow');
    body.append(el('div', '', task.name || task.machine || id));
    body.append(el('div', 'sub', `${kind} · ${status} · ${scheduleState}`));
    pick.append(body);
    pick.onclick = () => selectAreaItem('tasks', id);
    li.append(pick);
    ul.append(li);
  }
  if (!state.tasks.length) ul.append(el('li', 'empty', state.areaError || (state.repo ? 'まだタスクがない' : '')));
}

function workflowState(workflow) {
  const run = state.workflowRuns.find((item) => item.workflowId === workflow.id || item.input?.workflowId === workflow.id);
  const labels = { launching: '起動中', planning: '計画中', executing: '実行中', waiting: '要確認', stalled: '要確認', done: '完了', failed: '失敗', cancelled: '停止済み' };
  return run ? (labels[run.state] || run.state || '実行中') : workflow.valid === false ? '要修正' : '未実行';
}

function renderWorkflowItems() {
  const ul = $('workflows');
  ul.replaceChildren();
  for (const workflow of state.workflows) {
    const status = workflowState(workflow);
    const li = el('li', `row-item${workflow.id === state.selectedWorkflow ? ' active' : ''}${status === '要確認' ? ' attention' : ''}`);
    const pick = el('button', 'list-pick');
    const body = el('span', 'grow');
    body.append(el('div', '', workflow.name || workflow.id));
    body.append(el('div', 'sub', `${workflow.teaching ? workflow.teachingStatus : status}${workflow.nodes ? ` · ${workflow.nodes}工程` : ''}`));
    pick.append(body);
    pick.onclick = () => selectAreaItem('workflows', workflow.id);
    li.append(pick);
    ul.append(li);
  }
  if (!state.workflows.length) ul.append(el('li', 'empty', state.areaError || (state.repo ? 'まだワークフローがない' : '')));
}

// サイドバーの「共有」に未読のひとことの数を出す
function renderShareUnread() {
  const button = $('area-share');
  const count = shareEnabled() ? Share.unread() : 0;
  let badge = button.querySelector('.unread');
  if (!count) { if (badge) badge.remove(); return; }
  if (!badge) { badge = el('span', 'unread'); button.append(badge); }
  badge.textContent = String(count);
}

function renderAreaContext() {
  const info = AgentNavigation.areaInfo(state.area);
  $('area-list-title').textContent = info.label;
  $('session-new').setAttribute('aria-label', info.createLabel);
  $('session-new').title = info.createLabel;
  for (const id of ['sessions', 'tasks', 'workflows', 'share-requests']) $(id).hidden = id !== info.listId;
  $('session-filter-row').hidden = state.area !== 'conversation';
  $('session-new').hidden = state.area === 'share';      // 共有の依頼は会話から出す
  if (state.area === 'conversation') renderSessions();
  else if (state.area === 'tasks') renderTaskItems();
  else if (state.area === 'workflows') renderWorkflowItems();
  else Share.render();
}

// 選んでいたタスクを新しい一覧の中から選び直す（無ければ設定の記憶、それも無ければ先頭）。
function pickSelectedTask(repo, tasks) {
  const remembered = (state.config.lastTask || {})[repo] || state.selectedTask;
  const rememberedTask = tasks.find((item) => taskId(item) === remembered || item.machine === remembered);
  return rememberedTask ? taskId(rememberedTask) : taskId(tasks[0]);
}

// 実行状態（agent-loop が設定とファイル実体を確かめるぶん遅い）を後から重ねる。
// loadTaskItems は待たずに戻るので、届いたときに画面が別のリポジトリ・領域・タスク一覧へ
// 移っていたら（token / repo がずれていたら）捨てる。
function refreshTaskSnapshot(repo, token, definitions, teaching) {
  api.automation.runSnapshot(repo).then((snapshot) => {
    if (token !== state.taskToken || repo !== state.repo) return;
    state.tasks = AgentNavigation.taskItems(snapshot, definitions, teaching);
    state.selectedTask = pickSelectedTask(repo, state.tasks);
    state.taskStatusPending = false;
    renderAreaContext();
  }, (err) => {
    if (token !== state.taskToken || repo !== state.repo) return;
    state.areaError = (err && err.message) || String(err);
    state.taskStatusPending = false;
    renderAreaContext();
  });
}

// タスク一覧は「定義の確認」（速い・ファイルを読むだけ）と「実行状態の確認」（遅い）の 2 段に
// 分ける。定義が出た時点で一覧を見せて戻る——UI はここで止めない。実行状態は
// refreshTaskSnapshot が裏で取りに行き、届いたら重ねて出す。
async function loadTaskItems(repo) {
  const token = (state.taskToken += 1);
  let definitions;
  let teaching;
  try {
    [definitions, teaching] = await Promise.all([
      api.automation.listMachines(repo),
      api.automation.teachingList(repo),
    ]);
  } catch (err) {
    if (token !== state.taskToken || repo !== state.repo) return;
    state.areaError = (err && err.message) || String(err);
    state.tasks = [];
    state.taskStatusPending = false;
    renderAreaContext();
    return;
  }
  if (token !== state.taskToken || repo !== state.repo) return;
  state.tasks = AgentNavigation.taskItems(null, definitions, teaching);
  state.selectedTask = pickSelectedTask(repo, state.tasks);
  state.taskStatusPending = true;
  renderAreaContext();
  refreshTaskSnapshot(repo, token, definitions, teaching);
}

async function loadWorkflowItems(repo) {
  try {
    const [workflows, runs, teaching] = await Promise.all([
      api.automation.flowList(repo),
      api.automation.flowRunList(repo, 30),
      api.automation.flowTeachingList(repo),
    ]);
    const ready = workflows || [];
    const labels = { draft: '作成中', 'needs-trial': '試運転待ち', 'awaiting-confirmation': '確認待ち' };
    const drafts = (teaching || []).filter((item) => item.status !== 'ready' && !ready.some((flow) => flow.id === item.workflowId)).map((item) => ({
      id: item.workflowId, name: item.title, nodes: 0, valid: true, teaching: true,
      teachingStatus: labels[item.status] || item.status,
    }));
    state.workflows = [...drafts, ...ready];
    state.workflowRuns = runs || [];
    const remembered = (state.config.lastWorkflow || {})[repo] || state.selectedWorkflow;
    state.selectedWorkflow = state.workflows.some((item) => item.id === remembered) ? remembered : (state.workflows[0]?.id || '');
  } catch (err) {
    state.areaError = (err && err.message) || String(err);
    state.workflows = [];
    state.workflowRuns = [];
  }
  renderAreaContext();
}

async function loadAreaItems() {
  state.areaError = '';
  if (!state.repo || state.area === 'conversation') { renderAreaContext(); return; }
  if (state.area === 'tasks') await loadTaskItems(state.repo);
  else await loadWorkflowItems(state.repo);
}

// 会話からの「この依頼をタスクにする」（intent）は、新しいタスクの画面（action: new）として開き、
// 本文は親の作成フォーム（taskTeaching.js）が受け取る。
function frameMessage(action = '') {
  return {
    type: 'agent-app:navigate', area: state.area, root: state.repo,
    selected: state.area === 'tasks' ? state.selectedTask : state.selectedWorkflow,
    action: action || (state.area === 'tasks' && state.pendingTaskIntent ? 'new' : ''),
  };
}

function syncAutomationWorkbench(action = '') {
  if (state.area === 'conversation') return;
  return $('automation-workbench').navigate(frameMessage(action));
}

function renderAutomationHeader() {
  const workflows = state.area === 'workflows';
  $('automation-title').textContent = workflows ? 'ワークフロー' : 'タスク';
  $('automation-description').hidden = true;
  $('automation-description').textContent = '';
}

function setAutomationLoading(loading) {
  $('automation-content').setAttribute('aria-busy', String(!!loading));
  $('automation-loading').hidden = !loading;
}

// AI と作り始めたタスクを選び直し、その会話（AI相談）を開く。
async function openTaughtTask(machine) {
  state.pendingTaskIntent = null;
  state.selectedTask = `machine:${machine}`;
  const selected = state.selectedTask;
  const lastTask = { ...(state.config.lastTask || {}), [state.repo]: selected };
  state.config = { ...state.config, lastTask };
  api.saveConfig({ lastTask }).then((saved) => {
    if (state.selectedTask === selected) state.config = saved;
  }).catch((err) => notice(err.message, 'error'));
  // 親側の一覧更新は待たず、準備済みの下書きへ先に遷移する。共有ワークベンチも
  // 自分で下書き一覧を読むため、ここで同じ I/O を直列に待つ必要はない。
  loadAreaItems().catch((err) => notice(err.message, 'error'));
  await syncAutomationWorkbench('teach');
}

async function handleAutomationEvent(payload) {
  if (!payload || payload.type !== 'agent-app:changed' || payload.root !== state.repo || payload.area !== state.area) return;
  if (payload.selected) {
    const key = payload.area === 'tasks' ? 'lastTask' : 'lastWorkflow';
    if (payload.area === 'tasks') state.selectedTask = payload.selected;
    else state.selectedWorkflow = payload.selected;
    state.config = await api.saveConfig({ [key]: { ...(state.config[key] || {}), [state.repo]: payload.selected } });
  }
  await loadAreaItems();
}

async function selectAreaItem(area, id) {
  const token = (state.selectionToken += 1);
  let configReady;
  if (area === 'tasks') {
    state.selectedTask = id;
    const lastTask = { ...(state.config.lastTask || {}), [state.repo]: id };
    state.config = { ...state.config, lastTask };
    configReady = api.saveConfig({ lastTask });
  } else {
    state.selectedWorkflow = id;
    const lastWorkflow = { ...(state.config.lastWorkflow || {}), [state.repo]: id };
    state.config = { ...state.config, lastWorkflow };
    configReady = api.saveConfig({ lastWorkflow });
  }
  renderAreaContext();
  syncAutomationWorkbench();
  setSidebar(false);
  const saved = await configReady;
  if (token === state.selectionToken) state.config = saved;
}

// リポジトリを選ぶ。**ホストに聞くもの（CLI の有無・git worktree）を待たずに画面を出す。**
// どちらも 1 本の常駐シェルに並ぶので、Windows では WSL の起動と git status の分だけ
// 何秒も待つ——その間も会話一覧・ツリー・タスク一覧は手元のファイルだけで描ける。
// 届き次第そこだけ描き直し、待っている間に別のリポジトリへ移っていたら捨てる（repoToken）。
async function selectRepo(repo) {
  state.repo = repo || '';
  const token = (state.repoToken += 1);
  if (state.pendingTaskIntent && state.pendingTaskIntent.root !== state.repo) state.pendingTaskIntent = null;
  if (repo) state.config = await api.saveConfig({ lastRepo: repo });
  state.sessions = repo ? await api.listSessions(repo) : [];
  state.worktree = (state.config.lastWorktree || {})[state.repo] || '';
  state.worktrees = [];
  state.agents = [];
  state.agentsLoading = !!repo;
  state.agentsReady = repo
    ? api.listAgents(repo).catch((e) => { notice(e.message, 'error'); return []; }).then((agents) => {
      if (token !== state.repoToken) return;
      state.agents = agents;
      state.agentsLoading = false;
      renderAgents();
      renderRunSettingsSummary();
      renderRestrictions();
    })
    : Promise.resolve();
  // git worktree の確認は Windows / WSL では数秒かかることがある。初回の領域表示を
  // ここで止めず、一覧が届いた時点で作業フォルダ欄だけを更新する。
  state.capabilities = null;
  if (repo) {
    api.automation.capabilities(repo).then((caps) => {
      if (token !== state.repoToken) return;
      state.capabilities = caps;
      renderRestrictions();
    }, () => {});
  }
  refreshWorktrees({ token });
  renderRepos();
  renderAgents();
  newDraft();
  Files.setRoot(state.repo, activeWorktree(), { lastFile: (state.config.lastFiles || {})[state.repo] || '' }).catch(() => {});
  await loadAreaItems();
  syncAutomationWorkbench();
  if (state.changesOpen) refreshChanges();
}

// ---- 作業フォルダ（git worktree） -------------------------------------------

// 2 段で読む: まず `git worktree list` だけで一覧を出し、変更数・先行コミット数
// （worktree ごとの git status。Windows の /mnt/c では何秒もかかる）はあとから足す。
async function refreshWorktrees({ token = state.repoToken } = {}) {
  if (!state.repo || !worktreeUI()) {
    state.worktrees = [];                 // 機能を切っているときは git にも聞かない
    renderWorktreeSelect();
    Files.renderRoots([], false);
    return;
  }
  const apply = (res) => {
    if (token !== state.repoToken) return false;
    state.worktrees = (res && res.items) || [];
    // 下書きが覚えていた作業フォルダが消えていたら本体へ戻す（一覧を引けたときだけ言える）
    if (state.draft && state.worktree && !state.worktrees.some((w) => w.name === state.worktree && w.selectable)) {
      state.worktree = '';
      Files.setRoot(state.repo, activeWorktree(), {}).catch(() => {});
    }
    renderWorktreeSelect();
    Files.renderRoots(state.worktrees, true);
    return true;
  };
  try {
    if (!apply(await api.listWorktrees(state.repo, { withStatus: false }))) return;
    apply(await api.listWorktrees(state.repo, { withStatus: true }));
  } catch {
    if (token !== state.repoToken) return;
    state.worktrees = [];                 // git リポジトリでない等。本体だけで動く
    renderWorktreeSelect();
    Files.renderRoots(state.worktrees, true);
  }
}

function renderWorktreeSelect() {
  const sel = $('worktree');
  const on = worktreeUI();
  sel.closest('label').hidden = !on;
  $('wt-manage').hidden = !(on && state.config && state.config.useWorktree);
  if (!on) return;
  sel.replaceChildren();
  const cur = activeWorktree();
  const add = (value, label) => { const o = el('option', '', label); o.value = value; sel.append(o); };
  add('', 'リポジトリ本体');
  for (const w of state.worktrees.filter((x) => x.selectable)) add(w.name, `${w.name}（${w.branch || 'detached'}）`);
  // 会話が使っていた作業フォルダが消えていても、選択として見えるようにしておく
  // 「見つからない」と言えるのは一覧を引けたときだけ（機能を切っていると引いていない）
  if (cur && !state.worktrees.some((w) => w.name === cur && w.selectable)) add(cur, state.worktrees.length ? `${cur}（見つからない）` : cur);
  sel.value = cur;
  sel.disabled = !state.draft;            // 会話ごとに固定（tmux の cwd も CLI の文脈もそこで始まっている）
  sel.title = state.draft ? '会話ごとに git worktree で作業フォルダを分ける'
    : 'この会話の作業フォルダ（会話を作ったあとは変えられない）';
  renderRunSettingsSummary();
}

// ブランチ名 → フォルダ名（main の worktree.js と同じ規則。画面で先に見せるため）
function slug(branch) {
  return String(branch || '').trim().replace(/[^\w.@+-]+/g, '-').replace(/^[-.]+/, '').replace(/[-.]+$/, '').slice(0, 60);
}

function dialogError(text) {
  const n = $('wt-error');
  n.textContent = text || '';
  n.hidden = !text;
}

function renderWorktreeList() {
  const tb = $('wt-list');
  tb.replaceChildren();
  for (const w of state.worktrees) {
    const tr = el('tr');
    tr.append(el('td', 'wt-name', w.main ? 'リポジトリ本体' : (w.name || w.path)));
    tr.append(el('td', '', w.branch || `(detached ${String(w.head).slice(0, 7)})`));
    const state_ = [];
    if (w.dirty) state_.push(`${w.dirty} 変更`);
    if (w.ahead) state_.push(`${w.ahead} コミット先`);
    if (w.locked) state_.push('ロック中');
    if (!w.main && !w.selectable) state_.push('この画面の外で作られた');
    tr.append(el('td', 'sub', state_.join(' · ')));
    const act = el('td', 'wt-act');
    if (!w.main && w.name) {
      const used = state.sessions.filter((s) => s.worktree === w.name).length;
      const b = el('button', 'small danger', '削除');
      b.title = used ? `この作業フォルダを使っている会話が ${used} 件ある（会話自体は残る）` : '';
      b.onclick = () => removeWorktree(w, used);
      act.append(b);
    }
    tr.append(act);
    tb.append(tr);
  }
  if (!state.worktrees.length) {
    const tr = el('tr');
    tr.append(el('td', 'sub', 'Gitリポジトリではないため、作業フォルダを作成できません'));
    tb.append(tr);
  }
}

async function removeWorktree(w, used) {
  const warn = [
    `${w.name}（${w.branch || 'detached'}）を削除しますか？`,
    used ? `このフォルダを使う ${used} 件の会話は続行できなくなります。履歴は残ります。` : '',
    w.dirty ? `未コミットの変更: ${w.dirty} 件` : '',
    w.ahead ? `本体にないコミット: ${w.ahead} 件。ブランチ ${w.branch} は残ります。` : '',
  ].filter(Boolean).join('\n');
  if (!confirm(warn)) return;
  dialogError('');
  try {
    await api.removeWorktree(state.repo, w.name, { force: false });
  } catch (err) {
    // 未コミットの変更が残っていると git が断る。押し切るかはここで聞く
    if (!/未コミット/.test(err.message) || !confirm(`${err.message}\n\n未コミットの変更も削除しますか？`)) { dialogError(err.message); return; }
    try { await api.removeWorktree(state.repo, w.name, { force: true }); } catch (e2) { dialogError(e2.message); return; }
  }
  await afterWorktreeChange();
}

async function createWorktree() {
  const branch = $('wt-branch').value.trim();
  if (!branch) { dialogError('ブランチ名を入れてください'); return; }
  dialogError('');
  $('wt-create').disabled = true;
  try {
    const w = await api.createWorktree(state.repo, branch, $('wt-base').value.trim(), '');
    $('wt-branch').value = '';
    $('wt-base').value = '';
    $('wt-path').textContent = '';
    await afterWorktreeChange();
    if (state.draft) { state.worktree = w.name; await selectWorktree(w.name); }
    notice(w.reusedBranch ? `既存ブランチ ${w.branch} の作業フォルダ ${w.name} を作成しました` : `${w.name}（${w.branch}）を作成しました`);
  } catch (err) {
    dialogError(err.message);
  } finally {
    $('wt-create').disabled = false;
  }
}

async function afterWorktreeChange() {
  await refreshWorktrees();
  renderWorktreeList();
  state.sessions = state.repo ? await api.listSessions(state.repo) : [];
  renderSessions();
  if (state.changesOpen) refreshChanges();
}

// 下書きの作業フォルダを切り替える（ファイル画面と変更ビューもそこへ向ける）
async function selectWorktree(name) {
  state.worktree = name || '';
  state.config = await api.saveConfig({ lastWorktree: { ...(state.config.lastWorktree || {}), [state.repo]: state.worktree } });
  renderWorktreeSelect();
  await Files.setRoot(state.repo, state.worktree, {});
  if (state.changesOpen) refreshChanges();
}

// ---- 上: エージェント・モデル・モード ------------------------------------

const POLICY_VIEW = {
  recommended: { label: 'おすすめ', tier: 'medium' },
  saving: { label: '節約', tier: 'small' },
  quality: { label: '品質重視', tier: 'large' },
  direct: { label: '直接指定', tier: '' },
  shared: { label: '共有', tier: '' },
};
const SKILL_MODE_LABEL = { auto: '自動', manual: '手動選択', off: '使用しない' };
const PRIORITY_LABEL = { high: '高', normal: '通常', low: '低' };
// 最適化が効いていないときに選べる起動方針（settings.BASIC_POLICIES と同じ）。
const BASIC_POLICIES = ['recommended'];

// ローカル実行系（agent-herd の一族）が使えるか。一覧の仮想の `herd` の印で見る。届く前は「使える」と
// みなす（先に薄くして後で戻すより、戻すほうが目立たない）。
function herdAvailable() {
  if (state.agentsLoading || !state.agents.length) return state.capabilities ? !!state.capabilities.herd : true;
  return state.agents.some((a) => a.virtual && a.name === 'herd' && a.available);
}

// 「エージェントを最適化する」が効いているか（設定 × herd の有無）。効いていなければ、起動方針は
// おすすめ / 直接指定だけ、tier は medium だけ。
function optimized(config = state.config) {
  const execution = config && config.execution ? config.execution : {};
  return execution.optimizeAgents !== false && herdAvailable();
}

// 起動方針「共有」は設定 > 共有を使うと決めているときだけ選べる（LAN の参加者に依頼を回す）。
function shareEnabled(config = state.config) {
  return !!(config && config.share && config.share.enabled);
}

// いま入力欄が「共有に依頼」を向いているか（起動方針は選ばず、この入力先が決める）。
function sharing() {
  return state.input.mode === 'share' && shareEnabled();
}

function currentPolicy() {
  return sharing() ? 'shared' : $('policy').value;
}

function effectivePolicy(policy, on = optimized()) {
  const name = String(policy || '');
  if (name === 'direct') return name;
  if (name === 'shared') return shareEnabled() ? name : 'recommended';
  if (!POLICY_VIEW[name]) return 'recommended';
  return on || BASIC_POLICIES.includes(name) ? name : 'recommended';
}

// 選べない方針・tier・領域を薄くする（理由は出さない）。
function renderRestrictions() {
  const on = optimized();
  const select = $('policy');
  for (const option of select.options) option.disabled = !on && !BASIC_POLICIES.includes(option.value) && option.value !== 'direct';
  if (select.selectedOptions[0] && select.selectedOptions[0].disabled) { select.value = 'recommended'; renderRunSettingsSummary(); }
  const caps = state.capabilities;
  $('area-workflows').disabled = !!(caps && caps.agentFlow === false);
  renderSettingsRestrictions();
}

// 設定 > 実行制御: チェックの状態（保存前）と herd の有無で、方針と tier の行を薄くする。
function renderSettingsRestrictions() {
  const toggle = $('optimize-agents');
  const on = toggle.checked && herdAvailable();
  for (const input of document.querySelectorAll('input[name="default-policy"]')) {
    const allowed = on || BASIC_POLICIES.includes(input.value);
    input.disabled = !allowed;
    input.closest('label').classList.toggle('is-off', !allowed);
    if (!allowed && input.checked) { input.checked = false; document.querySelector('input[name="default-policy"][value="recommended"]').checked = true; }
  }
  for (const tier of ['small', 'medium', 'large']) {
    const allowed = on || tier === 'medium';
    $(`tier-${tier}-cli`).disabled = !allowed;
    $(`tier-${tier}-model`).disabled = !allowed;
    $(`tier-${tier}-cli`).closest('.tier-row').classList.toggle('is-off', !allowed);
  }
}

function skillCandidates() {
  const selection = state.config && state.config.instructions && state.config.instructions.skillSelection;
  return selection && Array.isArray(selection.candidates) ? selection.candidates : [];
}

function renderTurnSkills() {
  const mode = SKILL_MODE_LABEL[state.turnSkillMode] ? state.turnSkillMode : 'auto';
  const list = $('turn-skill-list');
  $('turn-skill-mode').value = mode;
  list.hidden = mode === 'off';
  list.replaceChildren();
  if (mode === 'auto') {
    const names = state.turnSkillPreview.map((item) => item.name);
    list.append(el('span', 'sub', names.length ? names.join(' · ') : '該当なし'));
    return;
  }
  for (const name of skillCandidates()) {
    const label = el('label', 'skill-choice');
    const input = el('input');
    input.type = 'checkbox';
    input.checked = state.turnSkills.includes(name);
    input.onchange = () => {
      state.turnSkills = input.checked ? [...new Set([...state.turnSkills, name])] : state.turnSkills.filter((item) => item !== name);
      renderRunSettingsSummary();
    };
    label.append(input, el('span', '', name));
    list.append(label);
  }
  if (!skillCandidates().length) list.append(el('span', 'sub', '候補なし'));
}

async function refreshTurnSkillPreview() {
  if (!state.repo || state.turnSkillMode !== 'auto') { state.turnSkillPreview = []; renderTurnSkills(); renderRunSettingsSummary(); return; }
  try {
    const result = await api.selectSkills(state.repo, $('prompt').value, 'auto', []);
    state.turnSkillPreview = result.selected || [];
  } catch { state.turnSkillPreview = []; }
  renderTurnSkills();
  renderRunSettingsSummary();
}

function selectedExecution(policy = currentPolicy()) {
  if (policy === 'direct') return { policy, tier: '', cli: $('cli').value, model: $('model').value.trim() };
  policy = effectivePolicy(policy);
  // 共有: エージェントは「どれでも」（'*' → 空）か、参加者が提供している名前
  if (policy === 'shared') return { policy, tier: '', cli: $('cli').value === '*' ? '' : $('cli').value, model: $('model').value.trim() };
  const view = POLICY_VIEW[policy] || POLICY_VIEW.recommended;
  const tier = state.config.execution.tiers[view.tier];
  return { policy, tier: view.tier, cli: tier.cli, model: tier.model || '' };
}

function renderAgents() {
  const sel = $('cli');
  sel.replaceChildren();
  const useTmux = state.config.transport === 'tmux' && state.host && state.host.tmux;
  const usable = state.agents.filter((a) => a.available);
  for (const a of usable) {
    const mark = useTmux ? (a.interactive ? '' : '（対話定義なし→ヘッドレス）') : (a.session === 'replay' ? '（履歴再送）' : a.session === 'continue' ? '（--continue）' : '');
    const o = el('option', '', `${a.name}${mark}`);
    o.value = a.name;
    sel.append(o);
  }
  if (!usable.length) {
    sel.append(el('option', '', state.agentsLoading || !state.host ? 'CLI を確認中…'
      : (state.host.platform === 'win32' ? 'WSL に CLI が無い' : 'この PC に CLI が無い')));
  }
  const want = state.current ? state.current.cli : state.config.lastCli;
  if ([...sel.options].some((o) => o.value === want)) sel.value = want;
}

function renderRunSettingsSummary() {
  const summary = $('run-settings-summary');
  if (!summary) return;
  // 「どれでも」の選択肢は、要約を組み立てる前に入れ替える（選ばれている CLI がそこで変わる）
  const shared = sharing();
  renderAnyAgentOption(shared);
  const selected = selectedExecution();
  const policy = POLICY_VIEW[selected.policy] || POLICY_VIEW.recommended;
  const agent = selected.cli || (shared ? 'どれでも' : 'エージェント未設定');
  const model = selected.model;
  const mode = (shared || $('permission-mode').value === 'ask') ? 'Ask'
    : ($('permission-mode').value === 'auto' ? '自動承認' : '確認あり');
  const location = activeWorktree() ? '分離フォルダ' : 'リポジトリ本体';
  const skillLabel = `スキル ${SKILL_MODE_LABEL[state.turnSkillMode] || SKILL_MODE_LABEL.auto}`;
  // 共有は「誰が・どの優先度で」だけ。起動方針・権限・作業フォルダはこの PC の話なので出さない
  summary.textContent = shared
    ? [`${agent}${model ? ` / ${model}` : ''}`, `優先度 ${PRIORITY_LABEL[$('priority').value] || '通常'}`, skillLabel].join(' · ')
    : [policy.label, `${agent}${model ? ` / ${model}` : ''}`, skillLabel, mode, location].filter(Boolean).join(' · ');
  summary.title = summary.textContent;
  $('direct-agent-settings').hidden = !(selected.policy === 'direct' || shared);
  $('policy-field').hidden = shared;
  $('permission-field').hidden = shared;
  $('share-priority-field').hidden = !shared;
  $('worktree-field').hidden = shared || !worktreeUI();
  const waiting = shareWaiting();
  $('send').setAttribute('aria-label', waiting ? 'メッセージを送信' : (shared ? '依頼を共有へ送信' : '依頼を送信'));
  $('send').querySelector('.send-label').textContent = waiting ? '送る' : (shared ? '依頼する' : '送信');
  renderTurnSkills();
}

// 共有のときだけ、エージェントの選択肢の先頭に「どれでも」を置く（外したら元の選択へ戻す）。
function renderAnyAgentOption(shared) {
  const sel = $('cli');
  const any = [...sel.options].find((o) => o.value === '*');
  if (shared) {
    if (any) return;
    const o = el('option', '', 'どれでも');
    o.value = '*';
    sel.prepend(o);
    if (!(state.current && state.current.cli && [...sel.options].some((x) => x.value === state.current.cli))) sel.value = '*';
  } else if (any) {
    if (sel.value === '*') sel.value = state.config.lastCli || '';
    any.remove();
  }
}

// この会話が共有の答えを待っているか。待っている間は端末ミラーに**引き受けた人の画面**が出る。
function shareWaiting(sess = state.current) {
  const id = sess && sess.share ? sess.share.id : '';
  if (!id) return null;
  const status = Share.status();
  const found = (status && status.mine ? status.mine : []).find((r) => r.id === id);
  if (found && !(found.state === 'open' || found.state === 'working')) return null;
  return { id, node: found ? found.executor : '', cli: found ? found.executorCli : '', state: found ? found.state : 'open' };
}

// 待っている間だけ出る、引き受けた人とのやり取り（吹き出しは共有画面と同じ talk.js）
function renderShareTalk(waiting) {
  const box = $('share-talk');
  box.hidden = !waiting;
  // 段が 1 つ増えるので、端末と履歴の取り分を詰める（利用者の開閉は触らない）
  $('chat').classList.toggle('has-talk', !!waiting);
  if (!waiting) return;
  const status = Share.status();
  const request = (status && status.mine ? status.mine : []).find((r) => r.id === waiting.id);
  const talk = (request && request.talk) || [];
  const unread = Talk.unread(waiting.id, talk);
  if (unread) box.open = true;
  $('share-talk-count').textContent = `${talk.length}件${unread ? ` · 未読 ${unread}` : ''}`;
  Talk.render($('share-talk-body'), { id: waiting.id, talk, me: (status && status.node) || '', read: box.open });
}

// 会話ヘッダーの状態の印。「確認待ち」のときだけ押せて、**答える場所へ連れて行く**（端末操作へ
// 切り替えて端末に焦点を移す）。何を聞かれているかは端末ミラーにそのまま出ているので、ここでは
// 繰り返さない。答え方も決めない——CLI によって `y` が効くもの（テキスト入力）と効かないもの
// （反転選択メニュー）があり、見分けるには画面の文言を読むことになる（ADR-1）。
function renderPhase(ph) {
  const node = $('phase');
  node.hidden = !ph;
  if (!ph) return;
  const answerable = ph.phase === 'attention';
  node.textContent = PHASE_LABEL[ph.phase] || ph.phase;
  node.className = `phase ${ph.phase}${answerable ? ' answerable' : ''}`;
  node.title = answerable ? '押すと端末操作へ移り、そのまま答えられる' : (ph.detail || '');
  if (answerable) {
    node.tabIndex = 0;
    node.setAttribute('role', 'button');
    node.onclick = () => setInputMode('terminal');
    node.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); node.click(); }
    };
  } else {
    node.removeAttribute('tabindex');
    node.removeAttribute('role');
    node.onclick = null;
    node.onkeydown = null;
  }
}

function renderHeader() {
  const cur = state.current;
  $('chat-title').textContent = cur ? (cur.title || '（無題）') : (state.repo ? `${basename(state.repo)} で新しい会話` : 'リポジトリを登録して会話を始める');
  // 別のリポジトリから分岐した会話は、題名の下に分岐元を 1 行出す（押すと元の会話へ戻る）
  const origin = cur && cur.originSession;
  $('chat-origin').hidden = !origin;
  if (origin) {
    $('chat-origin').textContent = `分岐元: ${basename(origin.repo)} › ${origin.title || '（無題）'}`;
    $('chat-origin').title = origin.repo;
    $('chat-origin').onclick = () => openSessionInRepo(origin.repo, origin.id).catch((err) => notice(err.message, 'error'));
  }
  // エージェント・モデル・モードは「次のターン」のもの。会話を開いていても変えられる
  $('cli').disabled = !state.repo;
  $('policy').disabled = !state.repo;
  if (cur) {
    $('policy').value = effectivePolicy(cur.policy === 'shared' ? state.config.execution.defaultPolicy : (cur.policy || 'direct'));
    if ([...$('cli').options].some((o) => o.value === cur.cli)) $('cli').value = cur.cli;
    $('model').value = cur.model || '';
    $('permission-mode').value = cur.readonly ? 'ask' : (cur.autoApprove ? 'auto' : 'confirm');
  } else {
    $('policy').value = effectivePolicy(state.config.execution.defaultPolicy);
    $('model').value = state.config.lastModel || '';
    $('permission-mode').value = state.config.execution.defaultReadonly ? 'ask'
      : (state.config.execution.defaultAutoApprove ? 'auto' : 'confirm');
  }
  $('session-new').disabled = !state.repo;
  $('changes-toggle').disabled = !state.repo;
  $('chat-more').hidden = !state.repo;
  $('composer').hidden = !state.repo;
  $('session-delete').hidden = !cur;
  $('session-rename').hidden = !cur;
  const busy = !!cur && (state.running.has(cur.id) || state.pending.has(cur.id));
  $('session-handoff').hidden = !cur || cur.kind !== 'conversation';
  $('session-handoff').disabled = busy || !!state.handoffId || !cur?.messages.length;
  $('session-handoff').textContent = state.handoffId === cur?.id ? '引き継ぎ中…' : '新しいセッションに引き継ぐ';
  $('stop').hidden = !busy;
  $('send').disabled = !state.repo || (!!cur && (state.pending.has(cur.id) || state.handoffId === cur.id));
  $('session-delete').disabled = !!cur && state.handoffId === cur.id;
  $('send').classList.toggle('sending', !!cur && state.pending.has(cur.id));
  if (!state.pending.size) $('send').classList.remove('sending');
  const tm = isTmux(cur);
  const ph = tm ? state.phases.get(cur.id) : null;
  renderPhase(ph);
  const waiting = shareWaiting(cur);
  $('term-restart').hidden = !(ph && (ph.phase === 'dead' || ph.phase === 'gone'));
  $('conversation-start').hidden = !!cur;
  $('terminal-stage').hidden = !(tm || (waiting && waiting.state === 'working'));
  // 端末（手元の tmux か、共有で映している相手の画面）があるときは、履歴は畳んだ脇役のまま
  const mirror = tm || !!(waiting && waiting.state === 'working');
  $('conversation-history').hidden = !cur;
  $('conversation-history').classList.toggle('history-only', !mirror);
  if (cur && !mirror) $('conversation-history').open = true;
  $('history-count').textContent = cur && cur.messages ? `${cur.messages.length}件` : '';
  $('term-agent').textContent = waiting ? `${waiting.node || '参加者'} の ${waiting.cli || 'AI'}`
    : (tm ? [cur.cli, cur.model].filter(Boolean).join(' · ') : '');
  $('term-name').textContent = waiting ? '共有 · 閲覧のみ' : (ph && ph.name ? `tmux -L agent-app attach -t ${ph.name}` : '');
  // 待っている間は、引き受けた人の tmux の画面をそのまま描く（キーは送れない）
  if (waiting && waiting.state === 'working') {
    const fresh = Term.current() !== waiting.id;
    Term.attachRemote(waiting.id, $('term-host'));
    if (fresh) {
      api.share.screen(waiting.id)
        .then((text) => { if (text && Term.current() === waiting.id) Term.applyScreen({ id: waiting.id, text }); })
        .catch(() => { /* まだ画面が無い */ });
    }
  } else if (Term.isRemote()) Term.detach();
  if (state.input.mode === 'terminal' && !tm) setInputMode('message', { focus: false });
  else {
    $('input-mode-terminal').disabled = !tm || !!(ph && (ph.phase === 'dead' || ph.phase === 'gone'));
    Term.setInputEnabled(state.input.mode === 'terminal' && !$('input-mode-terminal').disabled);
  }
  $('run-settings').hidden = !state.repo || !!waiting;
  $('attach').hidden = !!waiting;
  renderShareTalk(waiting);
  if ((state.input.mode === 'share' && !shareEnabled()) || waiting) setInputMode(state.input.mode, { focus: false });
  else $('input-mode-share').hidden = !shareEnabled();
  // 共有の答えを待っている間、「停止」は列からの取り下げになる
  $('stop').textContent = waiting ? '取り下げ' : '停止';
  renderRunSettingsSummary();
}

// ---- 中央: メッセージ --------------------------------------------------------

// 添付の印。id 持ち（写したファイル）は既定のアプリで開き、rel 持ち（作業フォルダの中）はビュアーで開く
function chipNode(a, { onRemove = null } = {}) {
  const c = el('span', `chip${a.rel ? ' repo' : ''}${onRemove ? '' : ' link'}`);
  c.append(el('span', 'name', a.rel || a.name));
  if (a.size != null) c.append(el('span', 'sub', fmtSize(a.size)));
  c.title = a.rel ? `${a.rel}（作業フォルダの中。パスを伝えるだけで写さない）` : a.name;
  if (onRemove) {
    const x = el('button', 'x', '×');
    x.title = '外す';
    x.onclick = (e) => { e.stopPropagation(); onRemove(); };
    c.append(x);
  } else if (a.rel) {
    c.onclick = () => { showView('files'); Files.setRoot(state.repo, activeWorktree(), {}).then(() => Files.openFile(a.rel)).then(() => Files.reveal(a.rel)); };
  } else if (a.id) {
    c.onclick = () => api.openAttachment(a.id, a.name).catch((e) => notice(e.message, 'error'));
  }
  if (!onRemove && c.onclick) {
    c.tabIndex = 0;
    c.setAttribute('role', 'button');
    c.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); c.click(); }
    };
  }
  return c;
}

function informationText(item) {
  if (!item) return '';
  if (item.type === 'file') return `${item.action || 'modified'} · ${item.title || ''}`;
  return item.title || item.text || '';
}

function responseDisclosure(kind, title, items, { open = false, running = false, raw = null } = {}) {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length && !running && !raw) return null;
  const details = el('details', `response-disclosure ${kind}`);
  details.open = open;
  const summary = el('summary');
  summary.append(el('span', 'disclosure-title', title));
  if (running) summary.append(el('span', 'spin'));
  else if (values.length) summary.append(el('span', 'disclosure-count', `${values.length}件`));
  details.append(summary);
  const body = el('div', 'disclosure-body');
  if (running && !values.length) body.append(el('div', 'response-item running', 'エージェントが依頼を処理しています'));
  for (const item of values) {
    const row = el('div', `response-item ${item.status || ''}`);
    row.append(el('span', 'response-dot'));
    const content = el('div', 'response-item-body');
    content.append(el('div', 'response-item-title', kind === 'information' ? informationText(item) : (item.text || item.title || '')));
    if (item.detail) content.append(el('pre', 'response-detail', item.detail));
    row.append(content);
    body.append(row);
  }
  if (raw) body.append(raw);
  details.append(body);
  return details;
}

function rawExecutionNode(id, tmuxMode) {
  const details = el('details', 'raw-execution');
  details.append(el('summary', '', '生ログ'));
  if (tmuxMode) {
    const tail = el('pre', 'tail', state.tails.get(id) || '');
    details.append(tail);
    const link = el('div', 'link');
    const button = el('button', 'small', '端末を操作');
    button.onclick = () => setInputMode('terminal');
    link.append(button);
    details.append(link);
  } else {
    const log = el('div', 'log');
    for (const line of state.logs.get(id) || []) log.append(logLine(line));
    details.append(log);
  }
  return details;
}

// 固定文や前の依頼を入力欄へ置く。**送らない**——送るのは利用者（「操作の見本」と同じ作法）。
// 書きかけは消さない。入っているのがこちらで入れた本文のときだけ入れ直す。
function fillPrompt(text, { attachments: files = [] } = {}) {
  const body = String(text || '');
  if (!body) return;
  const prompt = $('prompt');
  if (!prompt.value.trim() || prompt.value === state.filledPrompt) {
    prompt.value = body;
    state.filledPrompt = body;
  } else {
    inputStatus('error', '入力欄に書きかけがあります', 2600);
    return;
  }
  if (files.length) addAttachments(files);
  setInputMode('message', { focus: false });
  prompt.focus();
  prompt.setSelectionRange(prompt.value.length, prompt.value.length);
  refreshTurnSkillPreview();
}

// ターンが終わったあとの「次の一手」。設定 > 共通指示 の定型の依頼を、最後の応答の下にだけ
// 並べる（履歴の全応答に並べると画面が埋まる）。押すと入力欄に入るだけで、送らない。
function quickRequestActions(index) {
  const cur = state.current;
  if (!cur || index !== cur.messages.length - 1) return null;
  if (state.running.has(cur.id) || state.pending.has(cur.id)) return null;
  const requests = (state.config.instructions.quickRequests || []).filter((item) => item && item.text);
  if (!requests.length) return null;
  const actions = el('div', 'message-actions');
  for (const request of requests) {
    const button = el('button', 'message-action', request.label);
    button.type = 'button';
    button.title = request.text;
    button.onclick = () => fillPrompt(request.text);
    actions.append(button);
  }
  return actions;
}

// index … 会話の messages の中の位置（分岐の関連づけに使う）
function messageNode(m, index = -1) {
  const n = el('div', m.role === 'user' ? 'msg user' : 'response-turn');
  if (m.role === 'user') {
    // どのエージェント・モデル・モードへ出した依頼か（ターンごとに変わりうる）
    if (m.cli) {
      const who = el('div', 'who');
      who.append(el('span', 'tag', m.cli));
      if (m.model) who.append(el('span', 'tag', m.model));
      if (m.readonly) who.append(el('span', 'tag', 'Ask'));
      n.append(who);
    }
    n.append(document.createTextNode(m.text || ''));
    if (m.attachments && m.attachments.length) {
      const files = el('div', 'files');
      for (const a of m.attachments) files.append(chipNode(a));
      n.append(files);
    }
    const actions = el('div', 'message-actions');
    const teach = el('button', 'message-action', 'この依頼をタスクにする');
    teach.type = 'button';
    teach.onclick = () => beginTaskTeaching(m);
    const again = el('button', 'message-action', '入力欄に戻す');
    again.type = 'button';
    again.title = '本文と添付を入力欄へ戻す（送らない）';
    again.onclick = () => fillPrompt(m.text, { attachments: m.attachments || [] });
    actions.append(teach, again);
    n.append(actions);
  } else {
    const who = el('div', 'response-who');
    if (m.cli) who.append(el('span', 'tag', m.cli));
    if (m.model) who.append(el('span', 'tag', m.model));
    if (m.tier) who.append(el('span', 'tag', m.tier));
    if (who.children.length) n.append(who);
    const parts = m.parts && typeof m.parts === 'object' ? m.parts : {};
    const thinking = responseDisclosure('thinking', '思考・進捗', parts.thinking, { open: false });
    if (thinking) n.append(thinking);
    const answer = el('div', 'msg assistant answer-bubble');
    const body = el('div');
    answer.append(body);
    if (m.text) MD.mount(body, m.text).catch(() => { body.textContent = m.text; });
    if (m.error) answer.append(el('div', 'err', m.error));
    n.append(answer);
    const meta = [];
    if (m.elapsedMs != null) meta.push(`${Math.round(m.elapsedMs / 1000)} 秒`);
    if (m.code != null && m.code !== 0) meta.push(`終了コード ${m.code}`);
    if (m.stopped) meta.push('停止');
    const information = [...(Array.isArray(parts.information) ? parts.information : [])];
    if (meta.length && !information.length) information.push({ type: 'status', title: '実行結果', detail: meta.join(' · '), status: m.error ? 'error' : 'success' });
    const infoHasError = information.some((item) => item && item.status === 'error');
    const info = responseDisclosure('information', '実行情報', information, { open: !!(m.error || m.stopped || (m.code != null && m.code !== 0) || infoHasError) });
    if (info) n.append(info);
    const forkActions = forkActionsNode(m, index);
    if (forkActions) n.append(forkActions);
    const quick = quickRequestActions(index);
    if (quick) n.append(quick);
  }
  return n;
}

// 応答に別のフォルダへの依頼（@fork 行）があれば、回答の下に分岐のボタンを出す。
// すでにその応答から分岐していれば、ボタンの代わりに分岐先へのリンクにする。
function forkActionsNode(m, index) {
  const requests = ForkProtocol.parseForkRequests(m.text);
  const forks = (state.current && Array.isArray(state.current.forks) ? state.current.forks : []).filter((f) => index >= 0 && f.index === index);
  if (!requests.length && !forks.length) return null;
  const actions = el('div', 'message-actions');
  for (const fork of forks) {
    const link = el('button', 'message-action', `→ ${basename(fork.repo)}: ${fork.title || '（無題）'}`);
    link.type = 'button';
    link.title = `分岐した会話を開く（${fork.repo}）`;
    link.onclick = () => openSessionInRepo(fork.repo, fork.id).catch((err) => notice(err.message, 'error'));
    actions.append(link);
  }
  if (!forks.length) {
    for (const request of requests) {
      const button = el('button', 'message-action', `${basename(request.folder) || '別のフォルダ'} で続ける（新しい会話を分岐）`);
      button.type = 'button';
      button.title = request.folder;
      button.onclick = () => forkConversation(index, request).catch((err) => notice(err.message, 'error'));
      actions.append(button);
    }
  }
  return actions;
}

// 登録済みリポジトリの中から、AI が書いたフォルダに当たるものを探す（末尾の区切りと大文字小文字の違いは吸収）。
function registeredRepoFor(folder) {
  const norm = (p) => String(p || '').replace(/[\\/]+$/, '').replace(/\\/g, '/').toLowerCase();
  const want = norm(folder);
  return state.config.repos.find((r) => norm(r) === want) || '';
}

// 別のリポジトリへ分岐する。分岐先が未登録なら「リポジトリを追加」の既存ダイアログで登録してから進む。
async function handoffConversation() {
  const origin = state.current;
  if (!origin || state.handoffId) return;
  state.handoffId = origin.id;
  renderHeader();
  inputStatus('pending', '会話を要約しています…');
  try {
    const result = await api.handoffSession(origin.id);
    await openSessionInRepo(origin.repo, result.session.id);
    state.sessions = await api.listSessions(state.repo);
    renderSessions();
    const next = result.session;
    state.handoffId = next.id;
    state.pending.add(next.id);
    renderHeader();
    inputStatus('pending', '起動中… 確認が出たら端末で応答してください');
    try {
      const turn = await api.send(next.id, '保存済みの会話要約を引き継ぎ、利用者からの次の指示を待ってください。', {
        cli: next.cli, model: next.model, policy: next.policy, readonly: next.readonly,
        autoApprove: next.autoApprove, skillMode: 'off', skills: [], attachments: [],
      });
      if (turn?.warning) notice(turn.warning);
    } finally { state.pending.delete(next.id); }
  } finally {
    state.handoffId = '';
    inputStatus();
    renderHeader();
  }
}

async function forkConversation(index, request) {
  const origin = state.current;
  if (!origin) return;
  let repo = registeredRepoFor(request.folder);
  if (!repo) {
    if (!confirm(`${request.folder} は登録していないフォルダです。登録してから分岐しますか？`)) return;
    const cfg = await api.addRepo();
    if (!cfg) return;
    state.config = cfg;
    renderRepos();
    repo = cfg.lastRepo;
    if (!repo) return;
  }
  if (repo === origin.repo) { notice('分岐先には別のリポジトリを選んでください', 'error'); return; }
  const firstLine = String(request.prompt || '').split('\n').find((line) => line.trim()) || '';
  if (!confirm(`${basename(repo)} で新しい会話を分岐して、次の依頼を送ります。\n\n${firstLine.slice(0, 120)}`)) return;
  inputStatus('pending', `${basename(repo)} で会話を分岐中`);
  let result;
  try {
    result = await api.forkSession({ originId: origin.id, repo, prompt: request.prompt, index, skillMode: state.turnSkillMode });
  } finally { inputStatus(); }
  if (result.turn && result.turn.warning) notice(result.turn.warning);
  await openSessionInRepo(repo, result.session.id);
  state.running.add(result.session.id);
  renderHeader();
  renderSessions();
}

// 別のリポジトリの会話を開く（リポジトリ選択も切り替える。分岐元 ⇄ 分岐先の行き来）。
async function openSessionInRepo(repo, id) {
  if (repo && repo !== state.repo) {
    if (!state.config.repos.includes(repo)) throw new Error('登録していないフォルダです');
    await selectRepo(repo);
    renderRepos();
  }
  await showArea('conversation');
  await openSession(id);
}

function beginTaskTeaching(message) {
  try {
    const selected = selectedExecution(message.policy || 'direct');
    state.pendingTaskIntent = TaskIntent.create({
      id: globalThis.crypto && globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID() : `intent-${Date.now().toString(36)}`,
      root: state.repo,
      message,
      execution: { agent: message.cli || selected.cli, model: message.model || selected.model },
    });
    showArea('tasks').catch((err) => notice(err.message, 'error'));
  } catch (err) {
    notice(err.message, 'error');
  }
}

function workingNode(id, tmuxMode) {
  const n = el('div', 'response-turn working');
  n.id = `working-${id}`;
  const ph = state.phases.get(id);
  const parts = state.liveParts.get(id) || { thinking: [], information: [] };
  const thinking = [...(Array.isArray(parts.thinking) ? parts.thinking : [])];
  const liveInformation = Array.isArray(parts.information) ? parts.information : [];
  if (ph && ph.phase === 'attention') thinking.push({ text: ph.detail || '端末で確認を求めています', status: 'attention' });
  n.append(responseDisclosure('thinking', '思考・進捗', thinking, { open: true, running: true }));
  const info = responseDisclosure('information', '実行情報', liveInformation, {
    open: liveInformation.some((item) => item.status === 'error'), raw: rawExecutionNode(id, tmuxMode),
  });
  if (info) n.append(info);
  return n;
}

function logLine(line) {
  return el('div', line.kind, line.text);
}

function terminalSnapshotNode(snapshot) {
  const details = el('details', 'terminal-snapshot');
  const when = snapshot.capturedAt ? new Date(snapshot.capturedAt).toLocaleString() : '';
  const reason = snapshot.reason === 'agent_switch' ? '切替前' : snapshot.reason === 'pane_dead' ? '終了時' : '保存済み';
  const summary = el('summary');
  summary.append(el('span', 'terminal-snapshot-agent', [snapshot.agentCli, snapshot.model].filter(Boolean).join(' · ') || '端末'));
  summary.append(el('span', 'sub', `${reason}${when ? ` · ${when}` : ''}`));
  details.append(summary, el('pre', '', snapshot.screenText || ''));
  return details;
}

function renderMessages() {
  const box = $('messages');
  const start = $('conversation-start-content');
  box.replaceChildren();
  start.replaceChildren();
  const cur = state.current;
  if (!cur) {
    start.append(el('h2', '', state.repo ? '何をしたいですか？' : 'リポジトリがありません'));
    if (!state.repo) start.append(el('p', '', '作業するローカルリポジトリを登録してください。'));
    if (!state.repo) {
      const button = el('button', 'primary', 'リポジトリを追加');
      button.onclick = () => addRepo().catch((err) => notice(err.message, 'error'));
      start.append(button);
    }
    return;
  }
  for (const snapshot of cur.terminalSnapshots || []) box.append(terminalSnapshotNode(snapshot));
  cur.messages.forEach((m, index) => box.append(messageNode(m, index)));
  if (state.running.has(cur.id) && !isTmux(cur)) box.append(workingNode(cur.id, false));
  box.scrollTop = box.scrollHeight;
}

function newDraft() {
  state.current = null;
  state.draft = !!state.repo;
  notice('');
  Term.detach();
  setInputMode('message', { focus: false });
  renderAgents();
  renderWorktreeSelect();
  renderHeader();
  renderMessages();
  renderSessions();
}

// answer … 「確認待ち」から開いたとき。端末がつながってから端末操作へ移し、そのまま打てるようにする
async function openSession(id, { answer = false } = {}) {
  try {
    state.current = await api.readSession(id);
  } catch (err) {
    notice(err.message, 'error');
    return;
  }
  state.draft = false;
  notice('');
  renderAgents();
  renderWorktreeSelect();
  renderHeader();
  renderMessages();
  renderSessions();
  Files.setRoot(state.repo, activeWorktree(), {}).catch(() => {});
  if (state.changesOpen) refreshChanges();
  let attaching = null;
  if (isTmux(state.current)) attaching = attachTerm(state.current.id);
  else if (!shareWaiting()) Term.detach();
  setInputMode('message', { focus: false });
  if (!answer || !attaching) return;
  await attaching;
  if (state.current && state.current.id === id) setInputMode('terminal');
}

// tmux の会話を開く: main に tmux セッションを（無ければ起動して）持たせ、端末ミラーをつなぐ。
async function attachTerm(id) {
  const size = Term.size();
  try {
    const r = await api.termOpen(id, size.cols, size.rows);
    state.phases.set(id, { phase: r.phase, detail: r.detail, name: r.name });
    if (r.warning) notice(r.warning);
    if (state.current && state.current.id === id) {
      await Term.attach(id, $('term-host'));
      renderHeader();
    }
  } catch (err) {
    notice(err.message, 'error');
  }
}

// 次のターンの起動条件（画面の上で選んでいるもの）
function turnOptions() {
  const selected = selectedExecution();
  return {
    policy: selected.policy,
    ...(selected.policy === 'direct' || selected.policy === 'shared' ? { cli: selected.cli, model: selected.model } : {}),
    // 共有は読み取り専用（相手の PC で動く）
    readonly: selected.policy === 'shared' || $('permission-mode').value === 'ask',
    autoApprove: $('permission-mode').value === 'auto',
    skillMode: state.turnSkillMode,
    skills: state.turnSkillMode === 'manual' ? [...state.turnSkills] : [],
    ...(selected.policy === 'shared' ? { priority: $('priority').value } : {}),
  };
}

// 待っている間の送信は、引き受けた人へのひとこと（CLI には入らない）
async function sayToExecutor(waiting, text) {
  inputStatus('pending', '送信中…');
  try {
    await api.share.say(waiting.id, text);
    $('prompt').value = '';
    await Share.refresh();
    inputStatus('success', `✓ ${waiting.node || '引き受けた人'} へ送信済み`, 3000);
    renderHeader();
  } catch (err) {
    notice(err.message, 'error');
    inputStatus('error', '送信できませんでした。入力は残っています');
  }
}

async function sendPrompt() {
  const text = $('prompt').value.trim();
  const waiting = shareWaiting();
  if (waiting) { if (text) await sayToExecutor(waiting, text); return; }
  if ((!text && !state.attachments.length) || !state.repo) return;
  const opts = turnOptions();
  const selected = selectedExecution(opts.policy);
  const shared = opts.policy === 'shared';
  inputStatus('pending', shared ? '共有に送信中…' : `${selected.cli}を準備中…`);
  // 起動直後は CLI の有無と tmux の有無がまだ届いていないことがある（ホストの返事待ち）。
  // 経路（tmux / ヘッドレス）はその答えで決まるので、ここで待つ。
  await Promise.all([state.agentsReady, state.hostReady]);
  const agent = state.agents.find((a) => a.name === selected.cli && a.available);
  // 共有は相手の PC の CLI で動くので、この PC に使えるエージェントが無くてもよい
  if (!agent && !shared) { inputStatus(); notice('利用できるエージェントがありません', 'error'); return; }
  try {
    if (!state.current) {
      const transport = (state.config.transport === 'tmux' && state.host && state.host.tmux && agent && agent.interactive && !shared) ? 'tmux' : 'headless';
      state.current = await api.createSession({ repo: state.repo, ...opts, transport, worktree: state.worktree });
      state.draft = false;
      // CLI の起動確認に時間がかかっても、保存済みの会話はすぐ一覧に出す。
      state.sessions = await api.listSessions(state.repo);
      renderSessions();
      if (isTmux(state.current)) {
        renderHeader();
        await attachTerm(state.current.id);
      }
    }
    if (opts.policy === 'direct') {
      state.config = await api.saveConfig({ lastCli: opts.cli, lastModel: opts.model, lastReadonly: opts.readonly });
    }
    const id = state.current.id;
    const wasTmux = isTmux(state.current);
    state.logs.set(id, []);
    state.tails.set(id, '');
    state.liveParts.set(id, { thinking: [], information: [] });
    // 送信中（CLI の起動し直しを含む）は phase の更新で描き直しても送信ボタンを戻さない
    state.pending.add(id);
    renderHeader();
    let res;
    try {
      // 応答中の tmux へ流すのは、この PC の CLI と話しているときだけ。共有は列へ投函する
      if (!shared && wasTmux && state.running.has(id) && !state.attachments.length) {
        res = await api.termSubmit(id, text);
      } else {
        res = await api.send(id, text, { ...opts, attachments: state.attachments });
      }
    } finally { state.pending.delete(id); }
    $('prompt').value = '';
    state.turnSkillMode = (state.config.instructions.skillSelection || {}).defaultMode || 'auto';
    state.turnSkills = [];
    state.turnSkillPreview = [];
    state.attachments = [];
    state.filledPrompt = '';
    renderAttachments();
    if (!res.followup) state.running.add(id);
    state.current = await api.readSession(id);
    state.sessions = await api.listSessions(state.repo);
    // tmux で起動（し直）したなら端末ミラーをつなぎ直す。ヘッドレスの CLI へ移ったなら外す
    if (isTmux(state.current)) { if (!wasTmux || res.restarted || Term.current() !== id) await attachTerm(id); }
    else if (!shareWaiting()) Term.detach();
    if (res.warning) notice(res.warning);
    const sentAt = new Date(res.acceptedAt || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    inputStatus('success', shared ? `共有に送信済み ${sentAt}` : `${selected.cli}へ送信済み ${sentAt}`, 4000);
    $('send').classList.add('sent');
    setTimeout(() => $('send').classList.remove('sent'), 700);
    renderHeader();
    renderMessages();
    renderSessions();
  } catch (err) {
    notice(err.message, 'error');
    inputStatus('error', '送信できませんでした。入力は残っています');
    renderHeader();
  }
}

// ---- 添付 --------------------------------------------------------------------

function renderAttachments() {
  const row = $('attach-row');
  row.replaceChildren();
  row.hidden = !state.attachments.length;
  state.attachments.forEach((a, i) => row.append(chipNode(a, { onRemove: () => removeAttachment(i) })));
}

function addAttachments(list) {
  for (const a of list) {
    if (a.rel && state.attachments.some((x) => x.rel === a.rel)) continue;
    state.attachments.push(a);
  }
  renderAttachments();
}

function removeAttachment(i) {
  const [a] = state.attachments.splice(i, 1);
  if (a && a.id) api.discardAttachment(a.id).catch(() => {});
  renderAttachments();
}

// ドロップ・貼り付けで届いた File を main へ写す（中身を送る。画面は生のパスを持たない）
async function stageFiles(fileList) {
  const files = [...(fileList || [])].filter((f) => f && f.size != null);
  if (!files.length) return;
  const staged = [];
  for (const f of files) {
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      staged.push(await api.stageAttachment(f.name || 'image.png', buf));
    } catch (err) { notice(err.message, 'error'); }
  }
  addAttachments(staged);
}

async function pickAttachments() {
  try { addAttachments(await api.pickAttachments()); } catch (err) { notice(err.message, 'error'); }
}

// 作業フォルダの中のファイルを、写さずにパスで添える（ファイルビュアーと変更ビューの共通の口）
function attachRepoFile(rel) {
  if (!rel) return;
  addAttachments([{ rel, name: rel.split('/').pop() }]);
  notice(`添付しました: ${rel}`);
}

// 「ファイル」画面で開いているファイルを添える
function attachOpenFile() {
  const f = Files.state.open;
  if (!f) { notice('添付するファイルを開いてください'); return; }
  const wt = Files.state.worktree || '';
  if (wt !== activeWorktree()) { notice('会話の作業フォルダ内のファイルを選んでください', 'error'); return; }
  attachRepoFile(f.rel);
}

async function onTurnDone({ id, message }) {
  state.running.delete(id);
  state.liveParts.delete(id);
  if (state.current && state.current.id === id) {
    try { state.current = await api.readSession(id); } catch { state.current.messages.push(message); }
    renderHeader();
    renderMessages();
  }
  state.sessions = await api.listSessions(state.repo);
  renderSessions();
  if (state.changesOpen) refreshChanges();
}

function addLivePart(id, key, item) {
  const parts = state.liveParts.get(id) || { thinking: [], information: [] };
  const list = Array.isArray(parts[key]) ? parts[key] : [];
  list.push(item);
  if (list.length > 200) list.shift();
  parts[key] = list;
  state.liveParts.set(id, parts);
  if (state.current && state.current.id === id) renderMessages();
}

// ---- 右: 変更 ----------------------------------------------------------------

function renderDiff(text) {
  const box = $('diff');
  state.diffText = text || '';
  box.replaceChildren();
  if (!text) { box.append(el('div', 'empty', '差分なし')); return; }
  try {
    const ui = new Diff2HtmlUI(box, text, {
      drawFileList: false, matching: 'lines', outputFormat: state.diffSide ? 'side-by-side' : 'line-by-line',
      highlight: true, fileContentToggle: true, synchronisedScroll: true,
    });
    ui.draw();
    ui.highlightCode();
  } catch (err) {
    const pre = el('pre', '', text);
    pre.title = err.message;
    box.append(pre);
  }
}

async function refreshChanges() {
  if (!state.repo) return;
  const wt = activeWorktree();
  const scope = wt ? state.diffScope : 'worktree';
  // 「ブランチ（分岐元から積んだコミット）」は worktree のときだけ意味がある
  $('scope-worktree').closest('.seg').hidden = !wt;
  $('scope-worktree').classList.toggle('on', scope === 'worktree');
  $('scope-branch').classList.toggle('on', scope === 'branch');
  const ul = $('changed-files');
  let res;
  try { res = await api.changes(state.repo, wt, scope); } catch (err) { renderDiff(''); ul.replaceChildren(el('li', 'empty', err.message)); return; }
  ul.replaceChildren();
  // 作業フォルダの表示にはブランチが入っているので、本体のときだけブランチを足す
  $('changes-where').textContent = wt ? `変更 · ${worktreeLabel(wt)}` : `変更 · リポジトリ本体${res.branch ? ` · ${res.branch}` : ''}`;
  for (const f of res.files) {
    const li = el('li');
    li.append(el('span', 'tag', f.label), el('span', 'grow', f.file));
    li.title = `${f.file}（ダブルクリックでファイルを開く）`;
    // 差分を見ながら「このファイルを直して」と言えるように。中身はビュアーの「会話に添付」と同じ
    // （相対パスを次の依頼に添えるだけで、写さない）。消えたファイルは添えられない。
    if (f.label !== '削除') {
      const attach = el('button', 'small', '会話に添付');
      attach.type = 'button';
      attach.title = 'このファイルを次の依頼に添付する（写さずにパスを伝える）';
      attach.onclick = (event) => { event.stopPropagation(); attachRepoFile(f.file); };
      li.append(attach);
    }
    li.onclick = async () => { [...ul.children].forEach((c) => c.classList.remove('active')); li.classList.add('active'); renderDiff(await api.fileDiff(state.repo, wt, f.file, scope)); };
    li.ondblclick = () => { if (f.label !== '削除') { showView('files'); Files.setRoot(state.repo, wt, {}).then(() => Files.openFile(f.file)).then(() => Files.reveal(f.file)); } };
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); li.click(); }
    };
    ul.append(li);
  }
  if (res.error) ul.append(el('li', 'empty', res.error));
  else if (!res.files.length) ul.append(el('li', 'empty', scope === 'branch' ? '分岐元からのコミットは無い' : '作業ツリーは綺麗'));
  renderDiff(res.diff);
}

// ---- 画面の切り替え ------------------------------------------------------------

function showView(view) {
  state.view = view === 'files' ? 'files' : 'chat';
  $('chat').hidden = state.view !== 'chat';
  $('files').hidden = state.view !== 'files';
  $('view-chat').classList.toggle('on', state.view === 'chat');
  $('view-files').classList.toggle('on', state.view === 'files');
  $('view-chat').setAttribute('aria-current', state.view === 'chat' ? 'page' : 'false');
  $('view-files').setAttribute('aria-current', state.view === 'files' ? 'page' : 'false');
  api.saveConfig({ view: state.view }).catch(() => {});
  if (state.view === 'chat') Term.refit();
}

async function showArea(area, { persist = true } = {}) {
  state.area = AgentNavigation.normalizeArea(area);
  const share = state.area === 'share';
  const automation = state.area === 'tasks' || state.area === 'workflows';
  const workspace = state.area !== 'conversation';
  renderAutomationHeader();
  $('app').classList.toggle('workspace-mode', workspace);
  $('main').hidden = workspace;
  $('automation').hidden = !automation;
  $('share-area').hidden = !share;
  if (!share) Share.hide();
  const buttons = { conversation: $('area-work'), tasks: $('area-tasks'), workflows: $('area-workflows'), share: $('area-share') };
  for (const [name, button] of Object.entries(buttons)) {
    const selected = name === state.area;
    button.classList.toggle('on', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  renderAreaContext();
  setSidebar(false);
  $('changes').hidden = workspace || !state.changesOpen;
  if (share) {
    await Share.show();
  } else if (automation) {
    // 読み込み中に直前の領域の操作を残さない。見出しを先に切り替え、内容は準備後に一度で見せる。
    // タスクの実行状態（ファイル実体の確認を伴い遅い）はここでは待たない——一覧は定義が
    // 出た時点で見せ終え、実行状態は裏で重ねる（loadTaskItems / refreshTaskSnapshot）。
    setAutomationLoading(true);
    try {
      await loadAreaItems();
      await syncAutomationWorkbench();
    } finally {
      setAutomationLoading(false);
    }
  } else {
    const latest = await api.getConfig();
    state.config = latest;
    if (state.repo !== latest.lastRepo) await selectRepo(latest.lastRepo);
    Term.refit();
  }
  if (persist) state.config = await api.saveConfig({ area: state.area });
}

function setSidebar(open) {
  $('app').classList.toggle('sidebar-open', !!open);
  $('side-backdrop').hidden = !open;
  $('nav-toggle').setAttribute('aria-expanded', String(!!open));
  $('nav-toggle').setAttribute('aria-label', open ? 'メニューを閉じる' : 'メニューを開く');
}

// ---- 設定 --------------------------------------------------------------------

function selectSettingsTab(name) {
  for (const button of document.querySelectorAll('[data-settings-tab]')) {
    const selected = button.dataset.settingsTab === name;
    button.classList.toggle('on', selected);
    button.setAttribute('aria-selected', String(selected));
  }
  for (const panel of document.querySelectorAll('[data-settings-panel]')) panel.hidden = panel.dataset.settingsPanel !== name;
}

function fillAgentSelect(select, value) {
  select.replaceChildren();
  const seen = new Set();
  for (const agent of state.settingsAgents) {
    const option = el('option', '', `${agent.name}${agent.available ? '' : '（現在は利用不可）'}`);
    option.value = agent.name;
    select.append(option);
    seen.add(agent.name);
  }
  if (value && !seen.has(value)) {
    const option = el('option', '', `${value}（定義が見つかりません）`);
    option.value = value;
    select.append(option);
  }
  if (value) select.value = value;
}

function renderRecommendedSkills() {
  const box = $('recommended-skills');
  box.replaceChildren();
  for (const [index, skill] of state.settingsSkills.entries()) {
    const chip = el('span', 'setting-chip');
    chip.append(el('span', '', skill));
    const remove = el('button', 'quiet', '×');
    remove.type = 'button';
    remove.setAttribute('aria-label', `${skill}を外す`);
    remove.onclick = () => { state.settingsSkills.splice(index, 1); renderRecommendedSkills(); };
    chip.append(remove);
    box.append(chip);
  }
  if (!state.settingsSkills.length) box.append(el('span', 'sub', '設定なし'));
}

// 設定 > 共通指示「定型の依頼」。行の形は起動時アクション（.startup-row）と同じ。
function renderQuickRequests() {
  const box = $('quick-requests');
  box.replaceChildren();
  for (const [index, request] of state.settingsQuick.entries()) {
    const row = el('div', 'startup-row quick-row');
    const label = el('input');
    label.value = request.label || '';
    label.placeholder = 'ボタンの文字';
    label.setAttribute('aria-label', 'ボタンの文字');
    const text = el('input');
    text.value = request.text || '';
    text.placeholder = '押したときに入力欄へ入る依頼';
    text.setAttribute('aria-label', '依頼の本文');
    const controls = el('span', 'startup-controls');
    const remove = el('button', 'small quiet danger', '×');
    remove.type = 'button';
    remove.title = '削除';
    remove.onclick = () => { state.settingsQuick.splice(index, 1); renderQuickRequests(); };
    label.oninput = () => { request.label = label.value; };
    text.oninput = () => { request.text = text.value; };
    controls.append(remove);
    row.append(label, text, controls);
    box.append(row);
  }
  $('quick-add').disabled = state.settingsQuick.length >= 3;
  if (!state.settingsQuick.length) box.append(el('div', 'sub settings-empty', '定型の依頼はありません'));
}

function renderStartupActions() {
  const box = $('startup-actions');
  box.replaceChildren();
  for (const [index, action] of state.settingsActions.entries()) {
    const row = el('div', 'startup-row');
    const type = el('select');
    for (const [value, label] of [['skill', 'スキル'], ['command', 'コマンド']]) {
      const option = el('option', '', label);
      option.value = value;
      type.append(option);
    }
    type.value = action.type;
    type.setAttribute('aria-label', '種類');
    const value = el('input');
    value.value = action.value || '';
    value.placeholder = action.type === 'skill' ? 'スキル名' : '例: npm test';
    value.setAttribute('aria-label', '内容');
    const onError = el('select');
    for (const [optionValue, label] of [['warn', '失敗時: 続行'], ['fail', '失敗時: 停止']]) {
      const option = el('option', '', label);
      option.value = optionValue;
      onError.append(option);
    }
    onError.value = action.onError || 'warn';
    onError.hidden = action.type === 'skill';
    onError.setAttribute('aria-label', '失敗時');
    const controls = el('span', 'startup-controls');
    const up = el('button', 'small quiet', '↑');
    const down = el('button', 'small quiet', '↓');
    const remove = el('button', 'small quiet danger', '×');
    up.type = down.type = remove.type = 'button';
    up.disabled = index === 0;
    down.disabled = index === state.settingsActions.length - 1;
    up.title = '上へ'; down.title = '下へ'; remove.title = '削除';
    up.onclick = () => { [state.settingsActions[index - 1], state.settingsActions[index]] = [state.settingsActions[index], state.settingsActions[index - 1]]; renderStartupActions(); };
    down.onclick = () => { [state.settingsActions[index], state.settingsActions[index + 1]] = [state.settingsActions[index + 1], state.settingsActions[index]]; renderStartupActions(); };
    remove.onclick = () => { state.settingsActions.splice(index, 1); renderStartupActions(); };
    type.onchange = () => { action.type = type.value; if (action.type === 'skill') action.onError = 'warn'; renderStartupActions(); };
    value.oninput = () => { action.value = value.value; };
    onError.onchange = () => { action.onError = onError.value; };
    controls.append(up, down, remove);
    row.append(type, value, onError, controls);
    box.append(row);
  }
  if (!state.settingsActions.length) box.append(el('div', 'sub settings-empty', '起動時アクションはありません'));
}

function settingsPatch() {
  const checkedPolicy = document.querySelector('input[name="default-policy"]:checked');
  const tiers = {};
  for (const tier of ['small', 'medium', 'large']) {
    tiers[tier] = { cli: $(`tier-${tier}-cli`).value, model: $(`tier-${tier}-model`).value.trim() };
  }
  return {
    transport: $('use-tmux').checked ? 'tmux' : 'headless',
    notify: { background: $('notify-background').checked },
    useWorktree: $('use-worktree').checked,
    wslDistro: $('wsl-distro').value.trim(),
    instructions: {
      enabled: $('instruction-enabled').checked,
      text: $('instruction-text').value,
      forkEnabled: $('fork-enabled').checked,
      skills: state.settingsSkills,
      skillSelection: {
        enabled: $('skill-selection-enabled').checked,
        defaultMode: $('default-skill-mode').value,
        candidates: state.settingsSkills,
      },
      startupActions: state.settingsActions,
      quickRequests: state.settingsQuick,
    },
    execution: {
      defaultPolicy: checkedPolicy ? checkedPolicy.value : 'recommended',
      optimizeAgents: $('optimize-agents').checked,
      defaultReadonly: $('default-permission-mode').value === 'ask',
      defaultAutoApprove: $('default-permission-mode').value === 'auto',
      maxConcurrent: Number($('max-concurrent').value),
      tiers,
    },
    share: {
      ...(state.config.share || {}),
      enabled: $('share-enabled').checked,
      passphrase: $('share-passphrase').value,
      node: $('share-node').value.trim(),
      peers: $('share-peers').value.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean),
      port: Number($('share-port').value),
      accept: $('share-accept').value,
      clis: [...document.querySelectorAll('#share-clis input:checked')].map((input) => input.value),
      maxConcurrent: Number($('share-max-concurrent').value),
      dailyCap: Number($('share-daily-cap').value),
      perRequesterDailyCap: Number($('share-per-requester-cap').value),
    },
  };
}

// 設定 > 共有: 提供する AI の候補は、この PC で使える CLI（仮想の herd は除く）
function renderShareClis(selected) {
  const box = $('share-clis');
  box.replaceChildren();
  const usable = (state.settingsAgents || []).filter((a) => a.available && !a.virtual);
  for (const a of usable) {
    const label = el('label', 'skill-choice');
    const input = el('input');
    input.type = 'checkbox';
    input.value = a.name;
    input.checked = selected.includes(a.name);
    label.append(input, el('span', '', a.name));
    box.append(label);
  }
  if (!usable.length) box.append(el('span', 'sub', 'この PC に使える CLI が無い'));
}

async function renderShareStatus() {
  const box = $('share-status');
  try {
    const s = await api.share.status();
    if (!s.enabled) { box.textContent = '無効'; return; }
    if (s.state !== 'on') { box.textContent = s.error || '停止中'; return; }
    const peers = s.peers.length ? s.peers.map((p) => `${p.node}${p.info && p.info.can_accept ? '' : '（受けない）'}`).join(', ') : '未接続';
    box.textContent = `${s.node} · ポート ${s.port}${s.udp ? '' : ' · UDP なし'} · 今日 ${s.today ? s.today.count : 0} 件 · 参加者: ${peers}`;
  } catch (error) {
    box.textContent = error.message;
  }
}

async function openSettings() {
  state.config = await api.getConfig();
  const instructions = state.config.instructions;
  const execution = state.config.execution;
  state.settingsSkills = [...instructions.skills];
  state.settingsActions = instructions.startupActions.map((action) => ({ ...action }));
  state.settingsQuick = (instructions.quickRequests || []).map((item) => ({ ...item }));
  state.settingsAgents = state.agents.length ? state.agents : await api.listAgents('').catch(() => []);
  $('use-tmux').checked = state.config.transport === 'tmux';
  $('use-worktree').checked = state.config.useWorktree;
  $('notify-background').checked = (state.config.notify || {}).background !== false;
  $('wsl-distro').value = state.config.wslDistro || '';
  $('instruction-enabled').checked = instructions.enabled;
  $('fork-enabled').checked = instructions.forkEnabled !== false;
  $('instruction-text').value = instructions.text || '';
  $('skill-selection-enabled').checked = instructions.skillSelection.enabled;
  $('default-skill-mode').value = instructions.skillSelection.defaultMode;
  $('instruction-count').textContent = `${$('instruction-text').value.length} / 8000`;
  renderRecommendedSkills();
  renderQuickRequests();
  renderStartupActions();
  for (const tier of ['small', 'medium', 'large']) {
    fillAgentSelect($(`tier-${tier}-cli`), execution.tiers[tier].cli);
    $(`tier-${tier}-model`).value = execution.tiers[tier].model || '';
  }
  $('optimize-agents').checked = execution.optimizeAgents !== false;
  const policy = document.querySelector(`input[name="default-policy"][value="${execution.defaultPolicy}"]`);
  if (policy) policy.checked = true;
  renderSettingsRestrictions();
  $('default-permission-mode').value = execution.defaultReadonly ? 'ask'
    : (execution.defaultAutoApprove ? 'auto' : 'confirm');
  $('max-concurrent').value = execution.maxConcurrent;
  const share = state.config.share || {};
  $('share-enabled').checked = !!share.enabled;
  $('share-passphrase').value = share.passphrase || '';
  $('share-node').value = share.node || '';
  $('share-peers').value = (share.peers || []).join(', ');
  $('share-port').value = share.port != null ? share.port : 47801;
  $('share-accept').value = share.accept || (share.participate ? 'auto' : 'off');
  $('share-max-concurrent').value = share.maxConcurrent || 1;
  $('share-daily-cap').value = share.dailyCap != null ? share.dailyCap : 20;
  $('share-per-requester-cap').value = share.perRequesterDailyCap != null ? share.perRequesterDailyCap : 5;
  renderShareClis(share.clis || []);
  renderShareStatus();
  $('settings-error').hidden = true;
  $('settings-status').textContent = '';
  const candidates = await api.listSkills(state.repo).catch(() => []);
  $('skill-options').replaceChildren(...candidates.map((name) => {
    const option = el('option'); option.value = name; return option;
  }));
  selectSettingsTab('app');
  setSidebar(false);
  $('app-settings').showModal();
}

async function saveSettings() {
  const button = $('settings-save');
  const before = state.config;
  button.disabled = true;
  $('settings-error').hidden = true;
  try {
    state.config = await api.saveConfig(settingsPatch());
    state.settingsSkills = [...state.config.instructions.skills];
    state.settingsActions = state.config.instructions.startupActions.map((action) => ({ ...action }));
    state.settingsQuick = (state.config.instructions.quickRequests || []).map((item) => ({ ...item }));
    $('settings-status').textContent = '保存しました';
    if (before.wslDistro !== state.config.wslDistro) {
      try { state.host = await api.hostInfo(); } catch (error) { state.host = { platform: api.platform, tmux: '', error: error.message }; }
      renderHostStatus();
      await selectRepo(state.repo);
    } else {
      if (!state.config.useWorktree && !state.current) {
        state.worktree = '';
        await Files.setRoot(state.repo, '', {});
      }
      await refreshWorktrees();
      renderAgents();
      renderHeader();
      renderMessages();
      renderRestrictions();
    }
  } catch (error) {
    $('settings-error').textContent = error.message;
    $('settings-error').hidden = false;
  } finally {
    button.disabled = false;
  }
}

// ---- 配線 --------------------------------------------------------------------

// 起動。ホストの確認（Windows では WSL の起動 + ログインシェル）は待たずに始め、届いたら
// その表示だけ直す。それまでに要るのは設定と会話一覧だけで、どちらも手元のファイル。
async function init() {
  state.config = await api.getConfig();
  state.turnSkillMode = (state.config.instructions.skillSelection || {}).defaultMode || 'auto';
  state.hostReady = api.hostInfo()
    .then((info) => { state.host = info; }, (err) => { state.host = { platform: api.platform, tmux: '', error: err.message }; })
    .then(() => { renderHostStatus(); renderAgents(); renderRunSettingsSummary(); });
  Term.configure({
    onFocus: () => setInputMode('terminal', { focus: false }),
    onAccepted: () => {
      const activity = $('terminal-activity');
      activity.classList.remove('accepted');
      requestAnimationFrame(() => activity.classList.add('accepted'));
    },
    onError: () => inputStatus('error', '端末への入力に失敗しました'),
    onEscape: () => {
      const result = InputMode.handleEscape(state.input);
      state.input = result.state;
      if (!result.forward) setInputMode('message');
      return result.forward;
    },
  });
  Files.init();
  Share.init({
    el,
    notice,
    // 共有の画面から、答えが届いた会話へ移る
    openSession: (id) => showArea('conversation').then(() => openSession(id)).catch((err) => notice(err.message, 'error')),
  });
  Share.refresh().then(() => { renderHeader(); renderShareUnread(); }).catch(() => {});
  renderHostStatus();
  api.running().then((ids) => { for (const id of ids) state.running.add(id); renderSessions(); renderHeader(); }).catch(() => {});
  await selectRepo(state.config.lastRepo);
  showView(state.config.view);
  await showArea(state.config.area, { persist: false });

  $('area-work').onclick = () => showArea('conversation').catch((err) => notice(err.message, 'error'));
  $('area-tasks').onclick = () => showArea('tasks').catch((err) => notice(err.message, 'error'));
  $('area-workflows').onclick = () => showArea('workflows').catch((err) => notice(err.message, 'error'));
  $('area-share').onclick = () => showArea('share').catch((err) => notice(err.message, 'error'));
  $('automation-workbench').addEventListener('statemachine:changed', (event) => {
    handleAutomationEvent(event.detail).catch((err) => notice(err.message, 'error'));
  });
  $('automation-workbench').addEventListener('statemachine:teaching-view', (event) => TaskTeaching.show(event.detail));
  // 失敗した実行をAIへ渡すとき、最初の依頼を入力欄へ置く（送るのは利用者）
  $('automation-workbench').addEventListener('statemachine:teaching-prefill', (event) => TaskTeaching.prefill(event.detail));
  $('automation-workbench').addEventListener('statemachine:flow-teaching-view', (event) => FlowTeaching.show(event.detail));
  TaskTeaching.init({
    notice,
    shareEnabled: () => shareEnabled(),
    isRunning: (id) => state.running.has(id),
    executionOptions: (overrides = {}) => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      const autoApprove = overrides.autoApprove != null ? !!overrides.autoApprove : !!state.config.execution.defaultAutoApprove;
      return { policy: selected.policy, cli: overrides.agent || selected.cli, model: overrides.model != null ? overrides.model : selected.model, autoApprove };
    },
    executionDefaults: () => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      return { agent: selected.cli, model: selected.model, autoApprove: !!state.config.execution.defaultAutoApprove };
    },
    agentNames: () => state.agents.filter((agent) => agent.available !== false && agent.interactive !== false).map((agent) => agent.name),
    executionLabel: (overrides = {}) => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      const policy = POLICY_VIEW[selected.policy] || POLICY_VIEW.recommended;
      const cli = overrides.agent || selected.cli;
      const model = overrides.model != null ? overrides.model : selected.model;
      const autoApprove = overrides.autoApprove != null ? !!overrides.autoApprove : !!state.config.execution.defaultAutoApprove;
      return `${policy.label} · ${cli || 'エージェント未設定'}${model ? ` / ${model}` : ''}${autoApprove ? ' · 自動承認' : ' · 確認あり'}`;
    },
    takeIntent: () => {
      const intent = state.pendingTaskIntent && state.pendingTaskIntent.root === state.repo ? state.pendingTaskIntent : null;
      state.pendingTaskIntent = null;
      return intent;
    },
    openTask: (machine) => openTaughtTask(machine),
    cancelCreate: () => syncAutomationWorkbench(),
    reloadTasks: () => { if (state.area === 'tasks') loadAreaItems().catch(() => {}); },
    refreshWorkbench: () => $('automation-workbench').refresh(),
  });

  // ワークフローを AI と作る会話（タスクと同じ deps。見本の記録だけが無い）
  FlowTeaching.init({
    notice,
    shareEnabled: () => shareEnabled(),
    isRunning: (id) => state.running.has(id),
    agentNames: () => state.agents.filter((agent) => agent.available !== false && agent.interactive !== false).map((agent) => agent.name),
    executionOptions: (overrides = {}) => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      const autoApprove = overrides.autoApprove != null ? !!overrides.autoApprove : !!state.config.execution.defaultAutoApprove;
      return { policy: selected.policy, cli: overrides.agent || selected.cli, model: overrides.model != null ? overrides.model : selected.model, autoApprove };
    },
    executionDefaults: () => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      return { agent: selected.cli, model: selected.model, autoApprove: !!state.config.execution.defaultAutoApprove };
    },
    executionLabel: (overrides = {}) => {
      const selected = selectedExecution(effectivePolicy(state.config.execution.defaultPolicy));
      const policy = POLICY_VIEW[selected.policy] || POLICY_VIEW.recommended;
      const cli = overrides.agent || selected.cli;
      const model = overrides.model != null ? overrides.model : selected.model;
      const autoApprove = overrides.autoApprove != null ? !!overrides.autoApprove : !!state.config.execution.defaultAutoApprove;
      return `${policy.label} · ${cli || 'エージェント未設定'}${model ? ` / ${model}` : ''}${autoApprove ? ' · 自動承認' : ' · 確認あり'}`;
    },
    reloadWorkflows: async () => {
      await $('automation-workbench').reloadFlowTeaching();
      if (state.area === 'workflows') await loadAreaItems().catch(() => {});
    },
  });

  $('repo-select').onchange = () => selectRepo($('repo-select').value).catch((err) => notice(err.message, 'error'));
  $('repo-add').onclick = () => { $('repo-more').open = false; addRepo().catch((err) => notice(err.message, 'error')); };
  $('repo-remove').onclick = async () => {
    $('repo-more').open = false;
    if (!state.repo || !confirm(`${basename(state.repo)} の登録を解除しますか？会話は残ります。`)) return;
    state.config = await api.removeRepo(state.repo);
    await selectRepo(state.config.lastRepo);
  };
  $('session-new').onclick = () => {
    if (state.area === 'conversation') newDraft();
    else syncAutomationWorkbench('new');
  };
  $('session-delete').onclick = async () => {
    $('chat-more').open = false;
    await removeConversation(state.current);
  };
  $('session-handoff').onclick = () => handoffConversation().catch((err) => notice(err.message, 'error'));
  $('session-rename').onclick = () => {
    $('chat-more').open = false;
    renameConversation().catch((err) => notice(err.message, 'error'));
  };
  $('session-filter').oninput = () => { state.sessionFilter = $('session-filter').value; renderSessions(); };
  $('send').onclick = sendPrompt;
  $('stop').onclick = () => state.current && api.stop(state.current.id);
  $('input-mode-message').onclick = () => setInputMode('message');
  $('input-mode-terminal').onclick = () => setInputMode('terminal');
  $('input-mode-share').onclick = () => setInputMode('share');
  // 開いた時点で「読んだ」と数える（未読の印が消える）
  $('share-talk').addEventListener('toggle', () => { renderShareTalk(shareWaiting()); renderShareUnread(); });
  $('prompt').addEventListener('focus', () => {
    if (state.input.mode === 'terminal') setInputMode('message', { focus: false });
  });
  for (const button of document.querySelectorAll('[data-terminal-key]')) {
    button.onclick = () => {
      setInputMode('terminal', { focus: false });
      Term.sendKey(TERMINAL_KEYS[button.dataset.terminalKey] || '');
      Term.focus();
    };
  }
  $('prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$('send').hidden && !$('send').disabled) sendPrompt(); }
  });
  $('prompt').addEventListener('input', () => {
    clearTimeout(state.skillPreviewTimer);
    state.skillPreviewTimer = setTimeout(refreshTurnSkillPreview, 180);
  });
  // 上の選択は「次のターン」の起動条件。会話にも覚えさせ（開き直しても残る）、tmux で動いている
  // CLI と違えば次の依頼のときに起動し直す（claude / copilot は --resume で文脈を引き継ぐ）
  const onTurnOptionChange = async (key) => {
    const opts = turnOptions();
    const selected = selectedExecution(opts.policy);
    if (state.current) {
      const patch = key === 'policy' ? { policy: opts.policy, tier: selected.tier }
        : key === 'permission' ? { readonly: opts.readonly, autoApprove: opts.autoApprove }
          : { [key]: selected[key] };
      state.current = await api.updateSession(state.current.id, patch);
    }
    const configPatch = { lastReadonly: opts.readonly };
    if (opts.policy === 'direct') { configPatch.lastCli = selected.cli; configPatch.lastModel = selected.model; }
    state.config = await api.saveConfig(configPatch);
    if (state.current && isTmux(state.current) && state.current.live) {
      const live = state.current.live;
      const same = live.cli === selected.cli && String(live.model || '') === selected.model
        && !!live.readonly === opts.readonly && !!live.autoApprove === opts.autoApprove;
      notice(same ? '' : `次の依頼から ${selected.cli}${selected.model ? `（${selected.model}）` : ''}${opts.readonly ? '・Ask' : ''} に切り替わります`);
    }
    renderRunSettingsSummary();
    renderSessions();
  };
  $('policy').onchange = () => onTurnOptionChange('policy');
  $('cli').onchange = () => onTurnOptionChange('cli');
  $('model').onchange = () => onTurnOptionChange('model');
  $('permission-mode').onchange = () => onTurnOptionChange('permission');
  $('turn-skill-mode').onchange = () => {
    state.turnSkillMode = $('turn-skill-mode').value;
    state.turnSkills = [];
    state.turnSkillPreview = [];
    renderTurnSkills();
    renderRunSettingsSummary();
    refreshTurnSkillPreview();
  };
  // 添付: ボタン・ドロップ・貼り付け・「ファイル」画面から
  $('attach').onclick = pickAttachments;
  $('viewer-attach').onclick = attachOpenFile;
  const composer = $('composer');
  composer.addEventListener('dragover', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.preventDefault(); composer.classList.add('drop'); } });
  composer.addEventListener('dragleave', () => composer.classList.remove('drop'));
  composer.addEventListener('drop', (e) => { e.preventDefault(); composer.classList.remove('drop'); if (state.repo) stageFiles(e.dataTransfer.files); });
  $('prompt').addEventListener('paste', (e) => {
    const files = e.clipboardData ? [...e.clipboardData.files] : [];
    if (!files.length || !state.repo) return;
    e.preventDefault();
    stageFiles(files.map((f, i) => (f.name ? f : new File([f], `paste-${Date.now().toString(36)}-${i}.${(f.type.split('/')[1] || 'bin')}`, { type: f.type }))));
  });
  $('changes-toggle').onclick = () => { state.changesOpen = !state.changesOpen; $('changes').hidden = !state.changesOpen; $('changes-toggle').classList.toggle('on', state.changesOpen); if (state.changesOpen) refreshChanges(); Term.refit(); };
  $('changes-refresh').onclick = () => { refreshWorktrees(); refreshChanges(); };
  $('diff-style').onclick = () => { state.diffSide = !state.diffSide; $('diff-style').classList.toggle('on', state.diffSide); renderDiff(state.diffText); };
  $('scope-worktree').onclick = () => { state.diffScope = 'worktree'; refreshChanges(); };
  $('scope-branch').onclick = () => { state.diffScope = 'branch'; refreshChanges(); };
  $('open-folder').onclick = () => {
    $('chat-more').open = false;
    if (state.repo) api.openFolder(state.repo, activeWorktree()).catch((e) => notice(e.message, 'error'));
  };
  $('worktree').onchange = () => { if (state.draft) selectWorktree($('worktree').value); };
  $('wt-manage').onclick = async () => {
    if (!state.repo) return;
    dialogError('');
    await refreshWorktrees();
    renderWorktreeList();
    $('run-settings').open = false;
    $('wt-dialog').showModal();
  };
  $('wt-close').onclick = () => $('wt-dialog').close();
  $('wt-create').onclick = createWorktree;
  $('wt-branch').addEventListener('input', () => {
    const s = slug($('wt-branch').value);
    $('wt-path').textContent = s ? `フォルダ: .worktrees/${s}` : '';
  });
  $('wt-branch').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); createWorktree(); } });
  $('view-chat').onclick = () => showView('chat');
  $('view-files').onclick = () => showView('files');
  $('term-restart').onclick = async () => {
    if (!state.current) return;
    const id = state.current.id;
    const size = Term.size();
    try {
      const r = await api.termRestart(id, size.cols, size.rows);
      state.phases.set(id, { phase: r.phase, detail: r.detail, name: r.name });
      notice(r.warning || '');
      await Term.attach(id, $('term-host'));
      setInputMode('message');
      renderHeader();
    } catch (err) { notice(err.message, 'error'); }
  };
  for (const button of document.querySelectorAll('[data-settings-tab]')) button.onclick = () => selectSettingsTab(button.dataset.settingsTab);
  $('instruction-text').oninput = () => { $('instruction-count').textContent = `${$('instruction-text').value.length} / 8000`; };
  $('skill-add').onclick = () => {
    const name = $('skill-entry').value.trim().replace(/^[$/]+/, '');
    if (name && !state.settingsSkills.includes(name)) state.settingsSkills.push(name);
    $('skill-entry').value = '';
    renderRecommendedSkills();
  };
  $('skill-entry').onkeydown = (event) => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); $('skill-add').click(); } };
  $('quick-add').onclick = () => { if (state.settingsQuick.length < 3) state.settingsQuick.push({ label: '', text: '' }); renderQuickRequests(); };
  $('startup-add').onclick = () => { state.settingsActions.push({ type: 'skill', value: '', onError: 'warn' }); renderStartupActions(); };
  $('settings-open').onclick = () => openSettings().catch((error) => notice(error.message, 'error'));
  $('settings-close').onclick = () => $('app-settings').close();
  $('settings-save').onclick = saveSettings;
  $('optimize-agents').onchange = renderSettingsRestrictions;
  $('nav-toggle').onclick = () => setSidebar(!$('app').classList.contains('sidebar-open'));
  $('side-backdrop').onclick = () => setSidebar(false);
  document.addEventListener('click', (event) => closePopupMenus(document, event));
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    closePopupMenus(document);
    setSidebar(false);
  });

  api.onTurnStarted((p) => {
    const { id, warning } = p;
    state.running.add(id);
    TaskTeaching.onTurnStarted(p);
    FlowTeaching.onTurnStarted(p);
    if (!state.liveParts.has(id)) state.liveParts.set(id, { thinking: [], information: [] });
    if (state.current && state.current.id === id) {
      if (warning) notice(warning);
      renderHeader();
      renderMessages();
    }
  });
  api.onTurnProgress(({ id, item }) => addLivePart(id, 'thinking', item));
  api.onTurnInfo(({ id, item }) => addLivePart(id, 'information', item));
  api.onTurnLine(({ id, kind, text }) => {
    const lines = state.logs.get(id) || [];
    lines.push({ kind, text });
    if (lines.length > 2000) lines.shift();
    state.logs.set(id, lines);
    const node = document.querySelector(`#working-${id} .log`);
    if (node) { node.append(logLine({ kind, text })); node.scrollTop = node.scrollHeight; }
  });
  api.onTurnDone((p) => { TaskTeaching.onTurnDone(p); FlowTeaching.onTurnDone(p); return onTurnDone(p); });
  api.share.onScreen((p) => {
    if (!p || !p.sessionId) return;
    TaskTeaching.onShareScreen(p);
    FlowTeaching.onShareScreen(p);
    if (state.current && state.current.id === p.sessionId && Term.current() === p.id) Term.applyScreen({ id: p.id, text: p.text || '' });
  });
  api.share.onChanged(() => {
    renderShareUnread();
    if (state.current && state.current.share) renderHeader();
  });
  api.onTermScreen((p) => {
    state.tails.set(p.id, p.tail || '');
    Term.applyScreen(p);
    TaskTerm.applyScreen(p);
    const node = document.querySelector(`#working-${p.id} .tail`);
    if (node) node.textContent = p.tail || '';
  });
  // OS の通知を押したとき（main が前面に戻してから知らせる）
  api.onNotifyOpen((p) => {
    if (!p || !p.id) return;
    // 別のリポジトリの会話でも開けるように、まず会話を読んで置き場を確かめる
    api.readSession(p.id)
      .then((sess) => openSessionInRepo(sess.repo, p.id))
      .catch((err) => notice(err.message, 'error'));
  });
  api.onTermPhase((p) => {
    state.phases.set(p.id, { phase: p.phase, detail: p.detail, name: p.name });
    TaskTeaching.onTermPhase(p);
    FlowTeaching.onTermPhase(p);
    if (state.current && state.current.id === p.id) {
      renderHeader();
      renderMessages();
      if (p.phase === 'dead' || p.phase === 'gone') {
        setInputMode('message', { focus: false });
        inputStatus('error', 'セッション終了');
      }
    }
    renderSessions();
  });
}

function renderHostStatus() {
  $('wsl-row').hidden = api.platform !== 'win32';
  if (!state.host) { $('host-status').textContent = '実行環境を確認中…'; return; }
  const h = state.host;
  const parts = [];
  if (h.platform === 'win32') parts.push(h.distro ? `WSL: ${h.distro}` : 'WSL: 既定');
  parts.push(h.tmux ? (h.tmuxVersion || 'tmux あり') : 'tmux なし（ヘッドレスで動く）');
  if (h.error) parts.push(h.error);
  $('host-status').textContent = parts.join(' · ');
}

init().catch((err) => notice(err.message, 'error'));
