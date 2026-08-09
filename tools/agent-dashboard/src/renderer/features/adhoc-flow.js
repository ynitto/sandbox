'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('workflow-run', {
      render: feature.render,
      refresh: feature.refresh,
      available: () => true,
    });
    root.registerFeatureTab('workflow-settings', {
      render: feature.render,
      available: () => true,
    });
  }
})(typeof globalThis !== 'undefined' ? globalThis : window, (root) => {
  const KINDS = ['work', 'generate', 'classify', 'synthesize', 'verify', 'filter', 'judge', 'reduce', 'split', 'map'];
  const st = {
    overview: null,
    selectedRun: '',
    runDetail: null,
    editor: null,
    selectedNode: '',
    connectFrom: '',
    busy: '',
    notice: '',
  };
  const esc = (s) => root.esc(String(s == null ? '' : s));
  const $id = (id) => document.getElementById(id);
  const api = () => root.api;

  function active() {
    return ['workflow-run', 'workflow-settings'].some((id) => {
      const pane = $id(`tab-${id}`);
      return pane && pane.classList.contains('active');
    });
  }

  function statusLabel(status) {
    return ({ done: '終了', failed: '失敗', cancelled: '中止', running: '実行中', planning: '計画中' })[status]
      || String(status || '—');
  }

  function emptyWorkflow() {
    return { id: '', name: '', description: '', nodes: [] };
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  async function refresh() {
    if (!active()) return;
    try {
      st.overview = await api().adhocFlowOverview({ limit: 30 });
      if (st.selectedRun) {
        try { st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun }); }
        catch { st.runDetail = null; }
      }
    } catch (err) {
      st.notice = `読み込みに失敗しました: ${String((err && err.message) || err)}`;
    }
    render();
  }

  function flowOptions(ov) {
    const patterns = (ov.patterns || []).map((p) =>
      `<option value="pattern:${esc(p.id)}">${esc(p.label)}</option>`).join('');
    const custom = (ov.workflows || []).map((p) =>
      `<option value="custom:${esc(p.id)}">${esc(p.name)}</option>`).join('');
    return `<option value="auto">自動</option>`
      + (patterns ? `<optgroup label="標準">${patterns}</optgroup>` : '')
      + (custom ? `<optgroup label="カスタム">${custom}</optgroup>` : '');
  }

  function selectionFrom(value) {
    const [type, ...rest] = String(value || 'auto').split(':');
    return type === 'auto' ? { type: 'auto' } : { type, id: rest.join(':') };
  }

  function runsHtml(ov) {
    const rows = ov.runs || [];
    if (!rows.length) return '<div class="empty">実行履歴はありません</div>';
    return `<div class="wf-run-list">${rows.map((r) => `<button type="button" class="wf-run-row ${st.selectedRun === r.runId ? 'selected' : ''}"
      data-run-id="${esc(r.runId)}"><span><code>${esc(r.runId)}</code></span>
      <span>${esc(statusLabel(r.status))}</span><span class="muted">${esc(r.updatedAt || r.createdAt || '')}</span></button>`).join('')}</div>`;
  }

  function runDetailHtml(detail) {
    if (!detail || !detail.run) return '';
    const run = detail.run;
    const inbox = detail.inbox || {};
    let graph = '';
    try { graph = root.renderTaskFlow ? root.renderTaskFlow(run) : ''; } catch { /* 結果表示は続ける */ }
    const outputs = Object.values(run.nodes || {}).filter((n) => n.output || n.data).map((n) =>
      `<details><summary>${esc(n.id)} · ${esc(statusLabel(n.state))}</summary><pre class="qf-output">${esc(String(n.output || JSON.stringify(n.data || '', null, 2)).slice(0, 4000))}</pre></details>`).join('');
    const flowName = inbox.plan ? inbox.plan.name : inbox.pattern || '自動';
    return `<section class="wf-result">
      <div class="wf-section-head"><div><strong>${esc(statusLabel(run.status))}</strong>
        <span class="muted">${esc(flowName)} · af/${esc(run.runId || st.selectedRun)}</span></div>
        <div class="qf-row"><button type="button" id="wf-resubmit">再実行</button>
          <button type="button" id="wf-cancel">中止</button><button type="button" id="wf-delete-run">削除</button></div></div>
      ${run.failureReason ? `<p class="qf-failure">${esc(run.failureReason)}</p>` : ''}
      ${run.final && run.final.summary ? `<pre class="qf-output">${esc(String(run.final.summary).slice(0, 3000))}</pre>` : ''}
      <div class="qf-graph">${graph}</div>${outputs}</section>`;
  }

  function runHtml(ov) {
    const history = (ov.cwdHistory || []).map((cwd) => `<option value="${esc(cwd)}"></option>`).join('');
    return `<section class="wf-page" aria-label="ワークフロー実行">
      <div class="wf-title"><div><h2>ワークフロー</h2><p>Gitリポジトリでフローを実行します。</p></div></div>
      ${st.notice ? `<p class="qf-notice" role="status">${esc(st.notice)}</p>` : ''}
      <div class="wf-run-card">
        <label>フォルダ<input id="wf-cwd" type="text" list="wf-cwd-history" placeholder="/path/to/repository" autocomplete="off"></label>
        <datalist id="wf-cwd-history">${history}</datalist>
        <label>フロー<select id="wf-flow">${flowOptions(ov)}</select></label>
        <label class="wf-request">依頼<textarea id="wf-request" rows="4" placeholder="実行する内容"></textarea></label>
        <button type="button" class="primary" id="wf-submit" ${st.busy ? 'disabled' : ''}>${esc(st.busy || '実行')}</button>
      </div>
      <div class="wf-section-head"><h3>実行履歴</h3></div>${runsHtml(ov)}${runDetailHtml(st.runDetail)}
    </section>`;
  }

  function edgesHtml(nodes) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    return nodes.flatMap((target) => (target.deps || []).map((sourceId) => {
      const source = byId.get(sourceId);
      if (!source) return '';
      const x1 = Number(source.x) + 220;
      const y1 = Number(source.y) + 48;
      const x2 = Number(target.x);
      const y2 = Number(target.y) + 48;
      const bend = Math.max(40, Math.abs(x2 - x1) / 2);
      return `<path d="M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}" />`;
    })).join('');
  }

  function nodeHtml(node) {
    const selected = st.selectedNode === node.id ? ' selected' : '';
    return `<article class="wf-node${selected}" data-node-id="${esc(node.id)}" style="left:${Number(node.x)}px;top:${Number(node.y)}px">
      <button type="button" class="wf-port in" data-connect-in="${esc(node.id)}" aria-label="${esc(node.id)}へ接続"></button>
      <div class="wf-node-drag" data-drag-node="${esc(node.id)}"><span>${esc(node.kind)}</span><span class="wf-tier">${esc(node.tier)}</span></div>
      <div class="wf-node-body"><strong>${esc(node.id)}</strong><p>${esc(node.goal)}</p></div>
      <button type="button" class="wf-port out" data-connect-out="${esc(node.id)}" aria-label="${esc(node.id)}から接続"></button>
    </article>`;
  }

  function inspectorHtml(ov, workflow) {
    const node = workflow.nodes.find((n) => n.id === st.selectedNode);
    if (!node) return '<div class="empty">ノードを選択してください</div>';
    const tierOptions = (ov.tiers || []).map((t) =>
      `<option value="${esc(t.id)}" ${node.tier === t.id ? 'selected' : ''}>${esc(t.label)}</option>`).join('');
    const kindOptions = KINDS.map((kind) =>
      `<option value="${kind}" ${node.kind === kind ? 'selected' : ''}>${kind}</option>`).join('');
    return `<div class="wf-inspector" data-inspector="${esc(node.id)}">
      <label>名前<input id="wf-node-id" value="${esc(node.id)}"></label>
      <label>種類<select id="wf-node-kind">${kindOptions}</select></label>
      <label>tier<select id="wf-node-tier">${tierOptions}</select></label>
      <label>内容<textarea id="wf-node-goal" rows="5">${esc(node.goal)}</textarea></label>
      <button type="button" id="wf-node-delete">ノードを削除</button>
    </div>`;
  }

  function editorHtml(ov) {
    const workflow = st.editor || emptyWorkflow();
    const list = ov.workflows || [];
    const palette = KINDS.map((kind) =>
      `<button type="button" draggable="true" data-palette="${kind}">${kind}</button>`).join('');
    return `<section class="wf-page wf-settings" aria-label="ワークフロー設定">
      <div class="wf-title"><div><h2>カスタムフロー</h2><p>ノードを配置して接続します。</p></div>
        <button type="button" id="wf-new">新規作成</button></div>
      ${st.notice ? `<p class="qf-notice" role="status">${esc(st.notice)}</p>` : ''}
      <div class="wf-editor-layout">
        <aside class="wf-list"><h3>フロー</h3>${list.length ? list.map((item) =>
          `<button type="button" data-workflow-id="${esc(item.id)}" class="${workflow.id === item.id ? 'selected' : ''}">${esc(item.name)}</button>`).join('') : '<div class="empty">まだありません</div>'}</aside>
        <main class="wf-editor-main">
          <div class="wf-editor-head"><label>名前<input id="wf-name" value="${esc(workflow.name)}" placeholder="フロー名"></label>
            <label>説明<input id="wf-description" value="${esc(workflow.description)}" placeholder="任意"></label>
            <button type="button" class="primary" id="wf-save">保存</button>
            ${workflow.id ? '<button type="button" id="wf-delete">削除</button>' : ''}</div>
          <div class="wf-palette" aria-label="ノード一覧">${palette}</div>
          <div class="wf-canvas" id="wf-canvas" tabindex="0" aria-label="フローキャンバス">
            <svg aria-hidden="true"><g>${edgesHtml(workflow.nodes)}</g></svg>${workflow.nodes.map(nodeHtml).join('')}
          </div>
        </main>
        <aside class="wf-properties"><h3>ノード</h3>${inspectorHtml(ov, workflow)}</aside>
      </div>
    </section>`;
  }

  function render() {
    const ov = st.overview || { runs: [], workflows: [], patterns: [], tiers: [], cwdHistory: [] };
    const runPane = $id('tab-workflow-run');
    const settingsPane = $id('tab-workflow-settings');
    if (runPane) runPane.innerHTML = runHtml(ov);
    if (settingsPane) settingsPane.innerHTML = editorHtml(ov);
    wireRun(runPane);
    wireSettings(settingsPane, ov);
  }

  function wireRun(pane) {
    if (!pane) return;
    $id('wf-submit')?.addEventListener('click', async () => {
      st.busy = '実行中…';
      st.notice = '';
      const payload = {
        cwd: $id('wf-cwd')?.value || '',
        request: $id('wf-request')?.value || '',
        selection: selectionFrom($id('wf-flow')?.value || 'auto'),
      };
      render();
      try {
        const result = await api().adhocFlowSubmit(payload);
        st.selectedRun = result.runId;
        st.notice = `実行を開始しました · ${result.branch}`;
      } catch (err) {
        st.notice = String((err && err.message) || err);
      }
      st.busy = '';
      await refresh();
    });
    pane.querySelectorAll('[data-run-id]').forEach((row) => row.addEventListener('click', async () => {
      st.selectedRun = row.dataset.runId;
      try { st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun }); }
      catch (err) { st.notice = String((err && err.message) || err); }
      render();
    }));
    $id('wf-resubmit')?.addEventListener('click', async () => {
      try {
        const result = await api().adhocFlowResubmit({ runId: st.selectedRun });
        st.selectedRun = result.runId;
        st.notice = `再実行しました · af/${result.runId}`;
      } catch (err) { st.notice = String((err && err.message) || err); }
      await refresh();
    });
    $id('wf-cancel')?.addEventListener('click', async () => {
      try { await api().adhocFlowCancel({ runId: st.selectedRun }); }
      catch (err) { st.notice = String((err && err.message) || err); }
      await refresh();
    });
    $id('wf-delete-run')?.addEventListener('click', async () => {
      try {
        await api().adhocFlowDeleteRun({ runId: st.selectedRun });
        st.selectedRun = '';
        st.runDetail = null;
      } catch (err) { st.notice = String((err && err.message) || err); }
      await refresh();
    });
  }

  function collectWorkflow() {
    const workflow = st.editor || (st.editor = emptyWorkflow());
    workflow.name = $id('wf-name')?.value || workflow.name;
    workflow.description = $id('wf-description')?.value || '';
    const node = workflow.nodes.find((n) => n.id === st.selectedNode);
    if (node && $id('wf-node-id')) {
      const oldId = node.id;
      node.id = $id('wf-node-id').value.trim();
      node.kind = $id('wf-node-kind').value;
      node.tier = $id('wf-node-tier').value;
      node.goal = $id('wf-node-goal').value.trim();
      if (node.id && oldId !== node.id) {
        workflow.nodes.forEach((n) => { n.deps = (n.deps || []).map((id) => id === oldId ? node.id : id); });
        st.selectedNode = node.id;
      }
    }
    return workflow;
  }

  function addNode(kind, x, y, ov) {
    const workflow = collectWorkflow();
    const used = new Set(workflow.nodes.map((n) => n.id));
    let i = workflow.nodes.length + 1;
    while (used.has(`n${i}`)) i += 1;
    const tier = ov.tiers && ov.tiers[0] ? ov.tiers[0].id : '';
    workflow.nodes.push({ id: `n${i}`, goal: '{{request}}', kind, tier, deps: [], x, y });
    st.selectedNode = `n${i}`;
    render();
  }

  function wireSettings(pane, ov) {
    if (!pane) return;
    $id('wf-new')?.addEventListener('click', () => {
      st.editor = emptyWorkflow(); st.selectedNode = ''; st.connectFrom = ''; render();
    });
    pane.querySelectorAll('[data-workflow-id]').forEach((button) => button.addEventListener('click', () => {
      collectWorkflow();
      const found = (ov.workflows || []).find((item) => item.id === button.dataset.workflowId);
      st.editor = found ? clone(found) : emptyWorkflow();
      st.selectedNode = ''; st.connectFrom = ''; render();
    }));
    $id('wf-save')?.addEventListener('click', async () => {
      try {
        const result = await api().adhocFlowSaveWorkflow({ workflow: collectWorkflow() });
        st.editor = clone(result.saved);
        st.notice = '保存しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      await refresh();
    });
    $id('wf-delete')?.addEventListener('click', async () => {
      try {
        await api().adhocFlowDeleteWorkflow({ id: st.editor.id });
        st.editor = emptyWorkflow(); st.selectedNode = ''; st.notice = '削除しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      await refresh();
    });
    pane.querySelectorAll('[data-palette]').forEach((button) => {
      button.addEventListener('click', () => addNode(button.dataset.palette, 60 + ((st.editor?.nodes.length || 0) % 3) * 250, 70, ov));
      button.addEventListener('dragstart', (event) => event.dataTransfer.setData('text/workflow-kind', button.dataset.palette));
    });
    const canvas = $id('wf-canvas');
    canvas?.addEventListener('dragover', (event) => event.preventDefault());
    canvas?.addEventListener('drop', (event) => {
      event.preventDefault();
      const kind = event.dataTransfer.getData('text/workflow-kind');
      if (!KINDS.includes(kind)) return;
      const box = canvas.getBoundingClientRect();
      addNode(kind, Math.max(20, event.clientX - box.left - 110), Math.max(20, event.clientY - box.top - 30), ov);
    });
    pane.querySelectorAll('[data-node-id]').forEach((node) => node.addEventListener('click', () => {
      collectWorkflow(); st.selectedNode = node.dataset.nodeId; render();
    }));
    pane.querySelectorAll('[data-connect-out]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation(); st.connectFrom = button.dataset.connectOut; st.notice = '接続先を選択してください'; render();
    }));
    pane.querySelectorAll('[data-connect-in]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation();
      const target = st.editor.nodes.find((n) => n.id === button.dataset.connectIn);
      if (target && st.connectFrom && st.connectFrom !== target.id && !target.deps.includes(st.connectFrom)) {
        target.deps.push(st.connectFrom);
      }
      st.connectFrom = ''; st.notice = ''; render();
    }));
    $id('wf-node-delete')?.addEventListener('click', () => {
      const id = st.selectedNode;
      const workflow = collectWorkflow();
      workflow.nodes = workflow.nodes.filter((n) => n.id !== id);
      workflow.nodes.forEach((n) => { n.deps = n.deps.filter((dep) => dep !== id); });
      st.selectedNode = ''; render();
    });
    ['wf-node-id', 'wf-node-kind', 'wf-node-tier', 'wf-node-goal'].forEach((id) =>
      $id(id)?.addEventListener('change', () => { collectWorkflow(); render(); }));

    pane.querySelectorAll('[data-drag-node]').forEach((handle) => handle.addEventListener('pointerdown', (event) => {
      event.stopPropagation();
      const node = st.editor.nodes.find((n) => n.id === handle.dataset.dragNode);
      if (!node) return;
      const start = { x: event.clientX, y: event.clientY, left: Number(node.x), top: Number(node.y) };
      handle.setPointerCapture(event.pointerId);
      const move = (e) => {
        node.x = Math.max(0, start.left + e.clientX - start.x);
        node.y = Math.max(0, start.top + e.clientY - start.y);
        const card = pane.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
        if (card) { card.style.left = `${node.x}px`; card.style.top = `${node.y}px`; }
      };
      const up = () => { handle.removeEventListener('pointermove', move); render(); };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up, { once: true });
    }));
  }

  async function ensureLoaded() {
    if (!st.overview) await refresh();
    else render();
  }

  return { render: ensureLoaded, refresh, statusLabel, selectionFrom, _state: st };
});
