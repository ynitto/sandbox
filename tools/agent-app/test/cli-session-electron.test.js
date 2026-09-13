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

test('実機: CLI IDをtmux再起動後に復元し、編集再開の説明を必要な場合だけ送る', async (t) => {
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
for line in sys.stdin:
    with open(os.environ['TEST_INPUT_LOG'], 'a') as log: log.write(json.dumps({'sid':sid,'text':line})+'\\n')
    print('> ',flush=True)
`);
  for (const cli of ['claude', 'copilot', 'codex', 'kiro']) {
    const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../agents', `${cli}.json`)));
    spec.command = ['python3', stub, cli];
    spec.interactive.command = ['python3', stub, cli];
    spec.interactive.ready_pattern = '^>[[:space:]]*$';
    spec.env = { TEST_INPUT_LOG: path.join(root, 'input.jsonl'), CODEX_HOME: path.join(root, 'codex'), KIRO_HOME: path.join(root, 'kiro') };
    fs.writeFileSync(path.join(definitions, `${cli}.json`), JSON.stringify(spec));
  }
  store.saveConfig(ud, { repos: [root], lastRepo: root, area: 'work', share: { enabled: false } });
  const sessions = [];
  for (const kind of ['conversation', 'task', 'workflow']) for (const cli of ['claude', 'copilot', 'codex', 'kiro']) {
    sessions.push(store.createSession(ud, { repo: root, kind, cli, task: { machine: `example-${cli}` }, workflow: { id: `example-${cli}` } }));
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
    for (const session of sessions) {
      await shell.run(tmux.cmdKill(tmux.sessionName(session.id)));
      store.appendMessage(ud, session.id, { role: 'user', text: '以前の依頼' });
      store.appendMessage(ud, session.id, { role: 'assistant', text: '以前の回答' });
      store.setCliEntry(ud, session.id, session.cli, { seen: 2 });
    }
    app = await launch();
    win = await app.firstWindow();
    await win.waitForFunction(() => !!window.api);
    for (const session of sessions) {
      const result = await win.evaluate((id) => api.termOpen(id), session.id);
      assert.equal(result.restarted, true);
      assert.ok(result.argv.includes(saved.get(session.id)), `${session.kind}/${session.cli}: 保存IDで再開する`);
      assert.equal(store.cliEntry(store.readSession(ud, session.id), session.cli).id, saved.get(session.id));
      if (session.kind !== 'conversation') {
        const result = await win.evaluate(({ kind, repo, cli }) => {
          const payload = { repo, purpose: '既存の編集', machine: `example-${cli}`, workflowId: `example-${cli}` };
          return kind === 'task' ? api.automation.teachStart(payload) : api.automation.flowTeachStart(payload);
        }, { kind: session.kind, repo: root, cli: session.cli });
        assert.equal(result.started, false, `${session.kind}/${session.cli}: 復元時は再開プロンプトを送らない`);
        assert.equal(store.readSession(ud, session.id).messages.length, 2);
      }
    }
    // 復元後の明示指示・未共有の差分、新規／IDなしの初期説明も実際の入力で確かめる。
    for (const kind of ['task', 'workflow']) {
      for (const scenario of ['context', 'unseen', 'initial', 'no-id']) {
        let session = sessions.find((item) => item.kind === kind && item.cli === (scenario === 'context' ? 'codex' : 'claude'));
        if (scenario === 'initial' || scenario === 'no-id') {
          session = store.createSession(ud, { repo: root, kind, cli: 'claude',
            task: { machine: `${kind}-${scenario}` }, workflow: { id: `${kind}-${scenario}` } });
          sessions.push(session);
          if (scenario === 'no-id') store.appendMessage(ud, session.id, { role: 'user', text: '復元IDのない以前の依頼' });
        }
        if (scenario === 'unseen') store.appendMessage(ud, session.id, { role: 'user', text: '別エージェントで決めた変更' });
        const before = store.readSession(ud, session.id).messages.length;
        const result = await win.evaluate(({ kind, payload }) => kind === 'task'
          ? api.automation.teachStart(payload) : api.automation.flowTeachStart(payload), {
          kind, payload: { repo: root, machine: session.task?.machine, workflowId: session.workflow?.id,
            purpose: 'テストの作成', context: scenario === 'context' ? '工程2を修正する' : '' },
        });
        assert.equal(result.started, true, `${kind}/${scenario}: 必要な依頼を送る`);
        const until = Date.now() + 10000;
        while (store.readSession(ud, session.id).messages.length < before + 2 && Date.now() < until) {
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const current = store.readSession(ud, session.id);
        assert.equal(current.messages.length, before + 2, '応答完了まで記録する');
        const sent = fs.readFileSync(path.join(root, 'input.jsonl'), 'utf8').trim().split('\n')
          .map((line) => JSON.parse(line)).filter((line) => line.sid === store.cliEntry(current, session.cli).id)
          .map((line) => line.text).join('');
        if (scenario === 'context') {
          assert.match(sent, /今回の編集対象: 工程2を修正する/);
          assert.doesNotMatch(sent, /以前の依頼|まず内容を読み直し|まず workflow.yaml/);
        } else if (scenario === 'unseen') {
          assert.match(sent, /別エージェントで決めた変更/);
          assert.doesNotMatch(sent, /以前の依頼|まず内容を読み直し|まず workflow.yaml/);
        } else if (scenario === 'no-id') {
          assert.match(sent, /復元IDのない以前の依頼/);
          assert.match(sent, /下書き作成を再開/);
        } else {
          assert.ok(current.messages[before].text.length > 100, '初回の作成手順を省かない');
        }
      }
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
