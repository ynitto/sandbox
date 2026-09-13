'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const store = require('../src/main/store');
const host = require('../src/main/host');
const tmux = require('../src/main/tmux');

function playwright() {
  try { return require('playwright'); } catch {}
  const prefix = path.dirname(path.dirname(process.execPath));
  try { return require(path.join(prefix, 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('実機: 会話・タスク・ワークフローのCLI IDがアプリ終了後のtmux再起動でも復元される', async (t) => {
  const pw = playwright();
  if (!pw || spawnSync('tmux', ['-V']).status !== 0) { t.skip('Playwright または tmux が無い'); return; }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-resume-e2e-'));
  const ud = path.join(root, 'userdata');
  const definitions = path.join(root, 'agents');
  fs.mkdirSync(definitions);
  const stub = path.join(root, 'stub.py');
  fs.writeFileSync(stub, `import json,sys,subprocess,uuid,os
args=sys.argv[1:]
cli=args.pop(0)
sid=''
for flag in ('resume','--resume','--resume-id','--session-id'):
    if flag in args: sid=args[args.index(flag)+1]
if not sid: sid=str(uuid.uuid4())
if cli=='codex':
    notify=json.loads(next(a[7:] for a in args if a.startswith('notify=')))
    subprocess.run(notify+[json.dumps({'type':'agent-turn-complete','thread-id':sid})])
if cli=='kiro':
    config=json.load(open(os.path.join(os.environ['KIRO_HOME'],'agents','agent-app.json')))
    for hook in config['hooks']['agentSpawn']:
        subprocess.run(hook['command'],shell=True,input=json.dumps({'session_id':sid}),text=True)
print('native-session='+sid,flush=True)
print('> ',flush=True)
for line in sys.stdin: print('> ',flush=True)
`);
  for (const cli of ['claude', 'copilot', 'codex', 'kiro']) {
    const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../agents', `${cli}.json`)));
    spec.command = ['python3', stub, cli];
    spec.interactive.command = ['python3', stub, cli];
    spec.interactive.ready_pattern = '^>[[:space:]]*$';
    spec.env = { CODEX_HOME: path.join(root, 'codex'), KIRO_HOME: path.join(root, 'kiro') };
    fs.writeFileSync(path.join(definitions, `${cli}.json`), JSON.stringify(spec));
  }
  store.saveConfig(ud, { repos: [root], lastRepo: root, area: 'work', share: { enabled: false } });
  const sessions = [];
  for (const kind of ['conversation', 'task', 'workflow']) for (const cli of ['claude', 'copilot', 'codex', 'kiro']) {
    sessions.push(store.createSession(ud, { repo: root, kind, cli, task: { machine: 'example' }, workflow: { id: 'example' } }));
  }
  const launch = () => pw._electron.launch({ executablePath: require('electron'),
    args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${ud}`],
    env: { ...process.env, KIRO_AGENTS_DIR: definitions },
  });
  let app;
  const saved = new Map();
  const shell = new host.HostShell();
  try {
    app = await launch();
    let win = await app.firstWindow();
    await win.waitForFunction(() => !!window.api);
    for (const session of sessions) {
      const opened = await win.evaluate((id) => Promise.all([api.termOpen(id), api.termOpen(id)]), session.id);
      assert.equal(opened.filter((item) => item.restarted).length, 1, '同時に開いても起動とID発行は1回だけ');
      const until = Date.now() + 10000;
      while (!store.cliEntry(store.readSession(ud, session.id), session.cli)?.id && Date.now() < until) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const entry = store.cliEntry(store.readSession(ud, session.id), session.cli);
      assert.ok(entry, JSON.stringify({ expected: session.cli, fromDisk: store.readSession(ud, session.id).cliSessions, fromApp: (await win.evaluate((id) => api.readSession(id), session.id)).cliSessions, userData: await app.evaluate(({app}) => app.getPath('userData')) }));
      saved.set(session.id, entry.id);
    }
    assert.equal(new Set(saved.values()).size, sessions.length, '同じリポジトリの別会話とIDを共有しない');
    await app.close();
    app = null;
    for (const session of sessions) await shell.run(tmux.cmdKill(tmux.sessionName(session.id)));
    app = await launch();
    win = await app.firstWindow();
    await win.waitForFunction(() => !!window.api);
    for (const session of sessions) {
      const result = await win.evaluate((id) => api.termOpen(id), session.id);
      assert.equal(result.restarted, true);
      assert.ok(result.argv.includes(saved.get(session.id)), `${session.kind}/${session.cli}: 保存IDで再開する`);
      assert.equal(store.cliEntry(store.readSession(ud, session.id), session.cli).id, saved.get(session.id));
    }
  } finally {
    if (app) await app.close();
    for (const session of sessions) {
      await shell.run(tmux.cmdKill(tmux.sessionName(session.id)));
      fs.rmSync(path.join(os.homedir(), '.local/state/agent-app/cli-sessions', session.id), { recursive: true, force: true });
    }
    shell.close();
  }
});
