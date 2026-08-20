'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const adhoc = require('./adhoc');
const designSession = require('./design-session');
const taskQueue = require('./task-queue');
const preparation = require('../../preparation/main/preparation');
const profiles = require('../../orchestration/main/profiles');
const flowTiers = require('../../orchestration/main/flow-tiers');
const tuning = require('../../orchestration/main/tuning');

const DESIGN_SCOPES = new Set(['user', 'repository', 'builtin']);

function designReferenceError(message) {
  throw new Error(`設計フロー参照が不正です: ${message}`);
}

function unwrapDesignValue(raw) {
  let value = raw && typeof raw === 'object' ? raw : null;
  for (let index = 0; index < 3 && value; index += 1) {
    const nested = value.snapshot || value.flow || (value.design && value.design.flow);
    if (!nested || typeof nested !== 'object' || value.id || value.origin) break;
    value = nested;
  }
  return value;
}

function designReferenceFrom(raw) {
  const value = unwrapDesignValue(raw);
  if (!value) designReferenceError('id・scope・repository を指定してください');
  const origin = value.origin && typeof value.origin === 'object' ? value.origin : {};
  const id = value.id;
  const scope = value.scope == null ? (value._scope == null ? origin.scope : value._scope) : value.scope;
  const repository = value.repository == null
    ? (value._repository == null ? (value.cwd == null ? origin.repository : value.cwd) : value._repository)
    : value.repository;
  if (typeof id !== 'string' || !id.trim()) designReferenceError('id が必要です');
  if (typeof scope !== 'string' || !DESIGN_SCOPES.has(scope.trim())) {
    designReferenceError(`scope は ${[...DESIGN_SCOPES].join(' / ')} のいずれかが必要です`);
  }
  if (repository != null && typeof repository !== 'string') {
    designReferenceError('repository は文字列で指定してください');
  }
  const clean = {
    id: id.trim(), scope: scope.trim(), repository: String(repository || '').trim(),
  };
  if (clean.scope === 'repository' && !clean.repository) {
    designReferenceError('共有フローには repository が必要です');
  }
  if (clean.scope !== 'repository' && clean.repository) {
    designReferenceError('repository は共有フローにだけ指定できます');
  }
  return clean;
}

function designSelectionFrom(raw, mode) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const hasReference = value.id != null || value.scope != null || value.repository != null
    || value.flow || value.snapshot || value.designFlow || value.selection;
  if (!hasReference) {
    const flowId = designSession.MODE_FLOWS[String(mode || '')];
    if (!flowId) designReferenceError(`設計の進め方が不正です: ${mode || '(未指定)'}`);
    return { id: flowId, scope: 'builtin', repository: '' };
  }
  return designReferenceFrom(value.selection || value.designFlow || value.flow || value);
}

function definitionNodeSnapshot(node) {
  const out = {
    id: String(node.id),
    goal: String(node.goal || ''),
    kind: String(node.kind || 'work'),
    deps: Array.isArray(node.deps) ? node.deps.map(String) : [],
  };
  if (node.tier) out.tier = String(node.tier);
  if (node.interaction && typeof node.interaction === 'object') out.interaction = node.interaction;
  if (Array.isArray(node.methods) && node.methods.length) out.methods = node.methods;
  if (node.continuation) out.continuation = String(node.continuation);
  return out;
}

function definitionSnapshot(workflow) {
  return {
    version: Number(workflow.version) || 2,
    purpose: String(workflow.purpose || 'implementation'),
    libraryVisibility: String(workflow.libraryVisibility || 'library'),
    entry: Array.isArray(workflow.entry) ? workflow.entry.map(String) : [],
    exit: Array.isArray(workflow.exit) ? workflow.exit.map(String) : [],
    nodes: (Array.isArray(workflow.nodes) ? workflow.nodes : []).map(definitionNodeSnapshot),
  };
}

function digestDefinition(definition) {
  return `sha256:${crypto.createHash('sha256')
    .update(JSON.stringify(definition)).digest('hex')}`;
}

function designFlowSnapshot(workflow, reference) {
  const definition = definitionSnapshot(workflow);
  return {
    version: 1,
    id: workflow.id,
    name: workflow.name,
    origin: { scope: reference.scope, repository: reference.repository },
    digest: digestDefinition(definition),
    definition,
  };
}

function trustedDesignSnapshot(raw, label = '保存済み設計フロー') {
  const snapshot = preparation.normalizeDesignFlow(raw);
  if (!snapshot || snapshot.definition.purpose !== 'design') {
    throw new Error(`${label}が不正です: design purpose のsnapshotが必要です`);
  }
  try {
    adhoc.normalizeWorkflow({
      ...snapshot.definition,
      id: snapshot.id,
      name: snapshot.name,
    }, { purpose: 'design' });
  } catch (error) {
    throw new Error(`${label}のdefinitionが不正です: ${error && error.message ? error.message : '検証に失敗しました'}`, {
      cause: error,
    });
  }
  const digest = digestDefinition(snapshot.definition);
  if (snapshot.digest !== digest) {
    throw new Error(`${label}のdigestが定義と一致しません`);
  }
  return snapshot;
}

function repositoryForDesignReference(config, reference) {
  if (reference.scope !== 'repository') return '';
  const registered = adhoc.registeredRepositoryRoot(config, reference.repository);
  if (!registered) {
    throw new Error(`共有設計フローを利用できません: 登録済みリポジトリではありません (${reference.repository})`);
  }
  return registered;
}

function resolveDesignFlow(config, raw, purpose = 'design') {
  const reference = designReferenceFrom(raw);
  const repository = repositoryForDesignReference(config, reference);
  let workflow;
  try {
    workflow = adhoc.loadWorkflow(config, reference.id, {
      scope: reference.scope,
      cwd: repository,
      purpose,
    });
  } catch (error) {
    designReferenceError(error && error.message ? error.message : 'id を解決できません');
  }
  if (!workflow) {
    if (reference.scope === 'repository') {
      throw new Error(`共有設計フローを利用できません: ${reference.id} (${repository})`);
    }
    throw new Error(`設計フローを利用できません: ${reference.id} (${reference.scope})`);
  }
  if (purpose && workflow.purpose !== purpose) {
    throw new Error(`設計フローのpurposeが不一致です: ${reference.id} は ${workflow.purpose} 用です`);
  }
  const resolved = { ...reference, repository };
  return { workflow, reference: resolved, snapshot: designFlowSnapshot(workflow, resolved) };
}

function designCatalogSources(config, cwd) {
  const repository = cwd ? adhoc.registeredRepositoryRoot(config, cwd) : '';
  return [
    ...(repository ? [{ scope: 'repository', dir: adhoc.repositoryWorkflowDir(repository), repository }] : []),
    { scope: 'user', dir: adhoc.resolveWorkflowDir(config), repository: '' },
    { scope: 'builtin', dir: adhoc.resolveBuiltinWorkflowDir(config), repository: '' },
  ];
}

function listDesignCatalog(config, cwd) {
  return designCatalogSources(config, String(cwd || '').trim()).flatMap((source) => {
    if (!source.dir || !fs.existsSync(source.dir)) return [];
    return fs.readdirSync(source.dir, { withFileTypes: true }).flatMap((entry) => {
      if (!entry.isFile() || !entry.name.endsWith('.json')) return [];
      try {
        const workflow = adhoc.normalizeWorkflow(JSON.parse(
          fs.readFileSync(path.join(source.dir, entry.name), 'utf8')));
        if (workflow.purpose !== 'design') return [];
        const reference = { scope: source.scope, repository: source.repository };
        const definition = definitionSnapshot(workflow);
        return [{
          id: workflow.id,
          name: workflow.name,
          description: workflow.description,
          purpose: workflow.purpose,
          libraryVisibility: workflow.libraryVisibility,
          scope: source.scope,
          repository: source.repository,
          nodeCount: definition.nodes.length,
          nodes: definition.nodes,
          digest: digestDefinition(definition),
          // origin is metadata only. The renderer must send the three-field
          // reference back; definition/plan fields are never trusted on input.
          origin: reference,
        }];
      } catch {
        return [];
      }
    });
  }).sort((a, b) => String(a.name).localeCompare(String(b.name)));
}

function designFlowFromPreparation(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (raw.designFlow && typeof raw.designFlow === 'object') return raw.designFlow;
  if (raw.designSelection && typeof raw.designSelection === 'object') return raw.designSelection;
  if (raw.design && typeof raw.design === 'object') {
    if (raw.design.flow && typeof raw.design.flow === 'object') return raw.design.flow;
    if (raw.design.selection && typeof raw.design.selection === 'object') return raw.design.selection;
    if (raw.design.id != null || raw.design.scope != null || raw.design.repository != null) return raw.design;
  }
  return null;
}

function prepareDesignInput(config, raw, fallbackSnapshot = null) {
  const input = { ...(raw && typeof raw === 'object' ? raw : {}) };
  const selected = designFlowFromPreparation(input);
  const resolved = selected ? resolveDesignFlow(config, selected) : null;
  const snapshot = resolved ? resolved.snapshot
    : (fallbackSnapshot ? trustedDesignSnapshot(fallbackSnapshot, '継承する設計フロー') : null);
  // Do not pass Renderer-authored definition/plan/design state through to the
  // preparation domain. A snapshot here was made from a file read in main.
  delete input.designFlow;
  delete input.designSelection;
  delete input.definition;
  delete input.plan;
  if (snapshot) input.design = { flow: snapshot };
  else delete input.design;
  return { input, resolved, snapshot };
}

function attachDesignSnapshot(item, snapshot) {
  if (!item || !snapshot || item.route !== 'agent-design') return item;
  return { ...item, design: { ...(item.design || {}), flow: trustedDesignSnapshot(snapshot) } };
}

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  const flow = adhoc.flow;

  handle('adhocFlow:overview', ({ limit, cwd } = {}) => {
    let cfg = loadConfig();
    const lastSweep = Date.parse(String((cfg.adhocFlow && cfg.adhocFlow.lastRetentionSweepAt) || ''));
    if (!Number.isFinite(lastSweep) || Date.now() - lastSweep >= 86400000) {
      adhoc.sweepExpiredRuns(cfg);
      const lastRetentionSweepAt = new Date().toISOString();
      cfg = { ...cfg, adhocFlow: { ...cfg.adhocFlow, lastRetentionSweepAt } };
      saveConfig(cfg);
    }
    const busDir = adhoc.resolveBusDir(cfg);
    let runs = [];
    try {
      runs = flow.listRuns(busDir, Math.max(0, Number(limit) || 30));
    } catch {
      // バス未作成（初回）は空一覧でよい
    }
    const registeredProjects = adhoc.listProjects(cfg);
    const sourceFolders = cwd ? [cwd] : registeredProjects.map((project) => project.dir);
    let methods = [];
    try {
      const discovered = sourceFolders.length
        ? sourceFolders.flatMap((folder) => adhoc.availableMethods(cfg, { cwd: folder }))
        : adhoc.availableMethods(cfg, {});
      methods = [...new Map(discovered.map((method) => [`${method._from}:${method.source || method.id}`, method])).values()].map((m) => ({
        id: m.id,
        description: String(m.description || ''),
        origin: String(m.origin || ''),
        source: String(m.source || ''),
        // renderer の候補フィルタ（nodeMethodChoices）が読む宣言。落とすと
        // 「成果物の契約・engine 選択の指示文を工程の候補に混ぜない」が機能しない。
        kind: String(m.kind || 'rule'),
        selection: String(m.selection || ''),
        fragments: (Array.isArray(m.fragments) ? m.fragments : []).map((fragment) => ({
          role: String((fragment && fragment.role) || ''),
          text: String((fragment && fragment.text) || ''),
        })),
        when: m.when && typeof m.when === 'object' ? m.when : {},
        from: m._from || 'catalog',
        repository: m._repository || '',
      }));
    } catch {
      // 手法カタログが無い端末では選択肢を出さないだけ（実行は可能）
    }
    let agents = [];
    try {
      const a = require('../../orchestration/main/agents').list(cfg);
      agents = [
        ...a.builtins,
        ...a.dropins.filter((d) => !d.shadowed && !(d.errors || []).length).map((d) => d.name),
      ];
    } catch {
      // CLI カタログが読めなくても投入はできる（選択肢が既定だけになるだけ）
    }
    const profile = profiles.load(cfg);
    const tierNames = Object.entries(profile.tiers || {})
      .sort((a, b) => b[1].order - a[1].order)
      .map(([id, value]) => ({ id, label: value.label || id }));
    // 旧プリセットは初回表示時にユーザー共通ファイルへ移す。元設定は互換用に残す。
    const fallbackTier = tierNames[0] && tierNames[0].id;
    for (const preset of (cfg.adhocFlow && cfg.adhocFlow.presets) || []) {
      if (!fallbackTier) continue;
      try {
        if (adhoc.loadWorkflow(cfg, preset.id)) continue;
        adhoc.saveWorkflow(cfg, {
          ...preset,
          nodes: (preset.nodes || []).map((n) => ({ ...n, tier: n.tier || fallbackTier })),
        });
      } catch {
        // 壊れた旧プリセットは一覧を壊さず、従来設定に残す。
      }
    }
    return {
      busDir,
      runs,
      // 一覧の各行を run ではなく「人が依頼したタスク」で束ねるための最小メタデータ。
      // renderer にファイルパスや実行契約全体は渡さない。
      runInbox: runs.map((run) => {
        const inbox = adhoc.readInbox(busDir, run.runId) || {};
        return {
          id: run.runId,
          title: String(inbox.title || ''),
          purpose: String(inbox.purpose || 'implementation'),
          root_run_id: String(inbox.root_run_id || ''),
          previous_run_id: String(inbox.previous_run_id || ''),
        };
      }),
      presets: (cfg.adhocFlow && cfg.adhocFlow.presets) || [],
      workflows: (() => {
        const discovered = sourceFolders.length
          ? sourceFolders.flatMap((folder) => adhoc.listWorkflows(cfg, { cwd: folder, includeInternal: true }))
          : adhoc.listWorkflows(cfg, { includeInternal: true });
        return [...new Map(discovered.map((workflow) => [
          `${workflow._scope}:${workflow._repository || ''}:${workflow.id}`, workflow,
        ])).values()];
      })(),
      patterns: adhoc.patternCatalog(cfg),
      tiers: [{ id: 'auto', label: '自動（実行方針を継承）' }, ...tierNames],
      // 機能（ノード kind）・役割ごとの実行可能レベルと、オプションが宣言する下限
      kindTiers: flowTiers.catalog(),
      cwdHistory: (cfg.adhocFlow && cfg.adhocFlow.cwdHistory) || [],
      methods,
      tuning: tuning.load(cfg),
      // 同じ id が同梱カタログとリポジトリ配布の両方にある場合、availableMethods（実行時の
      // 手法ピッカー）と同じ優先順位（リポジトリが勝つ）に揃える。単純結合すると同 id が
      // 2 枚のカードで並び、同梱カード側のトグルから誤ってリポジトリ版を無視できてしまう。
      methodsCatalog: (() => {
        const repository = methods.filter((method) => method.from === 'repository');
        const shadowed = new Set(repository.map((method) => String(method.id)));
        return [
          ...tuning.catalog(cfg).filter((method) => !shadowed.has(String(method.id))).map((method) => ({
            ...method, storage: 'built-in', readonly: true,
            catalog_source: `methods/${method.id}@${tuning.sourceHash(method)}`,
          })),
          ...repository.map((method) => ({
            ...method, storage: 'registered-folder', readonly: true,
            catalog_source: method.source,
          })),
        ];
      })(),
      agents,
      projects: registeredProjects,
      retentionDays: Math.max(1, Number(cfg.adhocFlow && cfg.adhocFlow.retentionDays) || 30),
    };
  });

  // 設計フローは通常の実装フロー一覧と混ぜず、同じ id の別スコープも省略しない。
  // Renderer はここで得た metadata から三つの参照項目だけを返し、定義の解決と
  // digest の計算は常に main process が行う。
  handle('adhocFlow:designCatalog', ({ cwd } = {}) => ({
    items: listDesignCatalog(loadConfig(), cwd),
  }));

  handle('adhocFlow:run', ({ runId } = {}) => {
    const cfg = loadConfig();
    const busDir = adhoc.resolveBusDir(cfg);
    const runDir = path.join(busDir, 'runs', String(runId || ''));
    if (!runId || !fs.existsSync(runDir)) throw new Error(`run が見つかりません: ${runId}`);
    return {
      run: flow.readRun(runDir),
      events: flow.readRunEvents(runDir, 50),
      nodeEvents: flow.readNodeEvents(runDir),
      inbox: adhoc.readInbox(busDir, String(runId)),
    };
  });
  handle('adhocFlow:interactionResponse', ({ runId, interactionId, answer } = {}) => {
    const cfg = loadConfig();
    return flow.writeInteractionResponse(adhoc.resolveBusDir(cfg), String(runId || ''),
      String(interactionId || ''), answer);
  });

  handle('adhocFlow:submit', (payload) => {
    const cfg = loadConfig();
    const result = adhoc.submit(cfg, payload || {});
    const cwd = String((payload && payload.cwd) || '').trim();
    if (cwd) {
      const old = (cfg.adhocFlow && cfg.adhocFlow.cwdHistory) || [];
      const cwdHistory = [cwd, ...old.filter((p) => p !== cwd)].slice(0, 20);
      saveConfig({ ...cfg, adhocFlow: { ...cfg.adhocFlow, cwdHistory } });
    }
    return result;
  });

  handle('adhocFlow:resubmit', ({ runId } = {}) => adhoc.resubmit(loadConfig(), String(runId || '')));

  handle('adhocFlow:cancel', ({ runId, reason } = {}) => {
    const cfg = loadConfig();
    return flow.cancelRun(adhoc.resolveBusDir(cfg), String(runId || ''), {
      reason: String(reason || 'dashboard からのキャンセル'),
    });
  });

  handle('adhocFlow:forceComplete', ({ runId, reason } = {}) =>
    adhoc.forceComplete(loadConfig(), { runId: String(runId || ''), reason: String(reason || '') }));

  handle('adhocFlow:deleteRun', ({ runId } = {}) => {
    const cfg = loadConfig();
    return flow.prepareRunDeletion(adhoc.resolveBusDir(cfg), String(runId || ''));
  });

  handle('adhocFlow:savePreset', ({ preset } = {}) => {
    const cfg = loadConfig();
    const clean = adhoc.normalizePreset(preset);
    const list = ((cfg.adhocFlow && cfg.adhocFlow.presets) || []).filter((p) => p.id !== clean.id);
    list.push(clean);
    list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    saveConfig({ ...cfg, adhocFlow: { ...cfg.adhocFlow, presets: list } });
    return { presets: list, saved: clean };
  });

  handle('adhocFlow:deletePreset', ({ id } = {}) => {
    const cfg = loadConfig();
    const list = ((cfg.adhocFlow && cfg.adhocFlow.presets) || []).filter((p) => p.id !== String(id));
    saveConfig({ ...cfg, adhocFlow: { ...cfg.adhocFlow, presets: list } });
    return { presets: list };
  });

  handle('adhocFlow:saveWorkflow', ({ workflow } = {}) => ({
    saved: adhoc.saveWorkflow(loadConfig(), workflow),
  }));

  handle('adhocFlow:deleteWorkflow', ({ id, cwd, scope } = {}) => ({
    deleted: adhoc.deleteWorkflow(loadConfig(), String(id || ''), { cwd, scope }),
  }));

  handle('adhocFlow:snapshotSelection', ({ selection, cwd, purpose } = {}) => {
    const cfg = loadConfig();
    const selected = selection && typeof selection === 'object' ? selection : { type: 'auto' };
    if (purpose === 'design') {
      const resolved = resolveDesignFlow(cfg, {
        ...selected,
        // user/builtin の cwd は参照解決に不要。repository scope のときだけ
        // cwd を共有リポジトリ参照として補う。
        repository: selected.repository || (selected.scope === 'repository' ? cwd : ''),
      });
      return {
        flow: resolved.snapshot,
        selection: resolved.reference,
      };
    }
    // 実装フローの既存契約は維持するが、custom の definition/plan は一切渡さず、
    // id/scope/repository から main 側で読み直した定義だけを snapshot 化する。
    if (selected.type === 'custom' && selected.scope) {
      const repository = selected.repository || (selected.scope === 'repository' ? cwd : '');
      const reference = designReferenceFrom({ ...selected, repository });
      const resolvedRepository = reference.scope === 'repository'
        ? adhoc.registeredRepositoryRoot(cfg, reference.repository) : '';
      if (reference.scope === 'repository' && !resolvedRepository) {
        throw new Error(`共有フローを利用できません: 登録済みリポジトリではありません (${reference.repository})`);
      }
      const workflow = adhoc.loadWorkflow(cfg, reference.id, {
        scope: reference.scope, cwd: resolvedRepository,
      });
      if (!workflow) throw new Error(`フローを利用できません: ${reference.id} (${reference.scope})`);
      if (workflow.purpose !== 'implementation') {
        throw new Error(`フローのpurposeが不一致です: ${reference.id} は ${workflow.purpose} 用です`);
      }
      return { flow: adhoc.snapshotSelection(cfg, {
        type: 'custom', id: workflow.id, scope: reference.scope,
        ...(selected.nodeAssignments ? { nodeAssignments: selected.nodeAssignments } : {}),
      }, { cwd: resolvedRepository, purpose: 'implementation' }) };
    }
    return { flow: adhoc.snapshotSelection(cfg, selected, { cwd, purpose }) };
  });

  // 設計フロー（同梱 design-interactive / design-auto や任意のフロー）のノードごとの
  // 自動割り当てと選択可能な候補。定義は Renderer の入力ではなく参照から読む。
  handle('adhocFlow:designPreview', (payload = {}) => {
    const cfg = loadConfig();
    const resolved = resolveDesignFlow(cfg, designSelectionFrom(payload, payload.mode));
    return {
      preview: adhoc.flowAssignmentPreview(cfg, {
        id: resolved.workflow.id,
        cwd: resolved.reference.repository,
        scope: resolved.reference.scope,
        workflow: resolved.workflow,
        purpose: 'design',
      }),
      snapshot: resolved.snapshot,
      selection: resolved.reference,
    };
  });

  // 実装フロー（自動 plan）の役割・機能ごとの自動割り当てと選択可能な候補。
  // 実行前のフロー表示（作業準備の「実行」）が読む。
  handle('adhocFlow:executionPreview', () => ({
    preview: adhoc.executionAssignmentPreview(loadConfig()),
  }));

  handle('adhocFlow:promote', (payload) => adhoc.promote(loadConfig(), payload || {}));

  handle('adhocFlow:saveSettings', ({ retentionDays } = {}) => {
    const days = Number(retentionDays);
    if (!Number.isInteger(days) || days < 1 || days > 3650) {
      throw new Error('保持日数は1〜3650日の整数で指定してください');
    }
    const cfg = loadConfig();
    saveConfig({ ...cfg, adhocFlow: { ...cfg.adhocFlow, retentionDays: days } });
    return { retentionDays: days };
  });
  // 設計セッション（短い要望 → 実行できる設計書）。ラウンドの制御はここだけが持つ。
  handle('designSession:list', () => ({ sessions: designSession.listSessions(loadConfig()) }));
  handle('designSession:get', ({ id } = {}) => ({
    session: designSession.getSession(loadConfig(), String(id || '')),
  }));
  handle('designSession:start', (payload) => {
    // Renderer は参照だけを送れる。definition/plan/designFlow はここで捨て、
    // main が参照を解決して作ったsnapshotだけを startRound へ渡す。
    const cfg = loadConfig();
    const input = { ...(payload || {}) };
    const existingSession = String(input.id || '').trim();
    if (!existingSession) {
      const flowInput = input.selection || input.designFlow || input.flow || input.snapshot || {};
      const assignments = flowInput && typeof flowInput === 'object'
        && flowInput.nodeAssignments && typeof flowInput.nodeAssignments === 'object'
        ? flowInput.nodeAssignments : null;
      const resolved = resolveDesignFlow(cfg, designSelectionFrom(input, input.mode || 'interactive'));
      input.selection = {
        ...resolved.reference,
        ...(assignments ? { nodeAssignments: assignments } : {}),
      };
      input.resolvedFlowSnapshot = resolved.snapshot;
    } else if (input.selection && typeof input.selection === 'object') {
      // 継続roundは session に保存済みのsnapshotを使う。Rendererから定義を
      // 持ち込ませず、roundごとの変更可能項目はnodeAssignmentsだけに絞る。
      input.selection = input.selection.nodeAssignments && typeof input.selection.nodeAssignments === 'object'
        ? { nodeAssignments: input.selection.nodeAssignments } : undefined;
    }
    delete input.designFlow;
    delete input.designSelection;
    delete input.flow;
    delete input.snapshot;
    delete input.definition;
    delete input.plan;
    return { session: designSession.startRound(loadConfig(), input) };
  });
  handle('designSession:delete', ({ id } = {}) => ({
    deleted: designSession.deleteSession(loadConfig(), String(id || '')),
  }));
  handle('workflowTask:list', () => ({ tasks: taskQueue.list(loadConfig()) }));
  handle('workflowTask:create', (payload) => ({ task: taskQueue.create(loadConfig(), payload || {}) }));
  handle('workflowTask:delete', ({ id } = {}) => ({ deleted: taskQueue.remove(loadConfig(), String(id || '')) }));
  handle('workflowTask:execute', ({ id } = {}) =>
    taskQueue.execute(loadConfig(), String(id || ''), (payload) => adhoc.submit(loadConfig(), payload)));
  handle('preparation:list', (filters) => ({
    items: preparation.listItems(loadConfig(), filters || {}),
  }));
  handle('preparation:create', (payload) => {
    const cfg = loadConfig();
    const prepared = prepareDesignInput(cfg, payload || {});
    const item = attachDesignSnapshot(preparation.createItem({
      ...prepared.input, format: adhoc.designDocumentFormat(cfg, { cwd: prepared.input.cwd }),
    }), prepared.snapshot);
    return { item: preparation.saveItem(cfg, item) };
  });
  handle('preparation:recommend', (payload) => {
    const cfg = loadConfig();
    const raw = payload && typeof payload === 'object' ? payload : {};
    // 「実行できる設計書か」の書式は手法カタログが正典（対象フォルダの上書きも効く）。
    return {
      recommendation: preparation.recommendRoute({
        ...raw, format: adhoc.designDocumentFormat(cfg, { cwd: raw.cwd }),
      }),
    };
  });
  handle('preparation:get', ({ id } = {}) => ({
    item: preparation.getItem(loadConfig(), String(id || '')),
  }));
  handle('preparation:delete', ({ id } = {}) => ({
    deleted: preparation.removeItem(loadConfig(), String(id || '')),
  }));
  handle('preparation:createPackage', (payload) => {
    const cfg = loadConfig();
    const raw = payload && typeof payload === 'object' ? payload : {};
    const packageDefault = prepareDesignInput(cfg, raw);
    const candidates = Array.isArray(packageDefault.input.candidates)
      ? packageDefault.input.candidates.map((candidate) => {
        const prepared = prepareDesignInput(cfg, candidate, packageDefault.snapshot);
        return { input: prepared.input, snapshot: prepared.snapshot };
      }) : [];
    const packageInput = {
      ...packageDefault.input,
      format: adhoc.designDocumentFormat(cfg, { cwd: packageDefault.input.projectDir }),
      candidates: candidates.map((candidate) => candidate.input),
    };
    const package_ = preparation.createPackage(packageInput);
    package_.items = package_.items.map((item, index) =>
      attachDesignSnapshot(item, candidates[index] && candidates[index].snapshot));
    return { package: preparation.savePackage(cfg, package_), items: package_.items };
  });
  handle('preparation:startDesign', ({ id } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item) throw new Error('作業準備項目が見つかりません');
    const storedRawFlow = item.design && item.design.flow;
    const storedFlow = storedRawFlow ? trustedDesignSnapshot(storedRawFlow) : null;
    const resolved = storedFlow ? null
      : resolveDesignFlow(cfg, designSelectionFrom({}, item.designMode || 'interactive'));
    const flow = storedFlow || resolved.snapshot;
    const reference = storedFlow ? {
      id: storedFlow.id,
      scope: storedFlow.origin.scope,
      repository: storedFlow.origin.repository,
    } : resolved.reference;
    const selection = {
      type: 'custom',
      id: reference.id,
      scope: reference.scope,
      repository: reference.repository,
      ...(item.designAssignments ? { nodeAssignments: item.designAssignments } : {}),
    };
    const session = designSession.startRound(cfg, {
      target: item.target,
      sourceMode: 'new',
      mode: item.designMode || 'interactive',
      goal: item.goal,
      cwd: item.cwd || item.projectDir,
      sources: (item.materials || []).filter((material) =>
        (material.selectedFor || []).includes('design')),
      ...(item.designAssignments ? { nodeAssignments: item.designAssignments } : {}),
      selection,
      designFlow: flow,
      resolvedFlowSnapshot: flow,
    });
    const next = preparation.startDesign(item, {
      sessionId: session.id, runId: session.runId, designFlow: flow,
    });
    const updated = attachDesignSnapshot(next, flow);
    return { item: preparation.saveItem(cfg, updated), session };
  });
  handle('preparation:syncDesign', ({ id } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item || !item.design || !item.design.sessionId) throw new Error('設計セッションがありません');
    const session = designSession.getSession(cfg, item.design.sessionId);
    const sessionDocument = String(session.document || '').trim();
    const complete = preparation.isCompleteDesignDocument(sessionDocument,
      adhoc.designDocumentFormat(cfg, { cwd: item.cwd }));
    const previousDocument = String(item.design.document || '').trim();
    const successful = session.runStatus === 'done' && !String(session.error || '').trim() && complete;
    const next = {
      ...item,
      // 不完全/失敗した成果では旧成果を消さず、handoff可能な review にも進めない。
      phase: successful ? 'design-review' : 'designing',
      design: {
        ...item.design,
        document: complete ? sessionDocument : previousDocument,
        runIds: [...new Set((session.rounds || []).map((round) => round.runId).filter(Boolean))],
      },
    };
    return { item: preparation.saveItem(cfg, next), session };
  });
  handle('preparation:completeDesign', ({ id } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item || !item.design || !item.design.sessionId) throw new Error('設計セッションがありません');
    const session = designSession.getSession(cfg, item.design.sessionId);
    if (String(session.error || '').trim()) {
      throw new Error(`設計成果を完了できません: ${session.error}`);
    }
    const next = preparation.completeDesign(item, {
      sessionId: session.id,
      document: session.document,
      runIds: (session.rounds || []).map((round) => round.runId),
    }, adhoc.designDocumentFormat(cfg, { cwd: item.cwd }));
    return { item: preparation.saveItem(cfg, next), session };
  });
  handle('preparation:handoff', ({ id, executionOverrides, granularity, splitPolicy } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    const designFormat = adhoc.designDocumentFormat(cfg, { cwd: item && item.cwd });
    if (!item || !preparation.canHandoff(item, designFormat)) {
      throw new Error('実装準備が完了していません');
    }
    if (item.target === 'project') {
      const taskSpec = item.taskSpec || {};
      const spec = {
        ...taskSpec,
        title: item.title,
        desc: taskSpec.desc || item.goal,
        task_acceptance_criteria: taskSpec.task_acceptance_criteria || taskSpec.acceptance,
      };
      const result = adhoc.promote(cfg, { projectDir: item.projectDir, spec });
      const next = preparation.recordHandoff(item,
        { taskId: String(result && result.id || spec.id || item.id) }, designFormat);
      return { item: preparation.saveItem(cfg, next), result };
    }
    const result = adhoc.submit(cfg, {
      title: item.title,
      cwd: item.cwd,
      request: preparation.implementationRequest(item),
      selection: { type: 'auto' },
      ...(executionOverrides ? { executionOverrides } : {}),
      // 分け方（L2）は実行前の確認で人が選べる。未指定なら渡さない
      // ＝ノード側の agent-flow.yaml / 既定に従う（画面が既定を上書きしない）。
      ...(granularity ? { granularity } : {}),
      ...(splitPolicy ? { splitPolicy } : {}),
    });
    const next = preparation.recordHandoff(item, { runId: result.runId }, designFormat);
    return { item: preparation.saveItem(cfg, next), result };
  });

  handle('adhocFlow:copyMethod', ({ id, cwd, newId } = {}) => {
    const cfg = loadConfig();
    const method = adhoc.availableMethods(cfg, { cwd }).find((item) => String(item.id) === String(id));
    if (!method || method._from !== 'repository') throw new Error('登録フォルダの作業ルールが見つかりません');
    return { tuning: tuning.importMethod(cfg, method, newId) };
  });
}

module.exports = { registerIpc };
