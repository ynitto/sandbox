'use strict';

// 共有の画面。**会話画面と同じ形**で組む——左（サイドバー）が依頼の一覧、右が選んだ 1 件と
// 端末ミラー（`.terminal-stage`）。一枚のまとまりはタスクの概要と同じ `.execution-card`。
//
//   依頼    … 仲間が出した依頼（引き受けられる）と、自分が出した依頼（取り下げ・優先度）。
//             引き受けている依頼を選ぶと、自分の tmux の画面がそのまま出る（依頼者にも同じ画面が届く）。
//             端末に打てるのは引き受けた人だけ。依頼者は閲覧のみ。
//   ひとこと… 依頼にぶら下がる人と人のやり取り（`talk.js`。会話画面と同じ吹き出し）。CLI には入らない。
//   参加者  … 同じ合言葉で見つかった PC の一覧（1 枚のカード）。
//   引き受け … 自動で受ける / 選んで受ける / 受けない。設定 > 共有と同じ値で、ここからも変えられる。
//
// 状態は main の share:changed（`api.share.status()` と同じ形）で丸ごと届く。画面は持たない。
(function initShare() {
  const $ = (id) => document.getElementById(id);
  const PRIORITY_LABEL = { high: '高', normal: '通常', low: '低' };
  const STATE_LABEL = { open: '順番待ち', working: '実行中', done: '完了', failed: '失敗', cancelled: '取り下げ' };
  const TERMINAL_KEYS = { Escape: '\x1b', Tab: '\t', Enter: '\r', Newline: '\n', Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D', 'C-c': '\x03' };
  const GROUPS = [
    // 実行中には「自分が引き受けている依頼」と「仲間が自分の依頼を実行している分」が並ぶ。
    // どちらかは行の副題（誰の依頼か・どの PC か）と、選んだときの見出しで分かる。
    { key: 'working', label: '実行中', mark: '●' },
    { key: 'waiting', label: '順番待ち', mark: '○' },
    { key: 'done', label: '今日 完了', mark: '✓' },
  ];

  const state = { deps: null, visible: false, status: null, selected: '', view: 'request', screens: new Map(), busy: false, input: 'talk' };

  function term() { return window.ShareTerm; }
  function el(...args) { return state.deps.el(...args); }
  function notice(message, kind) { state.deps.notice(message, kind); }

  function time(value) {
    const at = Date.parse(value || '');
    return Number.isFinite(at) ? new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  }

  function elapsed(value) {
    const at = Date.parse(value || '');
    if (!Number.isFinite(at)) return '';
    const sec = Math.max(0, Math.round((Date.now() - at) / 1000));
    return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分${sec % 60 ? ` ${sec % 60} 秒` : ''}`;
  }

  // 一覧と右側で同じ 1 件を指すための、1 つの見方に揃える。
  //   mine     … 自分が出した依頼（取り下げ・優先度を変えられる）
  //   accepted … 自分が引き受けて実行している依頼（端末が出る）
  //   theirs   … 仲間が出した依頼（引き受けられる）
  function items() {
    const s = state.status;
    if (!s) return [];
    const out = [];
    for (const item of s.inflight || []) {
      out.push({
        id: item.id, kind: 'accepted', group: 'working', title: item.title || item.id,
        who: item.posted_by, cli: item.cli, startedAt: item.started_at, state: 'working',
        summary: item.summary, talk: item.talk || [],
      });
    }
    const accepted = new Set((s.inflight || []).map((i) => i.id));
    for (const item of s.others || []) {
      if (accepted.has(item.id)) continue;
      out.push({
        id: item.id, kind: 'theirs', group: item.state === 'working' ? 'working' : 'waiting',
        title: item.title || item.id, who: item.posted_by, state: item.state, priority: item.priority,
        summary: item.summary, requires: (item.requires && item.requires.agent_cli) || [], postedAt: item.posted_at,
        executor: item.executor, canAccept: !!item.canAccept, reason: item.reason || '', cli: item.cli || '', talk: [],
        mode: item.mode, workspace: item.workspace,
      });
    }
    for (const item of s.mine || []) {
      const terminal = !(item.state === 'open' || item.state === 'working');
      out.push({
        id: item.id, kind: 'mine', group: terminal ? 'done' : (item.state === 'working' ? 'working' : 'waiting'),
        title: item.title || item.id, who: item.posted_by, state: item.state, priority: item.priority,
        summary: item.summary, requires: (item.requires && item.requires.agent_cli) || [], postedAt: item.posted_at,
        executor: item.executor, executorCli: item.executorCli, sessionId: item.sessionId, talk: item.talk || [],
        answer: item.answer, error: item.error, finishedAt: item.finished_at, claimedAt: item.claimed_at,
        mode: item.mode, workspace: item.workspace,
      });
    }
    return out;
  }

  function find(id) { return items().find((item) => item.id === id) || null; }

  function selected() {
    const list = items();
    const hit = list.find((item) => item.id === state.selected);
    return hit || list.find((item) => item.group === 'working') || list[0] || null;
  }

  // ---- サイドバーの一覧（会話一覧と同じ .list / .list-pick） ----------------------------

  function renderList() {
    const box = $('share-requests');
    if (!box) return;
    box.replaceChildren();
    const list = items();
    const current = selected();
    if (!list.length) {
      box.append(el('li', 'empty', state.status && state.status.enabled ? 'まだ依頼がない' : '設定 > 共有で使うと決める'));
      return;
    }
    for (const group of GROUPS) {
      const members = list.filter((item) => item.group === group.key);
      if (!members.length) continue;
      const head = el('li', 'side-head');
      head.append(el('span', '', `${group.mark} ${group.label}`), el('span', 'sub', String(members.length)));
      box.append(head);
      for (const item of members) {
        const li = el('li', `row-item ${current && current.id === item.id ? 'active' : ''}${item.state === 'working' ? ' running' : ''}`);
        const pick = el('button', 'list-pick');
        const body = el('span', 'grow');
        body.append(el('div', '', item.title));
        body.append(el('div', 'sub', [
          item.kind === 'mine' ? '自分' : item.who,
          item.kind === 'accepted' ? item.cli : (item.executor || (item.priority ? PRIORITY_LABEL[item.priority] : '')),
        ].filter(Boolean).join(' · ')));
        pick.append(body);
        const unread = Talk.unread(item.id, item.talk);
        if (unread) pick.append(el('span', 'unread', String(unread)));
        pick.onclick = () => { state.selected = item.id; render(); };
        li.append(pick);
        box.append(li);
      }
    }
  }

  // ---- 右側（見出し・カード・端末） -------------------------------------------------------

  function card(title, description, action) {
    const section = el('section', 'execution-card');
    const head = el('div', 'execution-card-head');
    const stack = el('div');
    stack.append(el('h3', '', title));
    if (description) stack.append(el('p', '', description));
    head.append(stack);
    if (action) head.append(action);
    section.append(head);
    return section;
  }

  function acceptCard(item) {
    const s = state.status;
    const mode = s ? s.accept : 'off';
    if (mode === 'auto') {
      return card('この PC で引き受ける', item.canAccept ? '空きが出しだい自動で引き受けます' : (item.reason || '自動で引き受けます'));
    }
    if (mode === 'off') return card('この PC で引き受ける', '「受けない」にしています');
    const button = el('button', 'primary', '引き受ける');
    button.disabled = !item.canAccept || state.busy;
    button.onclick = () => accept(item.id);
    const detail = item.canAccept
      ? `${item.cli} で読み取り専用に実行${s && s.today ? ` · 今日 ${s.today.count}${s.capacity ? '' : '（空き無し）'} 件` : ''}`
      : item.reason;
    return card('この PC で引き受ける', detail, item.canAccept ? button : null);
  }

  function mineCard(item) {
    if (item.state === 'open' || item.state === 'working') {
      const row = el('div', 'row');
      const priority = el('select');
      for (const [value, label] of Object.entries(PRIORITY_LABEL)) {
        const option = el('option', '', `優先度 ${label}`);
        option.value = value;
        priority.append(option);
      }
      priority.value = item.priority || 'normal';
      priority.disabled = item.state !== 'open';
      priority.onchange = () => run(() => window.api.share.setPriority(item.id, priority.value));
      const cancel = el('button', 'danger', '取り下げ');
      cancel.onclick = () => run(() => window.api.share.cancel(item.id));
      row.append(priority, cancel);
      return card('自分が出した依頼', item.state === 'working'
        ? `${item.executor || '仲間'} が実行中${item.claimedAt ? ` · ${elapsed(item.claimedAt)}` : ''}`
        : '空いている参加者が拾うのを待っています', row);
    }
    const open = el('button', '', '会話を開く');
    open.disabled = !item.sessionId;
    open.onclick = () => state.deps.openSession(item.sessionId);
    return card('答え', item.state === 'done'
      ? `${item.executor || ''}${item.executorCli ? ` · ${item.executorCli}` : ''} から会話に届いています`
      : (item.error || STATE_LABEL[item.state] || ''), open);
  }

  function requestView() {
    const box = $('share-cards');
    box.replaceChildren();
    const item = selected();
    const s = state.status;
    if (!s || !s.enabled) {
      box.append(blank('共有を使っていません', '設定 > 共有で合言葉と仲間の PC を入れると、この画面に依頼が並びます。'));
      return;
    }
    if (s.state !== 'on') { box.append(blank('共有が動いていません', s.error || 'もう一度 設定 > 共有 を確かめてください。')); return; }
    if (!item) {
      box.append(blank('依頼はまだありません', s.peers.length ? '仲間が依頼を出すとここに並びます。' : 'まだ仲間が見つかっていません。'));
      return;
    }
    // 見出しが依頼の 1 行目そのものなので、本文が 1 行で収まっているなら繰り返さない
    const body = String(item.summary || '');
    if (body.includes('\n') || body.length > 60) box.append(card('依頼の本文', body));
    if (item.kind === 'mine') box.append(mineCard(item));
    else if (item.kind === 'theirs') box.append(acceptCard(item));
    else if (item.kind === 'accepted') box.append(card('引き受けた依頼', `${item.who} の依頼を ${item.cli} で実行中 · ${elapsed(item.startedAt)}`));
  }

  function nodesView() {
    const box = $('share-cards');
    box.replaceChildren();
    const s = state.status;
    if (!s || s.state !== 'on') { box.append(blank('共有が動いていません', s && s.error ? s.error : '設定 > 共有で使うと決めてください。')); return; }
    const section = card('参加者', '同じ合言葉で見つかった PC');
    const table = el('table', 'share-nodes');
    const rows = [{ node: `${s.node}（この PC）`, info: s.me, self: true }, ...s.peers.map((p) => ({ node: p.node, info: p.info, seenAt: p.seenAt }))];
    for (const row of rows) {
      const info = row.info || {};
      const stale = !row.self && row.seenAt && Date.now() - Date.parse(row.seenAt) > 90 * 1000;
      const turns = info.turns || {};
      const tr = el('tr');
      tr.append(el('td', '', row.node));
      tr.append(el('td', 'sub', (info.agent_cli || []).join(', ') || '—'));
      tr.append(el('td', '', stale ? '不在' : (info.can_accept ? '受付中' : '上限')));
      tr.append(el('td', 'sub', turns.count != null ? `今日 ${turns.count} 件` : ''));
      tr.append(el('td', 'sub', (info.inflight || []).length ? `実行中 ${info.inflight.length}` : ''));
      table.append(tr);
    }
    section.append(table);
    box.append(section);
  }

  function blank(title, detail) {
    const box = el('div', 'blank compact');
    box.append(el('h2', '', title));
    if (detail) box.append(el('p', '', detail));
    return box;
  }

  function renderHead() {
    const item = selected();
    const s = state.status;
    $('share-request-title').textContent = item ? item.title : '共有';
    const meta = item ? [
      item.kind === 'mine' ? '自分' : item.who,
      time(item.postedAt || item.startedAt),
      item.priority ? `優先度 ${PRIORITY_LABEL[item.priority]}` : '',
      item.requires && item.requires.length ? item.requires.join(' か ') : '',
      item.kind === 'accepted' ? item.cli : '',
    ].filter(Boolean).join(' · ') : (s && s.node ? `${s.node} · 仲間 ${s.peers ? s.peers.length : 0}` : '');
    $('share-request-meta').textContent = meta;
    const badge = $('share-request-status');
    badge.hidden = !item;
    if (item) {
      const label = item.kind === 'accepted' ? '引き受け中' : (STATE_LABEL[item.state] || '');
      badge.textContent = item.state === 'working' && item.kind === 'theirs' && item.executor ? `${label} ${item.executor}` : label;
      badge.className = `status${item.state === 'working' ? ' active' : item.state === 'done' ? ' ok' : item.state === 'failed' ? ' ng' : ''}`;
    }
    $('share-view-request').classList.toggle('on', state.view === 'request');
    $('share-view-nodes').classList.toggle('on', state.view === 'nodes');
    if (s) $('share-accept-mode').value = s.accept || 'off';
    $('share-accept-mode').disabled = !(s && s.enabled);
  }

  // 端末は「自分が引き受けている依頼」と「仲間が自分の依頼を実行している間」に出す。
  // どちらも画面は便り（share:screen）で届くので、この PC の tmux は見ない。
  function renderTerminal() {
    const item = selected();
    const show = state.view === 'request' && !!item
      && (item.kind === 'accepted' || (item.kind === 'mine' && item.state === 'working'));
    $('share-terminal').hidden = !show;
    if (!show) { if (term().isRemote()) term().detach(); return; }
    $('share-term-agent').textContent = item.kind === 'accepted'
      ? `${item.cli} · この PC` : `${item.executor || '仲間'} の ${item.executorCli || 'AI'}`;
    $('share-term-note').textContent = item.kind === 'accepted' ? '依頼者にも同じ画面が届きます' : '閲覧のみ';
    const stop = $('share-term-stop');
    stop.hidden = item.kind !== 'accepted';
    stop.onclick = () => run(() => window.api.share.stopAccepted(item.id));
    // 打てるのは自分が引き受けている依頼だけ（自分の PC の自分の CLI）
    term().attachRemote(item.id, $('share-term-host'), {
      keys: item.kind === 'accepted' ? (data) => window.api.share.keys(item.id, data) : null,
    });
    const text = state.screens.get(item.id);
    if (text) term().applyScreen({ id: item.id, text });
    else window.api.share.screen(item.id).then((body) => {
      if (!body) return;
      state.screens.set(item.id, body);
      if (term().current() === item.id) term().applyScreen({ id: item.id, text: body });
    }).catch(() => { /* まだ画面が無い */ });
  }

  // ひとこと（人と人）。相手がいる間だけ出す——引き受けた依頼か、仲間が実行中の自分の依頼。
  function talkPartner(item) {
    if (!item) return '';
    if (item.kind === 'accepted') return item.who;
    if (item.kind === 'mine' && item.state === 'working') return item.executor || '仲間';
    return '';
  }

  function renderThread() {
    const item = selected();
    const partner = talkPartner(item);
    const talk = (item && item.talk) || [];
    const show = state.view === 'request' && !!item && (!!partner || talk.length > 0);
    $('share-thread').hidden = !show;
    $('share-composer').hidden = !(show && partner);
    if (!show) return;
    const unread = Talk.unread(item.id, talk);
    if (unread) $('share-thread').open = true;
    $('share-thread-count').textContent = `${talk.length}件${unread ? ` · 未読 ${unread}` : ''}`;
    Talk.render($('share-thread-body'), {
      id: item.id, talk, me: state.status ? state.status.node : '',
      read: $('share-thread').open,
    });
    if (!partner) return;
    const canType = item.kind === 'accepted';
    setInputMode(canType ? state.input : 'talk', { focus: false });
    $('share-mode-terminal').hidden = !canType;
    $('share-input-status').textContent = `${partner} へ送ります`;
    $('share-prompt').placeholder = `${partner} へ伝える`;
    $('share-stop').hidden = !canType;
  }

  // 入力先（ひとこと / 端末操作）。端末は自分が引き受けている依頼のときだけ打てる。
  function setInputMode(mode, { focus = true } = {}) {
    const item = selected();
    const canType = !!item && item.kind === 'accepted';
    const next = mode === 'terminal' && canType ? 'terminal' : 'talk';
    state.input = next;
    for (const [id, name] of [['share-mode-talk', 'talk'], ['share-mode-terminal', 'terminal']]) {
      $(id).setAttribute('aria-pressed', String(next === name));
      $(id).classList.toggle('on', next === name);
    }
    $('share-message-input').hidden = next === 'terminal';
    $('share-terminal-keys').hidden = next !== 'terminal';
    $('share-composer-toolbar').hidden = next === 'terminal';
    term().setInputEnabled(next === 'terminal' && term().canType());
    if (focus) { if (next === 'terminal') term().focus(); else $('share-prompt').focus(); }
  }

  async function say() {
    const item = selected();
    const text = $('share-prompt').value.trim();
    if (!item || !text) return;
    $('share-send').disabled = true;
    try {
      await window.api.share.say(item.id, text);
      $('share-prompt').value = '';
      await refresh();
      render();
    } catch (error) {
      notice(error.message, 'error');
    } finally {
      $('share-send').disabled = false;
    }
  }

  function render() {
    if (!state.visible) return;
    renderHead();
    renderList();
    if (state.view === 'request') requestView();
    else nodesView();
    renderTerminal();
    renderThread();
  }

  // ---- 操作 --------------------------------------------------------------------------

  async function run(action) {
    if (state.busy) return;
    state.busy = true;
    try {
      const next = await action();
      if (next && next.state) state.status = next;
      else await refresh();
    } catch (error) {
      notice(error.message, 'error');
    } finally {
      state.busy = false;
      render();
    }
  }

  async function accept(id) {
    await run(async () => {
      const result = await window.api.share.accept(id);
      state.selected = id;
      return result && result.state ? result : null;
    });
  }

  async function refresh() {
    try { state.status = await window.api.share.status(); } catch (error) { notice(error.message, 'error'); }
  }

  // ---- 外から ------------------------------------------------------------------------

  function init(deps) {
    state.deps = deps;
    $('share-view-request').onclick = () => { state.view = 'request'; render(); };
    $('share-send').onclick = () => say();
    $('share-stop').onclick = () => { const item = selected(); if (item) run(() => window.api.share.stopAccepted(item.id)); };
    $('share-mode-talk').onclick = () => setInputMode('talk');
    $('share-mode-terminal').onclick = () => setInputMode('terminal');
    $('share-thread').addEventListener('toggle', () => render());
    $('share-prompt').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); say(); }
    });
    for (const button of document.querySelectorAll('[data-share-key]')) {
      button.onclick = () => {
        setInputMode('terminal', { focus: false });
        term().sendKey(TERMINAL_KEYS[button.dataset.shareKey] || '');
        term().focus();
      };
    }
    term().configure({ onFocus: () => setInputMode('terminal', { focus: false }), onError: (error) => notice(error.message, 'error') });
    $('share-view-nodes').onclick = () => { state.view = 'nodes'; render(); };
    $('share-accept-mode').onchange = (event) => run(() => window.api.share.setMode(event.target.value));
    window.api.share.onChanged((status) => { state.status = status; render(); });
    window.api.share.onScreen((p) => {
      if (!p || !p.id) return;
      state.screens.set(p.id, p.text || '');
      if (state.visible && term().current() === p.id) term().applyScreen({ id: p.id, text: p.text || '' });
    });
  }

  async function show() {
    state.visible = true;
    await refresh();
    render();
    term().refit();
  }

  function hide() {
    state.visible = false;
    if (term().isRemote()) term().detach();
  }

  // 一覧に出ている未読の合計（サイドバーの印に使う）
  function unread() {
    return items().reduce((sum, item) => sum + Talk.unread(item.id, item.talk), 0);
  }

  function status() { return state.status; }

  window.Share = { init, show, hide, render, refresh, status, items, unread, select: (id) => { state.selected = id; render(); } };
}());
