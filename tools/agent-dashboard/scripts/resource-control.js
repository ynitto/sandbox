#!/usr/bin/env node
'use strict';

// Electron を起動せずに、dashboard と同じ資源制御モジュールを 1 回実行する。
const fs = require('fs');
const path = require('path');
const budget = require('../src/features/orchestration/main/budget');
const control = require('../src/features/orchestration/main/control');
const profiles = require('../src/features/orchestration/main/profiles');

const DEFAULT_INTERVAL_MS = 300000;

function writeStatus(cfg, nowMs) {
  try {
    const dir = path.join(control.resolveControlDir(cfg), 'status');
    fs.mkdirSync(dir, { recursive: true });
    const target = path.join(dir, 'agent-resource-controller.json');
    const tmp = `${target}.tmp.${process.pid}`;
    fs.writeFileSync(tmp, `${JSON.stringify({
      tool: 'agent-resource-controller', workload: 'dashboard',
      fresh_after_sec: DEFAULT_INTERVAL_MS / 1000,
      ts: new Date(nowMs).toISOString(),
    }, null, 2)}\n`);
    fs.renameSync(tmp, target);
  } catch {
    // status 失敗で制御判断まで止めない。
  }
}

function run(cfg = {}, nowMs = Date.now()) {
  const dir = budget.resolveBudgetDir(cfg);
  const config = budget.loadBudgetConfig(dir);
  const allocation = config.allocation || {};
  const intervalMs = Math.max(0, Number(allocation.rebalance_interval_sec) || 0) * 1000;
  const lastMs = Date.parse((config.computed || {}).computed_at || '');
  const rebalanceDue = allocation.mode === 'auto'
    && (!Number.isFinite(lastMs) || intervalMs === 0 || nowMs - lastMs >= intervalMs);
  if (rebalanceDue) budget.rebalance(cfg);
  const result = { rebalanced: rebalanceDue, ...profiles.apply(cfg) };
  writeStatus(cfg, nowMs);
  return result;
}

function start(loadConfig, intervalMs = DEFAULT_INTERVAL_MS) {
  const tick = () => {
    try { run(loadConfig()); }
    catch (err) { process.stderr.write(`agent-resource-control: ${err.message}\n`); }
  };
  tick();
  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === 'function') timer.unref();
  return timer;
}

function parseArgs(argv) {
  const cfg = { orchestration: {} };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--control-dir' && argv[i + 1]) cfg.orchestration.controlDir = argv[++i];
    else if (argv[i] === '--budget-dir' && argv[i + 1]) cfg.orchestration.budgetDir = argv[++i];
    else throw new Error(`不明な引数です: ${argv[i]}`);
  }
  return cfg;
}

if (require.main === module) {
  try {
    process.stdout.write(`${JSON.stringify(run(parseArgs(process.argv.slice(2))))}\n`);
  } catch (err) {
    process.stderr.write(`agent-resource-control: ${err.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = { run, start, parseArgs, DEFAULT_INTERVAL_MS };
