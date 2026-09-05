'use strict';

// 画面の状態は 1 か所。保存は main（store）がやり、ここは表示と操作だけ。
const state = {
  config: null,
  host: null,           // host:info（platform / tmux の有無）
  repo: '',
  agents: [],
  sessions: [],
  current: null,        // 開いている会話（store の中身）
  draft: false,         // 「新しい会話」を押してまだ 1 通も送っていない
  running: new Set(),   // 応答中の会話 ID
  logs: new Map(),      // 会話 ID → 応答中に流れた行（ヘッドレス）
  tails: new Map(),     // 会話 ID → 端末の末尾（tmux）
  phases: new Map(),    // 会話 ID → { phase, detail }
  changesOpen: false,
  termOpen: false,
  view: 'chat',
  diffSide: false,
  diffScope: 'worktree',   // 変更ビュー: 作業ツリー / ブランチ（分岐元から積んだコミット）
  diffText: '',
  worktrees: [],           // git worktree list の結果
  worktree: '',            // 「新しい会話」で選んでいる作業フォルダ（'' はリポジトリ本体）
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

function isTmux(sess) { return !!sess && sess.transport === 'tmux'; }

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

function renderRepos() {
  const ul = $('repos');
  ul.replaceChildren();
  for (const repo of state.config.repos) {
    const li = el('li', repo === state.repo ? 'active' : '');
    li.title = repo;
    li.append(el('span', 'grow', basename(repo)));
    const x = el('button', 'x', '×');
    x.title = '登録を外す';
    x.onclick = async (e) => { e.stopPropagation(); if (confirm(`${basename(repo)} の登録を外す？（会話は残る）`)) { state.config = await api.removeRepo(repo); await selectRepo(state.config.lastRepo); } };
    li.append(x);
    li.onclick = () => selectRepo(repo);
    ul.append(li);
  }
  if (!state.config.repos.length) ul.append(el('li', 'empty', '「追加」でローカルリポジトリを登録する'));
}

function renderSessions() {
  const ul = $('sessions');
  ul.replaceChildren();
  for (const s of state.sessions) {
    const ph = state.phases.get(s.id);
    const cls = [state.current && s.id === state.current.id ? 'active' : '', state.running.has(s.id) ? 'running' : (ph && ph.phase === 'attention' ? 'attention' : '')];
    const li = el('li', cls.join(' '));
    const body = el('span', 'grow');
    body.append(el('div', '', s.title || '（無題）'));
    const where = s.worktree ? `・${s.branch || s.worktree}` : '';
    body.append(el('div', 'sub', `${s.cli}${s.readonly ? '・Ask' : ''}${s.transport === 'tmux' ? '・tmux' : ''}${where} · ${s.count} 通`));
    li.append(body);
    li.onclick = () => openSession(s.id);
    ul.append(li);
  }
  if (!state.sessions.length) ul.append(el('li', 'empty', state.repo ? 'まだ会話がない' : ''));
}

async function selectRepo(repo) {
  state.repo = repo || '';
  if (repo) state.config = await api.saveConfig({ lastRepo: repo });
  state.agents = repo ? await api.listAgents(repo).catch((e) => { notice(e.message, 'error'); return []; }) : [];
  state.sessions = repo ? await api.listSessions(repo) : [];
  await refreshWorktrees();
  state.worktree = (state.config.lastWorktree || {})[state.repo] || '';
  if (state.worktree && !state.worktrees.some((w) => w.name === state.worktree && w.selectable)) state.worktree = '';
  renderRepos();
  renderAgents();
  newDraft();
  if (state.changesOpen) refreshChanges();
  Files.setRoot(state.repo, activeWorktree(), { lastFile: (state.config.lastFiles || {})[state.repo] || '' }).catch(() => {});
}

// ---- 作業フォルダ（git worktree） -------------------------------------------

async function refreshWorktrees() {
  if (!state.repo || !worktreeUI()) {
    state.worktrees = [];                 // 機能を切っているときは git にも聞かない
    renderWorktreeSelect();
    Files.renderRoots([], false);
    return;
  }
  try {
    const res = await api.listWorktrees(state.repo);
    state.worktrees = res.items || [];
  } catch {
    state.worktrees = [];                 // git リポジトリでない等。本体だけで動く
  }
  renderWorktreeSelect();
  Files.renderRoots(state.worktrees, true);
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
    tr.append(el('td', 'sub', 'git リポジトリではない（worktree は使えない）'));
    tb.append(tr);
  }
}

async function removeWorktree(w, used) {
  const warn = [
    `${w.name}（${w.branch || 'detached'}）を削除する？`,
    used ? `この作業フォルダを使っている会話が ${used} 件ある（会話の記録は残るが、続きは動かせなくなる）` : '',
    w.dirty ? `未コミットの変更が ${w.dirty} 件ある` : '',
    w.ahead ? `本体に無いコミットが ${w.ahead} 件ある（ブランチ ${w.branch} は残す）` : '',
  ].filter(Boolean).join('\n');
  if (!confirm(warn)) return;
  dialogError('');
  try {
    await api.removeWorktree(state.repo, w.name, { force: false });
  } catch (err) {
    // 未コミットの変更が残っていると git が断る。押し切るかはここで聞く
    if (!/未コミット/.test(err.message) || !confirm(`${err.message}\n\n変更ごと削除する？`)) { dialogError(err.message); return; }
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
    notice(w.reusedBranch ? `既にあるブランチ ${w.branch} を ${w.name} に持ってきた` : `${w.name}（${w.branch}）を作った`);
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
  if (!usable.length) sel.append(el('option', '', state.host && state.host.platform === 'win32' ? 'WSL に CLI が無い' : 'この PC に CLI が無い'));
  const want = state.current ? state.current.cli : state.config.lastCli;
  if ([...sel.options].some((o) => o.value === want)) sel.value = want;
}

function renderHeader() {
  const cur = state.current;
  $('chat-title').textContent = cur ? (cur.title || '（無題）') : (state.repo ? `${basename(state.repo)} で新しい会話` : 'リポジトリを登録して会話を始める');
  $('cli').disabled = !state.draft;
  if (cur) { $('cli').value = cur.cli; $('model').value = cur.model || ''; $('readonly').checked = !!cur.readonly; }
  else { $('model').value = state.config.lastModel || ''; $('readonly').checked = !!state.config.lastReadonly; }
  $('session-delete').hidden = !cur;
  const busy = !!cur && state.running.has(cur.id);
  $('send').hidden = busy;
  $('stop').hidden = !busy;
  $('send').disabled = !state.repo;
  const tm = isTmux(cur);
  $('term-toggle').hidden = !tm;
  $('term-toggle').classList.toggle('on', tm && state.termOpen);
  const ph = tm ? state.phases.get(cur.id) : null;
  $('phase').hidden = !ph;
  if (ph) {
    $('phase').textContent = PHASE_LABEL[ph.phase] || ph.phase;
    $('phase').className = `phase ${ph.phase}`;
    $('phase').title = ph.detail || '';
  }
  $('term-restart').hidden = !(ph && (ph.phase === 'dead' || ph.phase === 'gone'));
  $('term-drawer').hidden = !(tm && state.termOpen);
  $('term-name').textContent = ph && ph.name ? `tmux -L agent-app attach -t ${ph.name}` : '';
}

// ---- 中央: メッセージ --------------------------------------------------------

function messageNode(m) {
  const n = el('div', `msg ${m.role}`);
  if (m.role === 'user') n.textContent = m.text;
  else {
    const body = el('div');
    n.append(body);
    if (m.text) MD.mount(body, m.text).catch(() => { body.textContent = m.text; });
    if (m.error) n.append(el('div', 'err', m.error));
    const meta = [];
    if (m.elapsedMs != null) meta.push(`${Math.round(m.elapsedMs / 1000)} 秒`);
    if (m.code != null && m.code !== 0) meta.push(`終了コード ${m.code}`);
    if (m.stopped) meta.push('停止');
    if (meta.length) n.append(el('div', 'meta', meta.join(' · ')));
  }
  return n;
}

function workingNode(id, tmuxMode) {
  const n = el('div', 'msg assistant working');
  n.id = `working-${id}`;
  const head = el('div', 'head');
  head.append(el('span', 'spin'));
  const ph = state.phases.get(id);
  head.append(el('span', 'label', ph && ph.phase === 'attention' ? '端末で確認を求めている' : '応答中…'));
  n.append(head);
  if (tmuxMode) {
    const tail = el('pre', 'tail', state.tails.get(id) || '');
    n.append(tail);
    const link = el('div', 'link');
    const b = el('button', 'small', '端末で見る・操作する');
    b.onclick = () => toggleTerm(true);
    link.append(b);
    n.append(link);
  } else {
    const d = el('details');
    d.open = true;
    d.append(el('summary', '', 'ログ'));
    const log = el('div', 'log');
    for (const line of state.logs.get(id) || []) log.append(logLine(line));
    d.append(log);
    n.append(d);
  }
  return n;
}

function logLine(line) {
  return el('div', line.kind, line.text);
}

function renderMessages() {
  const box = $('messages');
  box.replaceChildren();
  const cur = state.current;
  if (!cur) {
    box.append(el('div', 'msg assistant', state.repo
      ? 'エージェントとモードを選んで、下に依頼を書く。応答中でも別の会話を開いて並行して進められる。'
      : '左の「追加」でローカルリポジトリを登録する。'));
    return;
  }
  for (const m of cur.messages) box.append(messageNode(m));
  if (state.running.has(cur.id)) box.append(workingNode(cur.id, isTmux(cur)));
  box.scrollTop = box.scrollHeight;
}

function newDraft() {
  state.current = null;
  state.draft = !!state.repo;
  notice('');
  Term.detach();
  renderAgents();
  renderWorktreeSelect();
  renderHeader();
  renderMessages();
  renderSessions();
}

async function openSession(id) {
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
  if (isTmux(state.current)) attachTerm(state.current.id);
  else Term.detach();
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

function toggleTerm(open) {
  state.termOpen = open == null ? !state.termOpen : !!open;
  renderHeader();
  if (state.termOpen && state.current) { Term.refit(); Term.focus(); }
}

async function sendPrompt() {
  const text = $('prompt').value.trim();
  if (!text || !state.repo) return;
  try {
    if (!state.current) {
      const cli = $('cli').value;
      const agent = state.agents.find((a) => a.name === cli && a.available);
      if (!agent) throw new Error('使えるエージェントがない');
      const transport = (state.config.transport === 'tmux' && state.host && state.host.tmux && agent.interactive) ? 'tmux' : 'headless';
      state.current = await api.createSession({ repo: state.repo, cli, model: $('model').value.trim(), readonly: $('readonly').checked, transport, worktree: state.worktree });
      state.draft = false;
      state.config = await api.saveConfig({ lastCli: cli, lastModel: $('model').value.trim(), lastReadonly: $('readonly').checked });
      if (transport === 'tmux') await attachTerm(state.current.id);
    }
    const id = state.current.id;
    state.logs.set(id, []);
    state.tails.set(id, '');
    $('send').disabled = true;
    await api.send(id, text);
    $('prompt').value = '';
    state.running.add(id);
    state.current = await api.readSession(id);
    state.sessions = await api.listSessions(state.repo);
    renderHeader();
    renderMessages();
    renderSessions();
  } catch (err) {
    notice(err.message, 'error');
    renderHeader();
  }
}

async function onTurnDone({ id, message }) {
  state.running.delete(id);
  if (state.current && state.current.id === id) {
    try { state.current = await api.readSession(id); } catch { state.current.messages.push(message); }
    renderHeader();
    renderMessages();
  }
  state.sessions = await api.listSessions(state.repo);
  renderSessions();
  if (state.changesOpen) refreshChanges();
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
    li.onclick = async () => { [...ul.children].forEach((c) => c.classList.remove('active')); li.classList.add('active'); renderDiff(await api.fileDiff(state.repo, wt, f.file, scope)); };
    li.ondblclick = () => { if (f.label !== '削除') { showView('files'); Files.setRoot(state.repo, wt, {}).then(() => Files.openFile(f.file)).then(() => Files.reveal(f.file)); } };
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
  api.saveConfig({ view: state.view }).catch(() => {});
  if (state.view === 'chat') Term.refit();
}

// ---- 配線 --------------------------------------------------------------------

async function init() {
  state.config = await api.getConfig();
  try { state.host = await api.hostInfo(); } catch (err) { state.host = { platform: api.platform, tmux: '', error: err.message }; }
  Files.init();
  renderHostStatus();
  for (const id of await api.running()) state.running.add(id);
  await selectRepo(state.config.lastRepo);
  showView(state.config.view);

  $('repo-add').onclick = async () => { const cfg = await api.addRepo(); if (cfg) { state.config = cfg; await selectRepo(cfg.lastRepo); } };
  $('session-new').onclick = () => newDraft();
  $('session-delete').onclick = async () => {
    if (!state.current || !confirm(isTmux(state.current) ? 'この会話を削除する？（tmux セッションも終了する）' : 'この会話を削除する？')) return;
    const wt = state.current.worktree || '';
    Term.detach();
    await api.removeSession(state.current.id);
    state.running.delete(state.current.id);
    state.phases.delete(state.current.id);
    state.sessions = await api.listSessions(state.repo);
    // 作業フォルダは会話とは別物なので、消すかどうかは別に聞く（他の会話が使っていることもある）
    const others = state.sessions.filter((s) => s.worktree === wt).length;
    if (wt && !others && confirm(`作業フォルダ ${wt} も削除する？（ブランチは残る）`)) {
      try { await api.removeWorktree(state.repo, wt, { force: false }); } catch (err) { notice(err.message, 'error'); }
      await refreshWorktrees();
    }
    newDraft();
  };
  $('send').onclick = sendPrompt;
  $('stop').onclick = () => state.current && api.stop(state.current.id);
  $('prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$('send').hidden && !$('send').disabled) sendPrompt(); }
  });
  $('cli').onchange = async () => { if (state.draft) state.config = await api.saveConfig({ lastCli: $('cli').value }); };
  $('model').onchange = async () => {
    const model = $('model').value.trim();
    if (state.current) state.current = await api.updateSession(state.current.id, { model });
    state.config = await api.saveConfig({ lastModel: model });
    if (state.current && isTmux(state.current)) notice('モデルの変更は次に CLI を起動し直したときから効く（「再起動」）');
  };
  $('readonly').onchange = async () => {
    const readonly = $('readonly').checked;
    if (state.current) state.current = await api.updateSession(state.current.id, { readonly });
    state.config = await api.saveConfig({ lastReadonly: readonly });
    if (state.current && isTmux(state.current)) notice('モードの変更は次に CLI を起動し直したときから効く（「再起動」）');
    renderSessions();
  };
  $('changes-toggle').onclick = () => { state.changesOpen = !state.changesOpen; $('changes').hidden = !state.changesOpen; $('changes-toggle').classList.toggle('on', state.changesOpen); if (state.changesOpen) refreshChanges(); Term.refit(); };
  $('changes-refresh').onclick = () => { refreshWorktrees(); refreshChanges(); };
  $('diff-style').onclick = () => { state.diffSide = !state.diffSide; $('diff-style').classList.toggle('on', state.diffSide); renderDiff(state.diffText); };
  $('scope-worktree').onclick = () => { state.diffScope = 'worktree'; refreshChanges(); };
  $('scope-branch').onclick = () => { state.diffScope = 'branch'; refreshChanges(); };
  $('open-folder').onclick = () => state.repo && api.openFolder(state.repo, activeWorktree()).catch((e) => notice(e.message, 'error'));
  $('worktree').onchange = () => { if (state.draft) selectWorktree($('worktree').value); };
  $('wt-manage').onclick = async () => {
    if (!state.repo) return;
    dialogError('');
    await refreshWorktrees();
    renderWorktreeList();
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
  $('term-toggle').onclick = () => toggleTerm();
  $('term-close').onclick = () => toggleTerm(false);
  $('term-restart').onclick = async () => {
    if (!state.current) return;
    const id = state.current.id;
    const size = Term.size();
    try {
      const r = await api.termRestart(id, size.cols, size.rows);
      state.phases.set(id, { phase: r.phase, detail: r.detail, name: r.name });
      notice(r.warning || '');
      await Term.attach(id, $('term-host'));
      renderHeader();
    } catch (err) { notice(err.message, 'error'); }
  };
  $('use-tmux').checked = state.config.transport === 'tmux';
  $('use-tmux').onchange = async () => { state.config = await api.saveConfig({ transport: $('use-tmux').checked ? 'tmux' : 'headless' }); renderAgents(); };
  $('use-worktree').checked = state.config.useWorktree;
  $('use-worktree').onchange = async () => {
    state.config = await api.saveConfig({ useWorktree: $('use-worktree').checked });
    // 切ったら新しい会話は本体で始める（既にある worktree と会話はそのまま残る）
    if (!state.config.useWorktree) { state.worktree = ''; await Files.setRoot(state.repo, '', {}); }
    await refreshWorktrees();
    renderHeader();
    if (state.changesOpen) refreshChanges();
  };
  $('wsl-distro').value = state.config.wslDistro || '';
  $('wsl-distro').onchange = async () => {
    state.config = await api.saveConfig({ wslDistro: $('wsl-distro').value.trim() });
    try { state.host = await api.hostInfo(); } catch (err) { state.host = { platform: api.platform, tmux: '', error: err.message }; }
    renderHostStatus();
    await selectRepo(state.repo);
  };

  api.onTurnStarted(({ id, warning }) => { if (state.current && state.current.id === id && warning) notice(warning); });
  api.onTurnLine(({ id, kind, text }) => {
    const lines = state.logs.get(id) || [];
    lines.push({ kind, text });
    if (lines.length > 2000) lines.shift();
    state.logs.set(id, lines);
    const node = document.querySelector(`#working-${id} .log`);
    if (node) { node.append(logLine({ kind, text })); node.scrollTop = node.scrollHeight; }
  });
  api.onTurnDone(onTurnDone);
  api.onTermScreen((p) => {
    state.tails.set(p.id, p.tail || '');
    Term.applyScreen(p);
    const node = document.querySelector(`#working-${p.id} .tail`);
    if (node) node.textContent = p.tail || '';
  });
  api.onTermPhase((p) => {
    state.phases.set(p.id, { phase: p.phase, detail: p.detail, name: p.name });
    if (state.current && state.current.id === p.id) {
      renderHeader();
      const label = document.querySelector(`#working-${p.id} .label`);
      if (label) label.textContent = p.phase === 'attention' ? '端末で確認を求めている' : '応答中…';
      if (p.phase === 'attention' && !state.termOpen) notice('CLI が端末で確認を求めている。「端末」を開いて答える');
      else if (p.phase !== 'attention' && $('notice').textContent.startsWith('CLI が端末で確認')) notice('');
    }
    renderSessions();
  });
}

function renderHostStatus() {
  const h = state.host || {};
  $('wsl-row').hidden = h.platform !== 'win32';
  const parts = [];
  if (h.platform === 'win32') parts.push(h.distro ? `WSL: ${h.distro}` : 'WSL: 既定');
  parts.push(h.tmux ? (h.tmuxVersion || 'tmux あり') : 'tmux なし（ヘッドレスで動く）');
  if (h.error) parts.push(h.error);
  $('host-status').textContent = parts.join(' · ');
}

init().catch((err) => notice(err.message, 'error'));
