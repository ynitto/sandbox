'use strict';

(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('workflows', {
      refresh: feature.refresh,
      refreshNeeds: feature.refreshNeeds,
      available: () => true,
      badge: feature.badge,
    });
    root.registerFeatureTab('workflow-run', {
      render: feature.render,
      available: () => true,
    });
    root.registerFeatureTab('workflow-needs', { render: feature.render, available: () => true });
    root.registerFeatureTab('workflow-edit', { render: feature.render, available: () => true });
    root.registerFeatureTab('workflow-settings', {
      render: feature.render,
      available: () => true,
    });
  }
})(typeof globalThis !== 'undefined' ? globalThis : window, (root) => {
  const KINDS = ['work', 'generate', 'classify', 'synthesize', 'verify', 'filter', 'judge', 'reduce', 'split', 'map',
    'human', 'extract', 'retrieve'];
  const START = '__start__';
  const END = '__end__';
  const KIND_META = {
    work: ['作業', '依頼に対して成果を作る'],
    generate: ['生成', '異なる候補を作る'],
    classify: ['分類', '入力を分類して進め方を決める'],
    synthesize: ['統合', '複数の成果を一つにまとめる'],
    verify: ['検証', '成果が条件を満たすか確かめる'],
    filter: ['選別', '候補を基準で絞り込む'],
    judge: ['判定', '候補を比較して選ぶ'],
    reduce: ['集約', '分割結果を構造化してまとめる'],
    split: ['分割', '入力を実行時に複数工程へ分ける'],
    map: ['個別処理', '分割された各項目を処理する'],
    human: ['人の確認', '人の判断や入力を待って再開する'],
    extract: ['抽出', '入力から根拠付きの項目を取り出す'],
    retrieve: ['取得', '資料を読み、根拠付きの情報源を返す'],
  };
  const DEFAULT_GOALS = {
    work: '依頼を満たすため、この工程で担当する作業を完了する。',
    generate: '依頼を満たす成果候補を1つ作る。',
    classify: '依頼を分類し、後続の進め方を決める。',
    synthesize: '前の工程の成果を統合し、依頼への成果を1つにまとめる。',
    verify: '前の工程の成果が依頼と完了条件を満たすか検証する。',
    filter: '前の工程の候補を依頼の条件で選別する。',
    judge: '前の工程の候補を比較し、依頼に最も合うものを選ぶ。',
    reduce: '前の工程の構造化データを集約する。',
    split: '依頼を独立して処理できる単位に分割する。',
    map: '分割された各項目に、依頼された処理を適用する。',
    human: '人の判断や入力を受け取り、後続工程へ渡す。',
    extract: '入力から必要な項目を根拠とともに抽出する。',
    retrieve: '指定された範囲を調べ、参照できる根拠とともに情報を取得する。',
  };
  const ROLE_META = { worker: '作業', verify: '検証', human: '人間', planner: '計画', evaluator: '判定', session: 'セッション' };
  const ROLE_KIND = { worker: 'work', verify: 'verify' };
  const TIER_LABELS = { basic: '単純作業', small: '軽量', medium: '標準', large: '高性能', auto: '自動', '自動': '自動' };
  const RUN_POLICY_LABELS = { recommended: 'おすすめ', saving: '節約', quality: '品質優先', cost: 'コスト優先', custom: 'カスタム' };
  const ICONS = {
    close: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"></path></svg>',
    plus: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></svg>',
    dock: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h16v14H4zM15 5v14"></path></svg>',
  };
  // 実行できる設計書の必須節と、よく使われる言い換え。見出しでも「目的:」でも拾う。
  // 判定は決定的（LLM を使わない）で、足りなくても実行は止めない——外部の CLI / IDE が
  // 書いた設計書は書式が多様で、ここで弾くと取り込みの摩擦が支援の価値を上回る。
  const REQUEST_SECTIONS = [
    ['目的', ['目的', '狙い', 'ゴール']],
    ['変更対象', ['変更対象', '対象', 'スコープ', '範囲']],
    ['受入基準', ['受入基準', '受け入れ基準', '完了条件']],
    ['検証方法', ['検証方法', '検証', 'テスト方法', '確認方法']],
  ];
  const REQUEST_TEMPLATE = ['## 目的', '', '', '## 変更対象', '', '', '## 受入基準', '', '- [ ] ',
    '', '## 検証方法', '', ''].join('\n');
  const DESIGN_SOURCE_MODES = [
    ['new', '一から設計する', '対話しながら要件と設計を詰めます。'],
    ['continue', '続きから設計する', '設計途中の Markdown を読み、対話を続けます。'],
    ['use-as-is', '設計書をそのまま使う', '完成済みの Markdown を実行可能な形へ変換します。'],
  ];
  const st = {
    overview: null,
    design: { sessions: [], current: null, busy: '', notice: '', timer: null, open: false },
    selectedRun: '',
    runDetail: null,
    editor: null,
    selectedNode: '',
    selectedEdge: null,
    connectFrom: '',
    pickerFrom: '',
    zoom: 1,
    inspectorPosition: null,
    dirty: false,
    busy: '',
    notice: '',
    runView: 'overview',
    selectedNeed: '',
  };
  const esc = (s) => root.esc(String(s == null ? '' : s));
  const $id = (id) => document.getElementById(id);
  const api = () => root.api;
  const tierLabel = (value, fallback = '') => TIER_LABELS[String(value || '')] || fallback || String(value || '');

  // 機能（kind）とオプション（continuation）から、このノードが選べる実行レベル。
  // カタログ（overview.kindTiers）が無い・未知の kind は制限しない（加算的契約）。
  // 標準語彙の外の独自レベル名は適格性の対象外としてそのまま通す。
  function allowedTierIdsFor(node, ov) {
    const kindTiers = ov && ov.kindTiers;
    const spec = kindTiers && kindTiers.kinds && kindTiers.kinds[String((node && node.kind) || 'work')];
    if (!spec || !Array.isArray(spec.tiers)) return null;
    const order = Array.isArray(kindTiers.order) ? kindTiers.order : [];
    const minOption = node && node.continuation && spec.options
      ? spec.options[String(node.continuation)] : '';
    const tiers = minOption
      ? spec.tiers.filter((tier) => order.indexOf(tier) >= order.indexOf(minOption))
      : spec.tiers;
    return { tiers, order };
  }

  function tierEligible(node, ov, tierId) {
    const allowed = allowedTierIdsFor(node, ov);
    if (!allowed || tierId === 'auto') return true;
    if (!allowed.order.includes(String(tierId))) return true; // 独自レベル名は制限しない
    return allowed.tiers.includes(String(tierId));
  }

  // 依頼テキストの節見出し。「## 目的」「**目的**:」「目的:」のいずれも同じ節として数える。
  function sectionTitles(text) {
    const out = [];
    for (const line of String(text || '').split(/\r?\n/)) {
      const heading = line.match(/^ {0,3}#{1,6}\s+(.*?)\s*$/);
      const labelled = line.match(/^ {0,3}(?:[-*+]\s+)?\*{0,2}([^\s*][^:：*]{0,30})\*{0,2}\s*[:：]/);
      const title = heading ? heading[1] : labelled ? labelled[1] : '';
      if (title) out.push(title.replace(/[#*\s]/g, ''));
    }
    return out;
  }

  function readinessCheck(text) {
    const titles = sectionTitles(text);
    return {
      empty: !String(text || '').trim(),
      missing: REQUEST_SECTIONS
        .filter(([, words]) => !titles.some((title) => words.some((word) => title.includes(word))))
        .map(([label]) => label),
    };
  }

  function readinessHtml(text) {
    const { empty, missing } = readinessCheck(text);
    if (empty) return '<span class="muted">やりたいことを書くか、設計書を読み込みます。</span>';
    if (!missing.length) return '<span class="wf-readiness-ok">実行できる設計書の形になっています。</span>';
    return `<span class="wf-readiness-warn">足りない節: ${esc(missing.join('・'))}</span>`
      + '<span class="muted">このままでも実行できます。工程の解釈は agent-flow に委ねられます。</span>';
  }

  function runActive() {
    const pane = $id('tab-workflow-run');
    return pane && pane.classList.contains('active');
  }

  function statusLabel(status) {
    return ({ done: '終了', failed: '失敗', cancelled: '中止', running: '実行中', planning: '計画中' })[status]
      || String(status || '—');
  }

  function emptyWorkflow() {
    return { version: 2, id: '', name: '', description: '', entry: [], exit: [], nodes: [] };
  }

  function defaultGoal(kind) {
    return DEFAULT_GOALS[String(kind || 'work')] || DEFAULT_GOALS.work;
  }

  function roleForKind(kind) {
    return ({ human: 'human', verify: 'verify' })[String(kind || 'work')] || 'worker';
  }

  function roleLabelForKind(kind) {
    return ROLE_META[roleForKind(kind)] || ROLE_META.worker;
  }

  function kindLabelForKind(kind) {
    return (KIND_META[String(kind || 'work')] || KIND_META.work)[0];
  }

  function nodePresentation(node) {
    const role = roleLabelForKind(node && node.kind);
    const name = String((node && (node.label || node.id)) || role);
    return { role, name };
  }

  function methodRoles(method) {
    return [...new Set((method && Array.isArray(method.fragments) ? method.fragments : [])
      .map((fragment) => String((fragment && fragment.role) || '')).filter(Boolean))];
  }

  function nodeMethodChoices(methods, node) {
    const role = roleForKind(node && node.kind);
    const purpose = String((node && node.kind) || 'work');
    const includes = (values, value) => !Array.isArray(values) || !values.length
      || values.map(String).includes(String(value));
    return (methods || []).flatMap((method) => {
      const roles = methodRoles(method).filter((item) => item !== 'session');
      const sharedGraphOption = String(method.id || '') === 'failure-modes-first';
      if (sharedGraphOption ? !roles.includes(role) : roles.length !== 1 || roles[0] !== role) return [];
      const when = method.when || {};
      const purposes = Array.isArray(when.purposes) ? when.purposes.map(String) : [];
      if (!includes(when.engines, 'agent-flow') || !includes(when.workloads, 'flow')
        || !includes(when.roles, role) || (purposes.length && !purposes.includes(purpose) && !purposes.includes(role))
        || !includes(when.tiers, node && node.tier)) return [];
      const text = (method.fragments || []).filter((fragment) => fragment && fragment.role === role
        && String(fragment.text || '').trim()).map((fragment) => String(fragment.text).trim()).join('\n');
      if (!text) return [];
      return [{
        id: String(method.id || ''), description: String(method.description || method.id || ''),
        role, text, source: String(method.source || method.origin || ''),
        condition: methodPresentation(method).condition,
      }];
    });
  }

  function methodWorkflowPattern(method) {
    const methodId = String((method && method.id) || '').trim();
    if (!methodId || ['no-self-approval', 'failure-modes-first'].includes(methodId)) return null;
    const fragments = (method && Array.isArray(method.fragments) ? method.fragments : [])
      .filter((fragment) => fragment && ['worker', 'verify'].includes(String(fragment.role || ''))
        && String(fragment.role || '').trim() && String(fragment.text || '').trim());
    if (new Set(fragments.map((fragment) => String(fragment.role))).size < 2) return null;
    const base = methodId.replace(/[^a-zA-Z0-9_-]+/g, '-') || 'method';
    const patternLabel = methodId === 'derive-twice' ? '複数案を並行して統合' : '';
    const methodFor = (fragment) => ({
      id: methodId,
      description: patternLabel || String(method.description || methodId),
      role: String(fragment.role),
      text: String(fragment.text).trim(),
      source: String(method.source || method.origin || ''),
    });
    const nodes = fragments.map((fragment, index) => {
      const id = `${base}-${index + 1}`;
      const role = String(fragment.role);
      return {
        id,
        label: ROLE_META[role] || role,
        goal: defaultGoal(ROLE_KIND[role] || 'work'),
        kind: ROLE_KIND[role] || 'work',
        deps: index ? [`${base}-${index}`] : [],
        method: methodFor(fragment),
      };
    });
    return {
      id: `method:${methodId}`,
      methodId,
      type: 'method',
      label: patternLabel || String(method.description || methodId),
      description: `${methodPresentation(method).roles.join(' → ')}の工程セット`,
      template: { nodes },
    };
  }

  function methodWorkflowPatterns(methods) {
    return (methods || []).map(methodWorkflowPattern).filter(Boolean);
  }

  function workflowFromPattern(pattern, tier) {
    const source = pattern && pattern.template && Array.isArray(pattern.template.nodes)
      ? pattern.template.nodes : [];
    const hidden = new Set(pattern && pattern.id === 'fan-out-and-synthesize'
      ? source.filter((node) => !(node.deps || []).length).slice(3).map((node) => String(node.id)) : []);
    const raw = source.filter((node) => !hidden.has(String(node.id))).map((node) => ({
      ...node, deps: (node.deps || []).map(String).filter((id) => !hidden.has(id)),
    }));
    const byId = new Map(raw.map((node) => [String(node.id), node]));
    const depths = new Map();
    const depthOf = (id) => {
      if (depths.has(id)) return depths.get(id);
      const node = byId.get(id);
      const depth = node && Array.isArray(node.deps) && node.deps.length
        ? 1 + Math.max(...node.deps.map(depthOf)) : 0;
      depths.set(id, depth);
      return depth;
    };
    const rows = new Map();
    const nodes = raw.map((node) => {
      const depth = depthOf(String(node.id));
      const row = rows.get(depth) || 0;
      const kind = String(node.kind || 'work');
      const goal = String(node.goal || '').trim();
      const continuation = pattern.id === 'classify-and-act' && kind === 'classify' ? 'route'
        : ['adversarial-verification', 'loop-until-done'].includes(pattern.id) && kind === 'verify' ? 'retry' : '';
      rows.set(depth, row + 1);
      return {
        id: String(node.id),
        label: String(node.label || (KIND_META[kind] || [])[0] || node.id),
        goal: !goal || goal.includes('{{request}}') ? defaultGoal(kind) : goal,
        kind,
        tier,
        deps: Array.isArray(node.deps) ? node.deps.map(String) : [],
        x: 300 + depth * 270,
        y: 70 + row * 140,
        ...(node.method ? { method: clone(node.method) } : {}),
        ...(continuation ? { continuation } : {}),
      };
    });
    const used = new Set(nodes.flatMap((node) => node.deps));
    return {
      ...emptyWorkflow(),
      name: `${String(pattern.label || pattern.id || '標準パターン')} のコピー`,
      description: String(pattern.description || ''),
      entry: nodes.filter((node) => !node.deps.length).map((node) => node.id),
      exit: nodes.filter((node) => !used.has(node.id)).map((node) => node.id),
      nodes,
    };
  }

  function insertPattern(workflow, pattern, tier, from, position) {
    const source = workflowFromPattern(pattern, tier);
    if (!source.nodes.length) return [];
    const used = new Set((workflow.nodes || []).map((node) => node.id));
    const ids = new Map();
    source.nodes.forEach((node) => {
      const base = String(node.id || 'node').replace(/[^a-zA-Z0-9_-]+/g, '-') || 'node';
      let id = base;
      let suffix = 2;
      while (used.has(id)) id = `${base}-${suffix++}`;
      used.add(id);
      ids.set(node.id, id);
    });
    const minX = Math.min(...source.nodes.map((node) => Number(node.x) || 0));
    const minY = Math.min(...source.nodes.map((node) => Number(node.y) || 0));
    const added = source.nodes.map((node) => ({
      ...node,
      id: ids.get(node.id),
      deps: (node.deps || []).map((id) => ids.get(id)),
      x: Number(position.x) + (Number(node.x) - minX),
      y: Number(position.y) + (Number(node.y) - minY),
    }));
    workflow.nodes.push(...added);
    const entries = new Set(source.entry.map((id) => ids.get(id)));
    if (from) added.filter((node) => entries.has(node.id)).forEach((node) => connectWorkflow(workflow, from, node.id));
    return added;
  }

  function patternColumns(pattern) {
    return workflowColumns(visualWorkflow(workflowFromPattern(pattern, '')));
  }

  function workflowColumns(workflow) {
    return [['開始'], ...workflowNodeColumns(workflow).map((nodes) =>
      nodes.map((node) => kindLabelForKind(node.kind))), ['終了']];
  }

  function workflowNodeColumns(workflow) {
    const columns = new Map();
    (workflow.nodes || []).forEach((node) => {
      const items = columns.get(node.x) || [];
      items.push(node);
      columns.set(node.x, items);
    });
    return [...columns].sort((a, b) => a[0] - b[0]).map((entry) => entry[1]);
  }

  function recommendedKinds(workflow, from) {
    if (from === START) return ['work', 'retrieve', 'extract', 'human'];
    const kind = (workflow.nodes || []).find((node) => node.id === from)?.kind;
    if (kind === 'generate') return ['filter', 'judge', 'synthesize'];
    if (kind === 'verify') return ['work', 'verify'];
    if (kind === 'split') return [];
    return ['work', 'verify', 'synthesize'];
  }

  function nextNodePosition(workflow, from) {
    const source = (workflow.nodes || []).find((node) => node.id === from);
    const pos = source ? { x: Number(source.x) + 280, y: Number(source.y) }
      : from === START ? { x: 300, y: 72 }
        : { x: 300 + ((workflow.nodes.length || 0) % 2) * 280,
          y: 190 + Math.floor((workflow.nodes.length || 0) / 2) * 150 };
    while ((workflow.nodes || []).some((node) => Math.abs(Number(node.x) - pos.x) < 230
      && Math.abs(Number(node.y) - pos.y) < 120)) pos.y += 140;
    return pos;
  }

  function methodPresentation(method) {
    const roles = methodRoles(method).map((role) => ROLE_META[role] || role);
    const when = method.when || {};
    const purposes = Array.isArray(when.purposes) ? when.purposes
      .map((purpose) => (KIND_META[String(purpose)] || [ROLE_META[String(purpose)] || purpose])[0]) : [];
    const target = [...new Set((purposes.length ? purposes : roles).filter(Boolean))];
    const conditions = [];
    if (Array.isArray(when.tiers) && when.tiers.length) conditions.push(`${when.tiers.map(tierLabel).join('・')}向け`);
    if (Number(when.max_relative_cost) === 0) conditions.push('ローカル実行向け');
    else if (Number(when.min_relative_cost) > 0) conditions.push('クラウド実行向け');
    return { roles, target, condition: conditions.join('・') };
  }

  function nodeMethodOptionsHtml(methods, node) {
    const choices = nodeMethodChoices(methods, node);
    const current = String((node.method && node.method.id) || '');
    const role = roleForKind(node.kind);
    return `<section class="wf-node-method-options"><div><strong>この工程の追加ルール</strong>
      <small>この工程への依頼文だけに短い指示を追加します。エージェントや実行レベルは変わりません。</small></div>
      <div><label class="wf-method-option"><input type="radio" name="wf-node-method" data-node-method=""
        ${current ? '' : 'checked'}><span><strong>標準</strong><small>追加の指示なし</small></span></label>
      ${choices.map((choice) => `<label class="wf-method-option"><input type="radio" name="wf-node-method"
        data-node-method="${esc(choice.id)}" ${current === choice.id ? 'checked' : ''}><span>
        <strong>${esc(choice.description)}</strong>${choice.condition ? `<small>${esc(choice.condition)}</small>` : ''}
        <p tabindex="0">${esc(choice.text)}</p></span></label>`).join('')}</div></section>`;
  }

  function connectionError(workflow, from, to) {
    const nodes = Array.isArray(workflow && workflow.nodes) ? workflow.nodes : [];
    const byId = new Map(nodes.map((node) => [node.id, node]));
    if (from === END) return '終了からは接続できません';
    if (to === START) return '開始へは接続できません';
    if (from === START) {
      if (!byId.has(to)) return '接続先が見つかりません';
      if ((byId.get(to).deps || []).length) return '開始は依存のないノードだけへ接続できます';
      return (workflow.entry || []).includes(to) ? '接続済みです' : '';
    }
    if (to === END) {
      if (!byId.has(from)) return '接続元が見つかりません';
      const hasNext = nodes.some((node) => (node.deps || []).includes(from));
      if (hasNext) return '終了へは末端ノードだけを接続できます';
      return (workflow.exit || []).includes(from) ? '接続済みです' : '';
    }
    const source = byId.get(from);
    const target = byId.get(to);
    if (!source || !target) return '接続するノードが見つかりません';
    if (from === to) return 'ノード自身には接続できません';
    if (source.kind === 'split') return 'split の後段は実行時に自動生成されます';
    if ((target.deps || []).includes(from)) return '接続済みです';
    const outgoing = new Map(nodes.map((node) => [node.id, []]));
    nodes.forEach((node) => (node.deps || []).forEach((dep) => outgoing.get(dep)?.push(node.id)));
    const stack = [to];
    const seen = new Set();
    while (stack.length) {
      const id = stack.pop();
      if (id === from) return '接続すると循環します';
      if (seen.has(id)) continue;
      seen.add(id);
      stack.push(...(outgoing.get(id) || []));
    }
    return '';
  }

  function connectWorkflow(workflow, from, to) {
    const error = connectionError(workflow, from, to);
    if (error) throw new Error(error);
    if (from === START) workflow.entry = [...new Set([...(workflow.entry || []), to])];
    else if (to === END) workflow.exit = [...new Set([...(workflow.exit || []), from])];
    else {
      const target = workflow.nodes.find((node) => node.id === to);
      target.deps = [...new Set([...(target.deps || []), from])];
      workflow.entry = (workflow.entry || []).filter((id) => id !== to);
      workflow.exit = (workflow.exit || []).filter((id) => id !== from);
    }
    return workflow;
  }

  function disconnectWorkflow(workflow, from, to) {
    if (from === START) workflow.entry = (workflow.entry || []).filter((id) => id !== to);
    else if (to === END) workflow.exit = (workflow.exit || []).filter((id) => id !== from);
    else {
      const target = (workflow.nodes || []).find((node) => node.id === to);
      if (target) target.deps = (target.deps || []).filter((id) => id !== from);
    }
    return workflow;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function spaceForStart(workflow) {
    const nodes = workflow.nodes || [];
    if (!nodes.length) return workflow;
    const shift = Math.max(0, 300 - Math.min(...nodes.map((node) => Number(node.x) || 0)));
    if (shift) nodes.forEach((node) => { node.x = (Number(node.x) || 0) + shift; });
    return workflow;
  }

  // includeRun=false は要対応バッジだけの定期更新。設計セッションは実行画面にしか出ないので、
  // その経路では取りに行かない（5 秒ごとに要らない IPC を 2 本増やさない）。
  async function loadOverview({ includeRun = true } = {}) {
    if (includeRun && api().designSessionList) await loadDesign();
    try {
      st.overview = await api().adhocFlowOverview({ limit: 30 });
      if (includeRun && st.selectedRun) {
        try { st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun }); }
        catch { st.runDetail = null; }
      }
    } catch (err) {
      st.notice = `読み込みに失敗しました: ${String((err && err.message) || err)}`;
    }
  }

  async function refresh() {
    const editing = ['tab-workflow-edit', 'tab-workflow-settings'].some((id) => {
      const pane = $id(id);
      return pane && pane.classList.contains('active');
    });
    if (editing) return;
    await loadOverview();
    render();
  }

  async function refreshNeeds() {
    await loadOverview({ includeRun: false });
    renderWorkflowNeedsBadge();
    const pane = $id('tab-workflow-needs');
    if (pane && pane.classList.contains('active')) renderNeeds();
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

  function selectedFlowSummaryHtml(ov, value) {
    const selection = selectionFrom(value);
    if (selection.type === 'auto') {
      return `<article class="wf-selected-flow-card" data-flow-summary-type="auto">
        <header class="wf-selected-flow-heading"><span class="wf-template-kind">自動</span><h3>依頼に合わせて計画</h3></header>
        <div class="wf-selected-flow-content"><p class="wf-selected-flow-description">agent-flow が依頼に合わせて工程を計画し、実行します。</p></div></article>`;
    }
    const pattern = selection.type === 'pattern'
      ? (ov.patterns || []).find((item) => String(item.id) === selection.id) : null;
    if (pattern) {
      return `<article class="wf-selected-flow-card" data-flow-summary-type="pattern">
        <header class="wf-selected-flow-heading"><span class="wf-template-kind">標準フロー</span><h3>${esc(pattern.label || pattern.id)}</h3></header>
        <div class="wf-selected-flow-content">${miniFlowHtml(pattern)}
          <p class="wf-selected-flow-description">${esc(pattern.description || '標準の工程構成で実行します。')}</p></div></article>`;
    }
    const workflow = selection.type === 'custom'
      ? (ov.workflows || []).find((item) => String(item.id) === selection.id) : null;
    if (!workflow) return '';
    const nodes = Array.isArray(workflow.nodes) ? workflow.nodes.length : 0;
    return `<article class="wf-selected-flow-card" data-flow-summary-type="custom">
      <header class="wf-selected-flow-heading"><span class="wf-template-kind">カスタムフロー</span><h3>${esc(workflow.name || workflow.id)}</h3></header>
      <div class="wf-selected-flow-content"><p class="wf-selected-flow-description">${esc(workflow.description || `${nodes}工程のワークフロー`)}</p>
        <span class="wf-card-meta">${nodes}工程</span></div></article>`;
  }

  function sidebarRunsHtml(ov) {
    const rows = ov.runs || [];
    if (!rows.length) return '<div class="empty">実行履歴はありません</div>';
    return `<div class="wf-sidebar-runs">${rows.map((r) => `<button type="button" class="nav-item wf-sidebar-run ${st.selectedRun === r.runId ? 'selected' : ''}"
      data-workflow-run-id="${esc(r.runId)}"><span>${esc(String(r.request || r.runId).slice(0, 42))}</span>
      <small>${esc(statusLabel(r.status))} · ${esc(r.updatedAt || r.createdAt || '')}</small></button>`).join('')}</div>`;
  }

  function renderSidebar() {
    const target = $id('workflow-run-list');
    if (!target) return;
    target.innerHTML = sidebarRunsHtml(st.overview || {});
    target.querySelectorAll('[data-workflow-run-id]').forEach((button) => button.addEventListener('click', async () => {
      st.selectedRun = button.dataset.workflowRunId;
      st.runView = 'overview';
      try { st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun }); }
      catch (err) { st.notice = String((err && err.message) || err); }
      $id('tab-btn-workflow-run')?.click();
      renderSidebar();
      renderRun();
    }));
    renderWorkflowNeedsBadge();
  }

  function renderWorkflowNeedsBadge() {
    const badgeNode = $id('workflow-needs-badge');
    if (badgeNode) {
      const pending = workflowNeeds(st.overview || {}).filter((item) => !item.resolution && !item.expired && !item.responded).length;
      badgeNode.textContent = String(pending);
      badgeNode.classList.toggle('hidden', !pending);
      badgeNode.classList.toggle('warn', pending > 0);
    }
  }

  function interactionCardsHtml(interactions) {
    return (interactions || []).map((item) => {
      const disabled = item.resolution || item.expired || item.responded;
      const state = item.resolution ? '回答済み' : item.expired ? '期限切れ' : item.responded ? '回答送信済み' : '回答待ち';
      const comment = item.mode === 'input' ? ''
        : '<label>コメント（任意）<textarea data-interaction-comment rows="2"></textarea></label>';
      let control;
      if (item.mode === 'approval') {
        control = `<div class="qf-row"><button type="button" class="primary" data-interaction-submit="approved" ${disabled ? 'disabled' : ''}>承認</button>
          <button type="button" data-interaction-submit="rejected" ${disabled ? 'disabled' : ''}>却下</button></div>`;
      } else if (item.mode === 'choice') {
        control = `<label>回答<select data-interaction-option ${disabled ? 'disabled' : ''}>${(item.options || []).map((option) =>
    `<option value="${esc(option)}">${esc(option)}</option>`).join('')}</select></label>
          <button type="button" class="primary" data-interaction-submit="choice" ${disabled ? 'disabled' : ''}>回答する</button>`;
      } else {
        control = `<label>回答<textarea data-interaction-text rows="3" ${disabled ? 'disabled' : ''}></textarea></label>
          <button type="button" class="primary" data-interaction-submit="input" ${disabled ? 'disabled' : ''}>回答する</button>`;
      }
      return `<article class="wf-interaction-card" data-interaction-id="${esc(item.interaction_id)}" data-run-id="${esc(item.runId || st.selectedRun)}">
        <div><strong>人の確認</strong><span class="status-chip st-review">${state}</span></div>
        <p>${esc(item.prompt)}</p><small>対象: ${esc((item.audience || []).join(', '))} · 期限: ${esc(item.expires_at || '')}</small>
        ${comment}${control}</article>`;
    }).join('');
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
    const overrides = inbox.execution_overrides || {};
    const overrideRows = [...Object.entries(overrides.roles || {}).map(([key, value]) => ['役割', key, value]),
      ...Object.entries(overrides.kinds || {}).map(([key, value]) => ['機能', key, value])];
    const policyLabel = RUN_POLICY_LABELS[overrides.mode];
    const policyHtml = policyLabel ? `<p>実行方針: <strong>${esc(policyLabel)}</strong></p>` : '';
    const overrideHtml = overrideRows.length ? `${policyHtml}<dl class="wf-override-summary">${overrideRows.map(([group, key, value]) =>
      `<div><dt>${esc(group)} ${esc(key)}</dt><dd>${esc(tierLabel(value.tier, '自動'))} · ${esc(value.agent_cli || '自動')}${value.model ? ` / ${esc(value.model)}` : ''}</dd></div>`).join('')}</dl>`
      : policyHtml || '<p class="muted">エージェント選択は自動です。</p>';
    const events = (detail.events || []).map((event) => `<li><time>${esc(event.ts || '')}</time>
      <strong>${esc(event.kind || event.event || '更新')}</strong>${event.node ? ` · ${esc(event.node)}` : ''}${event.status ? ` · ${esc(event.status)}` : ''}</li>`).join('');
    const view = st.runView === 'process'
      ? `<div class="qf-graph">${graph}</div>${outputs || '<p class="muted">工程出力はまだありません。</p>'}`
      : st.runView === 'history'
        ? `<ol class="wf-event-list">${events || '<li class="muted">履歴はまだありません。</li>'}</ol>`
        : `${run.failureReason ? `<p class="qf-failure">${esc(run.failureReason)}</p>` : ''}
          ${run.final && run.final.summary ? `<pre class="qf-output">${esc(String(run.final.summary).slice(0, 3000))}</pre>` : ''}
          <h3>実行時のエージェント指定</h3>${overrideHtml}`;
    return `<section class="wf-result">
      <div class="wf-section-head"><div><strong>${esc(statusLabel(run.status))}</strong>
        <span class="muted">${esc(flowName)} · af/${esc(run.runId || st.selectedRun)}</span></div>
        <div class="qf-row"><button type="button" id="wf-resubmit">再実行</button>
          <button type="button" id="wf-cancel">中止</button><button type="button" id="wf-delete-run">削除</button></div></div>
      <nav class="flow-view-tabs" aria-label="実行詳細">
        ${[['overview', '概要'], ['process', '工程'], ['history', '履歴']].map(([key, label]) =>
          `<button type="button" data-run-view="${key}" class="${st.runView === key ? 'active' : ''}" aria-pressed="${st.runView === key}">${label}</button>`).join('')}
      </nav>${view}</section>`;
  }

  function overrideRowsHtml(ov, group, labels) {
    const catalog = (ov.kindTiers && ov.kindTiers[group]) || {};
    const agents = ov.agents || [];
    return Object.keys(labels).map((key) => {
      const allowed = catalog[key] && catalog[key].tiers ? catalog[key].tiers : (ov.tiers || []).map((tier) => tier.id).filter((id) => id !== 'auto');
      return `<div class="wf-override-row" data-override-group="${group}" data-override-key="${esc(key)}">
        <strong>${esc(labels[key])}</strong>
        <label>実行レベル<select data-override-tier><option value="">自動</option>${allowed.map((id) => `<option value="${esc(id)}">${esc(tierLabel(id))}</option>`).join('')}</select></label>
        <label>エージェント<select data-override-agent><option value="">自動</option>${agents.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('')}</select></label>
        <label>モデル<input type="text" data-override-model placeholder="自動"></label></div>`;
    }).join('');
  }

  function overridesHtml(ov) {
    const kindLabels = Object.fromEntries(KINDS.filter((kind) => kind !== 'human').map((kind) => [kind, kindLabelForKind(kind)]));
    const roleLabels = { planner: '計画', evaluator: '継続判定', worker: '作業全般', verify: '検証全般' };
    return `<section class="wf-run-policy" aria-label="今回だけの実行方針">
      <fieldset class="orch-policy-modes wf-run-policy-modes"><legend>今回だけ実行方針を選択</legend>
        <label class="orch-policy-card"><input type="radio" name="wf-run-policy-mode" value="recommended" checked><span><strong>おすすめ</strong><small>agent-control / agent-flow が自動で決めます。</small></span></label>
        <label class="orch-policy-card"><input type="radio" name="wf-run-policy-mode" value="saving"><span><strong>節約</strong><small>軽量を基本に利用量を抑えます。</small></span></label>
        <label class="orch-policy-card"><input type="radio" name="wf-run-policy-mode" value="quality"><span><strong>品質優先</strong><small>高性能を使って品質を優先します。</small></span></label>
        <label class="orch-policy-card"><input type="radio" name="wf-run-policy-mode" value="cost"><span><strong>コスト優先</strong><small>許可される最低レベルを使い、可能な機能は単純作業にします。</small></span></label>
        <label class="orch-policy-card"><input type="radio" name="wf-run-policy-mode" value="custom"><span><strong>カスタム</strong><small>役割・機能ごとに指定します。</small></span></label>
      </fieldset>
      <div class="wf-run-policy-custom" id="wf-run-policy-custom" hidden>
        <p class="muted">未指定は自動です。機能指定は役割指定より優先します。</p>
        <h3>役割ごと</h3>${overrideRowsHtml(ov, 'roles', roleLabels)}
        <h3>機能ごと</h3>${overrideRowsHtml(ov, 'kinds', kindLabels)}
      </div></section>`;
  }

  function executionOverridesForMode(mode, ov) {
    if (mode === 'recommended') return null;
    const catalog = (ov && ov.kindTiers) || {};
    const tierFor = (tiers) => mode === 'cost' ? tiers[0]
      : mode === 'quality' ? (tiers.includes('large') ? 'large' : tiers[tiers.length - 1])
        : ['small', 'medium', 'large'].find((tier) => tiers.includes(tier)) || tiers[tiers.length - 1];
    const select = (group) => Object.fromEntries(Object.entries(catalog[group] || {}).flatMap(([key, spec]) =>
      Array.isArray(spec.tiers) && spec.tiers.length ? [[key, { tier: tierFor(spec.tiers) }]] : []));
    if (['cost', 'saving', 'quality'].includes(mode)) {
      return { version: 1, mode, roles: select('roles'), kinds: select('kinds') };
    }
    return null;
  }

  // --- 設計セッション（短い要望 → 実行できる設計書） ---------------------------
  // 1 ラウンド = 1 本の設計 run。回答を待つ間 run は残らないので、答えを翌日に返しても
  // 何も期限切れにならない。毎ラウンド完全な設計書が返るため、どこで止めても実行できる。

  function designSessionListHtml() {
    const rows = st.design.sessions || [];
    if (!rows.length) return '';
    return `<div class="wf-design-sessions">${rows.map((item) => `<button type="button"
      class="nav-item ${st.design.current && st.design.current.id === item.id ? 'selected' : ''}"
      data-design-open="${esc(item.id)}"><span>${esc(String(item.goal || item.id).slice(0, 48))}</span>
      <small>${esc(designStatusText(item))}</small></button>`).join('')}</div>`;
  }

  function designStatusText(session) {
    if (!session) return '';
    if (session.runStatus === 'running') return '設計中…';
    if (session.error) return session.error;
    const count = session.questionCount == null ? (session.questions || []).length : session.questionCount;
    return count ? `質問 ${count} 件` : '設計書ができています';
  }

  function designStartFormHtml() {
    return `<div class="wf-design-start">
      <fieldset class="wf-design-modes"><legend>設計の始め方</legend>${DESIGN_SOURCE_MODES.map(([id, label, help], index) =>
        `<label class="orch-policy-card"><input type="radio" name="wf-design-source-mode" value="${id}"${index === 0 ? ' checked' : ''}>
          <span><strong>${esc(label)}</strong><small>${esc(help)}</small></span></label>`).join('')}</fieldset>
      <label data-design-goal-field>やりたいこと<textarea id="wf-design-goal" rows="3"
        placeholder="例: ワークフローの依頼欄に設計書を読み込めるようにする"></textarea></label>
      <label data-design-file-field hidden>Markdown ファイル<input id="wf-design-file" type="file"
        accept=".md,.markdown,text/markdown"><small>元ファイルは変更しません。</small></label>
      <div class="qf-row"><button type="button" class="primary-inline" id="wf-design-start"
        ${st.design.busy ? 'disabled' : ''}>${esc(st.design.busy || '設計を開始')}</button></div></div>`;
  }

  function designQuestionsHtml(session) {
    const questions = session.questions || [];
    if (!questions.length) return '';
    return `<div class="wf-design-questions"><h4>質問</h4>${questions.map((question, index) =>
      `<label class="wf-design-question"><span>${index + 1}. ${esc(question)}</span>
        <textarea data-design-answer="${index}" rows="2"></textarea></label>`).join('')}</div>`;
  }

  function designSessionHtml(session) {
    const running = session.runStatus === 'running';
    const document_ = String(session.document || '');
    const questions = session.questions || [];
    const rounds = (session.rounds || []).length;
    return `<div class="wf-design-current" data-design-id="${esc(session.id)}">
      <div class="wf-section-head"><div><strong>${esc(String(session.goal || '').slice(0, 60))}</strong>
        <span class="muted">ラウンド ${rounds} · ${esc(designStatusText(session))}</span></div>
        <div class="qf-row"><button type="button" data-design-new>別の設計を始める</button>
          <button type="button" data-design-delete>破棄</button></div></div>
      ${session.error ? `<p class="qf-failure">${esc(session.error)}</p>` : ''}
      ${running ? '<p class="muted">設計 run の完了を待っています。閉じても進みます。</p>' : ''}
      ${document_ ? `<details class="wf-design-doc"${questions.length ? '' : ' open'}>
        <summary>設計書（${document_.length} 文字）</summary>
        <pre class="qf-output">${esc(document_.slice(0, 12000))}</pre></details>` : ''}
      ${running ? '' : designQuestionsHtml(session)}
      ${running ? '' : `<div class="qf-row wf-design-actions">
        <button type="button" data-design-next ${st.design.busy ? 'disabled' : ''}>${
          questions.length ? '回答して次のラウンド' : 'もう一周して詰める'}</button>
        <button type="button" class="primary-inline" data-design-use ${document_ ? '' : 'disabled'}>この設計で実行</button>
      </div>`}</div>`;
  }

  function designCardHtml() {
    const current = st.design.current;
    return `<details class="wf-design-card" id="wf-design"${st.design.open ? ' open' : ''}>
      <summary>設計を練る<small>短いやりたいことから、実行できる設計書まで詰めます。</small></summary>
      <div class="wf-design-body">
        ${st.design.notice ? `<p class="qf-notice" role="status">${esc(st.design.notice)}</p>` : ''}
        ${designSessionListHtml()}
        ${current ? designSessionHtml(current) : designStartFormHtml()}
      </div></details>`;
  }

  function runHtml(ov) {
    if (st.selectedRun && st.runDetail) {
      return `<section class="wf-page wf-run-detail-page" aria-label="ワークフロー実行詳細">
        <div class="wf-title"><div><h2>実行中のワークフロー</h2><p>工程、成果、履歴を確認します。</p></div>
          <button type="button" class="primary-inline" id="wf-new-run">新しく実行</button></div>
        <p class="qf-notice" role="status"${st.notice ? '' : ' hidden'}>${esc(st.notice)}</p>
        ${runDetailHtml(st.runDetail)}</section>`;
    }
    const history = (ov.cwdHistory || []).map((cwd) => `<option value="${esc(cwd)}"></option>`).join('');
    return `<section class="wf-page" aria-label="ワークフロー実行">
      <div class="wf-title"><div><h2>ワークフロー</h2><p>Gitリポジトリでフローを実行します。</p></div></div>
      <p class="qf-notice" role="status" id="wf-notice"${st.notice ? '' : ' hidden'}>${esc(st.notice)}</p>
      <div id="wf-design-host">${designCardHtml()}</div>
      <div class="wf-run-card">
        <label class="wf-flow-field">フロー<select id="wf-flow">${flowOptions(ov)}</select></label>
        <label class="wf-cwd-field">フォルダ<input id="wf-cwd" type="text" list="wf-cwd-history" placeholder="/path/to/repository" autocomplete="off"></label>
        <datalist id="wf-cwd-history">${history}</datalist>
        <div class="wf-flow-summary" id="wf-flow-summary" aria-live="polite">${selectedFlowSummaryHtml(ov, 'auto')}</div>
        <div class="wf-request">
          <div class="wf-request-head"><span>依頼</span>
            <div class="wf-request-tools">
              <label class="wf-import-label">設計書を読み込む
                <input type="file" id="wf-import" accept=".md,.markdown,.txt,text/markdown,text/plain"></label>
              <button type="button" id="wf-template">4節の雛形</button>
              <button type="button" id="wf-request-expand">大きく表示</button>
            </div></div>
          <textarea id="wf-request" rows="4"
            placeholder="実行する内容。設計書の全文を貼り付け・ドラッグしても構いません"></textarea>
          <p class="wf-readiness" id="wf-readiness" aria-live="polite">${readinessHtml('')}</p>
        </div>
        ${overridesHtml(ov)}
        <div class="settings-save-actions wf-run-actions"><button type="button" class="primary-inline" id="wf-submit"
          ${st.busy ? 'disabled' : ''}>${esc(st.busy || '実行')}</button></div>
      </div>
    </section>`;
  }

  function boundaryPositions(nodes) {
    const maxX = nodes.length ? Math.max(...nodes.map((node) => Number(node.x) || 0)) : 260;
    return { start: { x: 24, y: 72 }, end: { x: Math.max(580, maxX + 300), y: 72 } };
  }

  function visualWorkflow(workflow) {
    const real = (workflow.nodes || []).filter((node) => !node.runtime);
    const nodes = real.map((node) => ({ ...node, deps: [...(node.deps || [])] }));
    const exit = new Set(workflow.exit || []);
    for (const node of real) {
      if (node.continuation === 'route') {
        const id = `${node.id}--runtime-route`;
        nodes.push({
          id, label: '専門作業', goal: '分類結果に応じた専門工程', kind: 'work', tier: '自動',
          deps: [node.id], x: Number(node.x) + 270, y: Number(node.y), runtime: true,
        });
        if (exit.delete(node.id)) exit.add(id);
      }
      if (node.kind === 'split') {
        const maps = [0, 1, 2].map((index) => ({
          id: `${node.id}--runtime-map-${index + 1}`, label: '個別処理', goal: '分割項目を個別処理',
          kind: 'map', tier: '自動', deps: [node.id], x: Number(node.x) + 270,
          y: Number(node.y) + index * 120, runtime: true,
        }));
        const reduce = {
          id: `${node.id}--runtime-reduce`, label: '集約', goal: '個別の成果を集約',
          kind: 'reduce', tier: '自動', deps: maps.map((item) => item.id),
          x: Number(node.x) + 540, y: Number(node.y) + 120, runtime: true,
        };
        nodes.push(...maps, reduce);
        if (exit.delete(node.id)) exit.add(reduce.id);
      }
    }
    return { ...workflow, nodes, exit: [...exit] };
  }

  function edgePath(source, target) {
    const x1 = Number(source.x) + 220;
    const y1 = Number(source.y) + 48;
    const x2 = Number(target.x);
    const y2 = Number(target.y) + 48;
    const bend = Math.max(40, Math.abs(x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  }

  function edgeMarkup(from, to, source, target, readonly) {
    const d = edgePath(source, target);
    const runtime = source.runtime || target.runtime;
    const selected = st.selectedEdge && st.selectedEdge.from === from && st.selectedEdge.to === to;
    return `${readonly || runtime ? '' : `<path class="wf-edge-hit" d="${d}" data-edge-from="${esc(from)}" data-edge-to="${esc(to)}"
      data-edge-path-from="${esc(from)}" data-edge-path-to="${esc(to)}"
      role="button" tabindex="0" aria-label="接続を選択 ${esc(from)} から ${esc(to)}"></path>`}
      <path class="wf-edge${runtime ? ' runtime' : ''}${selected && !readonly ? ' selected' : ''}" d="${d}" data-edge-path-from="${esc(from)}"
        data-edge-path-to="${esc(to)}" marker-end="url(#wf-arrow)"></path>`;
  }

  function edgesHtml(workflow, readonly = false) {
    const nodes = workflow.nodes || [];
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const pos = boundaryPositions(nodes);
    const start = { x: pos.start.x, y: pos.start.y };
    const end = { x: pos.end.x, y: pos.end.y };
    const edges = [];
    for (const target of nodes) {
      for (const sourceId of target.deps || []) {
        const source = byId.get(sourceId);
        if (source) edges.push(edgeMarkup(sourceId, target.id, source, target, readonly));
      }
    }
    for (const targetId of workflow.entry || []) {
      const target = byId.get(targetId);
      if (target) edges.push(edgeMarkup(START, targetId, start, target, readonly));
    }
    for (const sourceId of workflow.exit || []) {
      const source = byId.get(sourceId);
      if (source) edges.push(edgeMarkup(sourceId, END, source, end, readonly));
    }
    return edges.join('');
  }

  function retryEdgesHtml(workflow) {
    const byId = new Map((workflow.nodes || []).map((node) => [node.id, node]));
    return (workflow.nodes || []).filter((node) => node.continuation === 'retry').flatMap((node) =>
      (node.deps || []).map((id) => {
        const target = byId.get(id);
        return target ? edgeMarkup(node.id, id, { ...node, runtime: true }, target, true) : '';
      })).join('');
  }

  function updateEdgePaths(workflow, pane) {
    const nodes = visualWorkflow(workflow).nodes;
    const pos = boundaryPositions(nodes);
    const byId = new Map(nodes.map((node) => [node.id, node]));
    byId.set(START, pos.start);
    byId.set(END, pos.end);
    const end = pane.querySelector(`[data-node-id="${END}"]`);
    if (end) { end.style.left = `${pos.end.x}px`; end.style.top = `${pos.end.y}px`; }
    pane.querySelectorAll('[data-runtime-node-id]').forEach((card) => {
      const node = byId.get(card.dataset.runtimeNodeId);
      if (node) { card.style.left = `${node.x}px`; card.style.top = `${node.y}px`; }
    });
    pane.querySelectorAll('[data-edge-path-from]').forEach((path) => {
      const source = byId.get(path.dataset.edgePathFrom);
      const target = byId.get(path.dataset.edgePathTo);
      if (source && target) path.setAttribute('d', edgePath(source, target));
    });
  }

  function nodeIssue(workflow, node) {
    const rootNode = !(node.deps || []).length;
    const hasNext = (workflow.nodes || []).some((other) => (other.deps || []).includes(node.id));
    const leafNode = !hasNext;
    if (node.kind === 'split' && hasNext) return '分割は通常工程へ接続できません';
    if (rootNode && !(workflow.entry || []).includes(node.id)) return '開始に未接続';
    if (leafNode && !(workflow.exit || []).includes(node.id)) return '終了に未接続';
    return '';
  }

  function portState(workflow, target) {
    if (!st.connectFrom) return '';
    return connectionError(workflow, st.connectFrom, target) ? ' invalid' : ' valid';
  }

  function nodeHtml(node, workflow, readonly = false) {
    const selected = st.selectedNode === node.id ? ' selected' : '';
    const issue = nodeIssue(workflow, node);
    const { role, name } = nodePresentation(node);
    const method = node.method ? `<span class="wf-method">${esc(node.method.description || node.method.id)}</span>` : '';
    const continuation = ({ route: '分類後に専門工程を追加', retry: '未完了なら再作業・再検証' })[node.continuation] || '';
    const inputError = st.connectFrom ? connectionError(workflow, st.connectFrom, node.id) : '';
    if (readonly) return `<article class="wf-node" style="left:${Number(node.x)}px;top:${Number(node.y)}px">
      <div class="wf-node-drag"><span>${esc(role)}ロール</span><span class="wf-tier">${esc(tierLabel(node.tier))}</span></div>
      <div class="wf-node-body"><strong>${esc(name)}</strong><p>${esc(node.goal)}</p>${method}
        ${continuation ? `<span class="wf-continuation">${esc(continuation)}</span>` : ''}</div></article>`;
    return `<article class="wf-node${selected}${issue ? ' invalid' : ''}" data-node-id="${esc(node.id)}"
      style="left:${Number(node.x)}px;top:${Number(node.y)}px">
      <button type="button" class="wf-port in${portState(workflow, node.id)}" data-connect-in="${esc(node.id)}"
        title="${esc(inputError || 'ここへ接続')}" aria-label="${esc(name)}へ接続"></button>
      <div class="wf-node-drag" data-drag-node="${esc(node.id)}"><span>${esc(role)}ロール</span><span class="wf-tier">${esc(tierLabel(node.tier))}</span></div>
      <div class="wf-node-body"><strong>${esc(name)}</strong><p>${esc(node.goal)}</p>${method}
        ${continuation ? `<span class="wf-continuation">${esc(continuation)}</span>` : ''}
        ${issue ? `<span class="wf-node-issue">${esc(issue)}</span>` : ''}</div>
      <button type="button" class="wf-port out" draggable="true" data-connect-out="${esc(node.id)}"
        aria-label="${esc(name)}から接続"></button>
      ${node.kind === 'split' ? '' : `<button type="button" class="wf-add-next wf-icon-button" data-add-after="${esc(node.id)}" aria-label="次の工程を追加" title="次の工程を追加">${ICONS.plus}</button>`}
    </article>`;
  }

  function runtimeNodeHtml(node) {
    const { role, name } = nodePresentation(node);
    return `<article class="wf-node wf-runtime-node" data-runtime-node-id="${esc(node.id)}"
      aria-label="実行時に追加される${esc(role)}ロール、${esc(name)}"
      style="left:${Number(node.x)}px;top:${Number(node.y)}px">
      <div class="wf-node-drag"><span>${esc(role)}ロール</span><span class="wf-tier">実行時に追加</span></div>
      <div class="wf-node-body"><strong>${esc(name)}</strong><p>${esc(node.goal)}</p></div></article>`;
  }

  function boundaryNodesHtml(workflow, readonly = false) {
    const pos = boundaryPositions(workflow.nodes || []);
    const startSelected = st.selectedNode === START ? ' selected' : '';
    const endSelected = st.selectedNode === END ? ' selected' : '';
    const startIssue = !(workflow.entry || []).length;
    const endIssue = !(workflow.exit || []).length;
    const endError = st.connectFrom ? connectionError(workflow, st.connectFrom, END) : '';
    if (readonly) return `<article class="wf-node wf-boundary start" style="left:${pos.start.x}px;top:${pos.start.y}px">
        <div class="wf-node-body"><strong>開始</strong><p>ここから実行</p></div></article>
      <article class="wf-node wf-boundary end" style="left:${pos.end.x}px;top:${pos.end.y}px">
        <div class="wf-node-body"><strong>終了</strong><p>ここで完了</p></div></article>`;
    return `<article class="wf-node wf-boundary start${startSelected}${startIssue ? ' invalid' : ''}" data-node-id="${START}"
        style="left:${pos.start.x}px;top:${pos.start.y}px"><div class="wf-node-body"><strong>開始</strong><p>ここから実行</p>
        ${startIssue ? '<span class="wf-node-issue">工程へ接続してください</span>' : ''}</div>
        <button type="button" class="wf-port out" draggable="true" data-connect-out="${START}" aria-label="開始から接続"></button>
        <button type="button" class="wf-add-next wf-icon-button" data-add-after="${START}" aria-label="最初の工程を追加" title="最初の工程を追加">${ICONS.plus}</button></article>
      <article class="wf-node wf-boundary end${endSelected}${endIssue ? ' invalid' : ''}" data-node-id="${END}"
        style="left:${pos.end.x}px;top:${pos.end.y}px"><button type="button" class="wf-port in${portState(workflow, END)}"
        data-connect-in="${END}" title="${esc(endError || '終了へ接続')}" aria-label="終了へ接続"></button>
        <div class="wf-node-body"><strong>終了</strong><p>ここで完了</p>
        ${endIssue ? '<span class="wf-node-issue">末端を接続してください</span>' : ''}</div></article>`;
  }

  function inspectorHtml(ov, workflow) {
    if (st.selectedNode === START) {
      return '<div class="wf-inspector"><strong>開始</strong><p class="muted">ここから接続された工程を実行します。</p></div>';
    }
    if (st.selectedNode === END) {
      return '<div class="wf-inspector"><strong>終了</strong><p class="muted">ここへ到達するとフローが完了します。</p></div>';
    }
    const node = workflow.nodes.find((n) => n.id === st.selectedNode);
    if (!node) return '<div class="empty">ノードを選択してください</div>';
    const eligibleTiers = allowedTierIdsFor(node, ov);
    const tierOptions = (ov.tiers || []).filter((t) => tierEligible(node, ov, t.id)).map((t) =>
      `<option value="${esc(t.id)}" ${node.tier === t.id ? 'selected' : ''}>${esc(
        t.id === 'auto' ? t.label : tierLabel(t.id, t.label)
      )}</option>`).join('');
    const tierHelp = eligibleTiers
      ? `この機能に任せられる実行レベル: ${eligibleTiers.tiers.map((tier) => tierLabel(tier)).join('・')}。自動は実行方針に応じてこの範囲で決まります。`
      : '';
    const kindOptions = KINDS.map((kind) =>
      `<option value="${kind}" ${node.kind === kind ? 'selected' : ''}>${esc(KIND_META[kind][0])}</option>`).join('');
    const interaction = node.interaction || { mode: 'approval', prompt: node.goal, audience: ['reviewer'], timeout_seconds: 604800 };
    const interactionHtml = node.kind === 'human' ? `<section class="wf-human-options">
      <label>確認方法<select id="wf-human-mode">
        ${[['approval', '承認・却下'], ['choice', '選択'], ['input', '入力']].map(([value, label]) =>
    `<option value="${value}" ${interaction.mode === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
      <label>確認内容<textarea id="wf-human-prompt" rows="4">${esc(interaction.prompt || '')}</textarea></label>
      ${interaction.mode === 'choice' ? `<label>選択肢<textarea id="wf-human-options" rows="3"
        placeholder="1行に1つ">${esc((interaction.options || []).join('\n'))}</textarea></label>
        <label>期限時の既定値<select id="wf-human-default"><option value="">自動選択しない</option>
          ${(interaction.options || []).map((option) => `<option value="${esc(option)}"
            ${interaction.default_option === option ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select></label>` : ''}
      <label>対象グループ<input id="wf-human-audience" value="${esc((interaction.audience || ['reviewer']).join(', '))}"></label>
      <label>期限（秒）<input id="wf-human-timeout" type="number" min="1" step="1"
        value="${esc(interaction.timeout_seconds || 604800)}"></label>
      <small>回答は同期されます。期限切れは、選択式で既定値を指定した場合だけ自動で続行します。</small>
    </section>` : '';
    const optionMinTier = (option) => {
      const spec = ov.kindTiers && ov.kindTiers.kinds && ov.kindTiers.kinds[node.kind];
      const min = spec && spec.options && spec.options[option];
      const base = spec && Array.isArray(spec.tiers) ? spec.tiers[0] : '';
      return min && min !== base ? `実行レベルは「${tierLabel(min)}」以上になります。` : '';
    };
    const continuation = node.kind === 'classify'
      ? ['route', '分類後に専門工程を追加', `分類結果に応じた作業工程を agent-flow が追加します。${optionMinTier('route')}`]
      : node.kind === 'verify'
        ? ['retry', '未完了なら修正工程を追加して再検証', `検証が失敗したときだけ、修正と再検証を上限回数まで追加します。${optionMinTier('retry')}`] : null;
    return `<div class="wf-inspector" data-inspector="${esc(node.id)}">
      <div><strong>${esc(roleLabelForKind(node.kind))}ロール</strong><p class="wf-kind-help">
        ${esc((KIND_META[node.kind] || [node.kind])[0])} · ${esc((KIND_META[node.kind] || ['', ''])[1])}</p></div>
      <label>表示名<input id="wf-node-label" value="${esc(node.label || node.id)}"></label>
      <label>ID<input id="wf-node-id" value="${esc(node.id)}"></label>
      <label>種類<select id="wf-node-kind">${kindOptions}</select></label>
      ${node.kind === 'human' ? '' : `<label>実行レベル<select id="wf-node-tier">${tierOptions}</select>
        ${tierHelp ? `<small class="wf-tier-help">${esc(tierHelp)}</small>` : ''}</label>`}
      <label>この工程の目的<textarea id="wf-node-goal" rows="6">${esc(node.goal)}</textarea>
        <small class="wf-goal-help">この工程で達成したいことを自然文で書きます。依頼全文・前工程の成果・出力形式は agent-flow が実行時に補います。</small></label>
      ${interactionHtml}
      <details class="wf-runtime-context"><summary>agent-flow が自動で追加</summary>
        <p>${esc((KIND_META[node.kind] || [node.kind])[0])}としての役割、依頼全文、前工程の成果、作業規律、出力形式。</p></details>
      ${continuation ? `<label class="wf-continuation-option"><input type="checkbox" id="wf-node-continuation"
        value="${continuation[0]}" ${node.continuation === continuation[0] ? 'checked' : ''}><span><strong>${continuation[1]}</strong>
        <small>${continuation[2]}</small></span></label>` : ''}
      ${node.kind === 'human' ? '' : nodeMethodOptionsHtml(ov.methods, node)}
      <button type="button" id="wf-node-delete">ノードを削除</button>
    </div>`;
  }

  function miniFlowHtml(pattern) {
    const repeat = pattern && ['loop-until-done', 'adversarial-verification'].includes(pattern.id);
    const repeatLabel = pattern && pattern.id === 'adversarial-verification'
      ? '問題があれば生成へ戻る' : '未完了なら作業へ戻る';
    const columns = workflowNodeColumns(visualWorkflow(workflowFromPattern(pattern, '')));
    const flow = [[{ boundary: '開始' }], ...columns, [{ boundary: '終了' }]];
    return `<div class="wf-mini-flow${repeat ? ' loop' : ''}"
      aria-label="雛形の接続例${repeat ? `。${repeatLabel}` : ''}">${flow.map((column, index) =>
      `${index ? `<span class="wf-mini-edge${[...flow[index - 1], ...column].some((node) => node.runtime) ? ' runtime' : ''}"
        style="grid-column:${index * 2};grid-row:1" aria-hidden="true"><svg viewBox="0 0 22 10">
        <path d="M1 5h18"></path><path d="m15 1 4 4-4 4"></path></svg></span>` : ''}
      <div style="grid-column:${index * 2 + 1};grid-row:1">${column.map((node) => {
    if (node.boundary) return `<i>${node.boundary}</i>`;
    return `<i${node.runtime ? ' class="runtime" title="実行時に追加"' : ''}><b>${esc(kindLabelForKind(node.kind))}</b></i>`;
  }).join('')}</div>`).join('')}
      ${repeat ? `<span class="wf-loop-back runtime" style="grid-column:3 / ${flow.length * 2 - 2};grid-row:2">
        <svg aria-hidden="true" preserveAspectRatio="none" viewBox="0 0 100 20">
          <path d="M80 0v18H20V0"></path><path d="m16 4 4-4 4 4"></path></svg></span>` : ''}</div>`;
  }

  function templateCardHtml(pattern) {
    const data = pattern.type === 'method'
      ? `data-method-pattern-id="${esc(pattern.methodId)}"` : `data-pattern-id="${esc(pattern.id)}"`;
    const kind = pattern.type === 'method' ? '<em class="wf-template-kind">作業ルール</em>' : '';
    return `<button type="button" class="wf-template-card" ${data}>
      <strong>${esc(pattern.label || pattern.id)}${kind}</strong><small>${esc(pattern.description || '')}</small>
      ${miniFlowHtml(pattern)}<b>編集を始める →</b></button>`;
  }

  function savedWorkflowCardHtml(workflow) {
    const nodes = Array.isArray(workflow.nodes) ? workflow.nodes.length : 0;
    const readonly = workflow._scope === 'repository';
    return `<button type="button" class="wf-template-card wf-saved-card" data-workflow-id="${esc(workflow.id)}">
      <strong>${esc(workflow.name || workflow.id)}</strong>
      <small>${esc(workflow.description || `${nodes}工程のワークフロー`)}</small>
      <span class="wf-card-meta">${nodes}工程 · ${readonly ? '登録フォルダ（読み取り専用）' : '自分用（編集できます）'}</span>
      <b>${readonly ? '内容を見る →' : '編集する →'}</b></button>`;
  }

  function workflowLibraryHtml(ov) {
    const saved = ov.workflows || [];
    const patterns = ov.patterns || [];
    const methodPatterns = methodWorkflowPatterns(ov.methods);
    return `<section class="wf-page wf-settings wf-library" aria-label="ワークフロー設定">
      <div class="wf-title"><div><h2>ワークフロー</h2><p>保存済みを編集するか、新しく作成します。</p></div></div>
      ${st.notice ? `<p class="qf-notice" role="status">${esc(st.notice)}</p>` : ''}
      <section><div class="wf-section-head"><h3>保存済み</h3></div>
        ${saved.length ? `<div class="wf-template-grid">${saved.map(savedWorkflowCardHtml).join('')}</div>`
    : '<div class="empty">保存済みのワークフローはありません</div>'}</section>
      <section><div class="wf-section-head"><h3>新しく作る</h3></div>
        <div class="wf-template-grid"><button type="button" class="wf-template-card wf-blank-card" id="wf-new">
          ${ICONS.plus}
          <strong>一から作る</strong><small>空のキャンバスから工程を追加します。</small><b>編集を始める →</b></button>
          ${patterns.map(templateCardHtml).join('')}${methodPatterns.map(templateCardHtml).join('')}</div></section></section>`;
  }

  function patternChoices(ov) {
    const patterns = (ov.patterns || []).filter((pattern) =>
      pattern && pattern.template && Array.isArray(pattern.template.nodes) && pattern.template.nodes.length > 1);
    return [...patterns, ...methodWorkflowPatterns(ov.methods)];
  }

  function pickerHtml(ov, workflow) {
    if (!st.pickerFrom) return '';
    const recommended = recommendedKinds(workflow, st.pickerFrom);
    const card = (kind) => `<button type="button" class="wf-palette-card" draggable="true" data-palette="${kind}">
      <span>${esc(roleLabelForKind(kind))}ロール</span>
      <strong>${esc(KIND_META[kind][0])}</strong><small>${esc(KIND_META[kind][1])}</small></button>`;
    const patterns = patternChoices(ov);
    return `<section class="wf-node-picker" role="dialog" aria-label="次の工程を追加">
      <div class="wf-picker-head"><div><strong>次の工程</strong><small>${esc(st.pickerFrom === START ? '開始' : st.pickerFrom)} の後に追加</small></div>
        <button type="button" class="wf-icon-button" id="wf-picker-close" aria-label="閉じる" title="閉じる">${ICONS.close}</button></div>
      ${recommended.length ? `<div class="wf-picker-section"><span>おすすめ</span><div>${recommended.map(card).join('')}</div></div>` : ''}
      ${connectionError(workflow, st.pickerFrom, END) ? '' : '<button type="button" class="wf-end-choice" data-connect-end>終了につなぐ</button>'}
      <details><summary>すべての工程</summary><div class="wf-all-kinds">${KINDS.map(card).join('')}</div></details>
      ${patterns.length ? `<details><summary>工程セットを追加</summary><div class="wf-pattern-choices">${patterns.map((pattern) =>
    `<button type="button" class="wf-pattern-card" draggable="true" data-pattern-palette="${esc(pattern.id)}">
        <span><strong>${esc(pattern.label || pattern.id)}</strong><b>${pattern.type === 'method' ? '作業ルール' : '複数工程'}</b></span>
        <small>${esc(pattern.description || '')}</small>${miniFlowHtml(pattern)}</button>`).join('')}</div></details>` : ''}
    </section>`;
  }

  function canvasHtml(workflow, readonly = false) {
    const visual = visualWorkflow(workflow);
    const pos = boundaryPositions(visual.nodes || []);
    const width = Math.max(1100, pos.end.x + 260);
    const height = Math.max(720, ...(visual.nodes || []).map((node) => Number(node.y) + 180));
    return `<div class="wf-canvas${readonly ? ' readonly' : ''}" id="wf-canvas" tabindex="0" aria-label="フローキャンバス">
      <div class="wf-stage" style="--wf-zoom:${st.zoom};width:${width}px;height:${height}px">
        <svg aria-label="ノード間の接続"><defs><marker id="wf-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4"
          orient="auto"><path d="M 0 0 L 8 4 L 0 8 z"></path></marker></defs><g>${edgesHtml(visual, readonly)}${retryEdgesHtml(workflow)}</g></svg>
        ${boundaryNodesHtml(visual, readonly)}${visual.nodes.map((node) =>
    node.runtime ? runtimeNodeHtml(node) : nodeHtml(node, workflow, readonly)).join('')}
      </div></div>`;
  }

  function editorHtml(ov) {
    if (!st.editor) return workflowLibraryHtml(ov);
    const workflow = st.editor;
    const inspector = st.inspectorPosition;
    const inspectorClass = inspector ? ' floating' : '';
    const inspectorStyle = inspector ? ` style="left:${Number(inspector.x)}px;top:${Number(inspector.y)}px"` : '';
    const readonly = workflow._scope === 'repository';
    return `<section class="wf-page wf-settings" aria-label="ワークフロー設定">
      ${st.notice ? `<p class="qf-notice" role="status">${esc(st.notice)}</p>` : ''}
      <div class="wf-editor-layout"><main class="wf-editor-main">
        <div class="wf-editor-head"><button type="button" id="wf-library-home">← ワークフロー一覧</button>
          <label>名前<input id="wf-name" value="${esc(workflow.name)}" placeholder="フロー名"></label>
          <label>説明<input id="wf-description" value="${esc(workflow.description)}" placeholder="任意"></label>
          <button type="button" id="wf-fit">全体表示</button>
          ${readonly ? '<button type="button" class="primary" id="wf-copy">自分用にコピー</button>'
    : '<button type="button" class="primary" id="wf-save">保存</button>'}
          ${workflow.id && !readonly ? '<button type="button" id="wf-delete">削除</button>' : ''}</div>
        ${readonly ? '<p class="field-help">登録フォルダのフローは読み取り専用です。変更するには自分用にコピーしてください。DashboardはGit操作を行いません。</p>' : ''}
        ${st.connectFrom ? `<div class="wf-connect-status" role="status">接続先の丸を選択 · Escで解除</div>` : ''}
        <div class="wf-workspace">${canvasHtml(workflow)}${pickerHtml(ov, workflow)}
          ${st.selectedNode ? `<aside class="wf-properties${inspectorClass}"${inspectorStyle}><div class="wf-drawer-head" data-drag-inspector tabindex="0" aria-label="工程の設定パネル。ドラッグで移動できます">
            <div><h3>工程の設定</h3><small>ドラッグで移動</small></div><span>
            <button type="button" class="wf-icon-button" id="wf-inspector-dock" aria-label="右側へ戻す" title="右側へ戻す">${ICONS.dock}</button>
            <button type="button" class="wf-icon-button" id="wf-inspector-close" aria-label="閉じる" title="閉じる">${ICONS.close}</button></span></div>${inspectorHtml(ov, workflow)}</aside>` : ''}
        </div></main></div></section>`;
  }

  function renderRun() {
    const pane = $id('tab-workflow-run');
    if (!pane) return;
    pane.innerHTML = runHtml(st.overview || {});
    wireRun(pane);
  }

  // 設計カードだけを描き直す。実行フォームの入力（書きかけの依頼）を消さないため、
  // ラウンドの完了待ちではページ全体を再描画しない。
  function renderDesign() {
    const host = $id('wf-design-host');
    if (!host) return;
    host.innerHTML = designCardHtml();
    wireDesign(host);
  }

  async function loadDesign({ includeCurrent = true } = {}) {
    try {
      const result = await api().designSessionList();
      st.design.sessions = result.sessions || [];
    } catch (err) {
      st.design.notice = String((err && err.message) || err);
    }
    if (!includeCurrent || !st.design.current) return;
    try {
      const result = await api().designSessionGet({ id: st.design.current.id });
      st.design.current = result.session;
    } catch {
      st.design.current = null; // 破棄済み・壊れたセッションは選択から外すだけ
    }
  }

  // 走っているラウンドの完了を待つ。全体ポーリング（refreshNeedsOnly）は実行画面を
  // 描き直さないので、設計カードは自前で待つ。
  function watchDesign() {
    clearInterval(st.design.timer);
    st.design.timer = null;
    if (!st.design.current || st.design.current.runStatus !== 'running') return;
    st.design.timer = setInterval(async () => {
      const id = st.design.current && st.design.current.id;
      if (!id) { clearInterval(st.design.timer); st.design.timer = null; return; }
      try {
        const result = await api().designSessionGet({ id });
        if (result.session.runStatus === 'running') return;
        st.design.current = result.session;
        await loadDesign({ includeCurrent: false });
        clearInterval(st.design.timer);
        st.design.timer = null;
        renderDesign();
      } catch { /* 次の周回で拾う */ }
    }, 5000);
  }

  async function startDesignRound(payload) {
    st.design.busy = '設計中…';
    st.design.notice = '';
    renderDesign();
    // cwd は入力があるときだけ送る。空欄で上書きすると、継続ラウンドで
    // セッションが覚えているリポジトリを黙って外してしまう。
    const cwd = $id('wf-cwd')?.value || '';
    try {
      const result = await api().designSessionStart({ ...(cwd ? { cwd } : {}), ...payload });
      st.design.current = result.session;
      await loadDesign({ includeCurrent: false });
    } catch (err) {
      st.design.notice = String((err && err.message) || err);
    }
    st.design.busy = '';
    renderDesign();
    watchDesign();
  }

  function wireDesign(host) {
    if (!host) return;
    const card = $id('wf-design');
    if (card) card.addEventListener('toggle', () => { st.design.open = card.open; });
    host.querySelectorAll('[data-design-open]').forEach((button) => button.addEventListener('click', async () => {
      try {
        const result = await api().designSessionGet({ id: button.dataset.designOpen });
        st.design.current = result.session;
        st.design.notice = '';
      } catch (err) { st.design.notice = String((err && err.message) || err); }
      renderDesign();
      watchDesign();
    }));
    host.querySelector('[data-design-new]')?.addEventListener('click', () => {
      clearInterval(st.design.timer);
      st.design.timer = null;
      st.design.current = null;
      st.design.notice = '';
      renderDesign();
    });
    host.querySelector('[data-design-delete]')?.addEventListener('click', async () => {
      const id = st.design.current && st.design.current.id;
      if (!id) return;
      if (typeof root.confirmDialog === 'function' && !(await root.confirmDialog('この設計セッションを破棄しますか？'))) return;
      try {
        await api().designSessionDelete({ id });
        st.design.current = null;
        st.design.notice = 'セッションを破棄しました';
      } catch (err) { st.design.notice = String((err && err.message) || err); }
      await loadDesign({ includeCurrent: false });
      renderDesign();
    });
    const updateDesignSourceFields = () => {
      const sourceMode = host.querySelector('input[name="wf-design-source-mode"]:checked')?.value || 'new';
      const fileField = host.querySelector('[data-design-file-field]');
      const goalField = host.querySelector('[data-design-goal-field]');
      if (fileField) fileField.hidden = sourceMode === 'new';
      if (goalField) goalField.hidden = sourceMode !== 'new';
    };
    host.querySelectorAll('input[name="wf-design-source-mode"]').forEach((input) =>
      input.addEventListener('change', updateDesignSourceFields));
    updateDesignSourceFields();
    $id('wf-design-start')?.addEventListener('click', async () => {
      const sourceMode = host.querySelector('input[name="wf-design-source-mode"]:checked')?.value || 'new';
      const file = $id('wf-design-file')?.files?.[0];
      if (sourceMode !== 'new' && !file) {
        st.design.notice = 'Markdown ファイルを選択してください';
        renderDesign();
        return;
      }
      const document_ = file ? await file.text() : '';
      await startDesignRound({
        goal: sourceMode === 'new' ? ($id('wf-design-goal')?.value || '') : file.name,
        sourceMode,
        mode: sourceMode === 'use-as-is' ? 'auto' : 'interactive',
        document: document_,
        sources: file ? [{ kind: 'document', name: file.name, content: document_ }] : [],
      });
    });
    host.querySelector('[data-design-next]')?.addEventListener('click', () => {
      const answers = [...host.querySelectorAll('[data-design-answer]')]
        .sort((a, b) => Number(a.dataset.designAnswer) - Number(b.dataset.designAnswer))
        .map((field) => field.value);
      startDesignRound({ id: st.design.current.id, answers });
    });
    host.querySelector('[data-design-use]')?.addEventListener('click', () => {
      const request = $id('wf-request');
      const session = st.design.current;
      if (!request || !session) return;
      request.value = String(session.document || '');
      if (session.cwd && $id('wf-cwd') && !$id('wf-cwd').value) $id('wf-cwd').value = session.cwd;
      updateReadiness();
      st.design.open = false;
      if ($id('wf-design')) $id('wf-design').open = false;
      request.focus();
      request.scrollIntoView({ block: 'center' });
    });
  }

  function updateReadiness() {
    const badge = $id('wf-readiness');
    if (badge) badge.innerHTML = readinessHtml($id('wf-request')?.value || '');
  }

  function workflowNeeds(ov) {
    const rows = (ov.runs || []).flatMap((run) => (run.interactions || []).map((item) => ({
      ...item, runId: run.runId,
    })));
    const bucket = (item) => item.resolution || item.expired ? 'done' : item.responded ? 'sent' : 'open';
    const time = (item) => Date.parse(item.created_at || '') || 0;
    const rank = { open: 0, sent: 1, done: 2 };
    return rows.sort((a, b) => rank[bucket(a)] - rank[bucket(b)] || (bucket(a) === 'done' ? time(b) - time(a) : time(a) - time(b)));
  }

  function needsHtml(ov) {
    const items = workflowNeeds(ov);
    const bucket = (item) => item.resolution || item.expired ? 'done' : item.responded ? 'sent' : 'open';
    const selected = items.find((item) => `${item.runId}:${item.interaction_id}` === st.selectedNeed) || items[0] || null;
    if (selected) st.selectedNeed = `${selected.runId}:${selected.interaction_id}`;
    const list = items.map((item) => {
      const key = `${item.runId}:${item.interaction_id}`;
      const state = bucket(item);
      const label = item.resolution ? '回答済み' : item.expired ? '期限切れ' : item.responded ? '送信済み' : '未対応';
      return `<button type="button" class="wf-need-row ${st.selectedNeed === key ? 'selected' : ''}" data-workflow-need="${esc(key)}">
        <span class="status-chip ${state === 'open' ? 'st-blocked' : state === 'sent' ? 'st-review' : 'st-done'}">${label}</span>
        <strong>${esc(item.prompt || '人の確認')}</strong><small>${esc(item.runId)}</small></button>`;
    }).join('');
    return `<section class="wf-page"><div class="wf-title"><div><h2>要対応</h2><p>ワークフローが待っている人の判断を確認・回答します。</p></div></div>
      <div class="master-detail needs-layout"><aside class="master-list">${list || '<div class="empty">要対応はありません</div>'}</aside>
        <main class="detail-panel">${selected ? interactionCardsHtml([selected]) : '<div class="empty">要対応はありません</div>'}</main></div></section>`;
  }

  function renderNeeds() {
    const pane = $id('tab-workflow-needs');
    if (!pane) return;
    pane.innerHTML = needsHtml(st.overview || {});
    pane.querySelectorAll('[data-workflow-need]').forEach((button) => button.addEventListener('click', () => {
      st.selectedNeed = button.dataset.workflowNeed;
      renderNeeds();
    }));
    wireInteractionResponses(pane, async () => { await loadOverview(); renderNeeds(); });
  }

  function renderEdit() {
    const pane = $id('tab-workflow-edit');
    if (!pane) return;
    const oldCanvas = $id('wf-canvas');
    const scroll = oldCanvas ? [oldCanvas.scrollLeft, oldCanvas.scrollTop] : [0, 0];
    pane.innerHTML = editorHtml(st.overview || {});
    wireSettings(pane, st.overview || {});
    const canvas = $id('wf-canvas');
    if (canvas) { canvas.scrollLeft = scroll[0]; canvas.scrollTop = scroll[1]; }
  }
  const renderSettings = renderEdit;

  function settingsHtml(ov) {
    const methods = typeof root.orchMethodsPanelHtml === 'function'
      ? root.orchMethodsPanelHtml({ tuning: ov.tuning, methodsCatalog: ov.methodsCatalog }) : '';
    return `<section class="wf-page"><div class="wf-title"><div><h2>設定</h2><p>フロー、作業ルール、実行履歴を管理します。</p></div></div>
      ${st.notice ? `<p class="qf-notice" role="status">${esc(st.notice)}</p>` : ''}
      <div class="global-settings-card wf-settings-card">
        <header class="global-settings-card-heading"><span class="summary-kicker">フローと作業ルール</span>
          <h2>仕事の進め方</h2><p class="muted">自分用は編集できます。登録フォルダと組み込みの項目は読み取り専用で、自分用にコピーして変更します。</p></header>
        <div class="row"><button type="button" id="wf-open-library">フローを作成・編集</button></div>
      </div>
      ${methods}
      <div class="global-settings-card wf-settings-card">
        <header class="global-settings-card-heading"><span class="summary-kicker">実行履歴</span><h2>履歴を残す期間</h2>
          <p class="muted">終了した実行を何日残すか決めます。</p></header>
        <div class="field wf-settings-field"><label for="wf-retention-days">残す日数</label>
          <input id="wf-retention-days" type="number" min="1" max="3650" step="1" value="${esc(ov.retentionDays || 30)}">
          <p class="field-help">期限を過ぎた終端済みの実行を削除します。未対応の人による確認がある実行は保持します。</p></div>
        <div class="settings-save-actions wf-settings-actions"><button type="button" class="primary-inline" id="wf-save-settings">保存</button></div>
      </div></section>`;
  }

  function renderConfig() {
    const pane = $id('tab-workflow-settings');
    if (!pane) return;
    pane.innerHTML = settingsHtml(st.overview || {});
    $id('wf-open-library')?.addEventListener('click', () => {
      document.getElementById('tab-btn-workflow-edit')?.click();
    });
    if (typeof root.setupOrchestration === 'function') {
      root.setupOrchestration(pane, async () => { await loadOverview(); renderConfig(); });
    }
    $id('wf-save-settings')?.addEventListener('click', async () => {
      try {
        const result = await api().adhocFlowSaveSettings({ retentionDays: Number($id('wf-retention-days')?.value) });
        st.overview.retentionDays = result.retentionDays;
        st.notice = '設定を保存しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      renderConfig();
    });
  }

  function render() {
    if (runActive()) renderRun();
    const needs = $id('tab-workflow-needs');
    if (needs && needs.classList.contains('active')) renderNeeds();
    const edit = $id('tab-workflow-edit');
    if (edit && edit.classList.contains('active')) renderEdit();
    const settings = $id('tab-workflow-settings');
    if (settings && settings.classList.contains('active')) renderConfig();
    renderSidebar();
  }

  function executionOverridesFromForm(pane, ov) {
    const mode = pane.querySelector('input[name="wf-run-policy-mode"]:checked')?.value || 'recommended';
    if (mode !== 'custom') return executionOverridesForMode(mode, ov);
    const out = { version: 1, mode: 'custom', roles: {}, kinds: {} };
    pane.querySelectorAll('[data-override-group]').forEach((row) => {
      const value = {
        tier: row.querySelector('[data-override-tier]')?.value || '',
        agent_cli: row.querySelector('[data-override-agent]')?.value || '',
        model: row.querySelector('[data-override-model]')?.value || '',
      };
      if (value.tier || value.agent_cli || value.model) out[row.dataset.overrideGroup][row.dataset.overrideKey] = value;
    });
    return out;
  }

  function wireInteractionResponses(pane, after) {
    pane.querySelectorAll('[data-interaction-submit]').forEach((button) => button.addEventListener('click', async () => {
      const card = button.closest('[data-interaction-id]');
      const runId = card && (card.dataset.runId || st.selectedRun);
      if (!card || !runId) return;
      const kind = button.dataset.interactionSubmit;
      const answer = kind === 'input'
        ? { text: card.querySelector('[data-interaction-text]')?.value || '' }
        : kind === 'choice'
          ? { option: card.querySelector('[data-interaction-option]')?.value || '',
            comment: card.querySelector('[data-interaction-comment]')?.value || '' }
          : { decision: kind, comment: card.querySelector('[data-interaction-comment]')?.value || '' };
      try {
        await api().adhocFlowInteractionResponse({
          runId, interactionId: card.dataset.interactionId, answer,
        });
        st.notice = '回答を送信しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      await after();
    }));
  }

  // 100KB を超える依頼は工程の入力として重すぎる。投入は止めず、警告だけ出す。
  const REQUEST_SIZE_WARN = 100 * 1024;

  async function importDesignFile(file) {
    if (!file) return;
    const request = $id('wf-request');
    if (!request) return;
    try {
      const text = await file.text();
      request.value = text;
      st.notice = text.length > REQUEST_SIZE_WARN
        ? `${file.name} を読み込みました（${Math.round(text.length / 1024)}KB。工程へ渡すには大きすぎるかもしれません）`
        : `${file.name} を読み込みました`;
    } catch (err) {
      st.notice = `設計書を読み込めませんでした: ${String((err && err.message) || err)}`;
    }
    updateReadiness();
    // 画面全体を描き直すと読み込んだばかりの依頼が消えるので、報せだけ差し替える
    const notice = $id('wf-notice');
    if (notice) {
      notice.textContent = st.notice;
      notice.hidden = !st.notice;
    }
  }

  function wireRequestTools() {
    const request = $id('wf-request');
    if (!request) return;
    request.addEventListener('input', updateReadiness);
    updateReadiness();
    $id('wf-import')?.addEventListener('change', async (event) => {
      await importDesignFile(event.target.files && event.target.files[0]);
      event.target.value = '';
    });
    request.addEventListener('dragover', (event) => {
      if (event.dataTransfer && [...event.dataTransfer.types].includes('Files')) event.preventDefault();
    });
    request.addEventListener('drop', async (event) => {
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      event.preventDefault();
      await importDesignFile(file);
    });
    $id('wf-template')?.addEventListener('click', () => {
      request.value = request.value.trim() ? `${request.value.replace(/\s+$/, '')}\n\n${REQUEST_TEMPLATE}` : REQUEST_TEMPLATE;
      updateReadiness();
      request.focus();
    });
    $id('wf-request-expand')?.addEventListener('click', () => {
      const expanded = request.rows > 8;
      request.rows = expanded ? 4 : 24;
      $id('wf-request-expand').textContent = expanded ? '大きく表示' : '元の高さ';
    });
  }

  function wireRun(pane) {
    if (!pane) return;
    $id('wf-new-run')?.addEventListener('click', () => {
      st.selectedRun = '';
      st.runDetail = null;
      st.notice = '';
      renderSidebar();
      renderRun();
    });
    wireDesign($id('wf-design-host'));
    wireRequestTools();
    const flowSelect = $id('wf-flow');
    const showFlowSummary = () => {
      const summary = $id('wf-flow-summary');
      if (summary) summary.innerHTML = selectedFlowSummaryHtml(st.overview || {}, $id('wf-flow')?.value || 'auto');
    };
    if (flowSelect) flowSelect.addEventListener('change', showFlowSummary);
    const custom = pane.querySelector('#wf-run-policy-custom');
    const showPolicyFields = () => {
      if (custom) custom.hidden = pane.querySelector('input[name="wf-run-policy-mode"]:checked')?.value !== 'custom';
    };
    pane.querySelectorAll('input[name="wf-run-policy-mode"]').forEach((input) => input.addEventListener('change', showPolicyFields));
    showPolicyFields();
    $id('wf-cwd')?.addEventListener('change', async () => {
      const cwd = $id('wf-cwd')?.value || '';
      const request = $id('wf-request')?.value || '';
      const selection = $id('wf-flow')?.value || 'auto';
      try {
        st.overview = await api().adhocFlowOverview({ limit: 30, cwd });
        renderRun();
        if ($id('wf-cwd')) $id('wf-cwd').value = cwd;
        if ($id('wf-request')) $id('wf-request').value = request;
        updateReadiness();
        if ($id('wf-flow') && [...$id('wf-flow').options].some((option) => option.value === selection)) {
          $id('wf-flow').value = selection;
          showFlowSummary();
        }
      } catch (err) {
        st.notice = String((err && err.message) || err);
        renderRun();
      }
    });
    $id('wf-submit')?.addEventListener('click', async () => {
      st.busy = '実行中…';
      st.notice = '';
      const payload = {
        cwd: $id('wf-cwd')?.value || '',
        request: $id('wf-request')?.value || '',
        selection: selectionFrom($id('wf-flow')?.value || 'auto'),
        executionOverrides: executionOverridesFromForm(pane, st.overview || {}),
      };
      renderRun();
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
      renderRun();
    }));
    wireInteractionResponses(pane, async () => { await loadOverview(); renderRun(); });
    pane.querySelectorAll('[data-run-view]').forEach((button) => button.addEventListener('click', () => {
      st.runView = button.dataset.runView;
      renderRun();
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
    if ($id('wf-name')) workflow.name = $id('wf-name').value;
    workflow.description = $id('wf-description')?.value || '';
    const node = workflow.nodes.find((n) => n.id === st.selectedNode);
    if (node && $id('wf-node-id')) {
      const oldId = node.id;
      node.label = $id('wf-node-label').value.trim() || node.id;
      node.id = $id('wf-node-id').value.trim();
      node.kind = $id('wf-node-kind').value;
      if (node.kind === 'human') {
        delete node.tier;
        delete node.method;
        const previous = node.interaction || {};
        const mode = $id('wf-human-mode')?.value || previous.mode || 'approval';
        const options = ($id('wf-human-options')?.value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
        node.interaction = {
          mode,
          prompt: $id('wf-human-prompt')?.value.trim() || previous.prompt || node.goal,
          audience: ($id('wf-human-audience')?.value || 'reviewer').split(',').map((item) => item.trim()).filter(Boolean),
          timeout_seconds: Math.max(1, Number($id('wf-human-timeout')?.value || previous.timeout_seconds || 604800)),
          ...(mode === 'choice' ? {
            options: options.length >= 2 ? options : (previous.options || ['はい', 'いいえ']),
            ...(($id('wf-human-default')?.value || previous.default_option)
              ? { default_option: $id('wf-human-default')?.value || previous.default_option } : {}),
          } : {}),
        };
      } else {
        node.tier = $id('wf-node-tier')?.value || node.tier || st.overview?.tiers?.[0]?.id || '';
        delete node.interaction;
      }
      node.goal = $id('wf-node-goal').value.trim();
      const continuation = $id('wf-node-continuation');
      const selected = continuation && continuation.checked ? continuation.value : '';
      if ((node.kind === 'classify' && selected === 'route') || (node.kind === 'verify' && selected === 'retry')) {
        node.continuation = selected;
      } else delete node.continuation;
      // 種類・オプションの変更で今の実行レベルが適格範囲の外になったら自動へ戻す
      //（黙って別の固定レベルへ振り替えない——自動は実行方針に応じて範囲内で決まる）。
      if (node.kind !== 'human' && node.tier && node.tier !== 'auto'
        && !tierEligible(node, st.overview, node.tier)) {
        st.notice = `「${node.label || node.id}」の実行レベル「${tierLabel(node.tier)}」は`
          + `この機能・オプションでは使えないため「自動」に戻しました`;
        node.tier = 'auto';
      }
      if (node.id && oldId !== node.id) {
        workflow.nodes.forEach((n) => { n.deps = (n.deps || []).map((id) => id === oldId ? node.id : id); });
        workflow.entry = (workflow.entry || []).map((id) => id === oldId ? node.id : id);
        workflow.exit = (workflow.exit || []).map((id) => id === oldId ? node.id : id);
        st.selectedNode = node.id;
      }
    }
    return workflow;
  }

  function addNode(kind, x, y, ov) {
    const workflow = collectWorkflow();
    const used = new Set(workflow.nodes.map((n) => n.id));
    const tier = ov.tiers && ov.tiers[0] ? ov.tiers[0].id : '';
    let i = workflow.nodes.length + 1;
    while (used.has(`n${i}`)) i += 1;
    const node = { id: `n${i}`, label: KIND_META[kind][0], goal: defaultGoal(kind), kind,
      ...(kind === 'human' ? { interaction: { mode: 'approval', prompt: 'この内容で進めてよいですか？',
        audience: ['reviewer'], timeout_seconds: 604800 } } : { tier }), deps: [], x, y };
    const baseId = node.id;
    let suffix = 2;
    while (used.has(node.id)) node.id = `${baseId}-${suffix++}`;
    node.x = x;
    node.y = y;
    workflow.nodes.push(node);
    const from = st.pickerFrom || st.connectFrom;
    if (from) {
      try { connectWorkflow(workflow, from, node.id); st.notice = ''; }
      catch (err) { st.notice = String((err && err.message) || err); }
    }
    st.connectFrom = '';
    st.pickerFrom = '';
    st.selectedEdge = null;
    st.selectedNode = node.id;
    st.dirty = true;
    renderSettings();
  }

  function addPattern(pattern, x, y, ov) {
    const workflow = collectWorkflow();
    const tier = ov.tiers && ov.tiers[0] ? ov.tiers[0].id : '';
    const from = st.pickerFrom || st.connectFrom;
    try {
      const added = insertPattern(workflow, pattern, tier, from, { x, y });
      st.selectedNode = added[0] ? added[0].id : '';
      st.notice = added.length ? `${added.length}工程を追加しました` : '追加できる工程がありません';
    } catch (err) { st.notice = String((err && err.message) || err); }
    st.connectFrom = '';
    st.pickerFrom = '';
    st.selectedEdge = null;
    st.dirty = true;
    renderSettings();
  }

  function palettePosition(workflow) {
    return nextNodePosition(workflow, st.pickerFrom || st.connectFrom);
  }

  function applyConnection(from, to) {
    const workflow = collectWorkflow();
    try { connectWorkflow(workflow, from, to); st.notice = ''; }
    catch (err) { st.notice = String((err && err.message) || err); }
    st.connectFrom = '';
    st.pickerFrom = '';
    st.selectedEdge = null;
    st.dirty = true;
    renderSettings();
  }

  function deleteSelectedEdge() {
    if (!st.selectedEdge || !st.editor) return;
    disconnectWorkflow(st.editor, st.selectedEdge.from, st.selectedEdge.to);
    st.selectedEdge = null;
    st.dirty = true;
    renderSettings();
  }

  function wireSettings(pane, ov) {
    if (!pane) return;
    const canLeave = () => !st.dirty || typeof root.confirm !== 'function' || root.confirm('保存していない変更を破棄しますか？');
    const resetSelection = () => {
      st.selectedNode = ''; st.selectedEdge = null; st.connectFrom = ''; st.pickerFrom = ''; st.zoom = 1;
      st.inspectorPosition = null;
    };
    $id('wf-library-home')?.addEventListener('click', () => {
      if (!canLeave()) return;
      st.editor = null; st.dirty = false; resetSelection(); renderSettings();
    });
    $id('wf-new')?.addEventListener('click', () => {
      if (!canLeave()) return;
      st.editor = emptyWorkflow(); st.dirty = false; resetSelection(); renderSettings();
    });
    pane.querySelectorAll('[data-workflow-id]').forEach((button) => button.addEventListener('click', () => {
      if (!canLeave()) return;
      const found = (ov.workflows || []).find((item) => item.id === button.dataset.workflowId);
      st.editor = found ? spaceForStart(clone(found)) : emptyWorkflow();
      st.dirty = false; resetSelection(); renderSettings();
    }));
    pane.querySelectorAll('[data-pattern-id]').forEach((button) => button.addEventListener('click', () => {
      const found = (ov.patterns || []).find((item) => item.id === button.dataset.patternId);
      if (!found || !canLeave()) return;
      st.editor = workflowFromPattern(found, ov.tiers?.[0]?.id || '');
      st.selectedNode = START; st.dirty = true; st.notice = '雛形を複製しました'; renderSettings();
    }));
    pane.querySelectorAll('[data-method-pattern-id]').forEach((button) => button.addEventListener('click', () => {
      const found = methodWorkflowPatterns(ov.methods).find((item) => item.methodId === button.dataset.methodPatternId);
      if (!found || !canLeave()) return;
      st.editor = workflowFromPattern(found, ov.tiers?.[0]?.id || '');
      st.selectedNode = START; st.dirty = true; st.notice = '作業ルールを工程へ展開しました'; renderSettings();
    }));
    $id('wf-fit')?.addEventListener('click', () => {
      const workflow = collectWorkflow();
      const canvas = $id('wf-canvas');
      const pos = boundaryPositions(workflow.nodes || []);
      const height = Math.max(180, ...(workflow.nodes || []).map((node) => Number(node.y) + 150));
      st.zoom = Math.max(.55, Math.min(1, (canvas.clientWidth - 32) / (pos.end.x + 250), (canvas.clientHeight - 32) / height));
      renderSettings();
      const next = $id('wf-canvas'); if (next) { next.scrollLeft = 0; next.scrollTop = 0; }
    });
    $id('wf-save')?.addEventListener('click', async () => {
      try {
        const result = await api().adhocFlowSaveWorkflow({ workflow: collectWorkflow() });
        st.editor = clone(result.saved);
        const index = (ov.workflows || []).findIndex((item) => item.id === result.saved.id);
        if (index < 0) (ov.workflows || (ov.workflows = [])).unshift(clone(result.saved));
        else ov.workflows[index] = clone(result.saved);
        st.dirty = false;
        st.notice = '保存しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      renderSettings();
    });
    $id('wf-copy')?.addEventListener('click', () => {
      const copied = collectWorkflow();
      delete copied._scope;
      delete copied._repository;
      copied.id = `${copied.id}-copy-${Date.now().toString(36)}`;
      copied.name = `${copied.name} のコピー`;
      st.editor = copied;
      st.dirty = true;
      st.notice = '自分用のコピーを作りました。内容を確認して保存してください';
      renderSettings();
    });
    $id('wf-delete')?.addEventListener('click', async () => {
      try {
        await api().adhocFlowDeleteWorkflow({
          id: st.editor.id, scope: st.editor._scope, cwd: st.editor._repository,
        });
        ov.workflows = (ov.workflows || []).filter((item) => item.id !== st.editor.id);
        st.editor = null; st.dirty = false; resetSelection(); st.notice = '削除しました';
      } catch (err) { st.notice = String((err && err.message) || err); }
      renderSettings();
    });
    $id('wf-picker-close')?.addEventListener('click', () => { st.pickerFrom = ''; renderSettings(); });
    $id('wf-inspector-close')?.addEventListener('click', () => { collectWorkflow(); st.selectedNode = ''; renderSettings(); });
    pane.querySelector('[data-connect-end]')?.addEventListener('click', () => applyConnection(st.pickerFrom, END));
    pane.querySelectorAll('[data-palette]').forEach((button) => {
      button.addEventListener('click', () => {
        const pos = palettePosition(st.editor || emptyWorkflow());
        addNode(button.dataset.palette, pos.x, pos.y, ov);
      });
      button.addEventListener('dragstart', (event) => event.dataTransfer.setData('text/workflow-kind', button.dataset.palette));
    });
    pane.querySelectorAll('[data-pattern-palette]').forEach((button) => {
      const pattern = patternChoices(ov).find((item) => item.id === button.dataset.patternPalette);
      button.addEventListener('click', () => {
        const pos = palettePosition(st.editor || emptyWorkflow());
        if (pattern) addPattern(pattern, pos.x, pos.y, ov);
      });
      button.addEventListener('dragstart', (event) => event.dataTransfer.setData('text/workflow-pattern', button.dataset.patternPalette));
    });
    const canvas = $id('wf-canvas');
    canvas?.addEventListener('dragover', (event) => {
      if (event.dataTransfer.types.includes('text/workflow-kind') || event.dataTransfer.types.includes('text/workflow-pattern')) {
        event.preventDefault();
      }
    });
    canvas?.addEventListener('drop', (event) => {
      event.preventDefault();
      const kind = event.dataTransfer.getData('text/workflow-kind');
      const patternId = event.dataTransfer.getData('text/workflow-pattern');
      const pattern = patternChoices(ov).find((item) => item.id === patternId);
      if ((!KINDS.includes(kind) && !pattern) || !st.editor) return;
      const box = canvas.getBoundingClientRect();
      const x = Math.max(20, (event.clientX - box.left + canvas.scrollLeft) / st.zoom - 110);
      const y = Math.max(20, (event.clientY - box.top + canvas.scrollTop) / st.zoom - 30);
      if (pattern) addPattern(pattern, x, y, ov); else addNode(kind, x, y, ov);
    });
    pane.querySelectorAll('[data-node-id]').forEach((node) => node.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      collectWorkflow(); st.selectedNode = node.dataset.nodeId; st.selectedEdge = null; renderSettings();
    }));
    pane.querySelectorAll('[data-connect-out]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation(); st.connectFrom = button.dataset.connectOut;
        st.pickerFrom = ''; st.selectedEdge = null; st.notice = ''; renderSettings();
      });
      button.addEventListener('dragstart', (event) => {
        event.stopPropagation(); st.connectFrom = button.dataset.connectOut;
        event.dataTransfer.setData('text/workflow-source', st.connectFrom);
        pane.querySelectorAll('[data-connect-in]').forEach((target) => {
          target.classList.add(connectionError(st.editor, st.connectFrom, target.dataset.connectIn) ? 'invalid' : 'valid');
        });
      });
      button.addEventListener('dragend', () => {
        if (st.connectFrom === button.dataset.connectOut) { st.connectFrom = ''; renderSettings(); }
      });
    });
    pane.querySelectorAll('[data-connect-in]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (st.connectFrom) applyConnection(st.connectFrom, button.dataset.connectIn);
      });
      button.addEventListener('dragover', (event) => {
        const source = event.dataTransfer.getData('text/workflow-source') || st.connectFrom;
        if (source && !connectionError(st.editor, source, button.dataset.connectIn)) event.preventDefault();
        event.stopPropagation();
      });
      button.addEventListener('drop', (event) => {
        event.preventDefault(); event.stopPropagation();
        applyConnection(event.dataTransfer.getData('text/workflow-source') || st.connectFrom, button.dataset.connectIn);
      });
    });
    pane.querySelectorAll('[data-add-after]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation(); st.pickerFrom = button.dataset.addAfter; st.connectFrom = ''; st.notice = ''; renderSettings();
    }));
    pane.querySelectorAll('[data-edge-from]').forEach((edge) => {
      const select = (event) => {
        event.stopPropagation(); st.selectedEdge = { from: edge.dataset.edgeFrom, to: edge.dataset.edgeTo };
        st.selectedNode = ''; renderSettings();
      };
      edge.addEventListener('click', select);
      edge.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') select(event);
        else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); select(event); deleteSelectedEdge(); }
      });
    });
    canvas?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { st.connectFrom = ''; st.pickerFrom = ''; st.notice = ''; renderSettings(); }
      else if ((event.key === 'Delete' || event.key === 'Backspace') && st.selectedEdge) {
        event.preventDefault(); deleteSelectedEdge();
      }
    });
    pane.querySelectorAll('[data-node-method]').forEach((input) => input.addEventListener('change', () => {
      const workflow = collectWorkflow();
      const node = workflow.nodes.find((item) => item.id === st.selectedNode);
      if (!node) return;
      const id = input.dataset.nodeMethod;
      if (!id) delete node.method;
      else {
        const choice = nodeMethodChoices(ov.methods, node).find((item) => item.id === id);
        if (!choice) return;
        node.method = {
          id: choice.id, description: choice.description, role: choice.role,
          text: choice.text, source: choice.source,
        };
      }
      st.dirty = true;
      renderSettings();
    }));
    $id('wf-inspector-dock')?.addEventListener('click', () => {
      collectWorkflow(); st.inspectorPosition = null; renderSettings();
    });
    const inspectorHandle = pane.querySelector('[data-drag-inspector]');
    inspectorHandle?.addEventListener('pointerdown', (event) => {
      if (event.target.closest('button')) return;
      const panel = event.currentTarget.closest('.wf-properties');
      const workspace = panel && panel.closest('.wf-workspace');
      if (!panel || !workspace) return;
      const panelBox = panel.getBoundingClientRect();
      const workspaceBox = workspace.getBoundingClientRect();
      const start = {
        x: event.clientX, y: event.clientY,
        left: panelBox.left - workspaceBox.left, top: panelBox.top - workspaceBox.top,
      };
      inspectorHandle.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        const x = Math.max(0, Math.min(workspace.clientWidth - panel.offsetWidth,
          start.left + moveEvent.clientX - start.x));
        const y = Math.max(0, Math.min(workspace.clientHeight - panel.offsetHeight,
          start.top + moveEvent.clientY - start.y));
        st.inspectorPosition = { x, y };
        panel.classList.add('floating');
        panel.style.left = `${x}px`;
        panel.style.top = `${y}px`;
      };
      const up = () => inspectorHandle.removeEventListener('pointermove', move);
      inspectorHandle.addEventListener('pointermove', move);
      inspectorHandle.addEventListener('pointerup', up, { once: true });
      inspectorHandle.addEventListener('pointercancel', up, { once: true });
    });
    $id('wf-node-delete')?.addEventListener('click', () => {
      const id = st.selectedNode;
      const workflow = collectWorkflow();
      workflow.nodes = workflow.nodes.filter((n) => n.id !== id);
      workflow.nodes.forEach((n) => { n.deps = n.deps.filter((dep) => dep !== id); });
      workflow.entry = (workflow.entry || []).filter((nodeId) => nodeId !== id);
      workflow.exit = (workflow.exit || []).filter((nodeId) => nodeId !== id);
      st.selectedNode = ''; st.selectedEdge = null; st.dirty = true; renderSettings();
    });
    ['wf-node-label', 'wf-node-id', 'wf-node-kind', 'wf-node-tier', 'wf-node-goal', 'wf-node-continuation',
      'wf-human-mode', 'wf-human-prompt', 'wf-human-options', 'wf-human-default', 'wf-human-audience', 'wf-human-timeout'].forEach((id) =>
      $id(id)?.addEventListener('change', () => { collectWorkflow(); st.dirty = true; renderSettings(); }));
    ['wf-name', 'wf-description'].forEach((id) => $id(id)?.addEventListener('input', () => {
      collectWorkflow(); st.dirty = true;
    }));

    pane.querySelectorAll('[data-drag-node]').forEach((handle) => handle.addEventListener('pointerdown', (event) => {
      event.stopPropagation();
      const workflow = st.editor;
      const node = workflow.nodes.find((n) => n.id === handle.dataset.dragNode);
      if (!node) return;
      const start = { x: event.clientX, y: event.clientY, left: Number(node.x), top: Number(node.y) };
      handle.setPointerCapture(event.pointerId);
      const move = (e) => {
        node.x = Math.max(0, start.left + (e.clientX - start.x) / st.zoom);
        node.y = Math.max(0, start.top + (e.clientY - start.y) / st.zoom);
        const card = pane.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
        if (card) { card.style.left = `${node.x}px`; card.style.top = `${node.y}px`; }
        updateEdgePaths(workflow, pane);
      };
      const up = () => { handle.removeEventListener('pointermove', move); st.dirty = true; renderSettings(); };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up, { once: true });
    }));
  }

  async function ensureLoaded() {
    const stablePane = [$id('tab-workflow-edit'), $id('tab-workflow-settings')]
      .find((pane) => pane && pane.classList.contains('active'));
    if (stablePane && stablePane.firstElementChild) return;
    if (!st.overview) await loadOverview();
    render();
  }

  function badge() {
    const count = workflowNeeds(st.overview || {}).filter((item) => !item.resolution && !item.expired && !item.responded).length;
    return count ? { text: String(count), tone: 'warn', title: `要対応 ${count} 件` } : null;
  }

  return {
    KINDS,
    render: ensureLoaded,
    refresh,
    refreshNeeds,
    badge,
    statusLabel,
    selectionFrom,
    selectedFlowSummaryHtml,
    defaultGoal,
    roleLabelForKind,
    nodePresentation,
    nodeMethodChoices,
    methodWorkflowPattern,
    workflowFromPattern,
    visualWorkflow,
    workflowColumns,
    insertPattern,
    patternColumns,
    recommendedKinds,
    nextNodePosition,
    methodPresentation,
    tierLabel,
    readinessCheck,
    REQUEST_TEMPLATE,
    executionOverridesForMode,
    edgePath,
    workflowLibraryHtml,
    connectionError,
    connectWorkflow,
    disconnectWorkflow,
    _state: st,
  };
});
