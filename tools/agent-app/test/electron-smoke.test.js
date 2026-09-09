'use strict';

// Electron を実際に起動し、親画面→タスク／ワークフロー iframe→IPC の境界を通す。
// DOM 単体では contextBridge やフレーム間の API 参照切れを検出できないため、
// 登録済みリポジトリのワークフローが表示されるところまで確認する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..');

function electronBinary() {
  try {
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const nodePrefix = path.dirname(path.dirname(process.execPath));
  try { return require(path.join(nodePrefix, 'lib', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright-core')); } catch { /* 次を試す */ }
  try { return require('/opt/node22/lib/node_modules/playwright'); } catch { return null; }
}

test('実機: 会話・タスク・ワークフローを移動し、登録済み項目を開ける', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (!pw || !pw._electron) { t.skip('Playwright の Electron ドライバが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い'); return; }

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-userdata-'));
  const appStore = require('../src/main/store');
  const machineStore = require('../src/main/automation/store');
  const flowStore = require('../src/main/automation/flow-store');
  machineStore.save(repo, {
    name: 'リリース確認', machine: 'release-check', purpose: '自動化統合の確認',
    steps: [{ kind: 'agent', title: '変更を確認', detail: '公開前の変更を確認する' }],
  });
  flowStore.save(repo, {
    version: 2, id: 'parallel-review', name: '並列レビュー', description: '複数の観点で変更を確認する',
    purpose: 'implementation', entry: ['review'], exit: ['review'],
    nodes: [{ id: 'review', label: '変更を確認', kind: 'work', goal: '{{request}} を確認する', deps: [], tier: 'auto' }],
  }, 'create');
  const flowBus = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-flow-bus-'));
  const flowRunId = 'app-history-test';
  const flowRun = path.join(flowBus, 'runs', flowRunId);
  fs.mkdirSync(path.join(flowBus, 'inbox'), { recursive: true });
  fs.mkdirSync(path.join(flowRun, 'results'), { recursive: true });
  fs.writeFileSync(path.join(flowBus, 'inbox', `${flowRunId}.json`), JSON.stringify({
    id: flowRunId, title: '以前の並列レビュー', request: '過去の変更を確認する', submitter: 'agent-app',
    readonly: true, submitted_at: '2026-09-06T01:00:00Z',
    submitter_context: { root: repo, workflow: 'parallel-review', parameters: {}, agent: 'codex', model: 'gpt-test' },
  }));
  fs.writeFileSync(path.join(flowRun, 'meta.json'), JSON.stringify({
    status: 'done', created_at: '2026-09-06T01:00:00Z', updated_at: '2026-09-06T01:01:00Z', request: '過去の変更を確認する',
  }));
  fs.writeFileSync(path.join(flowRun, 'graph.json'), JSON.stringify({
    nodes: { review: { id: 'review', kind: 'work', goal: '過去の変更を確認する', deps: [] } },
  }));
  fs.writeFileSync(path.join(flowRun, 'results', 'review.json'), JSON.stringify({
    status: 'done', output: '確認済み', finished_at: '2026-09-06T01:01:00Z',
  }));
  process.env.AGENT_APP_FLOW_BUS = flowBus;
  appStore.saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'work' });
  const session = appStore.createSession(userData, {
    repo, cli: 'codex', model: 'gpt-test', policy: 'quality', tier: 'large', transport: 'headless',
  });
  appStore.appendMessage(userData, session.id, { role: 'user', text: '画面を確認して' });
  appStore.appendMessage(userData, session.id, {
    role: 'assistant', cli: 'codex', model: 'gpt-test', text: '確認できました。', elapsedMs: 1200,
    parts: {
      thinking: [{ text: '関連する画面を確認した', status: 'done' }],
      information: [{ type: 'file', title: 'src/renderer.js', action: 'modified', status: 'success' }],
    },
  });
  for (let index = 0; index < 60; index += 1) {
    appStore.appendMessage(userData, session.id, {
      role: 'user', cli: 'codex', model: 'gpt-test',
      text: `スクロール確認 ${index + 1}: ${'履歴を十分に長くする。'.repeat(8)}`,
    });
  }

  // 偽の agent-herd と agent-flow を PATH に置く。一族（aider / ollama）が「使える」印になり `herd` が並び
  // 「エージェントを最適化する」が効く側（節約・品質重視・small / large tier）を実機で通せる。効かない側は
  // 設定のチェックを外して確かめる（agent-herd が無いのと同じ動き）。agent-flow は標準パターンの一覧だけ
  // 答え、サイドバーの「ワークフロー」を押せるようにする（実行は bus のファイルで見る）。agent-loop は置かない
  // ——履歴タブと定期実行のカードが薄くなる側を通すため。
  const fakeBin = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-fakebin-'));
  fs.writeFileSync(path.join(fakeBin, 'agent-herd'), '#!/bin/sh\nexit 0\n');
  fs.writeFileSync(path.join(fakeBin, 'agent-flow'), '#!/bin/sh\ncase "$1" in patterns) echo "[]";; esac\nexit 0\n');
  for (const name of ['agent-herd', 'agent-flow']) fs.chmodSync(path.join(fakeBin, name), 0o755);
  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, PATH: `${fakeBin}${path.delimiter}${process.env.PATH || ''}` },
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
    win.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

    await win.waitForSelector('#area-tasks');
    await win.waitForFunction(() => typeof document.getElementById('area-tasks').onclick === 'function', null, { timeout: 20000 });
    assert.match(await win.textContent('#side'), /会話.*タスク.*ワークフロー/s);
    await win.locator('#conversation-start').waitFor();
    const composerBefore = await win.locator('#composer').boundingBox();
    await win.click('#sessions .list-pick');
    await win.locator('.answer-bubble').waitFor();
    assert.strictEqual(await win.locator('#conversation-history').getAttribute('open'), '', '端末がない会話では履歴を主表示する');
    const composerModeHeights = await win.locator('#composer .composer-shell').evaluate((shell) => {
      const message = document.getElementById('message-input');
      const terminal = document.getElementById('terminal-keys');
      const toolbar = shell.querySelector('.composer-toolbar');
      const messageHeight = shell.getBoundingClientRect().height;
      message.hidden = true;
      toolbar.hidden = true;
      terminal.hidden = false;
      const terminalHeight = shell.getBoundingClientRect().height;
      terminal.hidden = true;
      toolbar.hidden = false;
      message.hidden = false;
      return { messageHeight, terminalHeight };
    });
    assert.ok(Math.abs(composerModeHeights.messageHeight - composerModeHeights.terminalHeight) <= 1,
      `入力モードで高さが変わる: ${JSON.stringify(composerModeHeights)}`);
    assert.match(await win.locator('.msg.user').first().textContent(), /画面を確認して/);
    assert.match(await win.locator('.answer-bubble').textContent(), /確認できました/);
    assert.strictEqual(await win.locator('.response-disclosure.thinking').getAttribute('open'), null, '完了後の思考は閉じる');
    assert.strictEqual(await win.locator('.response-disclosure.information').getAttribute('open'), null, '成功時の実行情報は閉じる');
    const historyScroll = await win.locator('#messages').evaluate((node) => {
      const before = node.scrollTop;
      node.scrollTop = 0;
      const atTop = node.scrollTop;
      node.scrollTop = node.scrollHeight;
      const sizes = {};
      for (const id of ['messages', 'conversation-history', 'chat', 'main', 'app']) {
        const element = document.getElementById(id);
        const style = getComputedStyle(element);
        sizes[id] = { clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, minHeight: style.minHeight, height: style.height, overflow: style.overflow, gridRow: style.gridRow };
      }
      return { before, atTop, after: node.scrollTop, clientHeight: node.clientHeight, scrollHeight: node.scrollHeight, sizes };
    });
    assert.ok(historyScroll.scrollHeight > historyScroll.clientHeight && historyScroll.after > historyScroll.atTop,
      `会話履歴をスクロールできない: ${JSON.stringify(historyScroll)}`);
    const composerAfter = await win.locator('#composer').boundingBox();
    assert.ok(composerBefore && composerAfter && Math.abs(composerBefore.y - composerAfter.y) <= 1,
      '会話開始前後で入力欄が動かない');
    if (process.env.AGENT_APP_CHAT_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_CHAT_SCREENSHOT });

    await win.click('#settings-open');
    await win.locator('#app-settings[open]').waitFor();
    await win.click('[data-settings-tab="instructions"]');
    await win.fill('#instruction-text', '回答は簡潔な日本語にする');
    await win.fill('#skill-entry', 'self-checking');
    await win.click('#skill-add');
    await win.click('#startup-add');
    await win.fill('.startup-row input', 'brainstorming');
    await win.click('[data-settings-tab="execution"]');
    // 「エージェントを最適化する」を外すと、節約・品質重視と small / large の行が薄くなり選べない
    // （agent-herd が無いのと同じ動き）。理由の文言は出さない。
    await win.uncheck('#optimize-agents');
    assert.strictEqual(await win.locator('input[name="default-policy"][value="quality"]').isDisabled(), true);
    assert.strictEqual(await win.locator('input[name="default-policy"][value="saving"]').isDisabled(), true);
    assert.strictEqual(await win.locator('input[name="default-policy"][value="recommended"]').isDisabled(), false);
    assert.strictEqual(await win.locator('#tier-large-cli').isDisabled(), true);
    assert.strictEqual(await win.locator('#tier-medium-cli').isDisabled(), false);
    assert.ok(await win.locator('.tier-row.is-off').count() === 2, 'small / large の行だけ薄い');
    assert.doesNotMatch(await win.locator('[data-settings-panel="execution"]').textContent(), /agent-herd が要ります/);
    if (process.env.AGENT_APP_RESTRICTED_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_RESTRICTED_SCREENSHOT });
    await win.check('#optimize-agents');
    assert.strictEqual(await win.locator('input[name="default-policy"][value="quality"]').isDisabled(), false, 'herd があれば元に戻る');
    await win.check('input[name="default-policy"][value="quality"]');
    await win.fill('#tier-large-model', 'gpt-quality');
    if (process.env.AGENT_APP_SETTINGS_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_SETTINGS_SCREENSHOT });
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    const saved = appStore.loadConfig(userData);
    assert.strictEqual(saved.instructions.text, '回答は簡潔な日本語にする');
    assert.deepStrictEqual(saved.instructions.skills, ['self-checking']);
    assert.deepStrictEqual(saved.instructions.skillSelection, { enabled: true, defaultMode: 'auto', candidates: ['self-checking'] });
    assert.deepStrictEqual(saved.instructions.startupActions, [{ type: 'skill', value: 'brainstorming', onError: 'warn' }]);
    assert.strictEqual(saved.execution.defaultPolicy, 'quality');
    assert.strictEqual(saved.execution.optimizeAgents, true);
    assert.strictEqual(saved.execution.tiers.large.model, 'gpt-quality');
    // 会話のターンごとの起動方針は、最適化が効いていれば 4 つとも選べる
    assert.strictEqual(await win.locator('#policy option[value="saving"]').isDisabled(), false);
    await win.click('#settings-close');

    await win.click('#area-tasks');
    const workspace = win.locator('#automation-workbench');
    await win.locator('#tasks .list-pick').first().waitFor({ timeout: 20000 });
    assert.strictEqual(await win.locator('#tasks .list-pick').count(), 1, `タスク一覧を取得できない: ${await win.locator('#tasks').textContent()} / ${errors.join(' | ')}`);
    assert.match(await win.locator('#tasks').textContent(), /リリース確認/);
    // 定義がある既存タスクは、教示ではなく実行詳細から開く。名前の横に「利用可能」が付く
    await workspace.locator('.task-detail-tabs').waitFor({ timeout: 20000 });
    assert.match(await workspace.locator('.execution-title').textContent(), /リリース確認.*利用可能/s);
    assert.strictEqual(await workspace.locator('.execution-title .eyebrow').count(), 0, '上位ヘッダーと重なる「タスク」ラベルを表示しない');
    assert.strictEqual(await workspace.locator('.teaching-page').count(), 0, '既存定義を教示画面で開かない');
    assert.strictEqual(await workspace.locator('.teaching-page-head').isHidden(), true, 'タスクの見出しがサイドバーと二重に出ている');
    assert.deepStrictEqual(await workspace.locator('.task-detail-tabs [role="tab"]').allTextContents(), ['概要', '手順', '履歴']);
    // 実行状態（agent-loop）は待たずに描き、届いた時点で描き直す。測るのは落ち着いてから。
    const boxOf = async (locator) => {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const box = await locator.boundingBox();
        if (box) return box;
        await win.waitForTimeout(100);
      }
      return null;
    };
    const portalTabsBox = await boxOf(workspace.locator('.task-detail-tabs'));
    const portalPanelBox = await boxOf(workspace.locator('.task-tab-panel'));
    const assertTaskLayout = async (name) => {
      const tabsBox = await boxOf(workspace.locator('.task-detail-tabs'));
      const panelBox = await boxOf(workspace.locator('.task-tab-panel'));
      const close = (left, right) => Math.abs(left - right) <= 1;
      assert.ok(portalTabsBox && tabsBox
        && close(tabsBox.x, portalTabsBox.x) && close(tabsBox.y, portalTabsBox.y) && close(tabsBox.width, portalTabsBox.width),
      `${name}でタブ位置が変わる: ${JSON.stringify({ portalTabsBox, tabsBox })}`);
      assert.ok(portalPanelBox && panelBox
        && close(panelBox.x, portalPanelBox.x) && close(panelBox.width, portalPanelBox.width),
      `${name}で左右のパディングが変わる: ${JSON.stringify({ portalPanelBox, panelBox })}`);
    };
    await workspace.locator('[data-task-tab="overview"]').click();
    await workspace.locator('.task-detail-tabs').waitFor({ timeout: 20000 });
    assert.match(await workspace.locator('.execution-title').textContent(), /リリース確認/s);
    await workspace.locator('#task-run-settings').waitFor();
    const runToolbar = workspace.locator('.run-toolbar');
    await runToolbar.waitFor();
    assert.doesNotMatch(await workspace.locator('.run-card').textContent(), /実行ごとにエージェントとモデルを選べます/);
    const toolbarControls = await Promise.all(['#task-run-settings > summary', '#run-start', '#run-check', '#run-stop']
      .map((selector) => workspace.locator(selector).boundingBox()));
    const toolbarCenters = toolbarControls.map((box) => box && box.y + (box.height / 2));
    assert.ok(toolbarCenters.every(Boolean) && Math.max(...toolbarCenters) - Math.min(...toolbarCenters) <= 1,
      `manual run settings and actions should share one row: ${JSON.stringify(toolbarControls)}`);
    assert.match(await workspace.locator('#task-run-settings-summary').textContent(), /品質重視.*copilot.*gpt-quality/);
    await workspace.locator('#task-run-settings > summary').click();
    await workspace.locator('#run-policy').selectOption('direct');
    await workspace.locator('#run-direct-settings').waitFor();
    await workspace.locator('#run-model').fill('task-model');
    await workspace.locator('#run-skill-mode').selectOption('manual');
    await workspace.locator('[data-run-skill="self-checking"]').check();
    assert.match(await workspace.locator('#task-run-settings-summary').textContent(), /直接指定.*task-model/);
    assert.match(await workspace.locator('#task-run-settings-summary').textContent(), /スキル 手動選択/);
    if (await workspace.locator('#schedule-toggle').isDisabled()) {
      // agent-loop が無ければ予定は足せない（カードごと薄い）
      assert.strictEqual(await workspace.locator('#daemon-toggle').isDisabled(), true);
    } else {
      await workspace.locator('#schedule-toggle').click();
      assert.deepStrictEqual(await workspace.locator('#schedule-destination option').allTextContents(), ['このリポジトリ', '共通設定']);
    }
    assert.strictEqual(await workspace.locator('.folder-pane').isHidden(), true, 'リポジトリ一覧が二重に表示されている');
    assert.strictEqual(await workspace.locator('.home-tabs').isHidden(), true, '主要タブが二重に表示されている');
    if (process.env.AGENT_APP_TASK_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TASK_SCREENSHOT });
    }
    await workspace.locator('[data-task-tab="steps"]').click();
    await workspace.locator('[data-step="0"]').waitFor();
    await assertTaskLayout('手順');
    assert.match(await workspace.locator('[data-step="0"]').textContent(), /変更を確認/);
    assert.strictEqual(await workspace.locator('.task-detail-tabs').count(), 1, '手順でもタスクタブを維持する');
    assert.strictEqual(await workspace.locator('#btn-home').isHidden(), true, '埋め込み編集では戻るボタンを表示しない');
    if (process.env.AGENT_APP_TASK_STEPS_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TASK_STEPS_SCREENSHOT });
    }
    assert.strictEqual(await workspace.locator('#b-assist').textContent(), 'AIと編集');
    assert.strictEqual(await workspace.locator('#b-run').textContent(), 'テスト');
    const toolbar = await workspace.locator('.embedded-editor-toolbar').boundingBox();
    const toolbarTitle = await workspace.locator('.embedded-editor-toolbar .bar-center').boundingBox();
    const toolbarActions = await workspace.locator('.embedded-editor-toolbar .bar-right').boundingBox();
    assert.ok(toolbar && toolbarActions && toolbarActions.x + toolbarActions.width <= toolbar.x + toolbar.width + 1,
      `editor actions should stay within the toolbar: ${JSON.stringify({ toolbar, toolbarActions })}`);
    assert.ok(toolbarTitle && toolbarActions && toolbarTitle.y + toolbarTitle.height <= toolbarActions.y + 1,
      `editor title and actions should use separate rows: ${JSON.stringify({ toolbarTitle, toolbarActions })}`);
    // 選択した工程から「編集」へ移り、対象を引き継いだ AI との会話（tmux の端末ミラー）が出る。
    // .execution-card で、中身は親の slot に載る。タブは概要 / 手順 / 履歴のまま。
    await workspace.locator('[data-step="0"]').click();
    await workspace.locator('#b-assist').click();
    await win.locator('#task-teaching:not([hidden])').waitFor({ timeout: 20000 });
    if (process.env.AGENT_APP_TEACHING_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TEACHING_SCREENSHOT });
    }
    await assertTaskLayout('編集');
    assert.match(await workspace.locator('.task-conversation-toolbar').textContent(), /AIと編集/);
    // 会話画面と同じ組み方: ツールバーの直下に会話面が付き、tmux を開く前の起動カードは
    // 残りの高さへ引き伸ばされず上に寄る。
    const teachingLayout = await win.evaluate(() => {
      const box = (node) => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height }; };
      const workbench = document.getElementById('automation-workbench').shadowRoot;
      return {
        toolbar: box(workbench.querySelector('.task-conversation-toolbar')),
        panel: box(workbench.querySelector('.task-tab-panel')),
        launch: box(document.getElementById('task-launch')),
      };
    });
    assert.ok(teachingLayout.launch.top - teachingLayout.toolbar.bottom <= 24,
      `起動カードがツールバーから離れている: ${JSON.stringify(teachingLayout)}`);
    assert.ok(teachingLayout.launch.height < teachingLayout.panel.height / 2,
      `起動カードが残りの高さへ引き伸ばされている: ${JSON.stringify(teachingLayout)}`);
    // tmux を開いた後の面を再現する（この環境では CLI を起動できない）。会話画面と同じく、
    // 端末が残りの高さを使い、入力欄が下に付く。
    const terminalLayout = await win.evaluate(() => {
      const box = (node) => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height, width: r.width }; };
      const workbench = document.getElementById('automation-workbench').shadowRoot;
      document.getElementById('task-launch').hidden = true;
      document.getElementById('task-terminal').hidden = false;
      document.getElementById('task-composer').hidden = false;
      window.TaskTerm.attach('layout-check', document.getElementById('task-term-host'));
      window.TaskTerm.refit();
      window.TaskTerm.applyScreen({ id: 'layout-check', text: '$ codex\n> 工程 1 を見直しています…', cursor: { x: 0, y: 2 } });
      return {
        toolbar: box(workbench.querySelector('.task-conversation-toolbar')),
        panel: box(workbench.querySelector('.task-tab-panel')),
        terminal: box(document.getElementById('task-terminal')),
        composer: box(document.querySelector('#task-composer')),
      };
    });
    assert.ok(terminalLayout.terminal.top - terminalLayout.toolbar.bottom <= 24,
      `端末がツールバーから離れている: ${JSON.stringify(terminalLayout)}`);
    assert.ok(terminalLayout.terminal.height >= 220 && terminalLayout.terminal.height > terminalLayout.composer.height,
      `端末が残りの高さを使っていない: ${JSON.stringify(terminalLayout)}`);
    assert.ok(terminalLayout.composer.top >= terminalLayout.terminal.bottom
      && terminalLayout.panel.bottom - terminalLayout.composer.bottom <= 24,
      `入力欄が端末の下に付いていない: ${JSON.stringify(terminalLayout)}`);
    assert.ok(Math.abs(terminalLayout.composer.width - terminalLayout.terminal.width) <= 2,
      `端末と入力欄の幅が揃っていない: ${JSON.stringify(terminalLayout)}`);
    if (process.env.AGENT_APP_TEACHING_TERMINAL_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TEACHING_TERMINAL_SCREENSHOT });
    }
    await win.evaluate(() => {
      window.TaskTerm.detach();
      document.getElementById('task-terminal').hidden = true;
      document.getElementById('task-composer').hidden = true;
      document.getElementById('task-launch').hidden = false;
    });
    assert.strictEqual(await workspace.locator('#editing-target').inputValue(), 'step:step_1', '選択した工程を編集対象へ引き継ぐ');
    await workspace.locator('#editing-target').selectOption('workflow');
    assert.strictEqual(await workspace.locator('#editing-target').inputValue(), 'workflow', '編集画面で全体へ切り替えられる');
    assert.strictEqual(await workspace.locator('[data-edit-back]').isVisible(), true, '工程へ戻れる');
    assert.strictEqual(await workspace.locator('.task-detail-tabs').count(), 1, '編集でもタスクタブを維持する');
    assert.deepStrictEqual(await workspace.locator('.task-detail-tabs [role="tab"]').allTextContents(), ['概要', '手順', '履歴']);
    assert.match(await workspace.locator('.task-detail-shell').textContent(), /リリース確認.*利用可能/s);
    assert.strictEqual(await win.locator('#task-teaching.in-card').count(), 1, 'カードの中の端末は枠と影を持たない');
    await workspace.locator('[data-edit-back]').click();
    await workspace.locator('[data-step="0"]').waitFor({ timeout: 20000 });
    // 履歴と定期実行は agent-loop のもの。無ければ履歴タブは押せず、定期実行のカードは薄い（理由は 1 行だけ）。
    if (await workspace.locator('[data-task-tab="history"]').isDisabled()) {
      await workspace.locator('[data-task-tab="overview"]').click();
      await workspace.locator('.execution-card.is-off').waitFor({ timeout: 20000 });
      assert.match(await workspace.locator('.execution-card.is-off').textContent(), /定期実行と履歴には agent-loop が要ります/);
      assert.strictEqual(await workspace.locator('#run-start').isDisabled(), false, 'agent-loop が無くても実行は押せる');
    } else {
      await workspace.locator('[data-task-tab="history"]').click();
      await workspace.locator('.execution-card').waitFor();
      await assertTaskLayout('履歴');
      if (process.env.AGENT_APP_TASK_HISTORY_SCREENSHOT) {
        await win.screenshot({ path: process.env.AGENT_APP_TASK_HISTORY_SCREENSHOT });
      }
    }
    await win.click('#session-new');
    await win.locator('#task-create:not([hidden])').waitFor();
    assert.match(await workspace.locator('.teaching-create').textContent(), /新しいタスク[\s\S]*何を自動化したいですか/);
    assert.strictEqual(await win.locator('#task-purpose').isVisible(), true, '目的の入力欄が親の作成フォームに出る');
    assert.strictEqual(await win.locator('#task-create-start').isVisible(), true, 'AIと作成を始められる');
    if (process.env.AGENT_APP_TASK_NEW_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TASK_NEW_SCREENSHOT });
    }

    await win.click('#area-workflows');
    await win.locator('#workflows .list-pick').first().waitFor({ timeout: 20000 });
    assert.match(await win.locator('#workflows').textContent(), /並列レビュー/);
    await workspace.locator('.flow-overview').waitFor({ timeout: 20000 });
    assert.match(await workspace.locator('.flow-overview').textContent(), /変更を確認/);
    await workspace.locator('[data-flow-tab="history"]').click();
    await workspace.locator('.flow-history').waitFor();
    assert.match(await workspace.locator('.flow-history').textContent(), /完了.*以前の並列レビュー/s);
    await workspace.locator('.flow-history [data-flow-run]').click();
    await workspace.locator('.flow-run-nodes').waitFor();
    assert.match(await workspace.locator('.execution-title').textContent(), /以前の並列レビュー/);
    await workspace.locator('[data-flow-back-run]').click();
    await workspace.locator('[data-flow-edit]').click();
    await workspace.locator('.flow-node-card').waitFor();
    assert.strictEqual(await workspace.locator('.flow-node-card').count(), 1, 'ワークフローの工程を編集できない');
    assert.strictEqual(await workspace.locator('[data-flow-start]').count(), 0, '編集時に実行フォームを重ねて出さない');
    await workspace.locator('[data-flow-close-editor]').click();
    await win.click('#session-new');
    await workspace.locator('.teaching-create').waitFor();
    assert.match(await workspace.locator('.teaching-create').textContent(), /新しいワークフローを教える/);
    await workspace.locator('[data-flow-teaching-cancel]').click();
    if (process.env.AGENT_APP_FLOW_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_FLOW_SCREENSHOT });
    }
    if (process.env.AGENT_APP_AUTOMATION_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_AUTOMATION_SCREENSHOT });
    }
    await win.click('#area-work');
    assert.strictEqual(await win.locator('body > #app > #main').isVisible(), true, '会話画面へ戻れない');
    assert.deepStrictEqual(errors, [], '画面でエラーが発生した');
  } finally {
    await electron.close();
  }
});
