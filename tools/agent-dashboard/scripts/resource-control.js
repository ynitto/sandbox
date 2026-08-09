#!/usr/bin/env node
'use strict';

// Electron を起動せずに、dashboard と同じ資源制御モジュールを 1 回実行する。
const budget = require('../src/features/orchestration/main/budget');
const profiles = require('../src/features/orchestration/main/profiles');

function run(cfg = {}, nowMs = Date.now()) {
  const dir = budget.resolveBudgetDir(cfg);
  const config = budget.loadBudgetConfig(dir);
  const allocation = config.allocation || {};
  const intervalMs = Math.max(0, Number(allocation.rebalance_interval_sec) || 0) * 1000;
  const lastMs = Date.parse((config.computed || {}).computed_at || '');
  const rebalanceDue = allocation.mode === 'auto'
    && (!Number.isFinite(lastMs) || intervalMs === 0 || nowMs - lastMs >= intervalMs);
  if (rebalanceDue) budget.rebalance(cfg);
  return { rebalanced: rebalanceDue, ...profiles.apply(cfg) };
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

module.exports = { run, parseArgs };
