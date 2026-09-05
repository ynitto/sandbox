'use strict';

// statemachine-maker と agent-loop の境界。renderer や IPC にコマンドの綴りを広げない。

function machineName(value) {
  const name = String(value || '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name) || name === '.' || name === '..') {
    throw new Error(`識別名が不正です: ${name}`);
  }
  return name;
}

function runSpec({ root, machine, agent = '', model = '', parameters = {} }) {
  const workflow = `.statemachine/${machineName(machine)}/workflow.yaml`;
  const args = ['statemachine', '--workflow', workflow, '--dir', String(root || '')];
  if (agent) args.push('--agent-cli', String(agent));
  if (model) args.push('--model', String(model));
  for (const key of Object.keys(parameters || {}).sort()) {
    const value = parameters[key];
    if (value == null) continue;
    args.push('--param', `${key}=${value}`);
  }
  return { command: 'agent-loop', args };
}

async function inspect({ root, capture }) {
  const repository = String(root || '');
  const result = await capture(
    'agent-loop',
    ['inspect', '--json', '--dir', repository],
    { cwd: repository, timeoutMs: 15000 },
  );
  if (!result.ok) {
    return {
      available: false,
      machines: [],
      history: [],
      daemon: { running: false },
      error: '実行基盤に接続できませんでした',
    };
  }
  try {
    return JSON.parse(result.stdout);
  } catch (err) {
    throw new Error('実行情報の応答を読み取れませんでした', { cause: err });
  }
}

async function saveSchedule({ root, payload, capture }) {
  const repository = String(root || '');
  const result = await capture(
    'agent-loop',
    ['schedule', '--json', '--dir', repository],
    { cwd: repository, timeoutMs: 15000, input: JSON.stringify(payload) },
  );
  let response;
  try {
    response = JSON.parse(result.stdout || '{}');
  } catch (err) {
    throw new Error('保存結果の応答を読み取れませんでした', { cause: err });
  }
  if (!result.ok) {
    throw new Error(response.error || result.error || result.stderr || '定期実行を保存できませんでした');
  }
  return response;
}

function parseResult(stdout, code) {
  const lines = String(stdout || '').split(/\r?\n/).filter((line) => line.startsWith('RESULT '));
  if (lines.length) {
    try {
      const value = JSON.parse(lines[lines.length - 1].slice('RESULT '.length));
      if (value && typeof value === 'object' && !Array.isArray(value)) return value;
    } catch { /* 下で欠落結果として扱う */ }
  }
  return { ok: false, error: `実行結果を確認できませんでした（終了コード ${code == null ? '?' : code}）` };
}

function startDaemon({ root, startDetached }) {
  const repository = String(root || '');
  return startDetached('agent-loop', ['--no-auto-attach'], { cwd: repository });
}

async function stopDaemon({ root, capture }) {
  const repository = String(root || '');
  const result = await capture('agent-loop', ['drain'], { cwd: repository, timeoutMs: 15000 });
  if (!result.ok) throw new Error(result.error || result.stderr || '自動実行を停止できませんでした');
  return { stopped: true };
}

async function readLog({ root, identity, capture }) {
  const repository = String(root || '');
  const result = await capture(
    'agent-loop', ['log', '--json', '--dir', repository],
    { cwd: repository, timeoutMs: 15000, input: JSON.stringify(identity) },
  );
  let response;
  try { response = JSON.parse(result.stdout || '{}'); } catch (err) {
    throw new Error('ログの応答を読み取れませんでした', { cause: err });
  }
  if (!result.ok) throw new Error(response.error || result.error || result.stderr || 'ログを開けませんでした');
  return response;
}

module.exports = { runSpec, inspect, saveSchedule, parseResult, startDaemon, stopDaemon, readLog };
