'use strict';

const fs = require('fs');
const path = require('path');
const adhoc = require('./adhoc');

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
    return {
      busDir,
      runs,
      presets: (cfg.adhocFlow && cfg.adhocFlow.presets) || [],
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

  handle('adhocFlow:submit', (payload) => adhoc.submit(loadConfig(), payload || {}));

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

  handle('adhocFlow:promote', (payload) => adhoc.promote(loadConfig(), payload || {}));
}

module.exports = { registerIpc };
