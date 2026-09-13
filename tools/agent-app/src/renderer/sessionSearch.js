'use strict';
const SessionSearch = (() => {
  const $ = id => document.getElementById(id);
  const api = window.api.sessionBrowser;
  let deps, visible = false, panels = [], requestId = '', generation = 0, timer, cursor = '', previous = '', selected, transfer;
  const name = p => String(p || '').split(/[/\\]/).filter(Boolean).pop() || 'フォルダ不明';
  const source = s => s === 'app' ? 'agent-app' : s === 'vscode' ? 'VS Code' : 'CLI';
  const node = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
  const button = (label, cls, fn) => { const b = node('button', cls, label); b.type = 'button'; b.onclick = fn; return b; };
  const date = value => value ? new Date(value * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '日時不明';
  function filters() {
    let since = 0, until = 0;
    const mode = $('search-date').value;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (mode === 'range') {
      if ($('search-since').value) since = new Date($('search-since').value + 'T00:00:00').getTime() / 1000;
      if ($('search-until').value) { const end = new Date($('search-until').value + 'T00:00:00'); end.setDate(end.getDate() + 1); until = end.getTime() / 1000; }
      if (until && since >= until) throw new Error('開始日と終了日を確認してください');
    } else if (mode) {
      const start = new Date(today);
      start.setDate(start.getDate() - (mode === 'yesterday' ? 1 : mode === 'today' ? 0 : Number(mode) - 1));
      since = start.getTime() / 1000;
      const end = new Date(today); if (mode !== 'yesterday') end.setDate(end.getDate() + 1);
      until = end.getTime() / 1000;
    }
    return { text: $('search-text').value.trim(), agent: $('search-agent').value, since, until,
      repo: $('search-repo').value.trim(), model: $('search-model').value.trim(), source: $('search-source').value,
      dateField: $('search-date-field').value, archived: $('search-archived').checked };
  }
  function cancel() {
    clearTimeout(timer); generation++;
    if (requestId) api.cancel(requestId).catch(() => {});
    requestId = ''; $('search-cancel').hidden = true;
  }
  function clearPreview() {
    previewVersion++; selected = null;
    $('search-preview').replaceChildren(node('p', 'sub', '会話を選ぶと内容を確認できます'));
    $('search-split').classList.remove('has-preview');
    for (const row of $('search-results').querySelectorAll('.active, .on')) row.classList.remove('active', 'on');
  }
  function clearResults() {
    clearPreview(); cursor = ''; previous = '';
    $('search-results').replaceChildren();
    $('search-next').hidden = true; $('search-prev').hidden = true;
    $('search-errors').hidden = true; $('search-error-detail').textContent = '';
  }
  async function search(pageCursor = '') {
    cancel(); const version = generation;
    if (!pageCursor) clearResults();
    else clearPreview();
    if (!visible) return;
    requestId = crypto.randomUUID();
    $('search-status').textContent = '会話を検索しています…'; $('search-cancel').hidden = false;
    $('search-next').disabled = true; $('search-prev').disabled = true;
    try {
      const result = await api.search({ query: filters(), requestId, cursor: pageCursor });
      if (version !== generation) return;
      cursor = result.cursor; previous = result.previous || '';
      clearPreview();
      $('search-results').replaceChildren();
      for (const record of result.sessions) {
        const row = button('', 'list-pick', () => preview(record.key));
        row.append(node('strong', '', record.title), node('span', 'sub', `${record.agent} · ${source(record.source)}`),
          node('span', 'sub', `${name(record.repo)} · ${date(record.updatedAt)}`), node('span', 'sub', record.snippet));
        row.dataset.key = record.key;
        if (selected?.key === record.key) row.classList.add('on');
        const item = node('li', 'row-item', ''); item.classList.toggle('active', selected?.key === record.key); item.append(row); $('search-results').append(item);
      }
      $('search-status').textContent = `${result.page || 1}ページ · ${result.sessions.length}件表示${result.totalExact === false ? ' · 続きがあります' : ''}${result.partial ? '（取得できた範囲）' : ''}${result.errors.length ? ' · ' + `${result.errors.length}件の取得エラー（詳細を確認）` : !result.sessions.length ? (cursor ? ' · この範囲に一致する会話はありません' : ' · 条件に合う会話がありません') : ''}`;
      $('search-errors').hidden = !result.errors.length;
      $('search-error-detail').textContent = result.errors.map(e => `${e.provider === 'vscode' ? 'VS Code' : e.provider || ''}: ${e.message}`).join('\n');
      $('search-next').hidden = !cursor; $('search-prev').hidden = !previous;
    } catch (err) { if (version === generation) $('search-status').textContent = err.message; }
    finally { if (version === generation) { requestId = ''; $('search-cancel').hidden = true; $('search-next').disabled = false; $('search-prev').disabled = false; } }
  }
  let previewVersion = 0;
  async function preview(key) {
    const version = ++previewVersion;
    $('search-preview').replaceChildren(node('p', 'sub', '会話を読み込んでいます…'));
    $('search-split').classList.add('has-preview');
    try {
      const record = await api.read(key);
      if (version !== previewVersion || !visible) return;
      selected = record;
      for (const row of $('search-results').querySelectorAll('[data-key]')) row.parentElement.classList.toggle('active', row.dataset.key === key);
      const box = $('search-preview'); box.replaceChildren();
      const back = button('検索結果へ戻る', 'small', () => $('search-split').classList.remove('has-preview')); back.id = 'search-back'; box.append(back);
      box.append(node('h3', '', record.title), node('p', 'sub', `${record.agent} · ${source(record.source)} · ${record.model || 'モデル不明'} · ${date(record.updatedAt)}`), node('p', 'sub', record.repo || 'フォルダ不明'));
      if (record.partial) box.append(node('p', 'sub', '会話の一部を読み取れません。元のアプリから会話のJSONを取り込んでください。'));
      for (const message of record.messages) {
        const row = node('article', 'search-message', '');
        row.append(node('strong', '', message.role === 'user' ? '利用者' : 'AI'), node('div', 'search-message-body', message.text));
        if (!record.partial && message.role === 'assistant' && message.complete !== false) row.append(button('ここからfork', 'small quiet', () => beginTransfer(record, message.id)));
        box.append(row);
      }
      const actions = node('div', 'row search-preview-actions', '');
      if (record.appId) actions.append(button('元の会話を開く', 'small', () => { close(); deps.openSession(record.repo, record.appId); }));
      const start = button('新しいセッションに引き継ぐ', 'primary', () => beginTransfer(record));
      start.disabled = record.partial || !record.messages.some(m => m.role === 'assistant' && m.complete !== false);
      actions.append(start); box.append(actions);
    } catch (err) { if (version === previewVersion) $('search-preview').replaceChildren(node('p', 'sub', err.message)); }
  }
  function repos(selectedRepo = '') {
    $('search-target-repo').replaceChildren(new Option('保存先を選択', ''), ...(deps.getConfig().repos || []).map(p => new Option(p, p)));
    $('search-target-repo').value = selectedRepo;
  }
  async function targetChanged() {
    const current = transfer;
    if (!current) return;
    current.token = ''; current.loading = true;
    $('search-summary').disabled = true; $('search-summary').value = '';
    $('search-transfer-start').textContent = '引き継ぎ内容を作る'; $('search-transfer-start').disabled = true;
    const repo = $('search-target-repo').value;
    $('search-target-agent').replaceChildren(new Option('エージェントを確認中…', ''));
    if (!repo) { current.loading = false; return; }
    try {
      const entries = await window.api.listAgents(repo);
      if (transfer !== current || repo !== $('search-target-repo').value) return;
      const available = entries.filter(a => a.available);
      current.agents = available;
      $('search-target-agent').replaceChildren(...available.map(a => new Option(a.name, a.name)));
      if (!available.length) $('search-target-agent').append(new Option('利用できるエージェントがありません', ''));
      const preferred = available.find(a => a.name === current.record.agent);
      if (preferred) $('search-target-agent').value = preferred.name;
      $('search-target-model').value = preferred ? current.record.model || '' : '';
      $('search-transfer-start').disabled = !available.length;
      $('search-transfer-status').textContent = '';
    } catch (err) { if (transfer === current) $('search-transfer-status').textContent = err.message; }
    finally { if (transfer === current) current.loading = false; }
  }
  function beginTransfer(record, boundary = null) {
    transfer = { record, boundary, mode: boundary == null ? 'handoff' : 'fork', token: '', busy: false };
    $('search-transfer-title').textContent = boundary == null ? '新しいセッションに引き継ぐ' : 'ここからfork';
    const excerpt = boundary == null ? '' : ' · ' + record.messages.find(m => m.id === boundary)?.text.slice(0, 100);
    $('search-transfer-source').textContent = record.title + excerpt;
    $('search-request').value = ''; $('search-transfer-status').textContent = '';
    repos(); $('search-target-permission').value = 'confirm';
    $('search-transfer-dialog').showModal(); targetChanged();
  }
  async function startTransfer() {
    const current = transfer;
    if (!current || current.busy || current.loading) return;
    current.busy = true; $('search-transfer-start').disabled = true;
    try {
      if (!current.token) {
        const repo = $('search-target-repo').value, cli = $('search-target-agent').value, model = $('search-target-model').value.trim();
        if (!repo || !cli) throw new Error('保存先とエージェントを選んでください');
        $('search-transfer-status').textContent = '引き継ぎ内容を整理しています…';
        for (const id of ['search-target-repo', 'search-target-agent', 'search-target-model', 'search-target-add']) $(id).disabled = true;
        current.requestId = crypto.randomUUID();
        const result = await api.prepare({ requestId: current.requestId, key: current.record.key, revision: current.record.revision, boundary: current.boundary, mode: current.mode, repo, cli, model });
        if (transfer !== current) return;
        current.token = result.token;
        $('search-summary').value = result.summary; $('search-summary').disabled = false;
        $('search-transfer-status').textContent = '内容を確認・編集して開始できます。';
        $('search-transfer-start').textContent = '新しいセッションで開始';
      } else {
        current.creating = true; $('search-transfer-close').disabled = true;
        const result = await api.create({ token: current.token, summary: $('search-summary').value, request: $('search-request').value, permission: $('search-target-permission').value });
        if (transfer !== current) return;
        $('search-transfer-dialog').close(); close();
        await deps.sendCreated(result);
      }
    } catch (err) { if (transfer === current) $('search-transfer-status').textContent = err.message; }
    finally {
      current.busy = false; current.creating = false;
      if (!transfer || transfer === current) {
      $('search-transfer-close').disabled = false;
      for (const id of ['search-target-repo', 'search-target-agent', 'search-target-model', 'search-target-add']) $(id).disabled = false;
      $('search-transfer-start').disabled = false;
      }
    }
  }
  function invalidate() {
    if (!transfer) return;
    transfer.token = ''; $('search-summary').value = ''; $('search-summary').disabled = true;
    $('search-transfer-start').textContent = '引き継ぎ内容を作る';
  }
  function open() {
    if (visible) { $('search-text').focus(); return; }
    visible = true; deps.hideSidebar();
    panels = ['main', 'automation', 'share-area', 'changes'].map(id => [id, $(id).hidden]);
    for (const [id] of panels) $(id).hidden = true;
    $('session-search').hidden = false; $('session-search-open').setAttribute('aria-expanded', 'true');
    $('search-text').focus(); search();
  }
  function close() {
    if (!visible) return;
    cancel(); previewVersion++; visible = false;
    $('session-search').hidden = true; $('session-search-open').setAttribute('aria-expanded', 'false');
    for (const [id, hidden] of panels) $(id).hidden = hidden;
    $('session-search-open').focus();
  }
  function init(dependencies) {
    deps = dependencies;
    // Reuse the actual conversation execution controls, including the editable model input.
    const control = $('direct-agent-settings').cloneNode(true);
    control.id = 'search-target-controls'; control.hidden = false;
    control.querySelector('#cli').id = 'search-target-agent'; control.querySelector('#model').id = 'search-target-model';
    $('search-execution-inputs').append(control);
    $('session-search-open').onclick = open; $('session-search-close').onclick = close;
    $('search-cancel').onclick = () => { cancel(); $('search-status').textContent = '検索を中止しました'; };
    $('search-next').onclick = () => search(cursor);
    $('search-prev').onclick = () => search(previous);
    const changed = () => { cancel(); clearResults(); $('search-date-range').hidden = $('search-date').value !== 'range'; timer = setTimeout(() => search(), 300); };
    for (const id of ['search-text', 'search-agent', 'search-date', 'search-repo', 'search-model', 'search-source', 'search-date-field', 'search-archived', 'search-since', 'search-until']) $(id).addEventListener('input', changed);
    $('search-text').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
    $('search-reset').onclick = () => {
      for (const id of ['search-text', 'search-agent', 'search-date', 'search-repo', 'search-model', 'search-source', 'search-since', 'search-until']) $(id).value = '';
      $('search-date-field').value = 'updated'; $('search-archived').checked = false; $('search-date-range').hidden = true; search();
    };
    for (const [id, folder] of [['search-import', false], ['search-folder', true]]) $(id).onclick = async () => {
      try { if (await api.import(folder)) { $('search-more').open = false; search(); } } catch (err) { $('search-status').textContent = err.message; }
    };
    $('search-target-repo').onchange = targetChanged;
    $('search-target-agent').onchange = () => { $('search-target-model').value = ''; invalidate(); };
    $('search-target-model').oninput = invalidate;
    $('search-target-add').onclick = async () => {
      try { const cfg = await window.api.addRepo(); if (cfg) { deps.setConfig(cfg); repos(cfg.lastRepo); await targetChanged(); } }
      catch (err) { $('search-transfer-status').textContent = err.message; }
    };
    $('search-transfer-close').onclick = () => $('search-transfer-dialog').close();
    $('search-transfer-dialog').addEventListener('cancel', e => { if (transfer?.creating) e.preventDefault(); });
    $('search-transfer-dialog').addEventListener('close', () => {
      if (transfer?.requestId && transfer.busy && !transfer.creating) api.cancel(transfer.requestId).catch(() => {});
      transfer = null;
    });
    $('search-transfer-start').onclick = startTransfer;
    $('session-search').addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); close(); } });
  }
  function openOrigin(origin) {
    $('search-reset').click();
    $('search-text').value = origin.title || '';
    $('search-source').value = origin.provider === 'vscode' ? 'vscode' : 'cli';
    open();
  }
  return { init, open, close, openOrigin };
})();
