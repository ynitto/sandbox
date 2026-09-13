'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { prepare } = require('../src/main/automation/tmux-run');

function fixture(overrides = {}) {
  const calls = [];
  let conv;
  class Conversation {
    constructor(opts) { this.opts = opts; conv = this; }
    async open() { calls.push('open'); }
    async waitReady() { calls.push('ready'); }
    async send(prompt, done) { calls.push(prompt); this.done = done; }
    async kill() { calls.push('kill'); }
    async keys(data) { calls.push(data); }
    async resize() {}
    async scroll() {}
    async capture() { return { ok: false }; }
  }
  const deps = {
    load: () => ({ interactive: {} }),
    interactiveCmd: (_spec, opts) => { calls.push(opts); return { argv: ['fake'], env: {} }; },
    hostOf: (root) => ({ cwd: root, distro: 'target', shell: {} }),
    probe: async () => ({ ok: true, tmux: '/bin/tmux' }),
    Conversation, compilePatterns: () => ({}), ...overrides,
  };
  return { deps, calls, conv: () => conv };
}

test('manual run opens an interactive writable terminal and delivers screens and completion', async () => {
  const f = fixture();
  const screens = [], exits = [];
  const run = await prepare({ root: '/repo', agent: 'fake', model: 'model', prompt: 'run task', requestId: 'r',
    onScreen: (p) => screens.push(p), onExit: (p) => exits.push(p) }, f.deps);
  await run.start();
  assert.deepEqual(f.calls.slice(1), ['open', 'ready', 'run task']);
  assert.equal(f.calls[0].readonly, false);
  assert.equal(f.calls[0].autoApprove, true);
  f.conv().opts.emit('term:screen', { text: 'working', cursor: { x: 2, y: 1 } });
  assert.equal(screens[0].text, 'working');
  await run.keys('yes\r');
  await f.conv().done({ text: 'done' });
  assert.equal(exits[0].code, 0);
  assert.equal(exits[0].stdout, 'done');
  assert.equal(f.calls.at(-1), 'kill');
});

test('missing tmux and noninteractive definitions fall back without launching a CLI', async () => {
  for (const overrides of [{ probe: async () => ({ ok: true, tmux: '' }) }, { load: () => ({}) }]) {
    const f = fixture(overrides);
    const run = await prepare({ root: '/repo', agent: 'fake' }, f.deps);
    assert.equal(run.start, undefined);
    assert.ok(run.warning);
    assert.equal(f.conv(), undefined);
  }
});

test('stop during startup never submits the task and completes once', async () => {
  const f = fixture();
  const exits = [];
  const run = await prepare({ root: '/repo', agent: 'fake', prompt: 'must not send', onExit: (p) => exits.push(p) }, f.deps);
  await run.stop();
  await run.start();
  assert.ok(!f.calls.includes('must not send'));
  assert.equal(exits.length, 1);
  assert.equal(exits[0].code, 1);
});

test('startup failure is reported and the pane is cleaned up', async () => {
  const f = fixture();
  const exits = [];
  const run = await prepare({ root: '/repo', agent: 'fake', onExit: (p) => exits.push(p) }, f.deps);
  f.conv().open = async () => { throw new Error('broken shell'); };
  await run.start();
  assert.equal(exits[0].code, 1);
  assert.match(exits[0].stderr, /broken shell/);
  assert.equal(f.calls.at(-1), 'kill');
});

test('scroll and resize wait for pane creation even when the terminal is already visible', async () => {
  const f = fixture();
  const run = await prepare({ root: '/repo', agent: 'fake', prompt: 'run' }, f.deps);
  let opened = false;
  let release;
  f.conv().open = () => new Promise((resolve) => { release = () => { opened = true; resolve(); }; });
  f.conv().scroll = async () => {
    if (!opened) throw new Error("||||||| \x1ecan't find pane: agent-app-run");
    return 'scrolled';
  };
  f.conv().resize = async () => {
    assert.ok(opened, 'ペインが作られてからリサイズする');
    return 'resized';
  };
  const starting = run.start();
  const scrolling = run.scroll(1).catch((error) => error.message);
  const resizing = run.resize(80, 24).catch((error) => error.message);
  release();
  await starting;
  assert.equal(await scrolling, 'scrolled');
  assert.equal(await resizing, 'resized');
  await run.stop();
});

test('pane cleanup suppresses an in-flight scroll error and late screen updates', async () => {
  const f = fixture();
  const screens = [];
  const run = await prepare({ root: '/repo', agent: 'fake', prompt: 'run', onScreen: (p) => screens.push(p) }, f.deps);
  await run.start();
  let rejectScroll;
  f.conv().scroll = () => new Promise((_resolve, reject) => { rejectScroll = reject; });
  const scrolling = run.scroll(1).catch((error) => error.message);
  await new Promise(setImmediate);
  await f.conv().done({ text: 'done' });
  rejectScroll(new Error("||||||| \x1ecan't find pane: agent-app-run"));
  assert.equal(await scrolling, false);
  f.conv().opts.emit('term:screen', { text: 'stale screen' });
  assert.deepEqual(screens, []);
});

test('completed and stopped runs scroll captured history after the pane is removed', async () => {
  for (const stop of [false, true]) {
    const f = fixture();
    const screens = [];
    const run = await prepare({ root: '/repo', agent: 'fake', prompt: 'run', onScreen: (p) => screens.push(p) }, f.deps);
    await run.start();
    f.conv().capture = async (opts) => {
      assert.equal(opts.history, true);
      assert.equal(opts.joinHistory, false);
      return { ok: true, screen: { cols: 80, rows: 5, cursor: { x: 1, y: 4 }, text: Array.from({ length: 20 }, (_, i) => `line ${i}`).join('\n') } };
    };
    if (stop) await run.stop(); else await f.conv().done({ text: 'done' });
    assert.equal(f.calls.at(-1), 'kill');
    await run.scroll(-100);
    assert.equal(screens.at(-1)?.text, 'line 0\nline 1\nline 2\nline 3\nline 4');
    assert.equal(screens.at(-1).scrollOffset, 15);
    await run.scroll(100);
    assert.match(screens.at(-1).text, /line 19$/);
    await run.resize(60, 8);
    assert.equal(screens.at(-1).rows, 8);
    assert.equal(await run.keys('must not send'), false);
    assert.ok(!f.calls.includes('must not send'));
  }
});

test('real tmux: manual run shows the CLI screen, finishes, and removes its pane', { timeout: 20000 }, async (t) => {
  const { spawnSync } = require('node:child_process');
  if (process.platform === 'win32' || spawnSync('tmux', ['-V']).status !== 0) return t.skip('tmux が無い');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const tmux = require('../src/main/tmux');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-manual-run-'));
  const script = path.join(root, 'cli.sh');
  fs.writeFileSync(script, `stty -echo
for ((i=1; i<=80; i++)); do printf '\\033[32mhistory-%s\\033[0m\\n' "$i"; done
printf 'manual test ready\\n> '
IFS= read -r line
printf '\\nworking (esc to interrupt)\\n'
sleep 0.6
printf '\\033[1A\\r\\033[2Kanswer: %s\\n> ' "$line"
while IFS= read -r line; do :; done
`);
  const requestId = require('node:crypto').randomUUID();
  const screens = [];
  let finish;
  const done = new Promise((resolve) => { finish = resolve; });
  const run = await prepare({ root, agent: 'fake', requestId, prompt: 'manual task',
    onScreen: (screen) => screens.push(screen.text), onExit: finish }, {
    load: () => ({ interactive: { readyPattern: '^>[[:space:]]*$', busyPattern: 'esc to interrupt', readyTimeoutSec: 5 } }),
    interactiveCmd: () => ({ argv: ['bash', script] }),
  });
  t.after(async () => {
    if (run.stop) await run.stop();
    require('../src/main/host').closeAll();
    fs.rmSync(root, { recursive: true, force: true });
  });
  assert.ok(run.start, run.warning);
  await run.start();
  const result = await done;
  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /answer: manual task/);
  assert.ok(screens.some((screen) => screen.includes('manual test ready')));
  assert.ok(screens.some((screen) => screen.includes('answer: manual task')));
  assert.notEqual(spawnSync('tmux', ['-L', tmux.SOCKET, 'has-session', '-t', tmux.sessionName(`run-${requestId}`)]).status, 0);
  await run.scroll(-1000);
  assert.match(screens.at(-1), /history-1\b/);
  assert.match(screens.at(-1), /\x1b\[/, '履歴の色を保持する');
  await run.scroll(1000);
  assert.match(screens.at(-1), /answer: manual task/);
});
