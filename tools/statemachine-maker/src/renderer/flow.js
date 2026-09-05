'use strict';

// 複数 AI ワークフローの画面。maker と agent-app の双方で同じ描画を使えるよう、
// IPC 呼び出しや既存画面の render/toast は生成時に受け取る。
window.createFlowFeature = function createFlowFeature(ctx) {
  const view = {
    root: '', loading: false, catalog: { kinds: [], patterns: [] }, context: null,
    flows: [], selected: '', workflow: null, issues: [], editor: null,
    runs: [], selectedRun: '', run: null, result: null, log: null,
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
    const focused = document.activeElement;
    return !focused || !focused.matches('[data-flow-request], [data-flow-param], [data-flow-model], [data-flow-answer-value], [data-flow-answer-comment]');
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
    const [catalog, flows, flowContext, runs] = await Promise.all([
      view.catalog.kinds.length ? Promise.resolve(view.catalog) : ctx.guard('ワークフローの準備', () => ctx.bridge.catalog()),
      ctx.guard('ワークフロー一覧', () => ctx.bridge.list(view.root)),
      ctx.guard('実行環境', () => ctx.bridge.context(view.root)),
      ctx.guard('実行履歴', () => ctx.bridge.runList(view.root, 30)),
    ]);
    if (root() !== view.root) return;
    if (catalog) view.catalog = catalog;
    view.flows = flows || [];
    view.context = flowContext;
    view.runs = runs || [];
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

  function sidebarHtml() {
    const flows = view.flows.map((item) => `<button type="button" class="execution-item ${!view.selectedRun && item.id === view.selected ? 'is-on' : ''}" data-flow-select="${e(item.id)}"><strong>${e(item.name)}</strong><span>${item.nodes} 工程${item.humanNodes ? ` · 人の確認 ${item.humanNodes}` : ''}${item.valid === false ? ' · 要修正' : ''}</span></button>`).join('');
    const runs = view.runs.map((item) => `<button type="button" class="execution-item ${item.runId === view.selectedRun ? 'is-on' : ''}" data-flow-run="${e(item.runId)}"><strong>${e(item.title || item.runId)}</strong><span>${e(stateLabel(item.state))} · ${item.progress.done + item.progress.failed}/${item.progress.total || '—'}</span></button>`).join('');
    return `<aside class="execution-list flow-side"><div class="flow-side-head"><span>保存済み</span><button type="button" class="tiny" data-flow-new title="新しく作る">＋</button></div>${flows || '<p class="muted small">まだありません。</p>'}<div class="flow-side-head flow-run-head"><span>実行履歴</span></div>${runs || '<p class="muted small">まだありません。</p>'}</aside>`;
  }

  function emptyHtml() {
    const unreadable = view.selected && view.flows.find((item) => item.id === view.selected && item.valid === false);
    if (unreadable) return `<div class="blank compact"><h2>内容を読み取れません</h2><p>${e(unreadable.file)} を直すか、この一覧から削除してください。</p><div class="row"><button type="button" class="danger" data-flow-delete-unreadable="${e(unreadable.id)}">一覧から削除</button></div></div>`;
    const patterns = view.catalog.patterns || [];
    return `<div class="blank compact"><h2>${e(featureName)}を作ります</h2><p>複数のAIに工程を分けて任せ、途中の確認や成果まで追跡できます。</p><div class="row"><button type="button" class="primary" data-flow-new>手動で作成</button>${patterns.length ? `<select data-flow-pattern aria-label="ひな形"><option value="">ひな形から作成…</option>${patterns.map((item) => `<option value="${e(item.id)}">${e(item.label)}</option>`).join('')}</select>` : ''}</div></div>`;
  }

  function issueHtml(issues = view.issues) {
    if (!issues.length) return '<p class="run-result ok">保存できる内容です。</p>';
    return `<ul class="flow-issues">${issues.map((item) => `<li class="${item.level === 'error' ? 'err' : 'warn'}">${e(item.message)}</li>`).join('')}</ul>`;
  }

  function editorHtml() {
    const draft = view.editor.workflow;
    const nodes = draft.nodes.map((node, index) => {
      const deps = draft.nodes.filter((candidate) => candidate.id && candidate !== node).map((candidate) => `<label><input type="checkbox" data-flow-dep="${index}" value="${e(candidate.id)}" ${node.deps.includes(candidate.id) ? 'checked' : ''}>${e(candidate.label || candidate.id)}</label>`).join('');
      const interaction = node.kind === 'human' ? (node.interaction || { mode: 'approval', prompt: '', options: [] }) : null;
      return `<article class="flow-node-card"><div class="flow-node-number">${index + 1}</div><div class="flow-node-form"><div class="grid2"><div class="field"><label>工程名</label><input data-flow-node="${index}" data-key="label" value="${e(node.label || '')}" placeholder="例: 仕様を整理"></div><div class="field"><label>工程の種類</label><select data-flow-node="${index}" data-key="kind">${view.catalog.kinds.map((item) => `<option value="${e(item.kind)}" ${item.kind === node.kind ? 'selected' : ''}>${e(item.label)}</option>`).join('')}</select></div></div><div class="field"><label>この工程で行うこと</label><textarea rows="3" data-flow-node="${index}" data-key="goal" placeholder="依頼全体は {{request}} と書けます">${e(node.goal || '')}</textarea></div><details class="flow-node-more" ${interaction ? 'open' : ''}><summary>つながりと詳細</summary><div class="details-body"><div class="field"><label>この前に終える工程</label><div class="flow-deps">${deps || '<small>先頭の工程です。</small>'}</div></div><div class="field"><label>工程の保存名</label><input class="mono" data-flow-node="${index}" data-key="id" value="${e(node.id || '')}"></div>${interaction ? interactionHtml(interaction, index) : ''}</div></details><div class="row"><button type="button" class="danger tiny" data-flow-remove-node="${index}" ${draft.nodes.length === 1 ? 'disabled' : ''}>工程を削除</button></div></div></article>`;
    }).join('');
    return `<section class="flow-editor"><header class="execution-title"><div><span class="eyebrow">${e(featureName)}</span><h2>${view.editor.mode === 'create' ? '新しく作る' : '内容を編集'}</h2></div><button type="button" class="ghost" data-flow-close-editor>閉じる</button></header><section class="execution-card"><div class="grid2"><div class="field"><label>名前</label><input data-flow-meta="name" value="${e(draft.name || '')}" placeholder="例: 変更案を並列レビュー"></div><div class="field"><label>保存名</label><input class="mono" data-flow-meta="id" value="${e(draft.id || '')}" ${view.editor.mode === 'update' ? 'disabled' : ''}></div></div><div class="field"><label>説明</label><textarea rows="2" data-flow-meta="description">${e(draft.description || '')}</textarea></div></section><div class="flow-node-list">${nodes}</div><button type="button" class="flow-add-node" data-flow-add-node>＋ 工程を追加</button><div id="flow-editor-issues">${issueHtml(view.editor.issues || [])}</div><div class="flow-sticky-actions"><button type="button" class="primary" data-flow-save>保存</button><button type="button" data-flow-preview>内容を確認</button></div></section>`;
  }

  function interactionHtml(interaction, index) {
    const options = Array.isArray(interaction.options) ? interaction.options.join('\n') : '';
    return `<div class="flow-human"><div class="field"><label>確認方法</label><select data-flow-interaction="${index}" data-key="mode"><option value="approval" ${interaction.mode === 'approval' ? 'selected' : ''}>承認・却下</option><option value="choice" ${interaction.mode === 'choice' ? 'selected' : ''}>選択肢</option><option value="input" ${interaction.mode === 'input' ? 'selected' : ''}>自由入力</option></select></div><div class="field"><label>質問</label><textarea rows="2" data-flow-interaction="${index}" data-key="prompt">${e(interaction.prompt || '')}</textarea></div>${interaction.mode === 'choice' ? `<div class="field"><label>選択肢（1行に1つ）</label><textarea rows="3" data-flow-interaction="${index}" data-key="options">${e(options)}</textarea></div>` : ''}</div>`;
  }

  function workflowHtml() {
    const workflow = view.workflow;
    if (!workflow) return emptyHtml();
    const summary = view.flows.find((item) => item.id === workflow.id) || { parameterKeys: [] };
    const nodes = workflow.nodes.map((node, index) => `<li><span class="flow-node-state">${index + 1}</span><div><strong>${e(node.label || node.id)}</strong><small>${e(kind(node.kind).label)}${node.deps.length ? ` · 前: ${e(node.deps.join('、'))}` : ''}</small><p>${e(node.goal)}</p></div></li>`).join('');
    const contextWarning = !view.context?.tools?.agentFlow?.ok
      ? `<p class="run-result warn">${e(view.context?.tools?.agentFlow?.summary || '実行環境を確認してください')}</p>` : '';
    const params = (summary.parameterKeys || []).map((key) => `<div class="field"><label>${e(key)}</label><input data-flow-param="${e(key)}" value="${e(view.parameters[key] || '')}"></div>`).join('');
    const agents = (view.context?.agents || ctx.agents()).map((name) => `<option value="${e(name)}" ${name === view.agent ? 'selected' : ''}>${e(name)}</option>`).join('');
    const canRun = !!view.context?.tools?.agentFlow?.ok && !!agents && !view.starting
      && !view.issues.some((item) => item.level === 'error');
    return `<header class="execution-title"><div><span class="eyebrow">${e(featureName)}</span><h2>${e(workflow.name)}</h2>${workflow.description ? `<p>${e(workflow.description)}</p>` : ''}</div><div class="row"><button type="button" class="ghost" data-flow-duplicate>複製</button><button type="button" class="ghost" data-flow-edit>編集</button><button type="button" class="danger ghost" data-flow-delete>削除</button></div></header>${view.issues.length ? `<div>${issueHtml()}</div>` : ''}<section class="execution-card flow-overview"><div class="execution-card-head"><div><h3>工程</h3><p>${workflow.nodes.length} 工程${workflow.nodes.some((node) => node.kind === 'human') ? ' · 途中で人の確認があります' : ''}</p></div></div><ol class="flow-node-summary">${nodes}</ol></section><section class="execution-card"><div class="execution-card-head"><div><h3>このワークフローを実行</h3><p>依頼をもとに、工程ごとにAIが作業します。</p></div></div>${contextWarning}<div class="field"><label>依頼内容</label><textarea rows="4" data-flow-request placeholder="何を完了してほしいか入力してください">${e(view.request)}</textarea></div>${params ? `<div class="run-inputs"><h3>実行時の入力</h3><div class="run-input-grid">${params}</div></div>` : ''}<div class="grid2"><div class="field"><label>使うAI</label><select data-flow-agent ${agents ? '' : 'disabled'}>${agents || '<option>利用できるAIがありません</option>'}</select></div><div class="field"><label>モデル（任意）</label><input data-flow-model value="${e(view.model)}"></div></div><label class="check-label"><input type="checkbox" data-flow-readonly ${view.readonly ? 'checked' : ''} ${view.context && !view.context.workspace.ok ? 'disabled' : ''}>読み取り専用で実行する</label>${view.context && !view.context.workspace.ok ? `<small class="muted">${e(view.context.workspace.reason)}。読み取り専用で実行できます。</small>` : ''}<div class="row"><button type="button" class="primary" data-flow-start ${canRun ? '' : 'disabled'}>${view.starting ? '開始中…' : '実行する'}</button></div></section>`;
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
    return `<header class="execution-title"><div><span class="eyebrow">実行状況</span><h2>${e(run.title)}</h2><p>${e(ctx.dateLabel(run.createdAt))} · ${run.readonly ? '読み取り専用' : '成果を書き込み'}</p></div><button type="button" class="ghost" data-flow-back-run>ワークフローへ戻る</button></header>${run.failure ? `<p class="run-result ng">${e(run.failure.message)}</p>` : ''}<section class="execution-card"><div class="execution-card-head"><div><h3>${e(stateLabel(run.state))}</h3><p>${run.progress.total ? `${run.progress.done} 完了${run.progress.failed ? ` · ${run.progress.failed} 失敗` : ''} / ${run.progress.total} 工程` : '工程を準備しています'}</p></div><span class="status ${statusClass(run.state)}">${e(stateLabel(run.state))}</span></div><div class="flow-progress"><span style="width:${pct}%"></span></div><p class="flow-request">${e(run.request)}</p><div class="row">${!run.terminal ? '<button type="button" class="danger" data-flow-cancel>停止</button>' : ''}<button type="button" data-flow-result>成果を取得</button>${['failed', 'launch-failed', 'stalled'].includes(run.state) ? '<button type="button" data-flow-log>ログを見る</button>' : ''}<button type="button" data-flow-rerun>同じ内容で再実行</button>${run.terminal ? '<button type="button" class="danger ghost" data-flow-delete-run>履歴を削除</button>' : ''}</div></section>${interactions}<section class="execution-card"><div class="execution-card-head"><div><h3>工程の進み具合</h3><p>工程ごとの担当と成果</p></div></div><ol class="flow-run-nodes">${nodes || '<li class="muted">計画を作成しています。</li>'}</ol></section>${delivery}${result}${log}`;
  }

  function html() {
    if (view.loading) return `<div class="blank compact"><p>${e(featureName)}を読み込んでいます…</p></div>`;
    return `<div class="flow-home-head"><div><h2>${e(featureName)}</h2><p>複数のAIに工程を分け、実行の進み具合と確認事項を追跡します。</p></div>${view.catalog.patterns.length ? `<select data-flow-pattern aria-label="ひな形"><option value="">ひな形から作成…</option>${view.catalog.patterns.map((item) => `<option value="${e(item.id)}">${e(item.label)}</option>`).join('')}</select>` : ''}</div><div class="execution-layout flow-layout">${sidebarHtml()}<section class="execution-detail">${view.editor ? editorHtml() : view.selectedRun ? runHtml() : workflowHtml()}</section></div>`;
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
    const target = document.getElementById('flow-editor-issues');
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
    view.selectedRun = '';
    view.run = null;
    view.result = null;
    view.log = null;
    view.editor = null;
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
      parameters: view.parameters, readonly: view.readonly, agent: view.agent, model: view.model,
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
    const value = document.querySelector(`[data-flow-answer-value="${CSS.escape(interactionId)}"]`);
    const comment = document.querySelector(`[data-flow-answer-comment="${CSS.escape(interactionId)}"]`);
    const answer = interaction.mode === 'approval' ? { decision: button.dataset.decision, comment: comment?.value || '' }
      : interaction.mode === 'choice' ? { option: value?.value || '' }
        : { text: value?.value || '' };
    const sent = await ctx.guard('回答', () => ctx.bridge.runRespond(root(), view.run.runId, interactionId, answer));
    if (sent) { await loadRun(view.run.runId, false); ctx.toast('回答を送信しました'); ctx.refresh(); }
  }

  function bind(main) {
    for (const button of main.querySelectorAll('[data-flow-select]')) button.addEventListener('click', () => selectFlow(button.dataset.flowSelect));
    for (const button of main.querySelectorAll('[data-flow-run]')) button.addEventListener('click', () => selectRun(button.dataset.flowRun));
    for (const button of main.querySelectorAll('[data-flow-new]')) button.addEventListener('click', () => { view.workflow = null; startEditor(null); });
    for (const select of main.querySelectorAll('[data-flow-pattern]')) select.addEventListener('change', () => {
      const pattern = view.catalog.patterns.find((item) => item.id === select.value);
      if (pattern) startEditor(pattern);
    });
    main.querySelector('[data-flow-edit]')?.addEventListener('click', () => startEditor(null));
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
    if (!id || !view.flows.some((item) => item.id === id)) return;
    await selectFlow(id);
  }

  function create() {
    view.workflow = null;
    startEditor(null, false);
  }

  return { activate, rootChanged, html, bind, select, create };
};
