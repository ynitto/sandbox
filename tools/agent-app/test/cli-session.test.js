'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const script = path.join(__dirname, '../src/main/cli-session.py');

function run(...args) {
  const result = spawnSync('python3', [script, ...args], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim() ? JSON.parse(result.stdout) : null;
}

test('Codex の完了通知で会話固有のIDを記録し、既存の通知も実行する', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-session-'));
  const capture = path.join(root, 'capture.json');
  const chained = path.join(root, 'chained.json');
  fs.writeFileSync(capture, JSON.stringify({ token: 'generation-one', id: '' }));
  const payload = JSON.stringify({ type: 'agent-turn-complete', 'thread-id': 'codex-thread-one' });
  run('record', capture, 'generation-one', 'codex', JSON.stringify(['python3', '-c',
    'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])', chained]), payload);
  assert.equal(JSON.parse(fs.readFileSync(capture)).id, 'codex-thread-one');
  assert.equal(fs.readFileSync(chained, 'utf8'), payload);
});

 test('古い起動の通知は新しい起動のIDを上書きせず、別会話にも混ざらない', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-stale-'));
  const a = path.join(root, 'a.json');
  const b = path.join(root, 'b.json');
  fs.writeFileSync(a, JSON.stringify({ token: 'new', id: 'current' }));
  fs.writeFileSync(b, JSON.stringify({ token: 'other', id: 'other-session' }));
  run('record', a, 'old', 'kiro', '[]', JSON.stringify({ session_id: 'old-session' }));
  run('record', a, 'new', 'kiro', '[]', JSON.stringify({ session_id: '../invalid' }));
  assert.equal(JSON.parse(fs.readFileSync(a)).id, 'current');
  assert.equal(JSON.parse(fs.readFileSync(b)).id, 'other-session');
  run('record', a, 'new', 'kiro', '[]', JSON.stringify({ session_id: 'new-session' }));
  assert.equal(JSON.parse(fs.readFileSync(a)).id, 'new-session');
});

test('Kiro は元のカスタムエージェントを保ち、起動・完了hookでIDを記録する', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-kiro-'));
  const home = path.join(root, 'kiro');
  const runtime = path.join(root, 'runtime');
  fs.mkdirSync(path.join(home, 'agents'), { recursive: true });
  fs.writeFileSync(path.join(home, 'agents', 'custom.json'), JSON.stringify({
    name: 'custom', prompt: 'keep prompt', tools: ['read'], hooks: { agentSpawn: [{ command: 'original' }] },
  }));
  const prepared = run('prepare', JSON.stringify({ cli: 'kiro', argv: ['kiro-cli', 'chat', '--agent', 'custom'],
    env: { KIRO_HOME: home }, cwd: root, runtime, token: 'kiro-one', chained: [] }));
  const agent = JSON.parse(fs.readFileSync(path.join(prepared.env.KIRO_HOME, 'agents', 'agent-app.json')));
  assert.equal(agent.prompt, 'keep prompt');
  assert.deepEqual(agent.tools, ['read']);
  assert.equal(agent.hooks.agentSpawn[0].command, 'original');
  assert.equal(agent.hooks.agentSpawn.length, 2);
  assert.equal(agent.hooks.stop.length, 1);
  assert.deepEqual(prepared.argv, ['kiro-cli', 'chat', '--agent', 'agent-app']);
  assert.equal(JSON.parse(fs.readFileSync(path.join(home, 'agents', 'custom.json'))).hooks.agentSpawn.length, 1);
  const event = spawnSync('bash', ['-c', agent.hooks.agentSpawn[1].command], {
    encoding: 'utf8', input: JSON.stringify({ session_id: 'kiro-saved-id' }),
  });
  assert.equal(event.status, 0, event.stderr);
  assert.equal(JSON.parse(fs.readFileSync(prepared.file)).id, 'kiro-saved-id');
});

test('Codex 起動に通知を接続し、保存されたIDで特定の会話を再開する', async () => {
  const capture = require('../src/main/cliSession');
  const agentCli = require('../src/main/agentCli');
  const store = require('../src/main/store');
  const { HostShell } = require('../src/main/host');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-resume-'));
  const codexHome = path.join(root, 'codex-home');
  fs.mkdirSync(codexHome);
  fs.writeFileSync(path.join(codexHome, 'config.toml'), 'notify = ["existing-notifier"]');
  const session = store.createSession(root, { repo: root, cli: 'codex' });
  const shell = new HostShell();
  try {
    const launch = await capture.prepare({ shell, home: root, id: session.id, cli: 'codex', cwd: root,
      argv: ['codex'], env: { CODEX_HOME: codexHome } });
    const notify = JSON.parse(launch.argv.at(-1).slice('notify='.length));
    assert.deepEqual(JSON.parse(notify.at(-1)), ['existing-notifier']);
    const result = await shell.exec([...notify, JSON.stringify({ type: 'agent-turn-complete', 'thread-id': 'saved-codex-id' })]);
    assert.equal(result.ok, true, result.error);
    const sid = await capture.read({ shell, home: root, id: session.id, cli: 'codex' });
    store.setCliEntry(root, session.id, 'codex', { id: sid });
    const restored = store.readSession(root, session.id);
    const cmd = agentCli.interactiveCmd(agentCli.load('codex', root), { cliSession: store.cliEntry(restored, 'codex').id });
    assert.deepEqual(cmd.argv.slice(0, 3), ['codex', 'resume', 'saved-codex-id']);
    assert.equal(cmd.resumed, true);
  } finally { shell.close(); }
});

test('通知設定はプロファイルと明示指定を尊重し、不正な設定では起動を書き換えない', async () => {
  const capture = require('../src/main/cliSession');
  const { HostShell } = require('../src/main/host');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-profile-'));
  const home = path.join(root, 'codex');
  fs.mkdirSync(home);
  fs.writeFileSync(path.join(home, 'config.toml'), 'notify = ["base"]\n[profiles.legacy]\nnotify = ["legacy"]');
  fs.writeFileSync(path.join(home, 'work.config.toml'), 'notify = ["work", "with space"]');
  const shell = new HostShell();
  const options = { shell, home: root, cwd: root, id: 'profile-test', cli: 'codex', env: { CODEX_HOME: home } };
  try {
    for (const [args, expected] of [
      [['-p', 'work'], ['work', 'with space']],
      [['--profile=legacy'], ['legacy']],
      [['--profile', 'work', '-c', 'notify=["explicit"]'], ['explicit']],
      [['--config=notify=[]'], []],
    ]) {
      const prepared = await capture.prepare({ ...options, argv: ['codex', ...args] });
      assert.deepEqual(JSON.parse(JSON.parse(prepared.argv.at(-1).slice(7)).at(-1)), expected);
    }
    await assert.rejects(capture.prepare({ ...options, argv: ['codex', '-p', '../other'] }), /プロファイル/);
    await assert.rejects(capture.prepare({ ...options, argv: ['codex', '-c', 'notify="invalid"'] }), /通知設定/);
    const plain = await capture.prepare({ ...options, cli: 'custom', argv: ['custom'] });
    assert.deepEqual(plain.argv, ['custom']);
    assert.equal(await capture.read({ ...options, cli: 'custom' }), '');
    assert.equal(await capture.read({ ...options, id: 'missing' }), '');
    await assert.rejects(capture.read({ ...options, id: '../unsafe' }), /記録先/);
    const file = path.join(root, '.local/state/agent-app/cli-sessions/profile-test/codex/session.json');
    fs.writeFileSync(file, 'not json');
    assert.equal(await capture.read(options), '');
    fs.writeFileSync(file, JSON.stringify({ id: '../unsafe' }));
    assert.equal(await capture.read(options), '');
  } finally { shell.close(); }
});

test('Kiro の既定エージェント設定も継承する', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-cli-kiro-default-'));
  const home = path.join(root, 'kiro');
  fs.mkdirSync(path.join(home, 'agents'), { recursive: true });
  fs.mkdirSync(path.join(home, 'settings'));
  fs.writeFileSync(path.join(home, 'settings/cli.json'), JSON.stringify({ 'chat.defaultAgent': 'reviewer' }));
  fs.writeFileSync(path.join(home, 'agents/reviewer.json'), JSON.stringify({ name: 'reviewer', prompt: 'review instructions' }));
  const prepared = run('prepare', JSON.stringify({ cli: 'kiro', argv: ['kiro-cli', 'chat'],
    env: { KIRO_HOME: home }, cwd: root, runtime: path.join(root, 'runtime'), token: 'default', chained: [] }));
  const agent = JSON.parse(fs.readFileSync(path.join(prepared.env.KIRO_HOME, 'agents/agent-app.json')));
  assert.equal(agent.prompt, 'review instructions');
});
