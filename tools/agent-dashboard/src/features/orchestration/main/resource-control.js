'use strict';

// 資源制御の駆動体。dashboard（main.js）が常駐で回すのも、ヘッドレス CLI
// （scripts/resource-control.js）が 1 回だけ回すのも、このモジュール。
//
// ここは **src/ の中** に置く。以前は scripts/ に本体があり main.js がそれを require して
// いたが、scripts/ は package.json の build.files に含まれないので、dist:portable で
// 作った exe では起動直後に「Cannot find module」で落ちていた（開発起動では在るので
// 気づけない）。main プロセスから require するものは src/ から出さないこと。
// test/packaging-assets.test.js がこの規則を機械的に検査する。
const fs = require('fs');
const path = require('path');
const budget = require('./budget');
const control = require('./control');
const profiles = require('./profiles');

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
  const result = { rebalanced: rebalanceDue, ...profiles.apply(cfg, { nowMs }) };
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

module.exports = { run, start, parseArgs, DEFAULT_INTERVAL_MS };
