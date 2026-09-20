'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const store = require('../src/main/store');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('Electron: automatic choice launches the selected model and persists across turns; settings stay in one dialog', { timeout: 120000 }, async t => {
  const pw = playwright(); if (!pw?._electron) return t.skip('Playwright unavailable');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-selection-'));
  const data = path.join(root, 'data'), definitions = path.join(root, 'agents');
  fs.mkdirSync(definitions);
  const stub = path.join(root, 'stub.js');
  fs.writeFileSync(stub, "process.stdin.resume(); process.stdin.on('end', () => setTimeout(() => console.log('answer from '+process.argv.slice(2).join(' ')), 200));");
  for (const cli of ['alpha', 'beta']) fs.writeFileSync(path.join(definitions, `${cli}.json`), JSON.stringify({ name: cli, command: [process.execPath, stub, cli], model_flag: '--model', default_model: cli + '-model', headless_autonomy: 'tool-loop', relative_cost: 1 }));
  store.saveConfig(data, { repos: [root], lastRepo: root, lastCli: 'alpha', transport: 'headless', useWorktree: false,
    allocation: { mode: 'auto' }, execution: { tiers: { small: { cli: 'alpha', model: 'alpha-model' }, medium: { cli: 'alpha', model: 'alpha-model' }, large: { cli: 'beta', model: 'beta-model' } } },
    audit: { enabled: false }, evaluation: { mode: 'off' }, share: { enabled: false }, update: { onStartup: false } });
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`], env: { ...process.env, KIRO_AGENTS_DIR: definitions } });
  try {
    const win = await app.firstWindow(); win.setDefaultTimeout(15000);
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function');
    await app.evaluate(({ app }) => {
      const req = process.getBuiltinModule('module').createRequire(`${app.getAppPath()}/package.json`);
      req('./src/main/agents').listAgents = async () => ['alpha', 'beta'].map(name => ({ name, available: true, interactive: false }));
      req('./src/main/audit').Auditor.prototype.limits = async () => ({ agentLimits: [] });
      const runner = req('./src/main/automation/runner'), original = runner.capture;
      global.selectionCalls = [];
      runner.capture = async (name, args, opts) => {
        if (name === 'agent-loop' && args[0] === 'inspect') return { ok: true, stdout: JSON.stringify({ available: true, tasks: [{ id: 'prompt:demo', kind: 'prompt', entry: { prompt: '手動実行の依頼' } }], machines: [], history: [] }) };
        if (name !== 'agent-herd' || args[0] !== 'select') return original(name, args, opts);
        global.selectionCalls.push({ args, input: opts.input });
        return { ok: true, stdout: JSON.stringify({ selected: { agent_cli: 'beta', model: 'beta-model' }, stage: 'jev' }) };
      };
      const stream = runner.stream;
      runner.stream = (command, args, opts) => {
        global.manualLaunch = { command, args };
        return stream(command, args, { ...opts, onExit: result => { global.manualFinished = true; opts.onExit?.(result); } });
      };
    });
    await win.reload();
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function' && !state.agentsLoading);
    await win.click('#settings-open');
    await win.getByRole('tab', { name: '実行制御', exact: true }).click();
    assert.equal(await win.inputValue('#usage-mode'), 'auto');
    assert.equal(await win.locator('#tier-large-model').isEnabled(), true);
    assert.match(await win.textContent('.execution-models th'), /自動選択の候補/);
    assert.match(await win.textContent('#usage-allocation-state'), /依頼内容から/);
    assert.equal(await win.locator('dialog[open]').count(), 1);
    await win.screenshot({ path: '/tmp/agent-app-jev-settings.png' });
    await win.setViewportSize({ width: 375, height: 900 });
    assert.equal(await win.locator('.settings-content').evaluate(n => n.scrollWidth <= n.clientWidth + 1), true);
    await win.screenshot({ path: '/tmp/agent-app-jev-settings-narrow.png' });
    await win.setViewportSize({ width: 1280, height: 900 });
    await win.click('#settings-close');
    await win.click('#session-new');
    assert.match(await win.textContent('#run-settings-summary'), /自動選択.*依頼内容から/);
    await win.fill('#prompt', '依頼に合わせてモデルを選んで');
    await win.click('#send');
    await win.waitForFunction(() => state.current?.modelSelection?.cli === 'beta' && !state.pending.size);
    const id = await win.evaluate(() => state.current.id);
    await win.waitForFunction(() => !state.running.size);
    let saved = store.readSession(data, id);
    assert.equal(saved.model, 'beta-model');
    assert.equal(saved.modelSelection.stage, 'jev');
    assert.match(saved.messages.at(-1).text, /answer from beta --model beta-model/);
    assert.match(await win.textContent('#run-settings-summary'), /beta/);
    await win.fill('#prompt', '続けて');
    await win.click('#send');
    await win.waitForFunction(() => state.current?.messages.length >= 4 && !state.running.size);
    assert.equal(await app.evaluate(() => global.selectionCalls.length), 1);
    saved = store.readSession(data, id);
    assert.equal(saved.messages.at(-1).model, 'beta-model');
    await win.screenshot({ path: '/tmp/agent-app-jev-result.png' });
    const manual = await win.evaluate(root => api.automation.runStart({ root, taskId: 'prompt:demo', mode: 'run', agent: 'alpha', model: 'alpha-model', policy: 'recommended' }), root);
    assert.ok(manual.executionInformation.some(item => item.title.includes('beta / beta-model')));
    const launch = await app.evaluate(() => global.manualLaunch);
    assert.equal(launch.args[launch.args.indexOf('--model') + 1], 'beta-model');
    assert.equal(await app.evaluate(() => global.selectionCalls.length), 2);
    for (let attempt = 0; attempt < 50; attempt++) {
      if (await app.evaluate(() => !!global.manualFinished)) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(await app.evaluate(() => !!global.manualFinished), true);
    const prepared = await win.evaluate(root => api.automation.teachPrepare({ repo: root, machine: 'select-test', purpose: '選択の確認', policy: 'recommended', allocation: 'auto' }), root);
    assert.equal(prepared.session.allocation, 'auto');
    const direct = await win.evaluate(root => api.automation.teachPrepare({ repo: root, machine: 'select-test', policy: 'direct', cli: 'beta', model: 'beta-model' }), root);
    assert.equal(direct.session.cli, 'beta');
    assert.equal(direct.session.allocation, '');
    const renewed = await win.evaluate(root => api.automation.teachPrepare({ repo: root, machine: 'select-test', policy: 'recommended', allocation: 'auto', newSession: true }), root);
    assert.equal(renewed.session.allocation, 'auto');
    assert.notEqual(renewed.session.id, prepared.session.id);
    await app.evaluate(({ app }) => {
      const req = process.getBuiltinModule('module').createRequire(`${app.getAppPath()}/package.json`);
      req('./src/main/automation/runner').capture = async (name, args, opts) => {
        if (args[0] !== 'select') return { ok: false, stdout: '' };
        global.waitingForSelection = true;
        return new Promise(resolve => opts.signal.addEventListener('abort', () => resolve({ ok: false, stdout: '' }), { once: true }));
      };
    });
    await win.click('#session-new');
    await win.fill('#prompt', '停止する依頼');
    await win.click('#send');
    await win.waitForFunction(() => state.pending.size > 0);
    // Wait until the stub has the real main-process AbortSignal.
    for (let attempt = 0; attempt < 50; attempt++) {
      if (await app.evaluate(() => !!global.waitingForSelection)) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(await app.evaluate(() => !!global.waitingForSelection), true);
    await win.click('#stop');
    await win.waitForFunction(() => state.pending.size === 0);
    const cancelled = store.readSession(data, await win.evaluate(() => state.current.id));
    assert.equal(cancelled.messages.length, 0);
    assert.equal(cancelled.modelSelection, undefined);
    assert.equal(await win.inputValue('#prompt'), '停止する依頼');
  } finally { await app.close(); fs.rmSync(root, { recursive: true, force: true }); }
});
