'use strict';

const fs = require('fs');
const path = require('path');
const adhoc = require('./adhoc');
const designSession = require('./design-session');
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

  handle('adhocFlow:copyMethod', ({ id, cwd, newId } = {}) => {
    const cfg = loadConfig();
    const method = adhoc.availableMethods(cfg, { cwd }).find((item) => String(item.id) === String(id));
    if (!method || method._from !== 'repository') throw new Error('登録フォルダの作業ルールが見つかりません');
    return { tuning: tuning.importMethod(cfg, method, newId) };
  });
}

module.exports = { registerIpc };
