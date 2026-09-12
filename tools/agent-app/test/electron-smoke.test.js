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
  // 別のリポジトリへの分岐を実機で通すための 2 つ目のリポジトリ
  const otherRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-shared-lib-'));
  appStore.saveConfig(userData, {
    repos: [repo, otherRepo], lastRepo: repo, area: 'work',
    // 共有: 合言葉だけ入れて受け口を開く（仲間はいない）。自分が出した依頼を画面に出すため、
    // 依頼の控え（requests.json）を先に置く。
    share: { enabled: true, passphrase: 'smoke', node: 'smoke-pc', port: 0, udp: false, accept: 'manual' },
  });
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
  // 応答に別のフォルダへの依頼（@fork 行）が 2 つ: 1 つは分岐済み（リンクになる）、1 つは未分岐（ボタンになる）
  const forkedIndex = appStore.appendMessage(userData, session.id, {
    role: 'assistant', cli: 'codex', model: 'gpt-test', elapsedMs: 900,
    text: `共通ライブラリ側の型定義も直す必要があります。\n\n@fork ${otherRepo}\n型定義 User に role を追加してください。`,
  }).messages.length - 1;
  const forked = appStore.createSession(userData, {
    repo: otherRepo, cli: 'codex', model: 'gpt-test', policy: 'quality', tier: 'large', transport: 'headless',
    origin: { sessionId: session.id, repo, index: forkedIndex },
  });
  appStore.appendMessage(userData, forked.id, { role: 'user', text: '型定義 User に role を追加してください。' });
  appStore.appendMessage(userData, session.id, {
    role: 'assistant', cli: 'codex', model: 'gpt-test', elapsedMs: 800,
    text: `もう 1 つ、ドキュメント側にも反映が要ります。\n\n@fork ${path.join(os.tmpdir(), 'agent-app-not-registered')}\nREADME に role の説明を足してください。`,
  });
  for (let index = 0; index < 60; index += 1) {
    appStore.appendMessage(userData, session.id, {
      role: 'user', cli: 'codex', model: 'gpt-test',
      text: `スクロール確認 ${index + 1}: ${'履歴を十分に長くする。'.repeat(8)}`,
    });
  }

  const { Requester } = require('../src/main/share/requester');
  const shareRequests = new Requester({ userData, node: 'smoke-pc', file: path.join(userData, 'share', 'requests.json') });
  shareRequests.post({
    title: 'ログ設計をレビュー', goal: 'ログ設計をレビューして', priority: 'high',
    summary: 'ログ設計をレビューして。\n回転の条件と、失敗したときにどこへ残すかを見てほしい。',
  });
  shareRequests.post({ title: '移行手順の要約', goal: '移行手順をまとめて', summary: '移行手順をまとめて' });
  // 仲間が実行中の依頼（引き受けた人の端末が自分の画面に映る側）
  const working = shareRequests.post({ title: 'テスト方針の相談', goal: 'テスト方針を相談したい', summary: 'テスト方針を相談したい' });
  shareRequests.claim(working.id, { who: 'pc-b', port: 47801, cli: 'claude' }, '127.0.0.1');
  // 人と人のやり取り（ひとこと）。自分の分と相手の分を 1 件ずつ
  shareRequests.get(working.id).talk.push({ who: 'smoke-pc', text: 'テストは走らせなくていいです', at: '2026-09-12T00:33:00Z' });
  shareRequests.message(working.id, { who: 'pc-b', text: '了解。読みだけで進めます' });
  // その依頼を待っている会話（端末ミラーとやり取りが会話画面にも出る）
  const shareSession = appStore.createSession(userData, {
    repo, cli: 'codex', model: 'gpt-test', policy: 'shared', tier: '', transport: 'headless',
  });
  appStore.appendMessage(userData, shareSession.id, { role: 'user', text: 'テスト方針の相談', policy: 'shared' });
  appStore.updateSession(userData, shareSession.id, { share: { id: working.id } });

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
    // AGENT_APP_DEBUG_ERRORS=1 を付けると、画面のエラーをその場で出す（落ちた場所を突き止めるため）
    const note = (text) => { errors.push(text); if (process.env.AGENT_APP_DEBUG_ERRORS) console.error(`[画面] ${text}`); };
    win.on('pageerror', (error) => note(`${error.name}: ${error.message}`));
    win.on('console', (message) => { if (message.type() === 'error') note(message.text()); });

    await win.waitForSelector('#area-tasks');
    await win.waitForFunction(() => typeof document.getElementById('area-tasks').onclick === 'function', null, { timeout: 20000 });
    assert.match(await win.textContent('#side'), /会話.*タスク.*ワークフロー/s);
    await win.locator('#conversation-start').waitFor();
    const composerBefore = await win.locator('#composer').boundingBox();
    await win.locator('#sessions .list-pick').filter({ hasText: '画面を確認して' }).click();
    await win.locator('.answer-bubble').first().waitFor();
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
    assert.match(await win.locator('.answer-bubble').first().textContent(), /確認できました/);
    assert.strictEqual(await win.locator('.response-disclosure.thinking').first().getAttribute('open'), null, '完了後の思考は閉じる');
    assert.strictEqual(await win.locator('.response-disclosure.information').first().getAttribute('open'), null, '成功時の実行情報は閉じる');
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
    // 別のリポジトリへの分岐: 分岐済みの応答は分岐先へのリンク、未分岐の応答は分岐のボタン（どちらも回答の下の .message-action）
    const forkLink = win.locator('.message-action', { hasText: `→ ${path.basename(otherRepo)}:` });
    await forkLink.waitFor();
    assert.match(await forkLink.textContent(), /型定義 User に role を追加してください/);
    const forkButton = win.locator('.message-action', { hasText: 'で続ける（新しい会話を分岐）' });
    assert.strictEqual(await forkButton.count(), 1, '未分岐の応答にだけ分岐のボタンが出る');
    assert.strictEqual(await win.locator('#chat-origin').isHidden(), true, '分岐元の会話には分岐元の行を出さない');
    if (process.env.AGENT_APP_CHAT_SCREENSHOT) {
      await forkButton.scrollIntoViewIfNeeded();
      await win.screenshot({ path: process.env.AGENT_APP_CHAT_SCREENSHOT });
    }
    // 分岐先を開く: リポジトリ選択が切り替わり、会話一覧はふつうの会話と同じ形、ヘッダーに分岐元の 1 行
    await forkLink.click();
    await win.locator('#chat-origin:visible').waitFor();
    assert.strictEqual(await win.inputValue('#repo-select'), otherRepo);
    assert.match(await win.textContent('#chat-origin'), new RegExp(`^分岐元: ${path.basename(repo)} › 画面を確認して$`));
    assert.match(await win.textContent('#sessions'), /型定義 User に role を追加してください/);
    if (process.env.AGENT_APP_FORK_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_FORK_SCREENSHOT });
    // 分岐元へ戻る
    await win.click('#chat-origin');
    await win.locator('#chat-origin').waitFor({ state: 'hidden' });
    assert.strictEqual(await win.inputValue('#repo-select'), repo);
    assert.match(await win.textContent('#chat-title'), /画面を確認して/);

    await win.click('#settings-open');
    await win.locator('#app-settings[open]').waitFor();
    const settingsDialogHeights = [];
    for (const tab of ['app', 'instructions', 'execution']) {
      await win.click(`[data-settings-tab="${tab}"]`);
      const box = await win.locator('#app-settings').boundingBox();
      settingsDialogHeights.push(box?.height || 0);
    }
    assert.ok(settingsDialogHeights.every((height) => Math.abs(height - settingsDialogHeights[0]) <= 1),
      `設定メニューの切替でダイアログの高さが変わる: ${settingsDialogHeights.join(', ')}`);
    await win.click('[data-settings-tab="instructions"]');
    await win.fill('#instruction-text', '回答は簡潔な日本語にする');
    await win.fill('#skill-entry', 'self-checking');
    await win.click('#skill-add');
    await win.click('#startup-add');
    await win.fill('#startup-actions .startup-row input', 'brainstorming');
    // 定型の依頼は既定の 2 つが入っている。3 つ目を足して、回答の下へ並ぶことを後で見る
    assert.strictEqual(await win.locator('#quick-requests .startup-row').count(), 2);
    await win.click('#quick-add');
    await win.fill('#quick-requests .startup-row:nth-child(3) input:nth-of-type(1)', '変更を要約する');
    await win.fill('#quick-requests .startup-row:nth-child(3) input:nth-of-type(2)', 'この会話でやった変更を 3 行で要約してください。');
    assert.strictEqual(await win.locator('#quick-add').isDisabled(), true, '定型の依頼は 3 つまで');
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
    assert.strictEqual(saved.instructions.quickRequests.length, 3);
    assert.deepStrictEqual(saved.instructions.quickRequests[2], { label: '変更を要約する', text: 'この会話でやった変更を 3 行で要約してください。' });
    // 前面に無いときの通知は既定でON（設定 > アプリの 1 行）
    assert.strictEqual(saved.notify.background, true);
    assert.strictEqual(saved.execution.defaultPolicy, 'quality');
    assert.strictEqual(saved.execution.optimizeAgents, true);
    assert.strictEqual(saved.execution.tiers.large.model, 'gpt-quality');
    // 会話のターンごとの起動方針は、最適化が効いていれば 4 つとも選べる
    assert.strictEqual(await win.locator('#policy option[value="saving"]').isDisabled(), false);
    await win.click('#settings-close');

    // 親画面のポップアップは、メニュー外の背景をクリックすると閉じる。
    for (const [menu, background] of [
      ['#repo-more', '#main'],
      ['#chat-more', '#side'],
      ['#run-settings', '#chat-title'],
    ]) {
      await win.click(`${menu} > summary`);
      assert.strictEqual(await win.locator(menu).getAttribute('open'), '', `${menu} を開けない`);
      await win.click(background, { position: { x: 4, y: 4 } });
      assert.strictEqual(await win.locator(menu).getAttribute('open'), null, `${menu} が背景クリックで閉じない`);
    }

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
    await workspace.locator('#task-run-settings > summary').click();
    assert.strictEqual(await workspace.locator('#task-run-settings').getAttribute('open'), '');
    await workspace.locator('.execution-title').click({ position: { x: 4, y: 4 } });
    assert.strictEqual(await workspace.locator('#task-run-settings').getAttribute('open'), null,
      'Shadow DOM 内の実行設定が背景クリックで閉じない');
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
    assert.strictEqual(await workspace.locator('#b-assist').textContent(), '編集');
    assert.strictEqual(await workspace.locator('#b-run').textContent(), 'テスト');
    assert.strictEqual(await workspace.locator('#b-record').count(), 0, 'agent-app の「その他」に旧記録を表示しない');
    await workspace.locator('.embedded-editor-toolbar details.more-menu > summary').click();
    assert.strictEqual(await workspace.locator('.embedded-editor-toolbar details.more-menu').getAttribute('open'), '');
    await workspace.locator('.execution-title').click({ position: { x: 4, y: 4 } });
    assert.strictEqual(await workspace.locator('.embedded-editor-toolbar details.more-menu').getAttribute('open'), null,
      'Shadow DOM 内のその他メニューが背景クリックで閉じない');
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
    assert.match(await workspace.locator('.task-conversation-toolbar').textContent(), /編集/);
    assert.strictEqual(await win.locator('#task-launch-title').count(), 0, '編集画面で「AIと編集」を重ねて表示しない');
    // 会話画面と同じ組み方: ツールバーの直下に会話面が付き、tmux を開く前から
    // 埋め込み端末と同じ高さの起動領域を確保する。
    const teachingLayout = await win.evaluate(() => {
      const box = (node) => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height, width: r.width }; };
      const workbench = document.getElementById('automation-workbench').shadowRoot;
      return {
        toolbar: box(workbench.querySelector('.task-conversation-toolbar')),
        panel: box(workbench.querySelector('.task-tab-panel')),
        controls: box(document.querySelector('.teach-start-toolbar')),
        launch: box(document.getElementById('task-terminal-placeholder')),
        composer: box(document.getElementById('task-composer-placeholder')),
      };
    });
    assert.ok(teachingLayout.controls.top - teachingLayout.toolbar.bottom <= 24,
      `編集コントロールがツールバーから離れている: ${JSON.stringify(teachingLayout)}`);
    assert.ok(teachingLayout.launch.top >= teachingLayout.controls.bottom
      && teachingLayout.launch.top - teachingLayout.controls.bottom <= 12,
      `tmux プレースホルダーが編集コントロールから離れている: ${JSON.stringify(teachingLayout)}`);
    assert.ok(teachingLayout.launch.height >= 220
      && teachingLayout.composer.top >= teachingLayout.launch.bottom
      && teachingLayout.panel.bottom - teachingLayout.composer.bottom <= 24,
      `起動領域が埋め込み端末と同じ残りの高さを使っていない: ${JSON.stringify(teachingLayout)}`);
    // tmux を開いた後の面を再現する（この環境では CLI を起動できない）。会話画面と同じく、
    // 端末が残りの高さを使い、入力欄が下に付く。
    const terminalLayout = await win.evaluate(() => {
      const box = (node) => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height, width: r.width }; };
      const workbench = document.getElementById('automation-workbench').shadowRoot;
      document.getElementById('task-terminal-placeholder').hidden = true;
      document.getElementById('task-composer-placeholder').hidden = true;
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
    assert.ok(Math.abs(terminalLayout.terminal.top - teachingLayout.launch.top) <= 1
      && Math.abs(terminalLayout.terminal.width - teachingLayout.launch.width) <= 1
      && Math.abs(terminalLayout.terminal.height - teachingLayout.launch.height) <= 1,
      `tmux 起動前後で埋め込み領域が移動している: ${JSON.stringify({ teachingLayout, terminalLayout })}`);
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
    // 失敗した実行を AI へ渡すときの最初の依頼は、入力欄へ置くだけ（送るのは利用者）。
    await win.evaluate(() => {
      window.TaskTeaching.state.session = { id: 'prefill-check', cli: 'codex', model: '' };
      window.TaskTeaching.prefill({ text: 'このタスクの実行が失敗しました。原因を調べて、手順を直してください。' });
    });
    assert.strictEqual(await win.locator('#task-prompt').inputValue(), 'このタスクの実行が失敗しました。原因を調べて、手順を直してください。');
    assert.strictEqual(await win.locator('#task-input-status').textContent(), '文面を確かめて「送信」を押してください');
    await win.evaluate(() => {
      window.TaskTeaching.state.session = null;
      document.getElementById('task-prompt').value = '';
      window.TaskTerm.detach();
      document.getElementById('task-terminal').hidden = true;
      document.getElementById('task-composer').hidden = true;
      document.getElementById('task-terminal-placeholder').hidden = false;
      document.getElementById('task-composer-placeholder').hidden = false;
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
    assert.strictEqual(await win.locator('#task-create-start').isVisible(), true, '作成を開始できる');
    assert.strictEqual(await win.locator('#task-create-start').textContent(), '作成開始');
    assert.strictEqual(await win.locator('#task-create-cancel').count(), 0, '新規作成画面に戻るボタンは置かない');
    assert.strictEqual(await win.locator('.task-save-name').isVisible(), true, '保存名は折りたたまず目的より前に表示する');
    await win.locator('#task-create .teach-execution-settings > summary').click();
    assert.strictEqual(await win.locator('#task-create .teach-execution-settings').getAttribute('open'), '');
    await workspace.locator('.teaching-create h2').click();
    assert.strictEqual(await win.locator('#task-create .teach-execution-settings').getAttribute('open'), null,
      'スロット内の作成設定が背景クリックで閉じない');
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
    // 「変更を相談」→ タスクと同じ形の会話（起動前は黒い端末の置き場と編集開始だけ）
    await workspace.locator('[data-flow-change-consult]').click();
    await win.locator('#flow-teaching:not([hidden])').waitFor({ timeout: 20000 });
    assert.strictEqual(await win.locator('#flow-teach-launch').isVisible(), true, '編集開始の前に起動領域を出す');
    assert.strictEqual(await win.locator('#flow-teach-start').textContent(), '編集開始');
    assert.strictEqual(await win.locator('#flow-teach-terminal').isVisible(), false, 'tmux を開く前に端末は出さない');
    assert.match(await workspace.locator('.execution-title').textContent(), /ワークフローを教える/);
    if (process.env.AGENT_APP_FLOW_TEACHING_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_FLOW_TEACHING_SCREENSHOT });
    }
    await win.click('#session-new');
    await workspace.locator('.teaching-create').waitFor();
    assert.match(await workspace.locator('.teaching-create').textContent(), /新しいワークフローを教える/);
    assert.strictEqual(await win.locator('#flow-teaching').isVisible(), false, '作成の入口では会話の置き場を出さない');
    await workspace.locator('[data-flow-teaching-cancel]').click();
    if (process.env.AGENT_APP_FLOW_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_FLOW_SCREENSHOT });
    }
    if (process.env.AGENT_APP_AUTOMATION_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_AUTOMATION_SCREENSHOT });
    }
    // 共有: 一覧（サイドバー）と、選んだ 1 件のカード。会話画面と同じ骨格で出る。
    await win.click('#area-share');
    await win.locator('#share-requests .list-pick').first().waitFor({ timeout: 20000 });
    assert.match(await win.locator('#share-requests').textContent(), /ログ設計をレビュー/);
    // 並びは優先度と投函時刻で決まるので、位置ではなく題名で選ぶ
    await win.locator('#share-requests .list-pick').filter({ hasText: 'ログ設計をレビュー' }).click();
    await win.locator('#share-cards .execution-card').first().waitFor();
    assert.match(await win.locator('#share-head').textContent(), /優先度 高/);
    assert.match(await win.locator('#share-cards').textContent(), /依頼の本文/);
    assert.strictEqual(await win.locator('#share-accept-mode').inputValue(), 'manual');
    await win.locator('#share-view-nodes').click();
    await win.locator('.share-nodes').waitFor();
    assert.match(await win.locator('#share-cards').textContent(), /参加者/);
    await win.locator('#share-view-request').click();
    // 実行中の依頼は、引き受けた人の端末がそのまま出る（会話と同じ .terminal-stage）
    await win.locator('#share-requests .list-pick').filter({ hasText: 'テスト方針の相談' }).click();
    await win.locator('#share-terminal').waitFor();
    assert.match(await win.locator('#share-term-agent').textContent(), /pc-b の claude/);
    assert.strictEqual(await win.locator('#share-term-note').textContent(), '閲覧のみ');
    const shareTerminal = await win.locator('#share-terminal').boundingBox();
    assert.ok(shareTerminal && shareTerminal.height >= 220, `端末が潰れている: ${JSON.stringify(shareTerminal)}`);
    // ひとこと（人と人）は吹き出しで、自分は右・相手は左。入力欄は「ひとこと」だけ（相手の PC の端末は打てない）
    await win.locator('#share-thread .talk-line').first().waitFor();
    assert.strictEqual(await win.locator('#share-thread .talk-line.mine').count(), 1);
    assert.strictEqual(await win.locator('#share-thread .talk-line.them').count(), 1);
    assert.match(await win.locator('#share-thread .talk-line.them').textContent(), /pc-b.*読みだけで進めます/s);
    assert.strictEqual(await win.locator('#share-composer').isVisible(), true, '相手がいる間は入力欄を出す');
    assert.strictEqual(await win.locator('#share-mode-terminal').isVisible(), false, '自分の依頼では端末操作を出さない');
    const mineBox = await win.locator('#share-thread .talk-line.mine').boundingBox();
    const themBox = await win.locator('#share-thread .talk-line.them').boundingBox();
    assert.ok(mineBox.x > themBox.x, `自分の吹き出しは右に寄せる: ${JSON.stringify({ mineBox, themBox })}`);
    if (process.env.AGENT_APP_SHARE_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_SHARE_SCREENSHOT });

    await win.click('#area-work');
    assert.strictEqual(await win.locator('body > #app > #main').isVisible(), true, '会話画面へ戻れない');
    // 待っている会話: 端末ミラーの下にやり取りが出て、入力欄はひとことになる
    await win.locator('#sessions .list-pick').filter({ hasText: 'テスト方針の相談' }).click();
    await win.locator('#share-talk:not([hidden])').waitFor({ timeout: 20000 });
    assert.strictEqual(await win.locator('#terminal-stage').isVisible(), true, '待っている間は相手の端末を映す');
    await win.locator('#share-talk summary').click();          // 共有の画面で既読にしたので、開いて確かめる
    assert.strictEqual(await win.locator('#share-talk .talk-line').count(), 2);
    assert.strictEqual(await win.locator('#share-talk .talk-line.mine').count(), 1);
    assert.strictEqual(await win.locator('#attach').isVisible(), false, 'ひとことに添付は要らない');
    assert.match(await win.locator('#share-talk-count').textContent(), /2件/);
    assert.strictEqual(await win.locator('#input-mode-share').getAttribute('aria-pressed'), 'true');
    assert.strictEqual(await win.locator('#input-mode-message').isDisabled(), true, '待っている間は手元の CLI へ送れない');
    assert.match(await win.locator('#send').textContent(), /送る/);
    assert.match(await win.locator('#prompt').getAttribute('placeholder'), /pc-b/);
    // 会話履歴も開いた「全部出ている」状態で、端末・やり取り・履歴・入力欄が重ならない
    await win.evaluate(() => { document.getElementById('conversation-history').open = true; });
    const stack = await win.evaluate(() => {
      const box = (id) => { const r = document.getElementById(id).getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height }; };
      return { terminal: box('terminal-stage'), talk: box('share-talk'), history: box('conversation-history'), composer: box('composer') };
    });
    assert.ok(stack.terminal.height >= 150, `端末が潰れている: ${JSON.stringify(stack)}`);
    assert.ok(stack.terminal.bottom <= stack.talk.top + 1
      && stack.talk.bottom <= stack.history.top + 1
      && stack.history.bottom <= stack.composer.top + 1, `段が重なっている: ${JSON.stringify(stack)}`);
    if (process.env.AGENT_APP_TALK_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_TALK_SCREENSHOT });
    // 会話の入力先に「共有に依頼」が並ぶ（設定 > 共有を使うと決めているとき）
    await win.locator('#sessions .list-pick').filter({ hasText: '画面を確認して' }).click();
    await win.locator('#input-mode-share').waitFor();
    await win.locator('#input-mode-share').click();
    assert.strictEqual(await win.locator('#input-mode-share').getAttribute('aria-pressed'), 'true');
    await win.locator('#run-settings summary').click();
    await win.locator('#share-priority-field').waitFor();
    assert.strictEqual(await win.locator('#policy-field').isVisible(), false, '共有では起動方針を出さない');
    assert.match(await win.locator('#run-settings-summary').textContent(), /どれでも.*優先度 通常/);
    assert.match(await win.locator('#send').textContent(), /依頼する/);
    if (process.env.AGENT_APP_SHARE_COMPOSER_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_SHARE_COMPOSER_SCREENSHOT });
    await win.keyboard.press('Escape');
    if (process.env.AGENT_APP_SHARE_SWITCH_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_SHARE_SWITCH_SCREENSHOT });
    assert.deepStrictEqual(errors, [], '画面でエラーが発生した');
  } finally {
    await electron.close();
  }
});
