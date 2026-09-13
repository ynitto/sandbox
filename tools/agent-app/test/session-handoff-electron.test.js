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

async function until(read, description) {
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    if (await read()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(description);
}

test('Electron: 会話の要約引き継ぎと、タスク・ワークフローの新しい編集セッション', async (t) => {
  const pw = playwright();
  if (!pw || spawnSync('tmux', ['-V']).status !== 0) { t.skip('Playwright または tmux が無い'); return; }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-handoff-e2e-'));
  const ud = path.join(root, 'userdata');
  const definitions = path.join(root, 'agents');
  fs.mkdirSync(definitions);
  const stub = path.join(root, 'stub.py');
  fs.writeFileSync(stub, `import sys,os,json,time
if sys.argv[1]=='summary':
    source=sys.stdin.read()
    open(os.environ['SUMMARY_INPUT'],'w').write(source)
    if os.path.exists(os.environ['FAIL_SUMMARY']):
        print('要約失敗',file=sys.stderr)
        sys.exit(1)
    print('目的: 画面を統一する。決定事項: 日本語で実装。次の作業: 検証を続ける。')
    sys.exit(0)
if os.path.exists(os.environ['WAIT_READY']):
    print('Do you trust this folder? [y/n]',flush=True)
    while os.path.exists(os.environ['WAIT_READY']): time.sleep(0.05)
    print('\\033[2J\\033[H',end='',flush=True)
print('> ',flush=True)
for line in sys.stdin: print('> ',flush=True)
`);
  const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../agents/claude.json')));
  spec.command = ['python3', stub, 'summary'];
  spec.interactive.command = ['python3', stub, 'interactive'];
  spec.interactive.ready_pattern = '^>[[:space:]]*$';
  spec.env = { WAIT_READY: path.join(root, 'wait-ready'), SUMMARY_INPUT: path.join(root, 'summary.txt'), FAIL_SUMMARY: path.join(root, 'fail-summary') };
  fs.writeFileSync(path.join(definitions, 'claude.json'), JSON.stringify(spec));
  store.saveConfig(ud, { repos: [root], lastRepo: root, area: 'work', share: { enabled: false } });
  require('../src/main/automation/store').save(root, {
    name: '編集対象', machine: 'edit-target', purpose: 'タスクを編集する',
    steps: [{ kind: 'agent', title: '確認', detail: '確認する' }],
  });
  require('../src/main/automation/flow-store').save(root, {
    version: 2, id: 'edit-flow', name: '編集対象', description: 'ワークフローを編集する',
    purpose: 'implementation', entry: ['review'], exit: ['review'],
    nodes: [{ id: 'review', label: '確認', kind: 'work', goal: '{{request}} を確認する', deps: [], tier: 'auto' }],
  }, 'create');
  const sessions = ['conversation', 'task', 'workflow'].map((kind) => {
    const session = store.createSession(ud, { repo: root, kind, cli: 'claude',
      task: { machine: 'edit-target' }, workflow: { id: 'edit-flow' } });
    store.appendMessage(ud, session.id, { role: 'user', text: '旧セッションだけの長い依頼' });
    store.appendMessage(ud, session.id, { role: 'assistant', text: '日本語で実装すると決定した' });
    store.setCliEntry(ud, session.id, 'claude', { id: session.id, seen: 2 });
    return session;
  });
  const shell = new host.HostShell();
  let app;
  try {
    app = await pw._electron.launch({ executablePath: require('electron'),
      args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${ud}`],
      env: { ...process.env, KIRO_AGENTS_DIR: definitions },
    });
    const win = await app.firstWindow();
    await win.waitForFunction(() => !!window.api && !!window.TaskTeaching);
    const errors = [];
    win.on('pageerror', (error) => errors.push(error.message));
    await win.evaluate((id) => openSessionInRepo(state.repo, id), sessions[0].id);
    await app.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      window.setMinimumSize(400, 400);
      window.setSize(520, 800);
    });
    await until(() => win.evaluate(() => innerWidth <= 520), '狭い幅に変更する');
    assert.equal(await win.evaluate(() => {
      const head = document.getElementById('chat-head');
      return head.scrollWidth <= head.clientWidth;
    }), true, '引き継ぎボタンが狭い画面でもはみ出さない');
    await win.screenshot({ path: path.join(root, 'handoff-narrow.png') });
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 900));
    fs.writeFileSync(path.join(root, 'wait-ready'), 'wait');
    await win.locator('#session-handoff').click();
    await until(() => win.evaluate((id) => state.current?.id !== id, sessions[0].id), '新しい会話に切り替わる');
    const current = await win.evaluate(() => state.current);
    await until(() => win.evaluate((id) => Term.current() === id, current.id), '入力受付を待たずに新しい端末を表示する');
    assert.ok(store.readSession(ud, current.id).messages.length, '起動待ちの間も要約を保存している');
    fs.unlinkSync(path.join(root, 'wait-ready'));
    await until(() => win.evaluate(() => !state.handoffId), '引き継ぎ依頼を送信する');
    assert.equal(current.origin.sessionId, sessions[0].id);
    assert.match(current.messages[0].text, /目的: 画面を統一する/);
    assert.doesNotMatch(current.messages[0].text, /旧セッションだけの長い依頼/);
    assert.match(fs.readFileSync(path.join(root, 'summary.txt'), 'utf8'), /旧セッションだけの長い依頼/);
    assert.equal(await win.evaluate((id) => state.sessions.some((s) => s.id === id), current.id), true);
    assert.match(await win.locator('#chat-title').textContent(), /引き継ぎ/);
    await until(() => store.readSession(ud, current.id).messages.length >= 3, '引き継ぎプロンプトの応答が終わる');
    assert.notEqual(store.cliEntry(store.readSession(ud, current.id), 'claude').id, sessions[0].id);

    // 要約できなければ新しい会話を作らず、元の履歴を維持する。
    fs.writeFileSync(path.join(root, 'fail-summary'), 'fail');
    const count = store.listSessions(ud, root).length;
    await assert.rejects(win.evaluate((id) => api.handoffSession(id), sessions[0].id), /要約失敗/);
    assert.equal(store.listSessions(ud, root).length, count);
    fs.unlinkSync(path.join(root, 'fail-summary'));

    for (const kind of ['task', 'workflow']) {
      const original = sessions.find((s) => s.kind === kind);
      await win.evaluate(({ kind, root }) => {
        if (kind === 'task') TaskTeaching.show({ root, machine: 'edit-target', editing: true, published: true });
        else FlowTeaching.show({ root, workflowId: 'edit-flow', existing: true });
      }, { kind, root });
      await until(() => win.evaluate((kind) => !!(kind === 'task' ? TaskTeaching : FlowTeaching).state.availableSession, kind), '既存の編集セッションを読み込む');
      const button = kind === 'task' ? 'task-new-session' : 'flow-teach-new-session';
      await win.evaluate((id) => document.getElementById(id).click(), button);
      await until(() => win.evaluate(({ kind, id }) => {
        const s = (kind === 'task' ? TaskTeaching : FlowTeaching).state;
        return s.session && s.session.id !== id && !s.pending;
      }, { kind, id: original.id }), '新しい編集セッションに切り替わる');
      const next = await win.evaluate((kind) => (kind === 'task' ? TaskTeaching : FlowTeaching).state.session, kind);
      await until(() => store.readSession(ud, next.id).messages.length >= 2, '初回編集プロンプトの応答が終わる');
      const saved = store.readSession(ud, next.id);
      assert.doesNotMatch(saved.messages[0].text, /旧セッションだけ|日本語で実装すると決定/);
      assert.match(saved.messages[0].text, kind === 'task' ? /edit-target/ : /edit-flow/);
      assert.notEqual(store.cliEntry(saved, 'claude').id, original.id);
      store.updateSession(ud, original.id, { title: '古い会話を後から更新' });
      const view = await win.evaluate(({ kind, root }) => kind === 'task'
        ? api.automation.teachPrepare({ repo: root, machine: 'edit-target' })
        : api.automation.flowTeachPrepare({ repo: root, workflowId: 'edit-flow' }), { kind, root });
      assert.equal(view.session.id, next.id);
      assert.equal(store.readSession(ud, original.id).messages.length, 2);
    }
    await win.evaluate((root) => {
      TaskTeaching.show({ root, creating: true, editing: true });
      FlowTeaching.show({ root, creating: true });
    }, root);
    assert.equal(await win.locator('#task-new-session').isVisible(), false);
    assert.equal(await win.locator('#flow-teach-new-session').isVisible(), false);
    assert.deepEqual(errors, []);
  } finally {
    if (app) await app.close();
    for (const session of store.listSessions(ud, root, { kind: '' })) {
      await shell.run(tmux.cmdKill(tmux.sessionName(session.id)));
      fs.rmSync(path.join(os.homedir(), '.local/state/agent-app/cli-sessions', session.id), { recursive: true, force: true });
    }
    shell.close();
  }
});
