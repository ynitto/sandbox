'use strict';

// statemachine-maker と agent-loop の境界。renderer や IPC にコマンドの綴りを広げない。

function machineName(value) {
  const name = String(value || '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name) || name === '.' || name === '..') {
    throw new Error(`識別名が不正です: ${name}`);
  }
  return name;
}

function runSpec({ root, machine, agent = '', model = '', parameters = {}, instruction = '', allowShells = [] }) {
  const workflow = `.statemachine/${machineName(machine)}/workflow.yaml`;
  const args = ['statemachine', '--workflow', workflow, '--dir', String(root || '')];
  if (agent) args.push('--agent-cli', String(agent));
  if (model) args.push('--model', String(model));
  if (instruction) args.push('--instruction', String(instruction));
  // シェルの許可はこの 1 実行の引数として渡す（渡さなければ harness は全部拒否）。
  for (const shell of allowShells || []) args.push('--allow-shell', String(shell));
  for (const key of Object.keys(parameters || {}).sort()) {
    const value = parameters[key];
    if (value == null) continue;
    args.push('--param', `${key}=${value}`);
  }
  return { command: 'agent-loop', args };
}

function taskPrompt(task, instruction = '') {
  const item = task && typeof task === 'object' ? task : {};
  if (item.kind !== 'prompt') throw new Error('このタスクはプロンプト型ではありません');
  const entry = item.entry && typeof item.entry === 'object' ? item.entry : {};
  const prompt = String(entry.prompt || item.description || '').trim();
  if (!prompt) throw new Error('手動実行するプロンプトがありません');
  return [String(instruction || '').trim(), prompt].filter(Boolean).join('\n\n');
}

function taskRunSpec({ root, task, agent = '', model = '', parameters = {}, instruction = '', allowShells = [] }) {
  const item = task && typeof task === 'object' ? task : {};
  if (item.kind === 'statemachine' || item.machine) {
    return runSpec({ root, machine: item.machine, agent, model, parameters, instruction, allowShells });
  }
  if (item.kind === 'command') {
    const name = String(item.entry?.name || item.name || '').trim();
    if (!name) throw new Error('実行するコマンドタスクの名前がありません');
    const args = ['command', '--entry', name, '--dir', String(root || '')];
    if (item.source?.path) args.push('--config', String(item.source.path));
    return { command: 'agent-loop', args };
  }
  if (item.kind !== 'prompt') throw new Error('このタスクは手動実行できません');
  const text = taskPrompt(item, instruction);
  const args = ['run', text];
  if (agent) args.push('--agent-cli', String(agent));
  if (model) args.push('--model', String(model));
  args.push('--dir', String(root || ''));
  return { command: 'agent-loop', args };
}

async function mutateTask({ root, payload, capture }) {
  const snapshot = await inspect({ root, capture });
  if (snapshot.available === false) throw new Error(snapshot.error);
  if (!snapshot.capabilities?.taskMutation) throw new Error('タスクの編集・削除には agent-loop の更新が必要です');
  const result = await capture('agent-loop', ['task', '--json', '--dir', String(root || '')], {
    cwd: String(root || ''), timeoutMs: 15000, input: JSON.stringify(payload),
  });
  let response;
  try { response = JSON.parse(result.stdout || '{}'); } catch { throw new Error('タスクの更新結果を読み取れませんでした'); }
  if (!result.ok || (!response.saved && !response.deleted)) throw new Error(response.error || result.error || result.stderr || 'タスクを更新できませんでした');
  return response;
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
      error: ['agent-loop inspect に失敗しました' + (result.status != null ? `（終了コード ${result.status}）` : ''), String(result.error || result.stderr || '').trim().slice(-4000)].filter(Boolean).join(': '),
    };
  }
  try {
    return JSON.parse(result.stdout);
  } catch (err) {
    throw new Error('実行情報の応答を読み取れませんでした: ' + String(result.stderr || result.stdout || 'agent-loop inspect の出力なし').trim().slice(-1000), { cause: err });
  }
}

async function saveSchedule({ root, payload, capture }) {
  const repository = String(root || '');
  if (payload && ('command' in payload || payload.entry && 'command' in payload.entry)) {
    const snapshot = await inspect({ root: repository, capture });
    if (snapshot.available === false) {
      throw new Error(`agent-loop に接続できないため保存できません。Windowsでは選択したWSL環境を確認してください（入力内容は保持しています）。${snapshot.error || ''}`);
    }
    if (snapshot.capabilities?.commandSchedule !== true) {
      throw new Error('実行環境の agent-loop が古いため、コマンドを保存できません。agent-loop を更新してから再度保存してください（入力内容は保持しています）');
    }
    const command = payload.command ?? payload.entry?.command;
    const argv = command && typeof command === 'object' && !Array.isArray(command) ? command.argv : command;
    if (typeof argv === 'string' && argv.split(/\r?\n/).filter((line) => line.trim()).length > 1
      && snapshot.capabilities?.commandSequence !== true) {
      throw new Error('複数行のコマンド実行には実行環境の agent-loop の更新が必要です（入力内容は保持しています）');
    }
  }
  if (payload?.schedule?.kind === 'preserve' && !('command' in (payload.entry || {}))
    || payload?.agentCli === '' && payload?.entry?.agent_cli) {
    const snapshot = await inspect({ root: repository, capture });
    if (snapshot.capabilities?.partialSchedule !== true) {
      throw new Error('詳細設定の部分編集とエージェントの既定値への変更には、実行環境の agent-loop の更新が必要です（入力内容は保持しています）');
    }
  }
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

module.exports = { machineName, runSpec, taskPrompt, taskRunSpec, mutateTask, inspect, saveSchedule, parseResult, startDaemon, stopDaemon, readLog };
