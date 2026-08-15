'use strict';

const fs = require('fs');
const path = require('path');
const adhoc = require('./adhoc');
const designSession = require('./design-session');
const taskQueue = require('./task-queue');
const preparation = require('../../preparation/main/preparation');
const profiles = require('../../orchestration/main/profiles');
const flowTiers = require('../../orchestration/main/flow-tiers');
const tuning = require('../../orchestration/main/tuning');

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
      presets: (cfg.adhocFlow && cfg.adhocFlow.presets) || [],
      workflows: (() => {
        const discovered = sourceFolders.length
          ? sourceFolders.flatMap((folder) => adhoc.listWorkflows(cfg, { cwd: folder }))
          : adhoc.listWorkflows(cfg);
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

  handle('adhocFlow:snapshotSelection', ({ selection, cwd } = {}) => ({
    flow: adhoc.snapshotSelection(loadConfig(), selection, { cwd }),
  }));

  // 設計フロー（同梱 design-interactive / design-auto や任意のフロー）のノードごとの
  // 自動割り当てと選択可能な候補。タスク追加ダイアログの割り当て UI が読む。
  handle('adhocFlow:designPreview', ({ mode, id, cwd, scope } = {}) => ({
    preview: adhoc.flowAssignmentPreview(loadConfig(), {
      id: designSession.MODE_FLOWS[String(mode || '')] || String(id || '')
        || designSession.MODE_FLOWS.interactive,
      cwd,
      scope,
    }),
  }));

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
  handle('designSession:start', (payload) => ({
    session: designSession.startRound(loadConfig(), payload || {}),
  }));
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
  handle('preparation:create', (payload) => ({
    item: preparation.saveItem(loadConfig(), preparation.createItem(payload || {})),
  }));
  handle('preparation:recommend', (payload) => ({
    recommendation: preparation.recommendRoute(payload || {}),
  }));
  handle('preparation:get', ({ id } = {}) => ({
    item: preparation.getItem(loadConfig(), String(id || '')),
  }));
  handle('preparation:delete', ({ id } = {}) => ({
    deleted: preparation.removeItem(loadConfig(), String(id || '')),
  }));
  handle('preparation:createPackage', (payload) => {
    const package_ = preparation.createPackage(payload || {});
    return { package: preparation.savePackage(loadConfig(), package_), items: package_.items };
  });
  handle('preparation:startDesign', ({ id } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item) throw new Error('作業準備項目が見つかりません');
    const session = designSession.startRound(cfg, {
      target: item.target,
      sourceMode: 'new',
      mode: 'interactive',
      goal: item.goal,
      cwd: item.cwd || item.projectDir,
      sources: (item.materials || []).filter((material) =>
        (material.selectedFor || []).includes('design')),
      ...(item.designAssignments ? { nodeAssignments: item.designAssignments } : {}),
    });
    const next = preparation.startDesign(item, { sessionId: session.id, runId: session.runId });
    return { item: preparation.saveItem(cfg, next), session };
  });
  handle('preparation:syncDesign', ({ id } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item || !item.design || !item.design.sessionId) throw new Error('設計セッションがありません');
    const session = designSession.getSession(cfg, item.design.sessionId);
    const next = {
      ...item,
      phase: session.runStatus === 'done' ? 'design-review' : 'designing',
      design: {
        ...item.design,
        document: String(session.document || ''),
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
    const next = preparation.completeDesign(item, {
      sessionId: session.id,
      document: session.document,
      runIds: (session.rounds || []).map((round) => round.runId),
    });
    return { item: preparation.saveItem(cfg, next), session };
  });
  handle('preparation:handoff', ({ id, executionOverrides } = {}) => {
    const cfg = loadConfig();
    const item = preparation.getItem(cfg, String(id || ''));
    if (!item || !preparation.canHandoff(item)) throw new Error('実装準備が完了していません');
    if (item.target === 'project') {
      const taskSpec = item.taskSpec || {};
      const spec = {
        ...taskSpec,
        title: item.title,
        desc: taskSpec.desc || item.goal,
        task_acceptance_criteria: taskSpec.task_acceptance_criteria || taskSpec.acceptance,
      };
      const result = adhoc.promote(cfg, { projectDir: item.projectDir, spec });
      const next = preparation.recordHandoff(item, { taskId: String(result && result.id || spec.id || item.id) });
      return { item: preparation.saveItem(cfg, next), result };
    }
    const result = adhoc.submit(cfg, {
      title: item.title,
      cwd: item.cwd,
      request: preparation.implementationRequest(item),
      selection: { type: 'auto' },
      ...(executionOverrides ? { executionOverrides } : {}),
    });
    const next = preparation.recordHandoff(item, { runId: result.runId });
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
