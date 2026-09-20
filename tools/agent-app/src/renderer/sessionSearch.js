'use strict';
const SessionSearch = (() => {
  const $ = id => document.getElementById(id);
  const api = window.api.sessionBrowser;
  // pending は中止に使う「まだ動いている検索」、showing は「いま画面に出している検索」。
  // 流れてくる結果は打ち止めの返事と競争するので、終わっても showing は消さない。
  let deps, visible = false, panels = [], pending = '', showing = '', generation = 0, timer, selected, transfer;
  // 見つかった端から受け取り、画面には 100 件ずつ足す（下まで来たら続きを描く）。
  const PAGE = 100;
  let rows = [], rendered = 0, state = null;
  // 評価するときだけ選択欄を出す。最初は未選択にする。
  let submitting = false, batchError = '', picking = false;
  const picked = new Set();
  let evaluation = null;   // evaluation:status（進み具合は main が持つ）
  const name = p => String(p || '').split(/[/\\]/).filter(Boolean).pop() || 'フォルダ不明';
  const source = s => s === 'app' ? 'agent-app' : s === 'vscode' ? 'VS Code' : 'CLI';
  const node = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
  const button = (label, cls, fn) => { const b = node('button', cls, label); b.type = 'button'; b.onclick = fn; return b; };
  const date = value => value ? new Date(value * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '日時不明';
  function sharedMode(value) { $('search-shared').checked = value; }
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
    return { shared: $('search-shared').checked, text: $('search-text').value.trim(), agent: $('search-agent').value, since, until,
      repo: $('search-repo').value.trim(), model: $('search-model').value.trim(), source: $('search-source').value,
      dateField: $('search-date-field').value, archived: $('search-archived').checked };
  }
  function cancel() {
    clearTimeout(timer); generation++;
    if (pending) api.cancel(pending).catch(() => {});
    pending = ''; showing = ''; $('search-cancel').hidden = true; batchControls();
  }
  function clearPreview() {
    previewVersion++; selected = null;
    $('search-preview').replaceChildren(node('p', 'sub', '会話を選ぶと内容を確認できます'));
    $('search-split').classList.remove('has-preview');
    for (const row of $('search-results').querySelectorAll('.active, .on')) row.classList.remove('active', 'on');
  }
  function clearResults() {
    clearPreview(); rows = []; rendered = 0; batchError = ''; picking = false; picked.clear(); batchControls();
    $('search-results').replaceChildren();
    $('search-errors').hidden = true; $('search-error-detail').textContent = '';
  }
  const number = value => Number(value || 0).toLocaleString();
  function status() {
    if (!state) return;
    const { running, scanned, pool, matched, errors, partial, capped } = state;
    let text;
    if (running) text = pool ? `検索中… ${number(scanned)} / ${number(pool)}件` : '検索中…';
    else text = matched ? `${number(matched)}件の検索結果` : '該当する会話はありません';
    if (capped) text += '（表示件数の上限）';
    if (partial) text += '・一部の結果のみ表示';
    if (errors) text += `・${number(errors)}件を読み込めませんでした`;
    $('search-status').textContent = text;
  }
  function addRow(record) {
    const row = button('', 'list-pick', () => preview(record.key));
    row.append(node('strong', '', record.title), node('span', 'sub', `${record.agent} · ${source(record.source)}${record.owner ? ` · ${record.owner}（共有）` : ''}`),
      node('span', 'sub', `${name(record.repo)} · ${date(record.updatedAt)}`), node('span', 'sub', record.snippet));
    row.dataset.key = record.key;
    const item = node('li', 'row-item', '');
    if (selected?.key === record.key) { row.classList.add('on'); item.classList.add('active'); }
    const check = document.createElement('input');
    check.type = 'checkbox'; check.className = 'row-check'; check.hidden = !picking;
    check.checked = picked.has(record.key);
    check.setAttribute('aria-label', `「${record.title}」を評価の対象にする`);
    check.onchange = () => {
      if (check.checked) picked.add(record.key); else picked.delete(record.key);
      batchControls();
    };
    item.append(check, row); $('search-results').append(item);
  }
  // 足元には評価の操作と進捗だけを置く。数字は main（evaluation:status）のもの。
  function batchControls() {
    const n = picking ? picked.size : rows.length;
    const busy = !!(evaluation && evaluation.batch && evaluation.batch.running);
    $('search-batch-start').textContent = picking ? `評価する（${n}件）` : 'まとめて評価';
    $('search-batch-cancel').hidden = !picking;
    $('search-batch-cancel').disabled = submitting;
    for (const check of $('search-results').querySelectorAll('.row-check')) {
      check.hidden = !picking;
      check.disabled = submitting || busy;
      check.checked = picked.has(check.nextElementSibling.dataset.key);
    }
    $('search-batch-start').disabled = !n || !!pending || busy || submitting;
    const b = evaluation && evaluation.batch;
    let text = '';
    if (b && b.running) text = `評価中 ${b.done + b.skipped} / ${b.total}`;
    else if (b) text = `評価完了 ${b.done}件 · 課題あり ${b.issues}件${b.skipped ? ` · 評価できず ${b.skipped}件` : ''}`;
    if (evaluation && evaluation.lastError && b && !b.running) text += ` · ${evaluation.lastError}`;
    $('search-batch-status').textContent = batchError || text;
    $('search-batch-status').hidden = !($('search-batch-status').textContent);
  }
  async function startBatch() {
    if (!picking) {
      if (!rows.length || pending || submitting || evaluation?.batch?.running) return;
      picking = true; batchError = '';
      picked.clear();
      batchControls();
      return;
    }
    const keys = [...picked];
    if (!keys.length || pending || submitting || evaluation?.batch?.running) return;
    submitting = true; batchError = ''; batchControls();
    try {
      evaluation = await window.api.evaluation.batch({ keys });
      picking = false; picked.clear();
    } catch (err) { batchError = err.message; }
    finally { submitting = false; batchControls(); }
  }
  // 画面が埋まるまで描き、あとはスクロールに合わせて足す。
  function renderMore() {
    const pane = $('search-results-pane');
    do {
      const slice = rows.slice(rendered, rendered + PAGE);
      if (!slice.length) return;
      for (const record of slice) addRow(record);
      rendered += slice.length;
    } while (rendered < rows.length && pane.scrollHeight <= pane.clientHeight);
  }
  async function search() {
    cancel();
    clearResults();
    if (!visible) return;
    const version = generation;
    const id = pending = showing = crypto.randomUUID();
    state = { running: true, scanned: 0, pool: 0, matched: 0, errors: 0, partial: false, capped: false };
    status();
    $('search-cancel').hidden = false;
    batchControls();
    try {
      const done = await api.search({ query: filters(), requestId: id });
      if (version !== generation) return;
      state = { ...state, ...done, running: false, errors: done.errors.length, matched: rows.length };
      $('search-errors').hidden = !done.errors.length;
      $('search-error-detail').textContent = done.errors.map(e => `${e.provider === 'vscode' ? 'VS Code' : e.provider || ''}: ${e.message}`).join('\n');
      status();
    } catch (err) { if (version === generation) $('search-status').textContent = err.message; }
    finally { if (version === generation) { pending = ''; $('search-cancel').hidden = true; batchControls(); } }
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
      box.append(node('h3', '', record.title), node('p', 'sub', `${record.agent} · ${source(record.source)}${record.owner ? ` · ${record.owner}（共有）` : ''} · ${record.model || 'モデル不明'} · ${date(record.updatedAt)}`), node('p', 'sub', record.repo || 'フォルダ不明'));
      if (record.partial) box.append(node('p', 'sub', '会話の一部を読み取れません。設定の「保存データ」から会話のJSONを取り込んでください。'));
      for (const message of record.messages) {
        const row = node('article', 'search-message', '');
        row.append(node('strong', '', message.role === 'user' ? '利用者' : 'AI'), node('div', 'search-message-body', message.text));
        if (!record.partial && message.role === 'assistant' && message.complete !== false) {
          // 会話画面の応答の下と同じ部品（.message-actions / .message-action）で出す。
          const actions = node('div', 'message-actions', '');
          actions.append(button('フォーク', 'message-action', () => beginTransfer(record, message.id)));
          row.append(actions);
        }
        box.append(row);
      }
      const actions = node('div', 'row search-preview-actions', '');
      if (record.appId) actions.append(button('元の会話を開く', 'small', () => { close(); deps.openSession(record.repo, record.appId); }));
      const start = button('フォーク', 'primary', () => beginTransfer(record));
      start.disabled = record.partial || !record.messages.some(m => m.role === 'assistant' && m.complete !== false);
      const more = node('details', 'more-menu', '');
      more.append(node('summary', '', 'その他の操作'));
      const menu = node('div', 'menu-panel', '');
      more.append(menu);
      actions.append(more, start); box.append(actions);
      if (record.appId) {
        const entries = await deps.getSessionActions(record);
        if (version !== previewVersion || !visible) return;
        start.disabled = !!entries.find(a => a.id === 'session-fork')?.disabled;
        for (const entry of entries.filter(a => !a.hidden && a.id !== 'session-fork')) {
          const action = button(entry.label, entry.id === 'session-delete' ? 'danger' : '', async () => {
            more.open = false; close();
            try { await deps.runSessionAction(record, entry.id); }
            catch (err) { alert(err.message); }
          });
          action.disabled = !!entry.disabled;
          menu.append(action);
        }
      } else {
        const routine = button('この作業を定型化', '', () => { more.open = false; beginTransfer(record, null, true); });
        routine.disabled = start.disabled;
        menu.append(routine, button('テキストに書き出す', '', async () => {
          more.open = false;
          try {
            const result = await api.export(record.key);
            $('search-status').textContent = result.warning || `テキストに書き出しました（${result.name}）`;
          } catch (err) { $('search-status').textContent = err.message; }
        }));
      }
    } catch (err) { if (version === previewVersion) $('search-preview').replaceChildren(node('p', 'sub', err.message)); }
  }
  function repos(selectedRepo = '') {
    $('search-target-repo').replaceChildren(new Option('保存先を選択', ''), ...(deps.getConfig().repos || []).map(p => { const option = new Option(name(p), p); option.title = p; return option; }));
    $('search-target-repo').value = selectedRepo;
  }
  async function targetChanged() {
    const current = transfer;
    if (!current) return;
    current.loading = true;
    $('search-transfer-start').textContent = current.mode === 'issue' ? '会話を始める' : current.routine ? '作成' : 'フォーク'; $('search-transfer-start').disabled = true;
    const repo = $('search-target-repo').value;
    const record = current.record || {};
    const worktree = record.appId && record.repo === repo ? record.defaults?.worktree : '';
    $('search-worktree-note').hidden = !worktree;
    $('search-worktree-note').textContent = worktree ? `現在の作業フォルダを使います: ${worktree}` : '';
    $('search-target-agent').replaceChildren(new Option('エージェントを確認中…', ''));
    if (!repo) { current.loading = false; return; }
    try {
      const entries = await window.api.listAgents(repo);
      if (transfer !== current || repo !== $('search-target-repo').value) return;
      const available = entries.filter(a => a.available);
      current.agents = available;
      $('search-target-agent').replaceChildren(...available.map(a => new Option(a.name, a.name)));
      if (!available.length) $('search-target-agent').append(new Option('利用できるエージェントがありません', ''));
      const preferred = available.find(a => a.name === record.agent);
      if (preferred) $('search-target-agent').value = preferred.name;
      $('search-target-model').value = preferred ? record.model || '' : '';
      $('search-transfer-start').disabled = !available.length;
      $('search-transfer-status').textContent = '';
    } catch (err) { if (transfer === current) $('search-transfer-status').textContent = err.message; }
    finally { if (transfer === current) { current.loading = false; executionLabel(); } }
  }
  function beginTransfer(record, boundary = null, routine = false) {
    transfer = { record, boundary, mode: 'fork', routine, busy: false };
    $('search-boundary').closest('label').hidden = false;
    $('search-intent').closest('label').hidden = false;
    $('search-transfer-title').textContent = routine ? 'この作業を定型化' : 'フォーク';
    const turns = record.messages.filter(m => m.role === 'assistant' && m.complete !== false);
    $('search-boundary').replaceChildren(...turns.map((m, i) => new Option(`${i + 1}: ${m.text.slice(0, 80)}`, m.id)));
    $('search-boundary').value = boundary == null ? turns.at(-1)?.id || '' : boundary;
    $('search-intent').querySelector('[value="session"]').disabled = routine;
    $('search-intent').value = routine ? 'task' : 'session';
    $('search-execution-settings').open = false;
    const excerpt = boundary == null ? '' : ' · ' + record.messages.find(m => m.id === boundary)?.text.slice(0, 100);
    $('search-transfer-source').textContent = record.title + excerpt;
    $('search-request').value = ''; $('search-transfer-status').textContent = '';
    const config = deps.getConfig();
    repos((config.repos || []).includes(record.repo) ? record.repo : config.lastRepo || '');
    $('search-target-permission').value = record.appId ? record.defaults?.permission || 'confirm' : 'confirm';
    $('search-transfer-dialog').showModal(); targetChanged();
  }
  // 課題（受信箱）を新しい会話へ渡す。フォークと同じダイアログで、リポジトリ・AI・モデル・権限だけを選ぶ
  // （フォークする位置・フォーク先は課題に無いので隠す）。
  async function handoffIssue(issue) {
    transfer = { record: null, issue, mode: 'issue', routine: false, busy: false };
    $('search-transfer-title').textContent = '会話を始める';
    $('search-transfer-source').textContent = issue.title || '';
    $('search-boundary').closest('label').hidden = true;
    $('search-intent').closest('label').hidden = true;
    $('search-request').value = ''; $('search-transfer-status').textContent = '';
    $('search-execution-settings').open = false;
    const config = deps.getConfig();
    repos(config.lastRepo || (config.repos || [])[0] || '');
    $('search-target-permission').value = 'confirm';
    $('search-transfer-dialog').showModal(); await targetChanged();
  }
  async function startIssue(current) {
    const repo = $('search-target-repo').value, cli = $('search-target-agent').value, model = $('search-target-model').value.trim();
    if (!repo || !cli) throw new Error('リポジトリとエージェントを選んでください');
    const permission = $('search-target-permission').value;
    const extra = $('search-request').value.trim();
    const prompt = extra ? `${current.issue.prompt}\n\n## 追加の依頼\n${extra}` : current.issue.prompt;
    $('search-transfer-status').textContent = '会話を作っています…';
    const config = deps.getConfig();
    const session = await window.api.createSession({ repo, cli, model, policy: 'direct', readonly: permission === 'ask', autoApprove: permission === 'auto', transport: config.transport, worktree: '' });
    current.creating = true; $('search-transfer-close').disabled = true;
    $('search-transfer-status').textContent = '課題を送っています…';
    await deps.sendCreated({ session: { ...session, cli, model, readonly: permission === 'ask', autoApprove: permission === 'auto' }, prompt });
    try { const handed = await window.api.insight.handoff(current.issue.id, { mark: true }); if (handed.warning) deps.notice(handed.warning, 'error'); }
    catch (err) { deps.notice(`受信箱から消せませんでした: ${err.message}`, 'error'); }
    deps.attentionChanged();
    $('search-transfer-dialog').close(); close();
  }
  // 会話画面から、いま開いている会話をフォークする（検索画面と同じダイアログ）。
  // boundary・target を渡すと、その位置とフォーク先を選んだ状態で開く。
  async function forkCurrent(id, { boundary = '', target = '' } = {}) {
    const record = await api.read('app:' + id);
    const completed = record.messages.filter(m => m.role === 'assistant' && m.complete !== false);
    const at = completed.some(m => m.id === boundary) ? boundary : completed.at(-1)?.id;
    if (!at) throw new Error('フォークできる応答がありません');
    beginTransfer(record, at);
    if (target) $('search-intent').value = target;
  }
  function executionLabel() {
    $('search-execution-summary').textContent = [$('search-target-agent').value || 'エージェントを選択', $('search-target-model').value || 'モデル自動', $('search-target-permission').selectedOptions[0]?.textContent].filter(Boolean).join(' · ');
  }
  async function startTransfer() {
    const current = transfer;
    if (!current || current.busy || current.loading) return;
    const controls = ['search-target-repo', 'search-target-agent', 'search-target-model', 'search-target-add', 'search-target-permission', 'search-boundary', 'search-intent', 'search-request'];
    current.busy = true; $('search-transfer-start').disabled = true;
    try {
      if (current.mode === 'issue') { for (const id of controls) $(id).disabled = true; await startIssue(current); return; }
      const repo = $('search-target-repo').value, cli = $('search-target-agent').value, model = $('search-target-model').value.trim();
      if (!repo || !cli) throw new Error('保存先とエージェントを選んでください');
      for (const id of controls) $(id).disabled = true;
      // フォーク先。セッション以外は、選ばれた形の作り方として整理する。
      const target = $('search-intent').value, intent = target === 'session' ? 'session' : 'routine';
      const label = $('search-intent').selectedOptions[0]?.textContent;
      $('search-transfer-status').textContent = intent === 'session' ? 'フォークする内容を整理しています…' : `内容を整理し、${label}としてまとめています…`;
      current.requestId = crypto.randomUUID();
      const prepared = await api.prepare({ requestId: current.requestId, key: current.record.key, revision: current.record.revision,
        boundary: $('search-boundary').value, mode: current.mode, intent, kind: intent === 'session' ? 'auto' : target, request: $('search-request').value, repo, cli, model });
      if (transfer !== current) return;
      current.creating = true; $('search-transfer-close').disabled = true;
      const result = await api.create({ token: prepared.token, summary: prepared.summary, request: $('search-request').value, permission: $('search-target-permission').value });
      if (transfer !== current) return;
      $('search-transfer-status').textContent = 'フォークを開始し、表示を準備しています…';
      if (result.method) await deps.importMethod(result);
      else await deps.sendCreated(result);
      $('search-transfer-dialog').close(); close();
    } catch (err) { if (transfer === current) $('search-transfer-status').textContent = err.message; }
    finally {
      current.busy = false; current.creating = false;
      if (!transfer || transfer === current) {
        $('search-transfer-close').disabled = false;
        for (const id of controls) $(id).disabled = false;
        $('search-transfer-start').disabled = false;
      }
    }
  }
  function open() {
    if (visible) { $('search-text').focus(); return; }
    visible = true; deps.hideSidebar();
    panels = ['main', 'automation', 'share-area', 'inbox-area', 'changes'].map(id => [id, $(id).hidden]);
    for (const [id] of panels) $(id).hidden = true;
    $('session-search').hidden = false; $('session-search-open').setAttribute('aria-expanded', 'true');
    $('search-text').focus(); search();
  }
  function close() {
    if (!visible) return;
    cancel(); previewVersion++; visible = false;
    picking = false; picked.clear(); batchControls();
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
    for (const [id, folder] of [['session-import', false], ['session-folder', true]]) $(id).onclick = async () => {
      $('session-import').disabled = $('session-folder').disabled = true;
      $('session-import-status').textContent = '';
      try {
        if (await api.import(folder)) {
          $('session-import-status').textContent = '検索対象に追加しました';
          search();
        }
      } catch (err) { $('session-import-status').textContent = err.message; }
      finally { $('session-import').disabled = $('session-folder').disabled = false; }
    };
    $('search-cancel').onclick = () => { cancel(); $('search-status').textContent = '検索を中止しました'; };
    api.onHit(payload => {
      if (payload.requestId !== showing) return;
      rows.push(...payload.sessions);
      if (state) { state.matched = rows.length; status(); }
      renderMore();
      batchControls();
    });
    api.onProgress(payload => {
      if (payload.requestId !== showing || !state) return;
      state.scanned = payload.scanned; state.pool = payload.pool; status();
    });
    $('search-results-pane').addEventListener('scroll', () => {
      const pane = $('search-results-pane');
      if (rendered < rows.length && pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 200) renderMore();
    });
    const changed = () => { cancel(); clearResults(); $('search-date-range').hidden = $('search-date').value !== 'range'; timer = setTimeout(() => search(), 300); };
    for (const id of ['search-text', 'search-agent', 'search-date', 'search-repo', 'search-model', 'search-source', 'search-date-field', 'search-archived', 'search-shared', 'search-since', 'search-until']) $(id).addEventListener('input', changed);
    $('search-text').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
    $('search-reset').onclick = () => {
      sharedMode(false);
      for (const id of ['search-text', 'search-agent', 'search-date', 'search-repo', 'search-model', 'search-source', 'search-since', 'search-until']) $(id).value = '';
      $('search-date-field').value = 'updated'; $('search-archived').checked = false; $('search-date-range').hidden = true; search();
    };
    $('search-target-repo').onchange = targetChanged;
    $('search-target-agent').onchange = () => { $('search-target-model').value = ''; executionLabel(); };
    $('search-target-model').oninput = () => { executionLabel(); };
    $('search-target-permission').onchange = executionLabel;
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
    $('search-batch-start').onclick = startBatch;
    $('search-batch-cancel').onclick = () => {
      picking = false; picked.clear(); batchError = ''; batchControls();
      $('search-batch-start').focus();
    };
    window.api.evaluation.onChanged(status => { evaluation = status; batchControls(); });
    window.api.evaluation.status().then(status => { evaluation = status; batchControls(); }).catch(() => {});
    $('session-search').addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); close(); } });
  }
  function openOrigin(origin) {
    $('search-reset').click();
    $('search-text').value = origin.title || '';
    $('search-source').value = origin.key?.startsWith('public:') ? 'app' : origin.provider === 'vscode' ? 'vscode' : 'cli';
    open();
    if (origin.key?.startsWith('public:')) { sharedMode(true); search(); }
  }
  async function forkPublic(key) { beginTransfer(await api.read(key)); }
  return { init, open, close, forkCurrent, openOrigin, forkPublic, handoffIssue };
})();
