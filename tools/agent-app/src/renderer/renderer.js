'use strict';

// 画面の状態は 1 か所。保存は main（store）がやり、ここは表示と操作だけ。
const state = {
  config: null,
  repo: '',
  agents: [],
  sessions: [],
  current: null,        // 開いている会話（store の中身）
  draft: false,         // 「新しい会話」を押してまだ 1 通も送っていない
  running: new Set(),   // 応答中の会話 ID
  logs: new Map(),      // 会話 ID → 応答中に流れた行
  changesOpen: false,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const basename = (p) => String(p).replace(/[\\/]+$/, '').split(/[\\/]/).pop();

function notice(text, kind = '') {
  const n = $('notice');
  n.textContent = text || '';
  n.className = kind;
  n.hidden = !text;
}

// ``` の囲みと `code` だけ。それ以外はそのまま（改行は pre-wrap で出る）。
function renderText(text) {
  const parts = String(text).split(/```[^\n]*\n?/);
  return parts.map((p, i) => (i % 2
    ? `<pre>${esc(p.replace(/\n$/, ''))}</pre>`
    : esc(p).replace(/`([^`\n]+)`/g, '<code>$1</code>'))).join('');
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
    const li = el('li', [state.current && s.id === state.current.id ? 'active' : '', state.running.has(s.id) ? 'running' : ''].join(' '));
    const body = el('span', 'grow');
    body.append(el('div', '', s.title || '（無題）'));
    body.append(el('div', 'sub', `${s.cli}${s.readonly ? '・Ask' : ''} · ${s.count} 通`));
    li.append(body);
    li.onclick = () => openSession(s.id);
    ul.append(li);
  }
  if (!state.sessions.length) ul.append(el('li', 'empty', state.repo ? 'まだ会話がない' : ''));
}

async function selectRepo(repo) {
  state.repo = repo || '';
  if (repo) state.config = await api.saveConfig({ lastRepo: repo });
  state.agents = repo ? await api.listAgents(repo) : [];
  state.sessions = repo ? await api.listSessions(repo) : [];
  renderRepos();
  renderAgents();
  newDraft();
  if (state.changesOpen) refreshChanges();
}

// ---- 上: エージェント・モデル・モード ------------------------------------

function renderAgents() {
  const sel = $('cli');
  sel.replaceChildren();
  const usable = state.agents.filter((a) => a.available);
  for (const a of usable) {
    const o = el('option', '', `${a.name}${a.session === 'replay' ? '（履歴再送）' : a.session === 'continue' ? '（--continue）' : ''}`);
    o.value = a.name;
    sel.append(o);
  }
  if (!usable.length) sel.append(el('option', '', 'この PC に CLI が無い'));
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
}

// ---- 中央: メッセージ --------------------------------------------------------

function messageNode(m) {
  const n = el('div', `msg ${m.role}`);
  n.innerHTML = renderText(m.text);
  if (m.role === 'assistant') {
    if (m.error) n.append(el('div', 'err', m.error));
    const meta = [];
    if (m.elapsedMs != null) meta.push(`${Math.round(m.elapsedMs / 1000)} 秒`);
    if (m.code != null && m.code !== 0) meta.push(`終了コード ${m.code}`);
    if (meta.length) n.append(el('div', 'meta', meta.join(' · ')));
  }
  return n;
}

function workingNode(id) {
  const n = el('div', 'msg assistant working');
  n.id = `working-${id}`;
  n.append(el('span', 'spin'));
  const d = el('details');
  d.open = true;
  d.append(el('summary', '', 'ログ'));
  const log = el('div', 'log');
  for (const line of state.logs.get(id) || []) log.append(logLine(line));
  d.append(log);
  n.append(d);
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
  if (state.running.has(cur.id)) box.append(workingNode(cur.id));
  box.scrollTop = box.scrollHeight;
}

function newDraft() {
  state.current = null;
  state.draft = !!state.repo;
  notice('');
  renderAgents();
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
  renderHeader();
  renderMessages();
  renderSessions();
}

async function sendPrompt() {
  const text = $('prompt').value.trim();
  if (!text || !state.repo) return;
  try {
    if (!state.current) {
      const cli = $('cli').value;
      if (!state.agents.some((a) => a.name === cli && a.available)) throw new Error('使えるエージェントがない');
      state.current = await api.createSession({ repo: state.repo, cli, model: $('model').value.trim(), readonly: $('readonly').checked });
      state.draft = false;
      state.config = await api.saveConfig({ lastCli: cli, lastModel: $('model').value.trim(), lastReadonly: $('readonly').checked });
    }
    const id = state.current.id;
    state.logs.set(id, []);
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
  const pre = $('diff');
  pre.replaceChildren();
  if (!text) { pre.textContent = '差分なし'; return; }
  for (const line of text.split('\n')) {
    const cls = line.startsWith('+++') || line.startsWith('---') ? 'head'
      : line.startsWith('diff ') ? 'head' : line.startsWith('@@') ? 'hunk'
        : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
    pre.append(el('span', cls, `${line}\n`));
  }
}

async function refreshChanges() {
  if (!state.repo) return;
  let res;
  try { res = await api.changes(state.repo); } catch (err) { renderDiff(''); $('changed-files').replaceChildren(el('li', 'empty', err.message)); return; }
  const ul = $('changed-files');
  ul.replaceChildren();
  for (const f of res.files) {
    const li = el('li');
    li.append(el('span', 'tag', f.label), el('span', 'grow', f.file));
    li.title = f.file;
    li.onclick = async () => { [...ul.children].forEach((c) => c.classList.remove('active')); li.classList.add('active'); renderDiff(await api.fileDiff(state.repo, f.file)); };
    ul.append(li);
  }
  if (res.error) ul.append(el('li', 'empty', res.error));
  else if (!res.files.length) ul.append(el('li', 'empty', '作業ツリーは綺麗'));
  renderDiff(res.diff);
}

// ---- 配線 --------------------------------------------------------------------

async function init() {
  state.config = await api.getConfig();
  for (const id of await api.running()) state.running.add(id);
  await selectRepo(state.config.lastRepo);

  $('repo-add').onclick = async () => { const cfg = await api.addRepo(); if (cfg) { state.config = cfg; await selectRepo(cfg.lastRepo); } };
  $('session-new').onclick = () => newDraft();
  $('session-delete').onclick = async () => {
    if (!state.current || !confirm('この会話を削除する？')) return;
    await api.removeSession(state.current.id);
    state.running.delete(state.current.id);
    state.sessions = await api.listSessions(state.repo);
    newDraft();
  };
  $('send').onclick = sendPrompt;
  $('stop').onclick = () => state.current && api.stop(state.current.id);
  $('prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$('send').hidden) sendPrompt(); }
  });
  $('cli').onchange = async () => { if (state.draft) state.config = await api.saveConfig({ lastCli: $('cli').value }); };
  $('model').onchange = async () => {
    const model = $('model').value.trim();
    if (state.current) state.current = await api.updateSession(state.current.id, { model });
    state.config = await api.saveConfig({ lastModel: model });
  };
  $('readonly').onchange = async () => {
    const readonly = $('readonly').checked;
    if (state.current) state.current = await api.updateSession(state.current.id, { readonly });
    state.config = await api.saveConfig({ lastReadonly: readonly });
    renderSessions();
  };
  $('changes-toggle').onclick = () => { state.changesOpen = !state.changesOpen; $('changes').hidden = !state.changesOpen; if (state.changesOpen) refreshChanges(); };
  $('changes-refresh').onclick = refreshChanges;
  $('open-folder').onclick = () => state.repo && api.openFolder(state.repo);

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
}

init().catch((err) => notice(err.message, 'error'));
