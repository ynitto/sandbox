'use strict';

const fs = require('fs');
const path = require('path');
const adhoc = require('./adhoc');
const profiles = require('../../orchestration/main/profiles');

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  const flow = adhoc.flow;

  handle('adhocFlow:overview', ({ limit } = {}) => {
    const cfg = loadConfig();
    const busDir = adhoc.resolveBusDir(cfg);
    let runs = [];
    try {
      runs = flow.listRuns(busDir, Math.max(0, Number(limit) || 30));
    } catch {
      // バス未作成（初回）は空一覧でよい
    }
    let methods = [];
    try {
      methods = adhoc.availableMethods(cfg).map((m) => ({
        id: m.id,
        description: String(m.description || ''),
        origin: String(m.origin || ''),
        fragments: (Array.isArray(m.fragments) ? m.fragments : []).map((fragment) => ({
          role: String((fragment && fragment.role) || ''),
          text: String((fragment && fragment.text) || ''),
        })),
        when: m.when && typeof m.when === 'object' ? m.when : {},
        from: m._from || 'catalog',
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
      workflows: adhoc.listWorkflows(cfg),
      patterns: adhoc.patternCatalog(cfg),
      tiers: [{ id: 'auto', label: '自動（実行方針を継承）' }, ...tierNames],
      cwdHistory: (cfg.adhocFlow && cfg.adhocFlow.cwdHistory) || [],
      methods,
      agents,
      projects: adhoc.listProjects(cfg),
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

  handle('adhocFlow:deleteWorkflow', ({ id } = {}) => ({
    deleted: adhoc.deleteWorkflow(loadConfig(), String(id || '')),
  }));

  handle('adhocFlow:snapshotSelection', ({ selection } = {}) => ({
    flow: adhoc.snapshotSelection(loadConfig(), selection),
  }));

  handle('adhocFlow:promote', (payload) => adhoc.promote(loadConfig(), payload || {}));
}

module.exports = { registerIpc };
