'use strict';

// 複数 AI ワークフローの画面。maker と agent-app の双方で同じ描画を使えるよう、
// IPC 呼び出しや既存画面の render/toast は生成時に受け取る。
window.createFlowFeature = function createFlowFeature(ctx) {
  const view = {
    root: '', loading: false, catalog: { kinds: [], patterns: [] }, context: null,
    flows: [], selected: '', workflow: null, issues: [], editor: null,
    teachings: [], selectedTeaching: '', teaching: null, creatingTeaching: false,
    teachingInput: '', teachingWorkflow: null, trialTeaching: null,
    runs: [], selectedRun: '', run: null, result: null, log: null, detailTab: 'overview',
    request: '', parameters: {}, readonly: false, agent: '', model: '', starting: false,
  };
  let previewTimer = null;
  let runTimer = null;
  let listTimer = null;

  const e = ctx.escape;
  const featureName = ctx.name || 'AIワークフロー';
  const root = () => ctx.root();
  const kind = (id) => view.catalog.kinds.find((item) => item.kind === id)
    || { kind: id, label: id, description: '' };

  function clearTimers() {
    clearTimeout(previewTimer);
    clearTimeout(runTimer);
    clearTimeout(listTimer);
  }

  function active() {
    return ctx.isActive() && root() === view.root;
  }

  function safeToRepaint() {
    const focused = ctx.activeElement ? ctx.activeElement() : document.activeElement;
    return !focused || !focused.matches('[data-flow-request], [data-flow-param], [data-flow-model], [data-flow-answer-value], [data-flow-answer-comment], [data-flow-teaching-purpose], [data-flow-teaching-trial-request]');
  }

  async function loadRun(runId, repaint = true) {
    if (!runId || !root()) return;
    const detail = await ctx.guard('実行状況', () => ctx.bridge.runRead(root(), runId));
    if (!detail || root() !== view.root || runId !== view.selectedRun) return;
    const changed = !view.run || view.run.revision !== detail.revision;
    view.run = detail;
    if (changed && repaint && active() && safeToRepaint()) ctx.refresh();
    schedulePolling();
  }

  async function loadRuns(repaint = true) {
    if (!root()) return;
    const runs = await ctx.guard('実行履歴', () => ctx.bridge.runList(root(), 30));
    if (!runs || root() !== view.root) return;
    view.runs = runs;
    if (view.selectedRun && !runs.some((item) => item.runId === view.selectedRun)) {
      view.selectedRun = '';
      view.run = null;
    }
    if (repaint && active() && safeToRepaint()) ctx.refresh();
    schedulePolling();
  }

  function schedulePolling() {
    clearTimeout(runTimer);
    clearTimeout(listTimer);
    if (!active()) return;
    if (view.run && !view.run.terminal) runTimer = setTimeout(() => loadRun(view.selectedRun), 2000);
    if (view.runs.some((item) => !item.terminal)) listTimer = setTimeout(() => loadRuns(), 5000);
  }

  async function readWorkflow(id, repaint = true) {
    if (!id) { view.workflow = null; view.issues = []; return; }
    const found = await ctx.guard('ワークフロー', () => ctx.bridge.read(root(), id));
    if (!found || id !== view.selected || root() !== view.root) return;
    view.workflow = found.workflow;
    view.issues = found.issues || [];
    view.parameters = Object.fromEntries((view.flows.find((item) => item.id === id)?.parameterKeys || []).map((key) => [key, view.parameters[key] || '']));
    if (repaint && active()) ctx.refresh();
  }

  async function loadRoot() {
    clearTimers();
    view.root = root();
    view.loading = true;
    view.context = null;
    view.flows = [];
    view.workflow = null;
    view.runs = [];
    view.run = null;
    view.selectedRun = '';
    if (active()) ctx.refresh();
    if (!view.root) { view.loading = false; return; }
    const [catalog, flows, flowContext, runs, teachings] = await Promise.all([
      view.catalog.kinds.length ? Promise.resolve(view.catalog) : ctx.guard('ワークフローの準備', () => ctx.bridge.catalog()),
      ctx.guard('ワークフロー一覧', () => ctx.bridge.list(view.root)),
      ctx.guard('実行環境', () => ctx.bridge.context(view.root)),
      ctx.guard('実行履歴', () => ctx.bridge.runList(view.root, 30)),
      ctx.guard('教示中のワークフロー', () => ctx.bridge.teachingList(view.root)),
    ]);
    if (root() !== view.root) return;
    if (catalog) view.catalog = catalog;
    view.flows = flows || [];
    view.context = flowContext;
    view.runs = runs || [];
    view.teachings = teachings || [];
    const agentNames = (flowContext && flowContext.agents) || [];
    const preferredAgent = (flowContext && flowContext.defaults.agent) || ctx.config().agent || '';
    view.agent = agentNames.includes(preferredAgent) ? preferredAgent : (agentNames[0] || '');
    view.model = (flowContext && flowContext.defaults.model) || ctx.config().model || '';
    view.readonly = !!(flowContext && !flowContext.workspace.ok);
    if (!view.flows.some((item) => item.id === view.selected)) view.selected = view.flows[0]?.id || '';
    view.loading = false;
    if (view.selected) await readWorkflow(view.selected, false);
    if (active()) ctx.refresh();
    schedulePolling();
  }

  async function activate() {
    if (view.root === root() && !view.loading && (view.context || !root())) {
      schedulePolling();
      return;
    }
    await loadRoot();
  }

  function rootChanged() {
    clearTimers();
    view.root = '';
    if (ctx.isActive()) activate();
  }

  function stateLabel(state) {
    return ({
      launching: '起動中', 'launch-failed': '起動失敗', planning: '計画中', executing: '実行中',
      evaluating: '評価中', verifying: '検証中', finalizing: '仕上げ中', waiting: '回答待ち',
      stalled: '応答なし', pending: '待機中', claimed: '作業中', parked: '外部確認中',
      done: '完了', failed: '失敗', cancelled: '停止済み',
    })[state] || state || '準備中';
  }

  function statusClass(state) {
    if (state === 'done') return 'ok';
    if (['failed', 'launch-failed'].includes(state)) return 'ng';
    if (['waiting', 'stalled'].includes(state)) return 'warn';
    if (!['cancelled'].includes(state)) return 'active';
    return '';
  }

  function workflowRuns(workflowId = view.workflow?.id || view.selected) {
    return view.runs.filter((run) => run.workflowId === workflowId || run.input?.workflowId === workflowId);
  }

  function workflowHistoryHtml() {
    const rows = workflowRuns().map((run) => `<li><span class="status ${statusClass(run.state)}">${e(stateLabel(run.state))}</span><div><strong>${e(run.title || run.runId)}</strong><small>${e(ctx.dateLabel(run.createdAt))} · ${run.progress.done + run.progress.failed}/${run.progress.total || '—'} 工程</small></div><button type="button" class="tiny" data-flow-run="${e(run.runId)}">詳細</button></li>`).join('');
    return `<section class="execution-card flow-history"><div class="execution-card-head"><div><h3>実行履歴</h3><p>このワークフローを選択中のリポジトリで実行した履歴</p></div></div>${rows ? `<ul class="run-history flow-run-history">${rows}</ul>` : '<p class="muted small">実行履歴はまだありません。</p>'}</section>`;
  }

  function sidebarHtml() {
    const teachings = view.teachings.filter((item) => item.status !== 'ready').map((item) => `<button type="button" class="execution-item ${item.workflowId === view.selectedTeaching ? 'is-on' : ''}" data-flow-teaching-select="${e(item.workflowId)}"><strong>${e(item.title)}</strong><span>${e(teachingStatusLabel(item.status))}</span></button>`).join('');
    const flows = view.flows.map((item) => `<button type="button" class="execution-item ${!view.selectedRun && item.id === view.selected ? 'is-on' : ''}" data-flow-select="${e(item.id)}"><strong>${e(item.name)}</strong><span>${item.nodes} 工程${item.humanNodes ? ` · 人の確認 ${item.humanNodes}` : ''}${item.valid === false ? ' · 要修正' : ''}</span></button>`).join('');
    const runs = view.runs.map((item) => `<button type="button" class="execution-item ${item.runId === view.selectedRun ? 'is-on' : ''}" data-flow-run="${e(item.runId)}"><strong>${e(item.title || item.runId)}</strong><span>${e(stateLabel(item.state))} · ${item.progress.done + item.progress.failed}/${item.progress.total || '—'}</span></button>`).join('');
    return `<aside class="execution-list flow-side"><div class="flow-side-head"><span>作成・変更中</span><button type="button" class="tiny" data-flow-new title="新しく教える">＋</button></div>${teachings || '<p class="muted small">ありません。</p>'}<div class="flow-side-head"><span>利用可能</span></div>${flows || '<p class="muted small">まだありません。</p>'}<div class="flow-side-head flow-run-head"><span>実行履歴</span></div>${runs || '<p class="muted small">まだありません。</p>'}</aside>`;
  }

  function emptyHtml() {
    const unreadable = view.selected && view.flows.find((item) => item.id === view.selected && item.valid === false);
    if (unreadable) return `<div class="blank compact"><h2>内容を読み取れません</h2><p>${e(unreadable.file)} を直すか、この一覧から削除してください。</p><div class="row"><button type="button" class="danger" data-flow-delete-unreadable="${e(unreadable.id)}">一覧から削除</button></div></div>`;
    const patterns = view.catalog.patterns || [];
    return `<div class="blank compact"><h2>${e(featureName)}を教えます</h2><p>実現したいことを普段の言葉で伝えてください。AIが必要な工程と確認方法を提案します。</p><div class="row"><button type="button" class="primary" data-flow-new>AIに相談して作る</button><button type="button" data-flow-manual-new>手動で作成</button>${patterns.length ? `<select data-flow-pattern aria-label="ひな形"><option value="">ひな形から作成…</option>${patterns.map((item) => `<option value="${e(item.id)}">${e(item.label)}</option>`).join('')}</select>` : ''}</div></div>`;
  }

  function teachingStatusLabel(status) {
    return ({ draft: '理解中', 'needs-trial': '試運転待ち', 'awaiting-confirmation': '確認待ち', ready: '利用可能' })[status] || status;
  }

  function workflowStages(nodes = []) {
    const stages = [];
    for (const node of nodes) {
      stages.push({ node, dynamic: false });
      const hasExplicitSuccessor = nodes.some((candidate) => candidate.deps.includes(node.id));
      if (node.kind === 'classify' && !hasExplicitSuccessor) {
        stages.push({
          dynamic: true,
          node: { id: `${node.id}-dynamic-work`, label: '分類結果に応じて実行', kind: 'work', deps: [node.id], goal: '分類結果に合う専門作業を実行時に生成します。' },
        });
      }
      if (node.kind === 'split') {
        stages.push({
          dynamic: true,
          node: { id: `${node.id}-dynamic-map`, label: '要素ごとに実行', kind: 'map', deps: [node.id], goal: '分割された要素の数だけ、実行工程を生成します。' },
        }, {
          dynamic: true,
          node: { id: `${node.id}-dynamic-reduce`, label: '結果を集約', kind: 'reduce', deps: [`${node.id}-dynamic-map`], goal: 'すべての実行結果を一つの成果へ集約します。' },
        });
      }
    }
    return stages;
  }

  function stageSummaryHtml(workflow) {
    return workflowStages(workflow.nodes).map((stage, index) => {
      const node = stage.node;
      const before = stage.dynamic ? '実行時に生成' : (node.deps.length ? `前: ${node.deps.join('、')}` : '開始工程');
      return `<li class="${stage.dynamic ? 'is-dynamic' : ''}"><span class="flow-node-state">${index + 1}</span><div><strong>${e(node.label || node.id)}</strong><small>${stage.dynamic ? '<span class="status">実行時に生成</span> · ' : ''}${e(kind(node.kind).label)} · ${e(before)}</small><p>${e(node.goal)}</p></div></li>`;
    }).join('');
  }

  function reworkLaneHtml(workflow) {
    const policies = Array.isArray(workflow && workflow.rework) ? workflow.rework : [];
    if (!policies.length) return '';
    const nodeLabel = (id) => {
      const index = workflow.nodes.findIndex((node) => node.id === id);
      const node = workflow.nodes[index];
      return `${index >= 0 ? `${index + 1}. ` : ''}${node ? node.label || node.id : id}`;
    };
    const exhausted = { human: '上限後は人に確認', fail: '上限後は失敗として終了', continue: '上限後はそのまま続行' };
    return `<aside class="flow-rework-lane" aria-label="反復と差し戻し"><strong>反復</strong>${policies.map((policy) => `<div class="flow-rework-line"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="flow-rework-path" d="M7 7h9a5 5 0 0 1 0 10H8"></path><path class="flow-rework-path" d="m7 4-3 3 3 3"></path></svg><div><strong>${policy.trigger === 'human-rejected' ? '却下されたら' : '検証に失敗したら'}「${e(nodeLabel(policy.to))}」へ戻る</strong><span>「${e(nodeLabel(policy.from))}」から戻り、最大 ${e(policy.maxIterations)} 回繰り返します。</span><small>${e(policy.instruction)} · ${e(exhausted[policy.onExhausted] || '')}</small></div></div>`).join('')}</aside>`;
  }

  // 教える画面。会話そのもの（端末ミラーと入力欄）は光の DOM 側（#flow-teaching）が描くので、
  // ここは見出しと「いまの候補」だけを持ち、置き場（slot）を開ける。タスクの教示と同じ形。
  function teachingHtml() {
    if (view.creatingTeaching) {
      announceTeaching(null);
      return `<div class="blank teaching-create"><span class="eyebrow">新しいワークフローを教える</span><h2>何を複数のAIで実現したいですか？</h2><p>工程を決め打ちせず、目的・成果・制約を伝えてください。</p><textarea rows="7" data-flow-teaching-purpose placeholder="例: 変更依頼を調査し、必要なら並列に実装して、品質確認後にレビュー可能な成果をまとめたい">${e(view.teachingInput)}</textarea><div class="row"><button type="button" class="primary" data-flow-teaching-create>AIに相談する</button><button type="button" data-flow-teaching-cancel>戻る</button></div></div>`;
    }
    const session = view.teaching;
    if (!session) { announceTeaching(null); return emptyHtml(); }
    announceTeaching({ root: root(), workflowId: session.workflowId, existing: !!(view.teachingWorkflow && view.teachingWorkflow.workflow), title: session.title || session.workflowId });
    const draft = view.teachingWorkflow && view.teachingWorkflow.workflow;
    const broken = draft && (view.teachingWorkflow.issues || []).filter((item) => item.level === 'error');
    const candidate = draft ? `<section class="execution-card"><div class="execution-card-head"><div><h3>候補の工程</h3><p>${e(draft.description || draft.name || '')}</p></div>${broken.length ? '<span class="status ng">要修正</span>' : ''}</div>${broken.length ? issueHtml(view.teachingWorkflow.issues) : `<div class="flow-graph-with-rework"><ol class="flow-node-summary">${stageSummaryHtml(draft)}</ol>${reworkLaneHtml(draft)}</div><div class="field"><label>代表的な依頼で試運転</label><textarea rows="3" data-flow-teaching-trial-request>${e(view.request || session.understanding.purpose)}</textarea></div><div class="row"><button type="button" class="primary" data-flow-teaching-trial>試運転する</button>${session.status === 'awaiting-confirmation' ? '<button type="button" data-flow-teaching-confirm>この内容で利用可能にする</button>' : ''}</div>`}</section>` : '';
    return `<header class="execution-title"><div><span class="eyebrow">ワークフローを教える</span><h2>${e(session.title || session.workflowId)}</h2><span class="status">${e(teachingStatusLabel(session.status))}</span></div></header>${candidate}<slot name="flow-teaching"></slot>`;
  }

  function issueHtml(issues = view.issues) {
    if (!issues.length) return '<p class="run-result ok">保存できる内容です。</p>';
    return `<ul class="flow-issues">${issues.map((item) => `<li class="${item.level === 'error' ? 'err' : 'warn'}">${e(item.message)}</li>`).join('')}</ul>`;
  }

  function editorHtml() {
    const draft = view.editor.workflow;
    const nodes = workflowStages(draft.nodes).map((stage, displayIndex) => {
      const node = stage.node;
      if (stage.dynamic) return `<article class="flow-node-card is-dynamic" aria-label="実行時に生成される工程"><div class="flow-node-number">${displayIndex + 1}</div><div class="flow-node-form"><div class="flow-dynamic-heading"><strong>${e(node.label)}</strong><span class="status">実行時に生成</span></div><p>${e(node.goal)}</p></div></article>`;
      const index = draft.nodes.indexOf(node);
      const deps = draft.nodes.filter((candidate) => candidate.id && candidate !== node).map((candidate) => `<label><input type="checkbox" data-flow-dep="${index}" value="${e(candidate.id)}" ${node.deps.includes(candidate.id) ? 'checked' : ''}>${e(candidate.label || candidate.id)}</label>`).join('');
      const interaction = node.kind === 'human' ? (node.interaction || { mode: 'approval', prompt: '', options: [] }) : null;
      return `<article class="flow-node-card"><div class="flow-node-number">${displayIndex + 1}</div><div class="flow-node-form"><div class="grid2"><div class="field"><label>工程名</label><input data-flow-node="${index}" data-key="label" value="${e(node.label || '')}" placeholder="例: 仕様を整理"></div><div class="field"><label>工程の種類</label><select data-flow-node="${index}" data-key="kind">${view.catalog.kinds.map((item) => `<option value="${e(item.kind)}" ${item.kind === node.kind ? 'selected' : ''}>${e(item.label)}</option>`).join('')}</select></div></div><div class="field"><label>この工程で行うこと</label><textarea rows="3" data-flow-node="${index}" data-key="goal" placeholder="依頼全体は {{request}} と書けます">${e(node.goal || '')}</textarea></div><details class="flow-node-more" ${interaction ? 'open' : ''}><summary>つながりと詳細</summary><div class="details-body"><div class="field"><label>この前に終える工程</label><div class="flow-deps">${deps || '<small>先頭の工程です。</small>'}</div></div><div class="field"><label>工程の保存名</label><input class="mono" data-flow-node="${index}" data-key="id" value="${e(node.id || '')}"></div>${interaction ? interactionHtml(interaction, index) : ''}</div></details><div class="row"><button type="button" class="danger tiny" data-flow-remove-node="${index}" ${draft.nodes.length === 1 ? 'disabled' : ''}>工程を削除</button></div></div></article>`;
    }).join('');
    const reworks = (draft.rework || []).map((policy, index) => {
      const fromOptions = draft.nodes.filter((node) => ['human', 'verify'].includes(node.kind)).map((node) => `<option value="${e(node.id)}" ${node.id === policy.from ? 'selected' : ''}>${e(node.label || node.id)}</option>`).join('');
      const toOptions = draft.nodes.map((node) => `<option value="${e(node.id)}" ${node.id === policy.to ? 'selected' : ''}>${e(node.label || node.id)}</option>`).join('');
      return `<article class="flow-rework-editor"><div class="grid2"><div class="field"><label>戻り元</label><select data-flow-rework="${index}" data-key="from">${fromOptions}</select></div><div class="field"><label>戻り先</label><select data-flow-rework="${index}" data-key="to">${toOptions}</select></div></div><div class="grid2"><div class="field"><label>きっかけ</label><select data-flow-rework="${index}" data-key="trigger"><option value="human-rejected" ${policy.trigger === 'human-rejected' ? 'selected' : ''}>人が却下</option><option value="verification-failed" ${policy.trigger === 'verification-failed' ? 'selected' : ''}>検証失敗</option></select></div><div class="field"><label>最大回数</label><input type="number" min="1" max="20" data-flow-rework="${index}" data-key="maxIterations" value="${e(policy.maxIterations || 1)}"></div></div><div class="field"><label>やり直すときの指示</label><textarea rows="2" data-flow-rework="${index}" data-key="instruction">${e(policy.instruction || '')}</textarea></div><button type="button" class="danger tiny" data-flow-remove-rework="${index}">差し戻しを削除</button></article>`;
    }).join('');
    return `<section class="flow-editor"><header class="execution-title"><div><span class="eyebrow">${e(featureName)}</span><h2>${view.editor.mode === 'create' ? '新しく作る' : '内容を編集'}</h2></div><button type="button" class="ghost" data-flow-close-editor>閉じる</button></header><section class="execution-card"><div class="grid2"><div class="field"><label>名前</label><input data-flow-meta="name" value="${e(draft.name || '')}" placeholder="例: 変更案を並列レビュー"></div><div class="field"><label>保存名</label><input class="mono" data-flow-meta="id" value="${e(draft.id || '')}" ${view.editor.mode === 'update' ? 'disabled' : ''}></div></div><div class="field"><label>説明</label><textarea rows="2" data-flow-meta="description">${e(draft.description || '')}</textarea></div></section><div class="flow-graph-with-rework"><div class="flow-node-list">${nodes}</div>${reworkLaneHtml(draft)}</div><button type="button" class="flow-add-node" data-flow-add-node>＋ 工程を追加</button><section class="execution-card"><div class="execution-card-head"><div><h3>差し戻し</h3><p>DAGの依存関係は循環させず、失敗時だけ外側の線で前工程へ戻します。</p></div><button type="button" data-flow-add-rework>＋ 差し戻し</button></div>${reworks || '<p class="muted small">差し戻しはありません。</p>'}</section><div id="flow-editor-issues">${issueHtml(view.editor.issues || [])}</div><div class="flow-sticky-actions"><button type="button" class="primary" data-flow-save>保存</button><button type="button" data-flow-preview>内容を確認</button></div></section>`;
  }

  function interactionHtml(interaction, index) {
    const options = Array.isArray(interaction.options) ? interaction.options.join('\n') : '';
    return `<div class="flow-human"><div class="field"><label>確認方法</label><select data-flow-interaction="${index}" data-key="mode"><option value="approval" ${interaction.mode === 'approval' ? 'selected' : ''}>承認・却下</option><option value="choice" ${interaction.mode === 'choice' ? 'selected' : ''}>選択肢</option><option value="input" ${interaction.mode === 'input' ? 'selected' : ''}>自由入力</option></select></div><div class="field"><label>質問</label><textarea rows="2" data-flow-interaction="${index}" data-key="prompt">${e(interaction.prompt || '')}</textarea></div>${interaction.mode === 'choice' ? `<div class="field"><label>選択肢（1行に1つ）</label><textarea rows="3" data-flow-interaction="${index}" data-key="options">${e(options)}</textarea></div>` : ''}</div>`;
  }

  function workflowHtml() {
    const workflow = view.workflow;
    if (!workflow) return emptyHtml();
    const summary = view.flows.find((item) => item.id === workflow.id) || { parameterKeys: [] };
    const nodes = stageSummaryHtml(workflow);
    const stages = workflowStages(workflow.nodes);
    const contextWarning = !view.context?.tools?.agentFlow?.ok
      ? `<p class="run-result warn">${e(view.context?.tools?.agentFlow?.summary || '実行環境を確認してください')}</p>` : '';
    const params = (summary.parameterKeys || []).map((key) => `<div class="field"><label>${e(key)}</label><input data-flow-param="${e(key)}" value="${e(view.parameters[key] || '')}"></div>`).join('');
    const agents = (view.context?.agents || ctx.agents()).map((name) => `<option value="${e(name)}" ${name === view.agent ? 'selected' : ''}>${e(name)}</option>`).join('');
    const canRun = !!view.context?.tools?.agentFlow?.ok && !!agents && !view.starting
      && !view.issues.some((item) => item.level === 'error');
    const tabs = `<nav class="task-detail-tabs flow-detail-tabs" role="tablist" aria-label="ワークフロー詳細"><button type="button" role="tab" data-flow-tab="overview" aria-selected="${view.detailTab === 'overview'}" class="${view.detailTab === 'overview' ? 'is-on' : ''}">概要</button><button type="button" role="tab" data-flow-tab="history" aria-selected="${view.detailTab === 'history'}" class="${view.detailTab === 'history' ? 'is-on' : ''}">実行履歴</button></nav>`;
    const overview = `${view.issues.length ? `<div>${issueHtml()}</div>` : ''}<section class="execution-card flow-overview"><div class="execution-card-head"><div><h3>工程</h3><p>${workflow.nodes.length} 固定工程${stages.length > workflow.nodes.length ? ` · 実行時に ${stages.length - workflow.nodes.length} 工程を生成` : ''}${workflow.nodes.some((node) => node.kind === 'human') ? ' · 途中で人の確認があります' : ''}</p></div></div><div class="flow-graph-with-rework"><ol class="flow-node-summary">${nodes}</ol>${reworkLaneHtml(workflow)}</div></section><section class="execution-card"><div class="execution-card-head"><div><h3>このワークフローを実行</h3><p>依頼をもとに、工程ごとにAIが作業します。</p></div></div>${contextWarning}<div class="field"><label>依頼内容</label><textarea rows="4" data-flow-request placeholder="何を完了してほしいか入力してください">${e(view.request)}</textarea></div>${params ? `<div class="run-inputs"><h3>実行時の入力</h3><div class="run-input-grid">${params}</div></div>` : ''}<div class="grid2"><div class="field"><label>使うAI</label><select data-flow-agent ${agents ? '' : 'disabled'}>${agents || '<option>利用できるAIがありません</option>'}</select></div><div class="field"><label>モデル（任意）</label><input data-flow-model value="${e(view.model)}"></div></div><p class="muted small">手動実行ではツールを自動承認します。</p><label class="check-label"><input type="checkbox" data-flow-readonly ${view.readonly ? 'checked' : ''} ${view.context && !view.context.workspace.ok ? 'disabled' : ''}>読み取り専用で実行する</label>${view.context && !view.context.workspace.ok ? `<small class="muted">${e(view.context.workspace.reason)}。読み取り専用で実行できます。</small>` : ''}<div class="row"><button type="button" class="primary" data-flow-start ${canRun ? '' : 'disabled'}>${view.starting ? '開始中…' : '実行する'}</button></div></section>`;
    return `<header class="execution-title"><div><span class="eyebrow">${e(featureName)}</span><h2>${e(workflow.name)}</h2>${workflow.description ? `<p>${e(workflow.description)}</p>` : ''}</div><div class="row"><button type="button" class="ghost" data-flow-change-consult>変更を相談</button><button type="button" class="ghost" data-flow-duplicate>複製</button><button type="button" class="ghost" data-flow-edit>編集</button><button type="button" class="danger ghost" data-flow-delete>削除</button></div></header>${tabs}${view.detailTab === 'history' ? workflowHistoryHtml() : overview}`;
  }

  function answerHtml(interaction) {
    if (interaction.state !== 'open') return `<p class="muted small">${interaction.state === 'answered' ? '回答を送信しました。処理への反映を待っています。' : '回答済みです。'}</p>`;
    if (interaction.mode === 'approval') return `<div class="field"><label>コメント（任意）</label><textarea rows="2" data-flow-answer-comment="${e(interaction.interactionId)}"></textarea></div><div class="row"><button type="button" class="primary" data-flow-answer="${e(interaction.interactionId)}" data-decision="approved">承認する</button><button type="button" class="danger" data-flow-answer="${e(interaction.interactionId)}" data-decision="rejected">却下する</button></div>`;
    if (interaction.mode === 'choice') return `<div class="field"><label>回答</label><select data-flow-answer-value="${e(interaction.interactionId)}">${interaction.options.map((option) => `<option value="${e(option)}">${e(option)}</option>`).join('')}</select></div><button type="button" class="primary" data-flow-answer="${e(interaction.interactionId)}">回答する</button>`;
    return `<div class="field"><label>回答</label><textarea rows="3" data-flow-answer-value="${e(interaction.interactionId)}"></textarea></div><button type="button" class="primary" data-flow-answer="${e(interaction.interactionId)}">回答する</button>`;
  }

  function runHtml() {
    const run = view.run;
    if (!run) return '<div class="blank compact"><p>実行状況を読み込んでいます…</p></div>';
    const pct = run.progress.total ? Math.round(((run.progress.done + run.progress.failed) / run.progress.total) * 100) : 4;
    const interactions = run.interactions.filter((item) => ['open', 'answered'].includes(item.state)).map((item) => `<section class="execution-card flow-answer-card"><span class="status warn">回答待ち</span><h3>${e(item.prompt)}</h3>${answerHtml(item)}</section>`).join('');
    const nodes = run.nodes.map((node) => `<li class="flow-run-node ${e(node.state)}"><span class="status ${statusClass(node.state)}">${e(stateLabel(node.state))}</span><div><strong>${e(node.id)}</strong><p>${e(node.goal)}</p>${node.who ? `<small>${e(node.who)}${node.agent?.cli ? ` · ${e(node.agent.cli)}${node.agent.model ? ` / ${e(node.agent.model)}` : ''}` : ''}</small>` : ''}${node.output ? `<details><summary>成果を見る</summary><pre>${e(node.output)}</pre></details>` : ''}</div></li>`).join('');
    const delivery = run.delivery && ['published', 'published-manually'].includes(run.delivery.state)
      ? `<section class="execution-card"><div class="execution-card-head"><div><h3>成果ブランチ</h3><p>${e(run.delivery.branch)}</p></div>${view.context?.capabilities?.openDelivery ? '<button type="button" class="primary" data-flow-open-delivery>作業フォルダで開く</button>' : ''}</div></section>` : '';
    const result = view.result ? `<section class="execution-card"><div class="execution-card-head"><h3>最終成果</h3><button type="button" class="tiny" data-flow-clear-result>閉じる</button></div><pre class="flow-result-text">${e(JSON.stringify(view.result, null, 2))}</pre></section>` : '';
    const log = view.log ? `<section class="execution-card"><div class="execution-card-head"><h3>起動ログ</h3><button type="button" class="tiny" data-flow-clear-log>閉じる</button></div><pre class="log flow-log">${e(view.log.tail || 'ログはありません。')}</pre></section>` : '';
    const trialCheck = view.trialTeaching && view.trialTeaching.runId === run.runId && run.terminal
      ? `<section class="execution-card"><h3>この結果は期待どおりですか？</h3><p>実際の成果を確認して、候補を利用可能にするか判断してください。</p><div class="row">${run.state === 'done' ? '<button type="button" class="primary" data-flow-teaching-trial-result="passed">期待どおり</button>' : ''}<button type="button" class="danger" data-flow-teaching-trial-result="failed">修正が必要</button></div></section>` : '';
    return `<header class="execution-title"><div><span class="eyebrow">実行状況</span><h2>${e(run.title)}</h2><p>${e(ctx.dateLabel(run.createdAt))} · ${run.readonly ? '読み取り専用' : '成果を書き込み'}</p></div><button type="button" class="ghost" data-flow-back-run>ワークフローへ戻る</button></header>${run.failure ? `<p class="run-result ng">${e(run.failure.message)}</p>` : ''}<section class="execution-card"><div class="execution-card-head"><div><h3>${e(stateLabel(run.state))}</h3><p>${run.progress.total ? `${run.progress.done} 完了${run.progress.failed ? ` · ${run.progress.failed} 失敗` : ''} / ${run.progress.total} 工程` : '工程を準備しています'}</p></div><span class="status ${statusClass(run.state)}">${e(stateLabel(run.state))}</span></div><div class="flow-progress"><span style="width:${pct}%"></span></div><p class="flow-request">${e(run.request)}</p><div class="row">${!run.terminal ? '<button type="button" class="danger" data-flow-cancel>停止</button>' : ''}<button type="button" data-flow-result>成果を取得</button>${['failed', 'launch-failed', 'stalled'].includes(run.state) ? '<button type="button" data-flow-log>ログを見る</button>' : ''}<button type="button" data-flow-rerun>同じ内容で再実行</button>${run.terminal ? '<button type="button" class="danger ghost" data-flow-delete-run>履歴を削除</button>' : ''}</div></section>${trialCheck}${interactions}<section class="execution-card"><div class="execution-card-head"><div><h3>工程の進み具合</h3><p>工程ごとの担当と成果</p></div></div><ol class="flow-run-nodes">${nodes || '<li class="muted">計画を作成しています。</li>'}</ol></section>${delivery}${result}${log}`;
  }

  let announced = false;
  function announceTeaching(detail) {
    announced = true;
    if (ctx.teachView) ctx.teachView(detail);
  }

  function html() {
    announced = false;
    const body = homeHtml();
    if (!announced) announceTeaching(null);
    return body;
  }

  function homeHtml() {
    if (view.loading) return `<div class="blank compact"><p>${e(featureName)}を読み込んでいます…</p></div>`;
    return `<div class="flow-home-head"><div><h2>${e(featureName)}</h2><p>目的を教え、実際の結果で確かめてから利用可能にします。</p></div><button type="button" class="primary" data-flow-new>新しいワークフローを教える</button></div><div class="execution-layout flow-layout">${sidebarHtml()}<section class="execution-detail">${view.editor ? editorHtml() : view.selectedRun ? runHtml() : view.creatingTeaching || view.teaching ? teachingHtml() : workflowHtml()}</section></div>`;
  }

  function newNode(draft) {
    const used = new Set(draft.nodes.map((node) => node.id));
    let n = draft.nodes.length + 1;
    while (used.has(`task_${n}`)) n += 1;
    return { id: `task_${n}`, label: `工程 ${n}`, kind: 'work', goal: '{{request}}', deps: draft.nodes.length ? [draft.nodes[draft.nodes.length - 1].id] : [], tier: 'auto' };
  }

  function startEditor(pattern, duplicate = false) {
    let workflow;
    let mode = 'create';
    if (pattern) {
      workflow = {
        version: 2, id: `flow-${Date.now().toString(36)}`, name: pattern.template.name || pattern.label,
        description: pattern.description || '', purpose: 'implementation', entry: [], exit: [],
        nodes: (pattern.template.nodes || []).map((node, index) => ({ id: String(node.id || `task_${index + 1}`), label: String(node.label || node.id || `工程 ${index + 1}`), kind: String(node.kind || 'work'), goal: String(node.goal || '{{request}}'), deps: Array.isArray(node.deps) ? node.deps.map(String) : [], tier: 'auto', ...(node.interaction ? { interaction: node.interaction } : {}) })),
        rework: Array.isArray(pattern.template.rework) ? pattern.template.rework.map((policy) => ({ ...policy })) : [],
      };
    } else if (view.workflow) {
      workflow = JSON.parse(JSON.stringify(view.workflow));
      if (duplicate) { workflow.id = `${workflow.id}-copy`.slice(0, 80); workflow.name = `${workflow.name} のコピー`; }
      else mode = 'update';
    } else {
      workflow = { version: 2, id: `flow-${Date.now().toString(36)}`, name: '', description: '', purpose: 'implementation', entry: [], exit: [], nodes: [{ id: 'task_1', label: '最初の工程', kind: 'work', goal: '{{request}}', deps: [], tier: 'auto' }] };
    }
    view.editor = { mode, workflow, issues: [], dirty: false };
    view.selectedRun = '';
    view.run = null;
    previewEditor(false);
    ctx.refresh();
  }

  async function previewEditor(repaint = true) {
    if (!view.editor) return;
    const editor = view.editor;
    const result = await ctx.guard('内容の確認', () => ctx.bridge.preview(root(), editor.workflow));
    if (!result || editor !== view.editor) return;
    editor.issues = result.issues || [];
    const target = ctx.query ? ctx.query('#flow-editor-issues') : document.getElementById('flow-editor-issues');
    if (target) target.innerHTML = issueHtml(editor.issues);
    if (repaint && active()) ctx.refresh();
  }

  function queuePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => previewEditor(false), 300);
  }

  async function saveEditor() {
    if (!view.editor) return;
    const saved = await ctx.guard('保存', () => ctx.bridge.save(root(), view.editor.workflow, view.editor.mode));
    if (!saved) return;
    view.editor.issues = saved.issues || [];
    if (!saved.saved) { ctx.refresh(); return; }
    view.editor = null;
    view.selected = saved.workflow.id;
    view.flows = (await ctx.guard('ワークフロー一覧', () => ctx.bridge.list(root()))) || view.flows;
    await readWorkflow(view.selected, false);
    ctx.toast(`${featureName}を保存しました`);
    ctx.changed('workflows', view.selected);
    ctx.refresh();
  }

  async function selectFlow(id) {
    view.selected = id;
    view.selectedTeaching = '';
    view.teaching = null;
    view.creatingTeaching = false;
    view.selectedRun = '';
    view.run = null;
    view.result = null;
    view.log = null;
    view.editor = null;
    view.detailTab = 'overview';
    ctx.refresh();
    await readWorkflow(id);
  }

  async function selectRun(id) {
    view.selectedRun = id;
    view.run = null;
    view.result = null;
    view.log = null;
    view.editor = null;
    ctx.refresh();
    await loadRun(id);
  }

  async function startRun() {
    if (!view.workflow || view.starting) return;
    view.starting = true;
    ctx.refresh();
    const started = await ctx.guard('実行開始', () => ctx.bridge.runStart({
      root: root(), source: { type: 'workflow', id: view.workflow.id }, request: view.request,
      parameters: view.parameters, readonly: view.readonly, autoApprove: true, agent: view.agent, model: view.model,
    }));
    view.starting = false;
    if (!started) { ctx.refresh(); return; }
    view.selectedRun = started.runId;
    view.run = null;
    await loadRuns(false);
    await loadRun(started.runId, false);
    ctx.toast(`${featureName}を開始しました`);
    ctx.changed('workflows', view.selected);
    ctx.refresh();
  }

  async function respond(button) {
    const interactionId = button.dataset.flowAnswer;
    const interaction = view.run?.interactions.find((item) => item.interactionId === interactionId);
    if (!interaction) return;
    const value = ctx.query ? ctx.query(`[data-flow-answer-value="${CSS.escape(interactionId)}"]`) : document.querySelector(`[data-flow-answer-value="${CSS.escape(interactionId)}"]`);
    const comment = ctx.query ? ctx.query(`[data-flow-answer-comment="${CSS.escape(interactionId)}"]`) : document.querySelector(`[data-flow-answer-comment="${CSS.escape(interactionId)}"]`);
    const answer = interaction.mode === 'approval' ? { decision: button.dataset.decision, comment: comment?.value || '' }
      : interaction.mode === 'choice' ? { option: value?.value || '' }
        : { text: value?.value || '' };
    const sent = await ctx.guard('回答', () => ctx.bridge.runRespond(root(), view.run.runId, interactionId, answer));
    if (sent) { await loadRun(view.run.runId, false); ctx.toast('回答を送信しました'); ctx.refresh(); }
  }

  // 下書きの定義（AI が書いた .agents/workflows/<id>.json）。まだ無ければ null
  async function readTeachingWorkflow(workflowId) {
    try { view.teachingWorkflow = await ctx.bridge.read(root(), workflowId); } catch { view.teachingWorkflow = null; }
    return view.teachingWorkflow;
  }

  async function selectTeaching(workflowId) {
    const session = await ctx.guard('教示中のワークフロー', () => ctx.bridge.teachingRead(root(), workflowId));
    if (!session) return;
    await readTeachingWorkflow(workflowId);
    view.selectedTeaching = workflowId;
    view.teaching = session;
    view.selected = '';
    view.workflow = null;
    view.selectedRun = '';
    view.run = null;
    view.editor = null;
    view.creatingTeaching = false;
    view.teachingInput = '';
    ctx.refresh();
  }

  // 新しく教える: 会話（tmux）を用意し、その最初のターンで AI に目的を渡す。
  async function createTeaching() {
    const purpose = view.teachingInput.trim();
    if (!purpose) { ctx.toast('実現したいことを入力してください', true); return; }
    const workflowId = await ctx.guard('ワークフローの作成', () => ctx.teachCreate({ root: root(), purpose }));
    if (!workflowId) return;
    view.teachings = (await ctx.guard('教示中のワークフロー', () => ctx.bridge.teachingList(root()))) || view.teachings;
    view.teachingInput = '';
    view.creatingTeaching = false;
    await selectTeaching(workflowId);
  }

  // 試運転の前に、AI が書いた定義をこの下書きの候補として取り込む（記録はそこに残る）。
  async function adoptDraft() {
    const session = await ctx.guard('候補の取り込み', () => ctx.bridge.teachAdopt(root(), view.teaching.workflowId));
    if (session) view.teaching = session;
    return session;
  }

  async function startTeachingTrial() {
    const draft = view.teachingWorkflow && view.teachingWorkflow.workflow;
    if (!view.teaching || !draft) return;
    const session = (await adoptDraft()) || view.teaching;
    const generation = session.generations.find((item) => item.id === session.activeGenerationId);
    if (!generation) return;
    const started = await ctx.guard('試運転', () => ctx.bridge.runStart({
      root: root(), source: { type: 'draft', workflow: draft },
      request: view.request || session.understanding.purpose, parameters: {}, readonly: view.readonly,
      agent: view.agent, model: view.model,
    }));
    if (!started) return;
    view.trialTeaching = { workflowId: session.workflowId, generationId: generation.id, runId: started.runId };
    view.selectedRun = started.runId;
    view.run = null;
    await loadRuns(false);
    await loadRun(started.runId, false);
    ctx.refresh();
  }

  async function recordTeachingTrial(outcome) {
    const trial = view.trialTeaching;
    if (!trial) return;
    const session = await ctx.guard('試運転結果', () => ctx.bridge.teachingRecordTrial(root(), trial.workflowId, {
      id: `trial-${Date.now().toString(36)}`, generationId: trial.generationId, runId: trial.runId, outcome,
      assessment: { confirmedByUser: true },
    }));
    if (!session) return;
    view.teaching = session;
    view.selectedTeaching = trial.workflowId;
    view.selectedRun = '';
    view.run = null;
    view.trialTeaching = null;
    ctx.toast(outcome === 'passed' ? '試運転の成功を記録しました' : '修正が必要として記録しました');
    ctx.refresh();
  }

  async function confirmTeaching() {
    const session = view.teaching;
    const generation = session && session.generations.find((item) => item.id === session.activeGenerationId);
    if (!generation) return;
    const saved = await ctx.guard('利用可能化', () => ctx.bridge.teachingConfirm(root(), session.workflowId, generation.id, generation.digest));
    if (!saved) return;
    view.teaching = saved;
    view.flows = (await ctx.guard('ワークフロー一覧', () => ctx.bridge.list(root()))) || view.flows;
    view.teachings = (await ctx.guard('教示中のワークフロー', () => ctx.bridge.teachingList(root()))) || view.teachings;
    view.selectedTeaching = '';
    view.selected = saved.workflowId;
    await readWorkflow(saved.workflowId, false);
    ctx.toast('ワークフローを利用可能にしました');
    ctx.changed('workflows', saved.workflowId);
    ctx.refresh();
  }

  function bind(main) {
    for (const button of main.querySelectorAll('[data-flow-select]')) button.addEventListener('click', () => selectFlow(button.dataset.flowSelect));
    for (const button of main.querySelectorAll('[data-flow-teaching-select]')) button.addEventListener('click', () => selectTeaching(button.dataset.flowTeachingSelect));
    for (const button of main.querySelectorAll('[data-flow-run]')) button.addEventListener('click', () => selectRun(button.dataset.flowRun));
    for (const button of main.querySelectorAll('[data-flow-tab]')) button.addEventListener('click', () => { view.detailTab = button.dataset.flowTab; ctx.refresh(); });
    for (const button of main.querySelectorAll('[data-flow-new]')) button.addEventListener('click', create);
    for (const button of main.querySelectorAll('[data-flow-manual-new]')) button.addEventListener('click', () => { view.workflow = null; view.creatingTeaching = false; startEditor(null); });
    main.querySelector('[data-flow-teaching-cancel]')?.addEventListener('click', () => { view.creatingTeaching = false; view.teachingInput = ''; ctx.refresh(); });
    main.querySelector('[data-flow-teaching-purpose]')?.addEventListener('input', (event) => { view.teachingInput = event.target.value; });
    main.querySelector('[data-flow-teaching-create]')?.addEventListener('click', createTeaching);
    main.querySelector('[data-flow-teaching-trial-request]')?.addEventListener('input', (event) => { view.request = event.target.value; });
    main.querySelector('[data-flow-teaching-trial]')?.addEventListener('click', startTeachingTrial);
    main.querySelector('[data-flow-teaching-confirm]')?.addEventListener('click', confirmTeaching);
    for (const button of main.querySelectorAll('[data-flow-teaching-trial-result]')) button.addEventListener('click', () => recordTeachingTrial(button.dataset.flowTeachingTrialResult));
    for (const select of main.querySelectorAll('[data-flow-pattern]')) select.addEventListener('change', () => {
      const pattern = view.catalog.patterns.find((item) => item.id === select.value);
      if (pattern) startEditor(pattern);
    });
    main.querySelector('[data-flow-edit]')?.addEventListener('click', () => startEditor(null));
    main.querySelector('[data-flow-change-consult]')?.addEventListener('click', async () => {
      let session = await ctx.bridge.teachingRead(root(), view.workflow.id);
      session = await ctx.bridge.teachingSave(root(), view.workflow.id, {
        ...session, workflowId: view.workflow.id, title: view.workflow.name,
        messages: session.messages.length ? session.messages : [{ role: 'user', text: `既存ワークフローを変更したい: ${view.workflow.description || view.workflow.name}` }],
        evidence: { ...session.evidence, references: [{ type: 'existing-workflow', workflow: view.workflow }] },
        understanding: { ...session.understanding, purpose: view.workflow.description || view.workflow.name },
      });
      view.teachings = (await ctx.bridge.teachingList(root())) || view.teachings;
      // 会話はここでは始めない。置き場の「編集開始」を押したときに、この対象で tmux を開く
      await selectTeaching(session.workflowId);
    });
    main.querySelector('[data-flow-duplicate]')?.addEventListener('click', () => startEditor(null, true));
    main.querySelector('[data-flow-close-editor]')?.addEventListener('click', async () => {
      if (view.editor.dirty && !window.confirm('保存していない変更があります。編集を閉じますか？')) return;
      view.editor = null;
      if (view.selected) await readWorkflow(view.selected, false);
      ctx.refresh();
    });
    main.querySelector('[data-flow-save]')?.addEventListener('click', saveEditor);
    main.querySelector('[data-flow-preview]')?.addEventListener('click', () => previewEditor());
    main.querySelector('[data-flow-add-node]')?.addEventListener('click', () => { view.editor.workflow.nodes.push(newNode(view.editor.workflow)); view.editor.dirty = true; queuePreview(); ctx.refresh(); });
    main.querySelector('[data-flow-add-rework]')?.addEventListener('click', () => {
      const draft = view.editor.workflow;
      const source = [...draft.nodes].reverse().find((node) => ['human', 'verify'].includes(node.kind) && node.deps.length);
      if (!source) { ctx.toast('前工程を持つ「人の確認」または「検証」工程を先に追加してください', true); return; }
      draft.rework ||= [];
      draft.rework.push({
        id: `rework_${draft.rework.length + 1}`, from: source.id, to: source.deps[0],
        trigger: source.kind === 'human' ? 'human-rejected' : 'verification-failed',
        instruction: '指摘を反映して再作業する', maxIterations: 1, onExhausted: 'human',
      });
      view.editor.dirty = true;
      queuePreview();
      ctx.refresh();
    });
    for (const input of main.querySelectorAll('[data-flow-rework]')) input.addEventListener(input.tagName === 'TEXTAREA' || input.tagName === 'INPUT' ? 'input' : 'change', () => {
      const policy = view.editor.workflow.rework[Number(input.dataset.flowRework)];
      policy[input.dataset.key] = input.dataset.key === 'maxIterations' ? Number(input.value) : input.value;
      if (input.dataset.key === 'from') {
        const source = view.editor.workflow.nodes.find((node) => node.id === input.value);
        policy.trigger = source && source.kind === 'human' ? 'human-rejected' : 'verification-failed';
      }
      view.editor.dirty = true;
      queuePreview();
    });
    for (const button of main.querySelectorAll('[data-flow-remove-rework]')) button.addEventListener('click', () => {
      view.editor.workflow.rework.splice(Number(button.dataset.flowRemoveRework), 1);
      view.editor.dirty = true;
      queuePreview();
      ctx.refresh();
    });
    for (const input of main.querySelectorAll('[data-flow-meta]')) input.addEventListener('input', () => { view.editor.workflow[input.dataset.flowMeta] = input.value; view.editor.dirty = true; queuePreview(); });
    for (const input of main.querySelectorAll('[data-flow-node]')) input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', () => {
      const node = view.editor.workflow.nodes[Number(input.dataset.flowNode)];
      node[input.dataset.key] = input.value;
      view.editor.dirty = true;
      if (input.dataset.key === 'kind') {
        if (input.value === 'human') node.interaction = { mode: 'approval', prompt: '', timeout_seconds: 604800, audience: ['reviewer'] };
        else delete node.interaction;
        ctx.refresh();
      }
      queuePreview();
    });
    for (const input of main.querySelectorAll('[data-flow-dep]')) input.addEventListener('change', () => {
      const node = view.editor.workflow.nodes[Number(input.dataset.flowDep)];
      node.deps = [...main.querySelectorAll(`[data-flow-dep="${input.dataset.flowDep}"]:checked`)].map((item) => item.value);
      view.editor.dirty = true;
      queuePreview();
    });
    for (const input of main.querySelectorAll('[data-flow-interaction]')) input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', () => {
      const node = view.editor.workflow.nodes[Number(input.dataset.flowInteraction)];
      node.interaction ||= { mode: 'approval', prompt: '' };
      node.interaction[input.dataset.key] = input.dataset.key === 'options' ? input.value.split('\n').map((line) => line.trim()).filter(Boolean) : input.value;
      view.editor.dirty = true;
      if (input.dataset.key === 'mode') ctx.refresh();
      queuePreview();
    });
    for (const button of main.querySelectorAll('[data-flow-remove-node]')) button.addEventListener('click', () => {
      const index = Number(button.dataset.flowRemoveNode);
      const removed = view.editor.workflow.nodes[index];
      view.editor.workflow.nodes.splice(index, 1);
      view.editor.workflow.nodes.forEach((node) => { node.deps = node.deps.filter((id) => id !== removed.id); });
      view.editor.workflow.rework = (view.editor.workflow.rework || []).filter((policy) => policy.from !== removed.id && policy.to !== removed.id);
      view.editor.dirty = true;
      queuePreview();
      ctx.refresh();
    });
    main.querySelector('[data-flow-delete]')?.addEventListener('click', async () => {
      if (!window.confirm(`「${view.workflow.name}」を削除しますか？実行履歴は残ります。`)) return;
      const deleted = await ctx.guard('削除', () => ctx.bridge.remove(root(), view.workflow.id));
      if (deleted) { view.selected = ''; await loadRoot(); ctx.toast(`${featureName}を削除しました`); ctx.changed('workflows', ''); }
    });
    main.querySelector('[data-flow-delete-unreadable]')?.addEventListener('click', async (event) => {
      const id = event.currentTarget.dataset.flowDeleteUnreadable;
      if (!window.confirm(`読み取れないワークフロー「${id}」を削除しますか？`)) return;
      const deleted = await ctx.guard('削除', () => ctx.bridge.remove(root(), id));
      if (deleted) { view.selected = ''; await loadRoot(); ctx.toast(`${featureName}を削除しました`); ctx.changed('workflows', ''); }
    });
    main.querySelector('[data-flow-request]')?.addEventListener('input', (event) => { view.request = event.target.value; });
    for (const input of main.querySelectorAll('[data-flow-param]')) input.addEventListener('input', () => { view.parameters[input.dataset.flowParam] = input.value; });
    main.querySelector('[data-flow-agent]')?.addEventListener('change', (event) => { view.agent = event.target.value; });
    main.querySelector('[data-flow-model]')?.addEventListener('input', (event) => { view.model = event.target.value; });
    main.querySelector('[data-flow-readonly]')?.addEventListener('change', (event) => { view.readonly = event.target.checked; });
    main.querySelector('[data-flow-start]')?.addEventListener('click', startRun);
    main.querySelector('[data-flow-back-run]')?.addEventListener('click', () => { view.selectedRun = ''; view.run = null; view.result = null; view.log = null; ctx.refresh(); });
    main.querySelector('[data-flow-cancel]')?.addEventListener('click', async () => {
      if (!window.confirm('この実行を停止しますか？')) return;
      const stopped = await ctx.guard('停止', () => ctx.bridge.runCancel(root(), view.run.runId, '画面から停止'));
      if (stopped) { await loadRun(view.run.runId, false); await loadRuns(false); ctx.refresh(); }
    });
    main.querySelector('[data-flow-result]')?.addEventListener('click', async () => { view.result = await ctx.guard('成果', () => ctx.bridge.runResult(root(), view.run.runId)); ctx.refresh(); });
    main.querySelector('[data-flow-log]')?.addEventListener('click', async () => { view.log = await ctx.guard('ログ', () => ctx.bridge.runLog(root(), view.run.runId)); ctx.refresh(); });
    main.querySelector('[data-flow-clear-result]')?.addEventListener('click', () => { view.result = null; ctx.refresh(); });
    main.querySelector('[data-flow-clear-log]')?.addEventListener('click', () => { view.log = null; ctx.refresh(); });
    main.querySelector('[data-flow-rerun]')?.addEventListener('click', () => {
      const input = view.run.input;
      const target = view.flows.find((item) => item.id === input.workflowId);
      if (!target) { ctx.toast('元のワークフローが見つかりません', true); return; }
      view.request = input.request;
      view.parameters = { ...input.parameters };
      view.readonly = input.readonly;
      view.agent = input.agent || view.agent;
      view.model = input.model || view.model;
      selectFlow(target.id);
    });
    main.querySelector('[data-flow-delete-run]')?.addEventListener('click', async () => {
      if (!window.confirm('この実行履歴を削除しますか？')) return;
      const deleted = await ctx.guard('履歴の削除', () => ctx.bridge.runDelete(root(), view.run.runId));
      if (deleted) { view.selectedRun = ''; view.run = null; await loadRuns(false); ctx.refresh(); }
    });
    main.querySelector('[data-flow-open-delivery]')?.addEventListener('click', async () => {
      const opened = await ctx.guard('成果を開く', () => ctx.bridge.openDelivery(root(), view.run.runId));
      if (opened) ctx.toast(`作業フォルダ「${opened.name}」を作成しました`);
    });
    for (const button of main.querySelectorAll('[data-flow-answer]')) button.addEventListener('click', () => respond(button));
    schedulePolling();
  }

  async function select(id) {
    await activate();
    if (!id) return;
    if (view.flows.some((item) => item.id === id)) await selectFlow(id);
    else if (view.teachings.some((item) => item.workflowId === id)) await selectTeaching(id);
  }

  function create() {
    view.workflow = null;
    view.creatingTeaching = true;
    view.teaching = null;
    view.selectedTeaching = '';
    view.selectedRun = '';
    view.editor = null;
    view.teachingInput = '';
    ctx.refresh();
  }

  // 教える会話は tmux（会話基盤）で進むので、AI の一問一答（automation:ai:*）は使わない。
  function onAiProgress() { return false; }
  function onAiResult() { return false; }

  // 会話が終わるたびに、AI が書いた定義と下書きの状態を読み直す（光の DOM 側が呼ぶ）。
  async function reloadTeaching() {
    if (!view.teaching) return;
    const [session] = await Promise.all([
      ctx.bridge.teachingRead(root(), view.teaching.workflowId).catch(() => null),
      readTeachingWorkflow(view.teaching.workflowId),
    ]);
    if (session) view.teaching = session;
    view.teachings = (await ctx.bridge.teachingList(root()).catch(() => null)) || view.teachings;
    if (active()) ctx.refresh();
  }

  return { activate, rootChanged, html, bind, select, create, onAiProgress, onAiResult, reloadTeaching };
};
