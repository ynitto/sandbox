'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const agentCli = require('../src/features/agent-project/main/agentCli');
const runner = require('../src/features/cowork/main/stateMachineRunner');

async function main() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-runner-'));
  const machineDir = path.join(repo, '.statemachine', 'one-step');
  fs.mkdirSync(path.join(machineDir, 'actions'), { recursive: true });
  fs.writeFileSync(path.join(machineDir, 'workflow.yaml'), [
    'name: one step',
    'initial_state: make',
    'config:',
    '  max_steps: 3',
    'states:',
    '  make:',
    '    action_file: actions/make.md',
    '    output_key: made',
    '    output_validator: "startswith:OK"',
    '  complete:',
    '    terminal: true',
    'transitions:',
    '  - from: make',
    '    to: complete',
    '    condition_rule: "startswith:last_output:OK"',
    '',
  ].join('\n'));
  fs.writeFileSync(path.join(machineDir, 'actions', 'make.md'), '`input.txt` を読み、out.txt を生成し、第1行に OK と返す。\n');
  fs.writeFileSync(path.join(repo, 'input.txt'), 'source\n');
  fs.writeFileSync(path.join(repo, 'worker.js'), "require('fs').writeFileSync(process.argv[2], 'done\\n'); console.log('created');\n");
  fs.writeFileSync(path.join(repo, 'fake-aider.js'), [
    "const at = process.argv.indexOf('--message');",
    "const prompt = at >= 0 ? process.argv[at + 1] : '';",
    "if (!process.argv.includes('--dry-run')) {",
    "  const fileAt = process.argv.indexOf('--file');",
    "  require('fs').writeFileSync(process.argv[fileAt + 1], 'done\\n');",
    "  console.log('aider warning\\nOK\\n\\npath: out.txt');",
    "} else if (prompt.includes('\\\"status\\\":0')) {",
    "  console.log(JSON.stringify({ type: 'write_files', paths: ['out.txt'] }));",
    "} else if (prompt.includes('TOOL_RESULT {\\\"rejected\\\":true')) {",
    "  console.log(JSON.stringify({ type: 'run', command: 'node', args: ['worker.js', 'out.txt'], timeout_sec: 5 }));",
    "} else {",
    "  console.log(JSON.stringify({ type: 'final', output: 'OK\\npath: out.txt' }));",
    "}",
    '',
  ].join('\n'));

  const spec = agentCli.normalize({
    name: 'fake-aider',
    command: [process.execPath, path.join(repo, 'fake-aider.js')],
    prompt_via: 'argv',
    prompt_flag: '--message',
    file_flag: '--file',
    read_flag: '--read',
    readonly_args: ['--dry-run'],
    readonly: 'enforced',
    timeout: 10,
  }, 'fake-aider', 'fake-aider.json');

  assert.throws(() => runner.projectPath(repo, '../outside'), /作業フォルダ外/);
  if (process.platform !== 'win32') {
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-outside-'));
    fs.symlinkSync(outside, path.join(repo, 'escape'));
    assert.throws(() => runner.projectPath(repo, 'escape/secret.txt'), /作業フォルダ外/);
  }
  assert.throws(() => runner.validateToolRequest({ type: 'run', command: 'bash', args: [] }, repo, []),
    /シェル/);
  const pythonScript = path.join(repo, 'tool.py');
  fs.writeFileSync(pythonScript, 'print("ok")\n');
  const pythonRequest = runner.validateToolRequest({ type: 'run', command: pythonScript, args: ['x'] }, repo, []);
  assert.match(path.basename(pythonRequest.command), /^python/);
  assert.strictEqual(pythonRequest.args[0], fs.realpathSync(pythonScript),
    '非実行のPythonスクリプトはPython経由にする');
  const siblingSkill = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-skill-'));
  const siblingScript = path.join(siblingSkill, 'tool.py');
  fs.writeFileSync(siblingScript, 'print("ok")\n');
  const siblingArg = path.relative(repo, siblingScript);
  assert.doesNotThrow(() => runner.validateToolRequest(
    { type: 'run', command: 'node', args: [siblingArg] }, repo, [{ root: siblingSkill }]
  ), 'ロード済みスキルへの相対パスは許可する');
  assert.strictEqual(runner.terminalStatus('complete', 'OK').ok, true);
  assert.strictEqual(runner.terminalStatus('failed', 'FETCH_FAILED').ok, false);
  assert.deepStrictEqual(
    runner.parseToolRequest('warning\n```json\n{"type":"final","output":"OK"}\n```\n'),
    { type: 'final', output: 'OK' }
  );
  assert.deepStrictEqual(
    runner.parseToolRequest('example {"type":"read_files","paths":["x"]}\nanswer {"type":"final","output":"OK"}'),
    { type: 'final', output: 'OK' },
    '説明中の契約例ではなく最後の完全な応答を選ぶ'
  );

  const result = await runner.runSkill({
    skillId: 'statemachine-use',
    cwd: repo,
    workflowPath: path.join(machineDir, 'workflow.yaml'),
    parameters: {},
    agent: { cli: 'aider', model: 'fake', spec, timeoutMs: 10000 },
  });
  assert.strictEqual(result.ok, true, result.error);
  assert.strictEqual(result.finalState, 'complete');
  assert.match(result.stdout, /^OK/);
  assert.strictEqual(fs.readFileSync(path.join(repo, 'out.txt'), 'utf8'), 'done\n');
  assert.ok(fs.existsSync(result.logFile), 'argv・cwd・結果を残すログがある');
  const events = fs.readFileSync(result.logFile, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
  assert.ok(events.some((event) => Array.isArray(event.argv)
    && event.argv.includes('--read') && event.argv.includes(fs.realpathSync(path.join(repo, 'input.txt')))),
  'アクションが参照する既存ファイルを Aider に割り当てる');
  console.log('ok - 未実行の成功申告を安全な書込へ補正し Python ハーネスで終端へ遷移する');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
