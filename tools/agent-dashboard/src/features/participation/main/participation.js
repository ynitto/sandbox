'use strict';

const crypto = require('crypto');
const os = require('os');
const childProcess = require('child_process');
const model = require('../model');

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}

function wslDistro(value) {
  const match = String(value || '').replace(/\//g, '\\')
    .match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  return match ? match[1] : '';
}

function toWslPath(value) {
  const source = String(value || '');
  const normalized = source.replace(/\//g, '\\');
  const unc = normalized.match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (unc) return (unc[1] || '').replace(/\\/g, '/') || '/';
  const drive = normalized.match(/^([A-Za-z]):(\\.*)?$/);
  if (drive) return `/mnt/${drive[1].toLowerCase()}${(drive[2] || '').replace(/\\/g, '/')}`;
  return source;
}

function buildFlowWorkerLaunch(options = {}) {
  const busDir = String(options.busDir || '');
  const projectDir = String(options.projectDir || '');
  const runId = String(options.runId || '');
  const nodeId = String(options.nodeId || 'dashboard-worker');
  const workerArgs = [
    '--bus', busDir, '--run-id', runId, 'work', '--node-id', nodeId, '--idle-exit',
  ];
  if (options.platform !== 'win32') {
    return { command: 'agent-flow', args: workerArgs, cwd: projectDir || undefined, wsl: false };
  }

  const distro = wslDistro(busDir) || wslDistro(projectDir);
  const linuxBus = toWslPath(busDir);
  const linuxProject = toWslPath(projectDir);
  const cd = linuxProject ? `cd ${shellQuote(linuxProject)} && ` : '';
  const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}`
    + `command -v agent-flow >/dev/null 2>&1 || { echo 'WSLにagent-flowがインストールされていません' >&2; exit 127; }; `
    + `exec agent-flow --bus ${shellQuote(linuxBus)} --run-id ${shellQuote(runId)} `
    + `work --node-id ${shellQuote(nodeId)} --idle-exit`;
  return {
    command: 'wsl.exe',
    args: [...(distro ? ['-d', distro] : []), '-e', 'sh', '-lc', script],
    cwd: undefined,
    wsl: true,
  };
}

function safeNodePart(value) {
  return String(value || '').replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'pc';
}

function startFlowWorker(payload = {}, deps = {}) {
  const busDir = String(payload.busDir || '');
  const projectDir = String(payload.projectDir || '');
  const runId = String(payload.runId || '');
  if (!busDir) throw new Error('参加先の実行データが見つかりません');
  if (!runId) throw new Error('参加する仕事が指定されていません');
  const platform = deps.platform || process.platform;
  const hostname = safeNodePart(deps.hostname || os.hostname());
  const nextId = deps.nextId || (() => crypto.randomBytes(3).toString('hex'));
  const nodeId = `dashboard-${hostname}-${safeNodePart(nextId())}`;
  const launch = buildFlowWorkerLaunch({ platform, busDir, projectDir, runId, nodeId });
  const spawn = deps.spawn || childProcess.spawn;
  if (launch.wsl) {
    const spawnSync = deps.spawnSync || childProcess.spawnSync;
    const probe = spawnSync(launch.command, [
      ...launch.args.slice(0, -1),
      'command -v agent-flow >/dev/null 2>&1',
    ], { encoding: 'buffer', timeout: 8000, windowsHide: true });
    if (probe.error) {
      return Promise.reject(new Error('WSLが利用できません。WindowsのWSL設定を確認してください'));
    }
    if (probe.status !== 0) {
      return Promise.reject(new Error(
        'WSLにagent-flowがインストールされていません。WSL内のPATHとインストールを確認してください'
      ));
    }
  }

  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(launch.command, launch.args, {
        cwd: launch.cwd,
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      });
    } catch (error) {
      reject(error);
      return;
    }
    child.once('error', reject);
    child.once('spawn', () => {
      child.unref();
      resolve({ started: true, pid: child.pid || 0, runId, nodeId });
    });
  });
}

module.exports = {
  flowCandidates: model.flowCandidates,
  amigosCandidates: model.amigosCandidates,
  buildFlowWorkerLaunch,
  startFlowWorker,
  toWslPath,
  wslDistro,
};
