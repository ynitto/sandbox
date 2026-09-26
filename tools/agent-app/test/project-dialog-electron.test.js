'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const APP = path.resolve(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('project dialog: shared knowledge/work repo, import names, results and runnable workflows', async t => {
  const pw = playwright();
  if (!pw?._electron) return t.skip('Electron unavailable');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'project-dialog-'));
  const data = path.join(dir, 'userdata');
  const workflowName = '未完了_' + 'long_workflow_name_'.repeat(12);
  const kb = path.join(dir, 'long-knowledge-repository-name-for-projects'), otherKb = path.join(dir, 'another-knowledge-repository');
  const repo = path.join(dir, 'long-application-repository-name'), source = path.join(dir, 'agent-project-source');
  for (const folder of [kb, otherKb, repo, source]) fs.mkdirSync(folder);
  require('../src/main/automation/store').save(otherKb, {
    name: '配置確認', machine: 'layout-check', purpose: 'UI確認',
    steps: [{ kind: 'agent', title: '確認', detail: '確認する' }],
  });
  fs.writeFileSync(path.join(source, 'charter.md'), '# 過去の仕事\n');
  fs.mkdirSync(path.join(source, 'archive'));
  fs.writeFileSync(path.join(source, 'archive', '実行結果 [1].md'), '# 過去の実行結果\nテストは成功しました。\n');
  fs.mkdirSync(path.join(source, 'backlog'));
  fs.writeFileSync(path.join(source, 'backlog', 'todo.md'), `# ${workflowName}\n追加する作業\n`);
  const store = require('../src/main/store');
  store.saveConfig(data, { repos: [kb, otherKb, repo], knowledgeRepos: [kb, otherKb], lastRepo: repo, area: 'projects',
    useWorktree: false, transport: 'headless', share: { enabled: false } });
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', `--user-data-dir=${data}`] });
  try {
    const win = await app.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('project-new')?.onclick === 'function' && state.area === 'projects');
    await app.evaluate(({ dialog }, source) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [source] });
    }, source);
    await win.evaluate(() => document.getElementById('project-new').click());
    await win.locator('#project-dialog').waitFor({ state: 'visible' });
    await win.locator('#project-name').fill('新しい 名前');
    assert.equal(await win.locator('#project-kb-path').textContent(), `${kb}/projects/新しい-名前/`);
    const dimensions = await win.evaluate(() => {
      const body = document.querySelector('#project-dialog .dlg-body').getBoundingClientRect();
      return ['project-kb', 'project-add-repo'].map(id => document.getElementById(id).getBoundingClientRect().width / body.width);
    });
    assert.ok(dimensions.every(ratio => ratio > .8), 'both selectors use the available width');
    await win.locator('#project-add-repo').selectOption(repo);
    await win.locator('#project-add').click();
    await win.waitForFunction(() => document.querySelector('#project-repos select'));
    assert.equal(await win.locator('#project-repos select option:checked').textContent(), '作業用');
    assert.deepEqual(await win.locator('#project-repos select option').allTextContents(), ['作業用', '参照専用']);
    assert.equal(await win.locator('#project-repos input[type=radio]').isChecked(), true);
    assert.ok((await win.locator('#project-repos').textContent()).includes(repo));
    assert.equal(await win.locator('#project-kb-path').isVisible(), true);
    assert.equal(await win.locator('#project-instructions').isVisible(), true);
    assert.equal(await win.locator('#project-error').isVisible(), false, 'origin is not required');
    for (const width of [1440, 1024, 768, 375]) {
      await win.setViewportSize({ width, height: 850 });
      assert.equal(await win.locator('#project-dialog .dlg-body').evaluate(n => n.scrollWidth > n.clientWidth), false, 'no horizontal scroll at ' + width);
    }
    await win.setViewportSize({ width: 1360, height: 900 });
    await win.screenshot({ path: '/tmp/agent-app-project-create-dialog.png' });
    const identity = await win.evaluate(async repo => {
      const first = await api.projects.remote(repo), second = await api.projects.remote(repo);
      return { first, second };
    }, repo);
    assert.ok(identity.first.localId);
    assert.equal(identity.second.localId, identity.first.localId, 'same local folder keeps its identity');
    await win.locator('#project-add-repo').selectOption(kb);
    await win.locator('#project-add').click();
    await win.waitForFunction(() => document.querySelectorAll('#project-repos select').length === 2);
    assert.equal(await win.locator('#project-add-repo option').evaluateAll(nodes => nodes.some(node => node.value === document.getElementById('project-kb').value)), false, 'already added repository is excluded');
    await win.locator('#project-repos input[type=radio]').nth(1).check();
    assert.equal(await win.locator('#project-repos input[type=radio]').first().isChecked(), false);
    await win.locator('#project-repos select').nth(1).selectOption('reference');
    assert.equal(await win.locator('#project-repos input[type=radio]').first().isChecked(), true, 'sole working repo becomes default');
    await win.locator('#project-save').click();
    await win.locator('#project-dialog').waitFor({ state: 'hidden' });
    await win.waitForFunction(repo => state.repo === repo && state.config.lastProject.endsWith('#新しい-名前'), repo);
    const saved = require('../src/main/projects').read(`${kb}#新しい-名前`).project;
    assert.equal(saved.repos[0].localId, identity.first.localId);
    assert.equal(saved.repos[0].url, undefined);
    assert.equal(saved.repos.length, 2);
    assert.equal(saved.repos[1].label, path.basename(kb));
    assert.ok(!JSON.stringify(saved).includes(repo), 'shared project definition contains no local absolute path');
    await win.evaluate(() => document.getElementById('project-edit').click());
    assert.equal(await win.locator('#project-repos select').first().inputValue(), 'work');
    assert.ok((await win.locator('#project-repos').textContent()).includes(repo), 'local folder resolves on reopening');
    await win.locator('#project-close').click();
    // 実行基盤への投入まで通し、外部のAI起動だけを差し替える。
    await app.evaluate(({ app }, { APP, dir }) => {
      const require = process.getBuiltinModule('module').createRequire(`${app.getAppPath()}/package.json`);
      const path = require('path');
      process.env.AGENT_APP_FLOW_BUS = path.join(dir, 'bus');
      process.env.AGENT_APP_FLOW_LOGS = path.join(dir, 'logs');
      const flow = require(path.join(APP, 'src/main/automation/agent-flow'));
      flow.context = async ({ root }) => ({ root, agents: ['codex'], defaults: { agent: 'codex' }, workspace: { ok: false, reason: 'test repository' }, tools: { agentFlow: { ok: true } }, capabilities: {} });
      flow.catalog = async () => ({ kinds: require(path.join(APP, 'src/main/automation/flow-model')).KIND_INFOS, patterns: [] });
      require(path.join(APP, 'src/main/automation/runner')).startDetached = async (...args) => { globalThis.workflowLaunch = args; return { pid: 1 }; };
    }, { APP, dir });
    await win.evaluate(async root => { await selectRepo(root); await showArea('workflows'); await syncAutomationWorkbench(); }, otherKb);
    await win.waitForFunction(() => document.getElementById('automation-workbench').shadowRoot.textContent.includes('ワークフロー'));
    await win.locator('#area-projects').click();
    await win.evaluate(() => document.getElementById('project-new').click());

    await win.locator('#project-name').fill('取り込み前の名前');
    await win.locator('#project-import').click();
    await win.locator('#project-summary').waitFor({ state: 'visible' });
    assert.equal(await win.locator('#project-name').inputValue(), '取り込み前の名前', 'import does not overwrite the entered name');
    assert.equal(await win.locator('#project-kb').isEnabled(), true);
    await win.locator('#project-kb').selectOption(otherKb);
    await win.locator('#project-name').fill('取り込み / 記録');
    const destination = `${otherKb}/projects/取り込み---記録/`;
    assert.equal(await win.locator('#project-kb-path').textContent(), destination);
    const summary = await win.locator('#project-summary').textContent();
    assert.match(summary, /1 資料 → 1 文書/);
    assert.match(summary, /成果・教訓/);
    assert.match(summary, /未完了タスク/);
    assert.equal(await win.locator('#project-dialog details').count(), 0);
    await win.getByRole('button', { name: '内容を選ぶ', exact: true }).click();
    await win.locator('#project-import-search').fill('未完了');
    assert.equal(await win.locator('#project-import-candidates input').count(), 1);
    await win.locator('#project-import-candidates input').check();
    await win.getByRole('button', { name: `${workflowName} の本文`, exact: true }).click();
    assert.match(await win.locator('#project-import-preview').textContent(), /追加する作業/);
    for (const width of [1440, 1024, 768, 375]) {
      await win.setViewportSize({ width, height: 850 });
      assert.equal(await win.locator('#project-import-picker .dlg-body').evaluate(n => n.scrollWidth > n.clientWidth), false, 'picker fits at ' + width);
    }
    await win.setViewportSize({ width: 1360, height: 900 });
    await win.screenshot({ path: '/tmp/agent-app-project-import-selection.png' });
    await win.locator('#project-import-search').fill('');
    await win.locator('#project-import-recommended').click();
    assert.match(await win.locator('#project-import-selection-count').textContent(), /1 \/ 2/);
    await win.locator('#project-import-picker-close').click();
    await win.locator('#project-import-group-pending').check();
    for (const width of [1440, 1024, 768, 375]) {
      await win.setViewportSize({ width, height: 850 });
      const layout = await win.locator('#project-kb-path').evaluate(n => ({
        overflow: n.scrollWidth > n.clientWidth, whiteSpace: getComputedStyle(n).whiteSpace,
        text: n.textContent, width: n.getBoundingClientRect().width,
      }));
      assert.equal(layout.overflow, false, 'path wraps at ' + width);
      assert.equal(layout.text, destination);
      assert.ok(layout.width > 0);
      assert.equal(await win.locator('#project-save').isVisible(), true);
    }
    await win.setViewportSize({ width: 1360, height: 900 });
    await win.screenshot({ path: '/tmp/agent-app-project-import-dialog.png' });
    await win.locator('#project-save').click();
    await win.locator('#project-dialog').waitFor({ state: 'hidden' });
    await win.waitForFunction(() => document.querySelector('#repo-select').selectedOptions[0]?.textContent === '取り込み / 記録');
    assert.ok(fs.existsSync(path.join(destination, 'outcomes.md')));
    assert.equal(fs.existsSync(path.join(destination, 'pending.md')), true);
    assert.equal(require('../src/main/projects').read(`${otherKb}#取り込み---記録`).project.name, '取り込み / 記録');
    assert.ok(fs.existsSync(path.join(source, 'archive', '実行結果 [1].md')), 'source is preserved');
    assert.equal(fs.existsSync(path.join(kb, 'projects', '取り込み---記録')), false, 'destination can be changed');
    await win.getByRole('button', { name: '索引を開く', exact: true }).click();
    await win.locator('#viewer-body a').filter({ hasText: '成果・教訓' }).click();
    await win.waitForFunction(() => document.querySelector('#viewer-body').textContent.includes('テストは成功しました'));
    assert.match(await win.locator('#viewer-body').textContent(), /過去の実行結果/);
    await win.locator('#area-projects').click();
    await win.getByRole('button', { name: workflowName, exact: true }).waitFor({ state: 'visible' });
    for (const width of [1440, 1024, 768, 375]) {
      await win.setViewportSize({ width, height: 900 });
      assert.equal(await win.locator('.project-home').evaluate(n => n.scrollWidth > n.clientWidth), false, 'project workflow list fits at ' + width);
    }
    await win.setViewportSize({ width: 1360, height: 900 });
    await win.getByRole('button', { name: workflowName, exact: true }).click();
    await win.waitForFunction(() => state.area === 'workflows');
    await win.locator('[data-flow-request]').waitFor({ state: 'visible' });
    assert.match(await win.locator('[data-flow-request]').inputValue(), /追加する作業/);
    const workflows = await win.evaluate(root => api.automation.flowList(root), otherKb);
    assert.equal(workflows.length, 1);
    const flow = await win.evaluate(({ root, id }) => api.automation.flowRead(root, id), { root: otherKb, id: workflows[0].id });
    assert.deepEqual(flow.workflow.nodes.map(node => node.kind), ['work', 'verify']);
    for (const width of [1440, 1024, 768, 375]) {
      await win.setViewportSize({ width, height: 900 });
      assert.equal(await win.locator('.execution-title').evaluate(n => n.scrollWidth > n.clientWidth), false, 'workflow title fits at ' + width);
    }
    await win.setViewportSize({ width: 1360, height: 900 });
    assert.equal(await win.locator('.flow-overview h3').first().textContent(), '実行');
    const runPosition = await win.locator('[data-flow-start]').evaluate(button => {
      const row = button.closest('.run-toolbar').getBoundingClientRect(), b = button.getBoundingClientRect();
      return Math.abs(row.right - b.right);
    });
    assert.ok(runPosition < 2, 'workflow run button aligns right');
    await win.locator('[data-flow-tab="steps"]').click();
    await win.locator('.flow-editor-toolbar').waitFor();
    const headerGap = await win.locator('.flow-editor-toolbar').evaluate(async header => {
      const pane = header.closest('.machine-pane');
      pane.scrollTop = 400;
      await new Promise(requestAnimationFrame);
      return header.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    });
    await win.screenshot({ path: '/tmp/agent-app-flow-steps-top.png' });
    assert.ok(Math.abs(headerGap) < 2, 'sticky steps header touches top: ' + headerGap);
    await win.screenshot({ path: '/tmp/agent-app-flow-steps-top.png' });
    await win.locator('[data-flow-test]').click();
    await win.locator('[data-flow-start]').waitFor();
    await win.screenshot({ path: '/tmp/agent-app-imported-workflow.png' });
    await win.locator('[data-flow-start]').click();
    await win.waitForFunction(() => document.getElementById('automation-workbench').shadowRoot.textContent.includes('起動中'));
    const launch = await app.evaluate(() => globalThis.workflowLaunch);
    assert.equal(launch[0], 'agent-flow');
    assert.equal(launch[2].cwd, otherKb);
    const inboxFiles = fs.readdirSync(path.join(dir, 'bus', 'inbox'));
    const inbox = JSON.parse(fs.readFileSync(path.join(dir, 'bus', 'inbox', inboxFiles[0]), 'utf8'));
    assert.match(inbox.request, /追加する作業/);
    assert.equal(inbox.submitter_context.workflow, workflows[0].id);
    assert.deepEqual(inbox.plan.nodes[1].deps, ['work']);
    await win.locator('#area-tasks').click();
    await win.locator('#tasks .list-pick').filter({ hasText: '配置確認' }).click();
    await win.locator('.run-card #task-run-settings').waitFor();
    assert.equal(await win.locator('.run-card #run-start, .run-card #run-stop').count(), 2);
    const taskLayout = await win.locator('.run-card').evaluate(card => {
      const r = card.getBoundingClientRect(), t = card.querySelector('.run-toolbar').getBoundingClientRect();
      return t.top > r.top && t.bottom < r.bottom;
    });
    assert.equal(taskLayout, true);
    await win.screenshot({ path: '/tmp/agent-app-task-run-inside.png' });
  } finally {
    await app.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
