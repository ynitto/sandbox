'use strict';

// Electron を起動せずに固定する: 構文・argv の組み立て・セッションの保存・git の読み取り・ファイル閲覧。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');
const agentCli = require('../src/main/agentCli');
const store = require('../src/main/store');
const git = require('../src/main/git');
const files = require('../src/main/files');
const text = require('../src/main/text');
const attachments = require('../src/main/attachments');
const settings = require('../src/main/settings');
const automationIpc = require('../src/main/automation/ipc');

const SRC = path.join(__dirname, '..', 'src');

test('main / ipc / preload / renderer は構文検査を通る', () => {
  for (const f of ['main/main.js', 'main/ipc.js', 'main/automation/ipc.js', 'main/agentCli.js', 'main/store.js', 'main/settings.js', 'main/sessionSetup.js', 'main/notify.js', 'main/executionGate.js', 'main/response.js', 'main/skills.js', 'main/git.js', 'main/host.js', 'main/tmux.js', 'main/files.js', 'main/text.js', 'main/attachments.js',
    'main/automation/teaching.js', 'preload.js', 'renderer/renderer.js', 'renderer/md.js', 'renderer/inputMode.js', 'renderer/term.js', 'renderer/files.js', 'renderer/navigation.js', 'renderer/taskIntent.js', 'renderer/teachingProtocol.js', 'renderer/taskTeaching.js', 'renderer/automation/flow.js', 'renderer/automation/teaching.js', 'renderer/automation/renderer.js']) {
    execFileSync(process.execPath, ['--check', path.join(SRC, f)]);
  }
  const main = fs.readFileSync(path.join(SRC, 'main/main.js'), 'utf8');
  assert.ok(main.includes('contextIsolation: true') && main.includes('sandbox: true'));
  assert.ok(fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8').includes("script-src 'self'"));
  // 画面の出し分けは hidden 属性でやる。label や .seg のように display を明示した要素では
  // 作者スタイルが UA の `[hidden] { display: none }` に勝ってしまうので、全体規則で押さえる。
  assert.match(fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8'), /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
});

test('画面は主要メニュー・会話・詳細設定の順に情報を分ける', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  assert.match(html, /<nav id="areas" class="app-menu"[^>]*aria-label="主要メニュー"/);
  assert.match(html, /id="area-work" class="on" aria-current="page"[\s\S]*?<span>会話<\/span>/);
  assert.ok(html.indexOf('id="session-new"') < html.indexOf('id="sessions"'), '新規作成を選択中領域の一覧見出しへ置く');
  assert.ok(html.indexOf('id="cli"') > html.indexOf('id="composer"'), '実行条件を入力欄の近くへ置く');
  assert.ok(html.indexOf('id="use-tmux"') > html.indexOf('id="app-settings"'), '高度な環境設定をダイアログへ置く');
  assert.match(html, /id="prompt"[^>]*placeholder="エージェントに依頼する"/);
  assert.match(css, /button:focus-visible[\s\S]*outline:/);
  assert.match(css, /@media \(max-width: 820px\)[\s\S]*sidebar-open/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(fs.readFileSync(path.join(SRC, 'renderer/files.js'), 'utf8'), /📁|📄|📝|🖼/);
  assert.match(fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8'), /el\('button', 'list-pick'\)/);
});

test('会話は依頼ごとにスキルの自動・手動・不使用を選べる', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  assert.match(html, /id="turn-skill-mode"[\s\S]*value="auto"[\s\S]*value="manual"[\s\S]*value="off"/);
  assert.match(renderer, /skillMode:\s*state\.turnSkillMode/);
  assert.match(renderer, /api\.selectSkills/);
});

test('tmux会話はメッセージ入力と端末操作を明示的に切り替える', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const term = fs.readFileSync(path.join(SRC, 'renderer/term.js'), 'utf8');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  assert.match(html, /id="input-mode-message"[^>]*aria-pressed="true"[^>]*>メッセージ</);
  assert.match(html, /id="input-mode-terminal"[^>]*aria-pressed="false"[^>]*>端末操作</);
  assert.match(html, /id="terminal-stage"[^>]*>[\s\S]*id="term-host"/);
  assert.match(html, /id="terminal-keys"[^>]*hidden[\s\S]*data-terminal-key="C-c"/);
  assert.match(html, /data-terminal-key="Enter"[^>]*aria-label="端末へEnterキーを送る"/);
  // 改行は送信と別のキー。仮想キーの「改行」と Shift+Enter は LF（Ctrl+J）として CLI へ届く
  assert.match(html, /data-terminal-key="Newline"[^>]*>改行</);
  assert.match(html, /data-task-key="Newline"[^>]*>改行</);
  assert.match(renderer, /Newline:\s*'\\n'/);
  assert.match(fs.readFileSync(path.join(SRC, 'renderer/taskTeaching.js'), 'utf8'), /Newline:\s*'\\n'/);
  assert.match(term, /attachCustomKeyEventHandler\(\(event\) => \{[\s\S]*event\.shiftKey[\s\S]*sendData\('\\n'\)/);
  assert.match(preload, /termScroll:\s*\(id, lines\)\s*=>\s*invoke\('term:scroll'/);
  // xterm の既定ホイール処理を止めるには hostEl の wheel リスナーでは足りない。
  // attachCustomWheelEventHandler で受け取り、tmux へ転送したら false を返す。
  assert.doesNotMatch(term, /addEventListener\('wheel'/);
  assert.match(term, /attachCustomWheelEventHandler\(\(event\) => \{[\s\S]*api\.termScroll[\s\S]*return false;\s*\}\);/);
  assert.match(term, /p\.scrollOffset > 0[\s\S]*\?25l/, '履歴表示中は現在位置のカーソルを重ねない');
  assert.match(renderer, /Enter:\s*'\\r'/);
  assert.doesNotMatch(html, /キー入力はそのまま CLI へ届く/);
  assert.match(renderer, /function setInputMode/);
  assert.doesNotMatch(renderer, /\$\('send'\)\.hidden = busy/);
  assert.match(term, /setInputEnabled/);
});

test('会話開始前後で本文と入力欄のグリッド位置を変えない', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  assert.match(html, /id="conversation-start"[^>]*class="conversation-start"/);
  assert.match(css, /#chat\s*\{[^}]*display:\s*grid[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto\s+auto/s);
  // 段は 4 つ（端末 / ひとこと / 会話履歴 / 入力欄）。ひとことは共有を待っている間だけ出て、
  // 隠れている間の段の高さは 0 なので、会話の見え方は変わらない。
  assert.match(css, /#share-talk\s*\{[^}]*grid-row:\s*2/s);
  assert.match(css, /#conversation-history\s*\{[^}]*grid-row:\s*3/s);
  assert.match(css, /#composer\s*\{[^}]*grid-row:\s*4/s);
  assert.match(css, /scrollbar-gutter:\s*stable/);
  assert.match(renderer, /\$\('conversation-start'\)\.hidden\s*=\s*!!cur/);
});

test('入力モード切替で入力ドックの基準高を変えず、会話履歴は閉じて始める', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const history = html.match(/<details id="conversation-history"[^>]*>/)?.[0] || '';
  assert.ok(history && !/\sopen(?:\s|>)/.test(history), '会話履歴を初期状態で開かない');
  assert.match(css, /\.composer-shell\s*\{[^}]*display:\s*grid[^}]*grid-template-rows:\s*34px minmax\(82px,\s*auto\) 40px/s);
  assert.match(css, /\.terminal-keys\s*\{[^}]*grid-row:\s*2\s*\/\s*4/s);
  assert.doesNotMatch(renderer, /conversation-history'\)\.open\s*=\s*false/, '再描画で利用者の開閉状態を上書きしない');
});

test('会話一覧の各行から対象セッションを削除できる', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  assert.match(renderer, /function removeConversation\(/);
  assert.match(renderer, /el\('button', 'session-remove', '削除'\)/);
  assert.match(renderer, /removeConversation\(s\)/);
  assert.match(css, /\.session-remove\s*\{/);
});

test('新しい会話はエージェントの起動待ちより前に一覧へ表示する', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const created = renderer.indexOf('state.current = await api.createSession');
  const sent = renderer.indexOf('res = await api.send', created);
  const listed = renderer.indexOf('state.sessions = await api.listSessions(state.repo)', created);
  const attached = renderer.indexOf('await attachTerm(state.current.id)', created);
  assert.ok(created >= 0 && listed > created && listed < sent, { created, listed, sent });
  assert.ok(attached > listed && attached < sent, { listed, attached, sent });
});

test('開始スキルは本依頼へ混ぜず、対話セッションへ先に1件ずつ送る', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  const setupSend = ipc.indexOf('conv.send(item.command, resolve, { enterCount })');
  const userSend = ipc.indexOf('conv.send(full, (message)');
  assert.ok(setupSend >= 0 && userSend > setupSend, { setupSend, userSend });
  assert.match(ipc, /cli === 'codex'.*startsWith\('\$'\) \? 2 : 1/);
  assert.doesNotMatch(ipc, /セッション開始時に、まず次のスキルコマンドを実行してください/);
});

test('config.json の主要設定を三つの設定画面から UI コントロールで編集できる', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  assert.match(html, /data-settings-tab="app"[^>]*>アプリ</);
  assert.match(html, /data-settings-tab="instructions"[^>]*>共通指示</);
  assert.match(html, /data-settings-tab="execution"[^>]*>実行制御</);
  assert.match(html, /id="instruction-enabled"/);
  assert.match(html, /id="instruction-text"[^>]*maxlength="8000"/);
  assert.match(html, /id="recommended-skills"/);
  assert.match(html, /id="startup-actions"/);
  for (const tier of ['small', 'medium', 'large']) {
    assert.match(html, new RegExp(`id="tier-${tier}-cli"`));
    assert.match(html, new RegExp(`id="tier-${tier}-model"`));
  }
  assert.match(html, /name="default-policy"[^>]*value="recommended"/);
  assert.match(html, /name="default-policy"[^>]*value="saving"/);
  assert.match(html, /name="default-policy"[^>]*value="quality"/);
  assert.match(html, /id="permission-mode"[\s\S]*value="confirm"[\s\S]*value="auto"[\s\S]*value="ask"/);
  assert.match(html, /id="default-permission-mode"[\s\S]*value="confirm"[\s\S]*value="auto"[\s\S]*value="ask"/);
  assert.doesNotMatch(html, /id="default-readonly"|id="default-auto-approve"/);
  assert.match(html, /id="max-concurrent"[^>]*min="1"[^>]*max="8"/);
  assert.match(html, /id="settings-save"/);
  assert.doesNotMatch(html, /id="config-json"|設定JSON/);
  assert.match(renderer, /summary\.title = summary\.textContent/);
  const styles = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  assert.match(styles, /\.run-settings > summary > span \{[^}]*min-width: 0;[^}]*text-overflow: ellipsis;/);
  assert.match(renderer, /function openSettings/);
  assert.match(renderer, /function settingsPatch/);
  assert.match(renderer, /function renderStartupActions/);
  assert.match(renderer, /api\.listSkills/);
});

test('エージェント応答を思考・回答・実行情報の三層で表示する', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  assert.match(renderer, /function responseDisclosure/);
  assert.match(renderer, /'思考・進捗'/);
  assert.match(renderer, /'実行情報'/);
  assert.match(renderer, /'msg assistant answer-bubble'/);
  assert.match(renderer, /api\.onTurnProgress/);
  assert.match(renderer, /api\.onTurnInfo/);
  assert.match(css, /\.response-turn/);
  assert.match(css, /\.answer-bubble/);
  assert.match(css, /\.response-disclosure/);
});

test('主要メニューは会話・タスク・ワークフローの三領域だけを表示する', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const menu = html.match(/<nav id="areas"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(menu, /id="area-work"[\s\S]*?<span>会話<\/span>/);
  assert.match(menu, /id="area-tasks"[\s\S]*?<span>タスク<\/span>/);
  assert.match(menu, /id="area-workflows"[\s\S]*?<span>ワークフロー<\/span>/);
  assert.doesNotMatch(menu, /id="area-automation"|<span>自動化<\/span>/);
});

test('リポジトリ選択と作成操作は選択中領域の一覧にまとめる', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  assert.match(html, /id="repository-context"[\s\S]*?id="repo-select"/);
  const context = html.match(/<div id="area-sidebar-context"[\s\S]*?<\/div>\s*<button[^>]+id="settings-open"/)?.[0] || '';
  assert.match(context, /id="area-list-title"/);
  assert.match(context, /id="session-new"[^>]*aria-label="新しい会話"/);
  assert.ok(html.indexOf('id="session-new"') > html.indexOf('id="areas"'), '作成操作を主要メニューより後へ置く');
  assert.doesNotMatch(html, /<ul id="repos"/);
});

test('旧領域を三領域へ移行し、各領域の表示名を返す', () => {
  const navigation = require('../src/renderer/navigation');
  assert.strictEqual(navigation.normalizeArea('work'), 'conversation');
  assert.strictEqual(navigation.normalizeArea('automation'), 'tasks');
  assert.strictEqual(navigation.normalizeArea('workflows'), 'workflows');
  assert.strictEqual(navigation.normalizeArea('unknown'), 'conversation');
  assert.deepStrictEqual(navigation.areaInfo('tasks'), { label: 'タスク', createLabel: '新しいタスク', listId: 'tasks' });
});

test('実行状態を取得できない場合も保存済み定義をタスク一覧へ出す', () => {
  const { taskItems } = require('../src/renderer/navigation');
  const saved = [{ machine: 'release-check', name: 'リリース確認' }];
  assert.deepStrictEqual(taskItems({ available: false, machines: [] }, saved), [{
    machine: 'release-check', name: 'リリース確認', parameters: [], schedule: null, history: [],
  }]);
  const runtime = [{ machine: 'release-check', name: 'リリース確認', history: [{ ok: true }] }];
  assert.strictEqual(taskItems({ available: true, machines: runtime }, saved), runtime);
  const catalog = [{ id: 'entry:abc', kind: 'prompt', name: '定期レビュー', schedules: [] }];
  assert.strictEqual(taskItems({ available: true, tasks: catalog, machines: runtime }, saved), catalog);
  assert.deepStrictEqual(taskItems(), []);
  assert.deepStrictEqual(taskItems({ machines: 'invalid' }, null), []);
});

test('ナビゲーション契約はブラウザでは window へ公開する', () => {
  const modulePath = require.resolve('../src/renderer/navigation');
  const originalWindow = global.window;
  try {
    global.window = {};
    delete require.cache[modulePath];
    require(modulePath);
    assert.strictEqual(global.window.AgentNavigation.areaInfo('workflows').label, 'ワークフロー');
  } finally {
    if (originalWindow === undefined) delete global.window;
    else global.window = originalWindow;
    delete require.cache[modulePath];
    require(modulePath);
  }
});

test('設定は三領域とリポジトリごとの最後のタスク・ワークフローを保存する', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-navigation-'));
  assert.strictEqual(store.saveConfig(ud, { area: 'automation' }).area, 'tasks');
  assert.strictEqual(store.saveConfig(ud, { area: 'workflows' }).area, 'workflows');
  const config = store.saveConfig(ud, {
    area: 'tasks', lastTask: { '/repo/a': 'release-check' }, lastWorkflow: { '/repo/a': 'parallel-review' },
  });
  assert.deepStrictEqual(config.lastTask, { '/repo/a': 'release-check' });
  assert.deepStrictEqual(config.lastWorkflow, { '/repo/a': 'parallel-review' });
});

test('領域切替はタスクとワークフローを独立して共有編集面へ伝える', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  assert.match(renderer, /AgentNavigation\.normalizeArea/);
  assert.match(renderer, /\$\('area-tasks'\)\.onclick\s*=\s*\(\)\s*=>\s*showArea\('tasks'\)/);
  assert.match(renderer, /\$\('area-workflows'\)\.onclick\s*=\s*\(\)\s*=>\s*showArea\('workflows'\)/);
  assert.match(renderer, /type:\s*'agent-app:navigate'/);
  assert.doesNotMatch(renderer, /\$\('area-automation'\)/);
});

test('共有編集面は親の領域選択に従い、独自のフォルダと主要タブを表示しない', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/automation-workbench.css'), 'utf8');
  assert.match(html, /id="automation-head"[\s\S]*id="automation-title"[\s\S]*id="automation-description"/);
  assert.match(renderer, /workbenchHost\.setController\(\{[\s\S]*navigate: navigateEmbedded,[\s\S]*refresh: refreshEmbedded,/);
  assert.match(renderer, /state\.homeTab = 'flows'/);
  assert.match(renderer, /state\.homeTab = teachesTask \? 'teach' : 'run'/);
  assert.match(css, /:host \.folder-pane[\s\S]*display:\s*none/);
  assert.match(css, /:host \.home-tabs[\s\S]*display:\s*none/);
});

test('領域切替中は前の領域の操作を隠し、共通見出しを先に更新する', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  assert.match(renderer, /function renderAutomationHeader\(\)/);
  assert.match(renderer, /setAutomationLoading\(true\)[\s\S]*await loadAreaItems\(\)[\s\S]*await syncAutomationWorkbench\(\)[\s\S]*setAutomationLoading\(false\)/);
  assert.match(css, /#automation-content\[aria-busy="true"\] #automation-workbench\s*\{[^}]*visibility:\s*hidden/);
  assert.match(css, /\.area-head\s*\{[^}]*min-height:\s*60px/);
});

test('タスク一覧は定義を先に見せ、実行状態（ファイル実体の確認を伴い遅い）は待たずに裏で重ねる', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  // loadAreaItems（showArea が待つ側）は定義の確認までしか待たない。実行状態は
  // refreshTaskSnapshot が非同期に重ねる——loadTaskItems はそれを呼び出すが待たない。
  assert.match(renderer, /async function loadTaskItems\(repo\)/);
  assert.match(renderer, /async function loadAreaItems\(\)[\s\S]{0,200}loadTaskItems\(state\.repo\)/);
  const loadTaskItemsBody = renderer.slice(
    renderer.indexOf('async function loadTaskItems(repo)'),
    renderer.indexOf('async function loadWorkflowItems'),
  );
  assert.match(loadTaskItemsBody, /Promise\.all\(\[\s*api\.automation\.listMachines\(repo\),\s*api\.automation\.teachingList\(repo\),\s*\]\)/, '定義とteachingは速い呼び出しだけをまとめて待つ');
  assert.doesNotMatch(loadTaskItemsBody, /await api\.automation\.runSnapshot/, 'loadTaskItems は実行状態の取得を待たない');
  assert.match(loadTaskItemsBody, /AgentNavigation\.taskItems\(null, definitions, teaching\)/, '実行状態が無くても定義から一覧を組める');
  assert.match(loadTaskItemsBody, /refreshTaskSnapshot\(repo, token, definitions, teaching\);\s*\n\}/, '実行状態の取得は待たずに委ねて戻る');
  // refreshTaskSnapshot は届いた時点で、別のリポジトリ・領域・一覧へ移っていたら捨てる。
  const refreshBody = renderer.slice(renderer.indexOf('function refreshTaskSnapshot'), renderer.indexOf('async function loadTaskItems'));
  assert.match(refreshBody, /api\.automation\.runSnapshot\(repo\)\.then\(/);
  assert.match(refreshBody, /token !== state\.taskToken \|\| repo !== state\.repo/);
  // 実行状態が届くまでは「未実行」と混同しない専用の表示にする。
  assert.match(renderer, /taskStatusPending:\s*false,/);
  assert.match(renderer, /const pending = state\.taskStatusPending && !task\.teachingStatus;/);
  assert.match(renderer, /pending \? '確認中…'/);
});

test('埋め込みワークベンチの初回表示も、AI 一覧と実行状態を待たない', () => {
  // タスク画面は親の一覧と埋め込みワークベンチの両方が揃って初めて出るので、待ちが片方に
  // 残っていると「押した初回に固まる」。遅いのは外部コマンドを起こす 2 つ——AI 一覧
  // （agent-herd defs）と実行状態（agent-loop inspect）で、Windows では WSL の起動を伴う。
  const maker = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const bodyOf = (from, to) => {
    const start = maker.indexOf(from);
    const end = maker.indexOf(to, start);
    assert.ok(start >= 0 && end > start, `${from} … ${to} が見つからない`);
    return maker.slice(start, end);
  };
  for (const [label, body] of [
    ['起動', bodyOf('async function init() {', 'initPromise = init();')],
    ['親からの遷移', bodyOf('async function navigateEmbedded(payload) {', 'if (workbenchHost) workbenchHost.setController')],
    ['リポジトリの切り替え', bodyOf('async function afterRootChange() {', 'async function addFolder()')],
  ]) {
    assert.doesNotMatch(body, /await\s+loadAgents\(/, `${label}が AI 一覧を待っている`);
    assert.doesNotMatch(body, /await\s+loadExecutionSnapshot\(/, `${label}が実行状態を待っている`);
  }
  // 1 回の表示で重ねて呼ばれるので、同じリポジトリの問い合わせには相乗りする
  assert.match(maker, /if \(agentsInFlight && agentsInFlight\.root === state\.root\) return agentsInFlight\.promise;/);
  // 待たずに読むものは、届いたときに描き直すところまでが 1 組。設定は実行方針とモデルの出どころ
  assert.match(maker, /state\.config = latestConfig;\s*\n\s*renderIfIdle\(\);/, '遅れて届いた設定が画面に出ない');
  // 届くまでは「未実行」「予定なし」「利用できる AI がありません」と混同しない
  assert.match(maker, /const pending = state\.execution\.loading && !state\.execution\.snapshot;/);
  assert.match(maker, /const status = pending \? '確認中…'/);
  assert.match(maker, /state\.agentsLoading \? '確認中…' : '利用できる AI がありません'/);
});

test('初回のタスク画面は worktree の状態確認を待たない', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const selectRepo = renderer.slice(
    renderer.indexOf('async function selectRepo(repo)'),
    renderer.indexOf('// ---- 作業フォルダ'),
  );
  assert.match(selectRepo, /refreshWorktrees\(\{ token \}\);/, 'worktree の取得は裏で開始する');
  assert.doesNotMatch(selectRepo, /await worktreesReady|await refreshWorktrees/, '初回表示を git worktree の確認で止めない');
});

test('タスク選択は設定保存より先に詳細へ伝え、ドラフトの見出しを即時に切り替える', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const selectItem = renderer.slice(
    renderer.indexOf('async function selectAreaItem(area, id)'),
    renderer.indexOf('// リポジトリを選ぶ。'),
  );
  assert.match(selectItem, /configReady = api\.saveConfig/);
  assert.match(selectItem, /state\.config = \{ \.\.\.state\.config, lastTask \};/, '遅れて届く実行状態も現在の選択を維持する');
  assert.match(selectItem, /renderAreaContext\(\);[\s\S]*syncAutomationWorkbench\(\);[\s\S]*await configReady;/,
    '選択表示と詳細の切替は設定保存を待たない');
  assert.doesNotMatch(selectItem, /state\.config = await api\.saveConfig[\s\S]*syncAutomationWorkbench/);
  assert.match(selectItem, /token === state\.selectionToken/, '前の項目の保存結果で現在の選択状態を戻さない');
});

test('タスク詳細は概要・手順・履歴に統一し、対象に応じて編集またはAI見直しを行える', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const workbenchCss = fs.readFileSync(path.join(SRC, 'renderer', 'automation-workbench.css'), 'utf8');
  assert.match(renderer, /detailTab:\s*'overview'/);
  assert.match(renderer, /class="task-detail-tabs"[^>]*role="tablist"/);
  assert.match(fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'teaching.js'), 'utf8'), /<slot name="teaching">/, '編集は親の会話（端末ミラー）を slot に載せる');
  assert.match(renderer, /data-task-tab="overview"[\s\S]*>概要</);
  assert.match(renderer, /data-task-tab="steps"[\s\S]*>手順</);
  assert.match(renderer, /data-task-tab="history"[\s\S]*>履歴</);
  assert.match(renderer, /function taskDetailShellHtml\(/);
  assert.match(renderer, /function bindTaskDetailTabs\(/);
  assert.ok(!renderer.includes('data-task-tab="teach"'), 'AI相談のタブは持たない');
  assert.match(renderer, /id="editing-target"/);
  const indexHtml = fs.readFileSync(path.join(SRC, 'renderer', 'index.html'), 'utf8');
  assert.match(indexHtml, /id="task-launch-agent"/, '起動設定は親の起動カードにある');
  // 権限は会話の「実行設定」と同じ言葉・同じ並びで、作成と編集の両方から選べる（読み取り専用は無い）
  for (const prefix of ['task-create', 'task-launch']) {
    assert.match(indexHtml, new RegExp(`id="${prefix}-permission"><option value="confirm">確認して実行</option><option value="auto">自動承認</option></select>`));
  }
  const teaching = fs.readFileSync(path.join(SRC, 'renderer', 'taskTeaching.js'), 'utf8');
  assert.match(teaching, /autoApprove: \$\(`\$\{prefix\}-permission`\)\.value === 'auto'/, '起動条件に権限を含める');
  assert.match(fs.readFileSync(path.join(SRC, 'main', 'ipc.js'), 'utf8'), /if \(p\.autoApprove != null\) store\.updateSession\(ud, summary\.id, \{ autoApprove: !!p\.autoApprove \}\)/,
    '既にある会話でも権限の切り替えが効く');
  assert.match(renderer, /id="b-assist"[^>]*>編集</);
  assert.match(renderer, /id="b-run"[^>]*>テスト</);
  assert.match(renderer, /class="edit-controls"/, 'エージェントと編集ボタンを一つの操作グループにする');
  assert.match(workbenchCss, /\.task-detail-shell\.is-editor \.task-tab-panel \{[^}]*grid-template-rows: auto minmax\(0, 1fr\)/,
    'ツールバーが折り返しても本文へ重ならない');
  assert.match(workbenchCss, /\.embedded-editor-toolbar \.bar-right \{[^}]*flex-wrap: wrap/,
    '狭いペインでは操作を折り返す');
  assert.match(renderer, /state\.aiReview\.scope = selected \? \{ type: 'step', stepId: selected\.id \} : \{ type: 'workflow' \}/,
    '選択中の工程を編集画面の初期対象へ引き継ぐ');
  assert.match(renderer, /target\.value === 'workflow'[\s\S]*stepId: target\.value\.slice\(5\)/,
    '編集画面内で全体と工程を切り替えられる');
  assert.match(renderer, /function editingCardHtml\([\s\S]*class="task-conversation-editor"/,
    'AI編集は実行カードに二重に囲わず会話レイアウトを使う');
  assert.match(renderer, /teachingFeature\.editorSlotHtml\(machine,[^)]*selected/,
    '選択した編集対象をタスク会話へ引き継ぐ');
  assert.match(renderer, /data-edit-back/);
  assert.match(renderer, /state\.execution\.detailTab === 'history'/);
  assert.match(renderer, /state\.execution\.detailTab === 'overview'[\s\S]*<h3>定期実行<\/h3>/);
  assert.match(renderer, /querySelectorAll\('\[data-task-tab\]'\)/);
  assert.match(renderer, /snapshot\.tasks/);
  assert.match(renderer, /data-task-delete/);
  assert.match(renderer, /automationHost\.deleteMachine/);
  assert.match(renderer, /id="task-run-settings" class="run-settings task-run-settings"/);
  assert.match(renderer, /id="run-policy"/);
  assert.match(renderer, /recommended:\s*\{ label: 'おすすめ'/);
  assert.match(renderer, /saving:\s*\{ label: '節約'/);
  assert.match(renderer, /quality:\s*\{ label: '品質重視'/);
  assert.match(renderer, /direct:\s*\{ label: '直接指定'/);
  assert.match(renderer, /id="run-agent"/);
  assert.match(renderer, /id="run-model"/);
  assert.match(renderer, /run-direct-settings/);
  assert.match(renderer, /id="run-skill-mode"/);
  assert.match(renderer, /data-run-skill/);
  assert.match(renderer, /id="schedule-destination"/);
  assert.match(renderer, />このリポジトリ<\/option>[\s\S]*>共通設定<\/option>/);
  assert.match(renderer, /item\.effective === false[\s\S]*未適用/);
});

test('埋め込み時の名称は自動化や AI ワークフローではなく三領域の語彙に揃える', () => {
  const shell = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const flow = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'flow.js'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  assert.doesNotMatch(shell, />自動化</);
  assert.match(shell, /<statemachine-workbench/);
  assert.match(flow, /const featureName = ctx\.name \|\| 'AIワークフロー'/);
  assert.match(renderer, /name:\s*embedded \? 'ワークフロー' : 'AIワークフロー'/);
});

test('タスクとワークフローの変更は親の一覧へ通知して再読込する', () => {
  const shell = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const maker = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const flow = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'flow.js'), 'utf8');
  assert.match(maker, /type:\s*'agent-app:changed'/);
  assert.match(flow, /ctx\.changed\('workflows'/);
  assert.match(shell, /statemachine:changed/);
  assert.match(shell, /handleAutomationEvent\(event\.detail\)/);
  assert.match(shell, /payload\.type !== 'agent-app:changed'[\s\S]*loadAreaItems\(\)/);
});

test('領域一覧の新規ワークフロー操作は選択中の項目を編集しない', () => {
  const flow = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'flow.js'), 'utf8');
  assert.match(flow, /function create\(\)\s*{\s*view\.workflow = null;\s*view\.creatingTeaching = true;/);
  assert.match(flow, /data-flow-manual-new/);
});

test('ワークフロー教示と差し戻しは通常のDAG依存から分離して表示する', () => {
  const flow = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'flow.js'), 'utf8');
  // 教える会話は agent-app の会話基盤（tmux）で進む。置き場は slot で、AI の一問一答は使わない
  assert.match(flow, /<slot name="flow-teaching"><\/slot>/);
  assert.ok(!flow.includes("mode: 'flow-teach'"), '教示を AI の一問一答（automation:ai）で回さない');
  assert.match(flow, /data-flow-teaching-trial/);
  assert.match(flow, /data-flow-teaching-confirm/);
  assert.match(flow, /flow-rework-lane/);
  assert.match(flow, /差し戻し/);
});

test('preload の窓口と ipc のチャネルが 1 対 1', () => {
  const pre = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  const makerIpc = fs.readFileSync(path.join(SRC, 'main', 'automation', 'handlers.js'), 'utf8');
  const invoked = [...pre.matchAll(/invoke\('([\w:]+)'/g)].map((m) => m[1]);
  const handled = [
    ...[...ipc.matchAll(/handle\('([\w:]+)'/g)].map((m) => m[1]),
    ...[...makerIpc.matchAll(/register\('([\w:]+)'/g)].map((m) => `automation:${m[1]}`),
  ];
  assert.deepStrictEqual([...new Set(invoked)].sort(), handled.sort());
});

test('index.html が読むスクリプトは vendor.js が写すものと画面のもので揃う', () => {
  const html = [
    fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8'),
  ].join('\n');
  const vendor = require('../scripts/vendor');
  const names = new Set(vendor.FILES.map(([, name]) => name));
  for (const m of html.matchAll(/(?:src|href)="vendor\/([^"]+)"/g)) {
    const f = m[1];
    if (f.startsWith('hljs/')) assert.ok(vendor.HLJS_EXTRA.includes(f.slice(5).replace('.min.js', '')), f);
    else assert.ok(names.has(f), `vendor.js が写さない: ${f}`);
  }
  for (const m of html.matchAll(/src="((?:automation\/)?[^"/]+\.js)"/g)) assert.ok(fs.existsSync(path.join(SRC, 'renderer', m[1])), m[1]);
  assert.ok(!html.includes('vendor/statemachine/'), '共有ワークベンチは自分のソース（renderer/automation/）から読む');
});

test('自動化は agent-app の登録リポジトリと設定を共有する', () => {
  const cfg = automationIpc.automationConfig({
    repos: ['/repo/a', '/repo/b'], lastRepo: '/repo/b',
    automationSkillDir: '/skill', automationAgent: 'codex', automationModel: 'm',
    execution: { defaultPolicy: 'quality', tiers: { large: { cli: 'copilot', model: 'large' } } },
  });
  assert.deepStrictEqual(cfg, {
    roots: ['/repo/a', '/repo/b'], lastRoot: '/repo/b', skillDir: '/skill', agent: 'codex', model: 'm', instructions: {},
    execution: { defaultPolicy: 'quality', tiers: { large: { cli: 'copilot', model: 'large' } } },
    taskInputs: {},
  });
  assert.deepStrictEqual(automationIpc.automationPatch({
    roots: ['/ignored'], lastRoot: '/repo/a', skillDir: '/next', agent: 'aider', model: '',
  }), {
    lastRepo: '/repo/a', automationSkillDir: '/next', automationAgent: 'aider', automationModel: '',
  });
  // 手動実行の「前回の値」も、同じ設定の窓口で往復する（値だけ。パスは持たない）
  assert.deepStrictEqual(automationIpc.automationConfig({
    repos: ['/repo/a'], lastRepo: '/repo/a', lastTaskInputs: { '/repo/a': { monthly: { month: '2026-08' } } },
  }).taskInputs, { '/repo/a': { monthly: { month: '2026-08' } } });
  assert.deepStrictEqual(automationIpc.automationPatch({
    taskInputs: { '/repo/a': { monthly: { month: '2026-09' } } },
  }), { lastTaskInputs: { '/repo/a': { monthly: { month: '2026-09' } } } });
});

test('共有編集面は agent-app の preload API（window.api.automation）へ直接つなぐ', () => {
  const shell = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const maker = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const vendor = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'vendor.js'), 'utf8');
  assert.match(shell, /automation\/workbench-element\.js/);
  assert.match(maker, /const automationHost = window\.api\.automation;/);
  assert.doesNotMatch(vendor, /statemachine/);
  assert.doesNotMatch(vendor, /replace\(\/\\bapi/);
});

test('共有編集面へ AI ワークフローの画面と IPC を同じ境界で載せる', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const flow = fs.readFileSync(path.join(SRC, 'renderer/automation/flow.js'), 'utf8');
  assert.ok(html.includes('automation/flow.js'));
  assert.ok(flow.includes('AIワークフロー'));
  assert.ok(preload.includes("invoke('automation:flow:run:start'"));
  assert.ok(preload.includes("invoke('automation:flow:run:respond'"));
  assert.ok(preload.includes("invoke('automation:flow:run:openDelivery'"));
});

test('ワークフロー詳細は選択中リポジトリの実行履歴へ移動できる', () => {
  const flow = fs.readFileSync(path.join(SRC, 'renderer/automation/flow.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/automation-workbench.css'), 'utf8');
  assert.ok(flow.includes('data-flow-tab="overview"') && flow.includes('data-flow-tab="history"'));
  assert.ok(flow.includes('function workflowRuns('), '選択中ワークフローへ履歴を絞る');
  assert.ok(flow.includes('data-flow-run'), '履歴から既存の実行詳細を開く');
  assert.match(flow, /class="execution-card flow-history"/);
  assert.match(css, /:host \.execution-list,[\s\S]*:host \.flow-home-head\s*\{\s*display:\s*none/);
});

test('会話の依頼と新しいタスクを同じ作成フォーム（AI との tmux 会話）へつなぐ', () => {
  const shell = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const maker = fs.readFileSync(path.join(SRC, 'renderer', 'automation', 'renderer.js'), 'utf8');
  const teaching = fs.readFileSync(path.join(SRC, 'renderer', 'taskTeaching.js'), 'utf8');
  assert.ok(html.includes('src="taskIntent.js"') && html.includes('src="taskTeaching.js"') && html.includes('src="teachingProtocol.js"'));
  assert.match(shell, /この依頼をタスクにする/);
  assert.match(shell, /TaskIntent\.create/);
  assert.match(shell, /pendingTaskIntent \? 'new' : ''/, 'intent は新しいタスクの画面として開く');
  assert.match(shell, /api\.automation\.teachingList/);
  assert.match(maker, /payload\.action === 'new'[\s\S]*teachingFeature\.create\(\)/);
  assert.match(teaching, /takeIntent/, '作成フォームが依頼の本文を受け取る');
  assert.match(teaching, /api\.automation\.teachStart\(/);
});

test('定義があるタスクは実行詳細から開き、一覧の状態語を共有ワークベンチと揃える', () => {
  const shell = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const maker = fs.readFileSync(path.join(SRC, 'renderer/automation/renderer.js'), 'utf8');
  const teaching = fs.readFileSync(path.join(SRC, 'renderer/automation/teaching.js'), 'utf8');
  assert.ok(!maker.includes('!!selectedTask.machine'), '既存定義を一律に教示画面へ送らない');
  assert.ok(maker.includes('data-run-teach') && maker.includes('AIに変更を相談'));
  assert.ok(teaching.includes('function presentTeachingStatus('));
  assert.ok(!shell.includes('試運転が必要') && !teaching.includes('試運転が必要'));
  assert.match(shell, /draft: '下書き', ready: '利用可能'/);
  assert.ok(!/teachingLabels = \{[^}]*needs-trial/.test(shell), 'タスクに試運転の状態は持たない（作成は AI との会話、確認は実行）');
});

// 同梱定義から出る argv。権限フラグと prompt の渡し方は agent-dashboard のゴールデンと同じ。
test('argv: 初回ターン（write / readonly）', () => {
  const uuid = /^[0-9a-f-]{36}$/;
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'P', model: 'M' });
  assert.match(claude.mintedSession, uuid);
  assert.deepStrictEqual(claude.argv, ['claude', '--session-id', claude.mintedSession, '-p', '--output-format', 'text',
    '--dangerously-skip-permissions', '--model', 'M']);
  assert.strictEqual(claude.stdin, 'P');

  const copilot = agentCli.turnCmd(agentCli.load('copilot'), { prompt: 'P', readonly: true });
  assert.deepStrictEqual(copilot.argv, ['copilot', '--session-id', copilot.mintedSession, '-s', '--allow-all-tools', '--no-color',
    '--available-tools=view,grep,glob', '--disable-builtin-mcps', '--no-custom-instructions', '-p', 'P']);
  assert.ok(copilot.readonlyWarning, 'best-effort の CLI には警告が付く');

  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'P' });
  assert.deepStrictEqual(codex.argv.slice(0, 2), ['codex', 'exec']);
  assert.ok(codex.argv.includes('--json') && codex.argv.at(-1) === '-' && codex.outputFile);
  assert.ok(codex.argv.includes(codex.outputFile));

  const kiro = agentCli.turnCmd(agentCli.load('kiro'), { prompt: 'P', model: 'M' });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'M', 'P']);
  assert.ok(kiro.listArgs);
});

test('argv: 継続ターンは resume をサブコマンド直後に差し込む', () => {
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'P', cliSession: 'S' });
  assert.deepStrictEqual(claude.argv, ['claude', '--resume', 'S', '-p', '--output-format', 'text', '--dangerously-skip-permissions']);
  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'P', cliSession: 'T' });
  assert.deepStrictEqual(codex.argv.slice(0, 4), ['codex', 'exec', 'resume', 'T']);
  const kiro = agentCli.turnCmd(agentCli.load('kiro'), { prompt: 'P', cliSession: 'K', readonly: true });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--resume-id', 'K', '--no-interactive', '--trust-tools=fs_read', 'P']);
});

test('argv: セッション機能の無い CLI は履歴を再送する', () => {
  const history = [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }];
  const t = agentCli.turnCmd(agentCli.load('vscode-copilot'), { prompt: 'c', history });
  assert.ok(t.stdin.includes('[user] a') && t.stdin.includes('[assistant] b') && t.stdin.endsWith('新しい依頼:\nc'));
  const cursor = agentCli.turnCmd(agentCli.load('cursor'), { prompt: 'c', history });
  assert.ok(cursor.argv.includes('--continue'), 'continue_args を持つ CLI はそれを使う');
});

// ターンごとにエージェントを変えられる: history は「その CLI がまだ見ていない分」で、
// セッションを再開できる CLI でも、別の CLI で進めた分があればそれを依頼の前に添える。
test('argv: 別の CLI で進めた分は、戻ってきた CLI へ追いつかせる', () => {
  const unseen = [
    { role: 'user', text: 'codex で直して', cli: 'codex', attachments: [{ rel: 'src/a.ts', name: 'a.ts' }] },
    { role: 'assistant', text: '直した', cli: 'codex' },
  ];
  // 再開できる CLI（claude）に未読があれば、resume しつつ本文の頭に添える
  const back = agentCli.turnCmd(agentCli.load('claude'), { prompt: '確認して', cliSession: 'S', history: unseen });
  assert.deepStrictEqual(back.argv.slice(0, 3), ['claude', '--resume', 'S']);
  assert.ok(back.stdin.startsWith('この会話には、あなたのセッションの外で進んだやり取りがある'), back.stdin);
  assert.ok(back.stdin.includes('[user → codex] codex で直して\n（添付: src/a.ts）') && back.stdin.includes('[assistant (codex)] 直した'));
  assert.ok(back.stdin.endsWith('新しい依頼:\n確認して'));
  // この会話で初めて使う CLI は、新しいセッションを発行して全部を添える
  const first = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', history: unseen });
  assert.ok(first.mintedSession && first.stdin.startsWith('これまでの会話（同じセッションの続きとして扱うこと）'));
  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'p', history: unseen });
  assert.ok(codex.stdin.startsWith('これまでの会話'), 'capture 型の CLI も初回は再送する');
  // 未読が無ければ本文はそのまま
  assert.strictEqual(agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', cliSession: 'S', history: [] }).stdin, 'p');
  // 添付は file_flag を宣言する CLI にだけ argv で渡る（本文には呼び出し側が書く）
  const aider = agentCli.turnCmd(agentCli.load('aider'), { prompt: 'p', files: ['/tmp/x.png', '/tmp/y.md'] });
  assert.ok(aider.argv.includes('--file') && aider.argv[aider.argv.indexOf('--file') + 1] === '/tmp/x.png' && aider.argv.filter((a) => a === '--file').length === 2);
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', files: ['/tmp/x.png'] });
  assert.ok(!claude.argv.includes('/tmp/x.png'));
});

// 対話起動（tmux）。write_args は interactive 節のものだけで、ヘッドレスの危険フラグは継承しない。
test('argv: 対話起動は interactive 節から組み、プロンプトを含まない', () => {
  const claude = agentCli.load('claude');
  assert.ok(claude.interactive && claude.interactive.busyPattern.includes('esc to interrupt'));
  const first = agentCli.interactiveCmd(claude, { model: 'M' });
  assert.deepStrictEqual(first.argv, ['claude', '--session-id', first.mintedSession, '--model', 'M']);
  assert.ok(!first.argv.includes('--dangerously-skip-permissions'));
  const approved = agentCli.interactiveCmd(claude, { model: 'M', autoApprove: true });
  assert.ok(approved.argv.includes('--dangerously-skip-permissions'));
  const ro = agentCli.interactiveCmd(claude, { readonly: true, cliSession: 'S' });
  assert.deepStrictEqual(ro.argv, ['claude', '--resume', 'S', '--permission-mode', 'plan']);
  assert.strictEqual(ro.mintedSession, '');
  assert.strictEqual(ro.resumed, true, 'resume できたなら文脈は引き継がれている');
  assert.strictEqual(first.resumed, false);

  const kiro = agentCli.interactiveCmd(agentCli.load('kiro'), { model: 'M' });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--trust-all-tools', '--model', 'M']);
  const kiroAgain = agentCli.interactiveCmd(agentCli.load('kiro'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(kiroAgain.argv, ['kiro-cli', 'chat', '--trust-all-tools']);
  assert.ok(kiroAgain.warning, '再開手段が無い CLI は起動し直すと履歴を添える旨を出す');
  assert.strictEqual(kiroAgain.resumed, false, '文脈は引き継げていない（最初の依頼で追いつかせる）');

  const codex = agentCli.interactiveCmd(agentCli.load('codex'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(codex.argv, ['codex']);
  assert.strictEqual(codex.resumed, false, 'IDを捕捉できていないCodexを無関係な直前セッションへ接続しない');

  const copilot = agentCli.interactiveCmd(agentCli.load('copilot'));
  assert.ok(copilot.argv.includes('--no-auto-update'));
  assert.ok(copilot.argv.includes('--allow-all-tools'));
  assert.ok(copilot.argv.includes('--allow-all-paths'));
  assert.match(agentCli.load('copilot').interactive.readyPattern, /┃/);
  assert.match(agentCli.load('copilot').interactive.busyPattern, /pending/);
  // interactive 節の無い定義は対話起動できない（一覧の印も false）
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-agents-'));
  fs.writeFileSync(path.join(dir, 'plain.json'), JSON.stringify({ command: ['plain-cli', '-p'], prompt_via: 'stdin' }));
  const saved = process.env.KIRO_AGENTS_DIR;
  process.env.KIRO_AGENTS_DIR = dir;
  try {
    assert.strictEqual(agentCli.load('plain').interactive, null);
    assert.throws(() => agentCli.interactiveCmd(agentCli.load('plain'), {}), /interactive/);
    assert.strictEqual(agentCli.list('').find((a) => a.name === 'plain').interactive, false);
  } finally { process.env.KIRO_AGENTS_DIR = saved; }
  assert.ok(agentCli.list('').find((a) => a.name === 'claude').interactive);
});

test('応答から端末の装飾と kiro の入力欄を剥がす', () => {
  // ipc.js は electron を require するので、その部分だけ差し替えて読む
  const Module = require('module');
  const orig = Module._load;
  Module._load = function (req, ...rest) { return req === 'electron' ? { ipcMain: {}, dialog: {}, shell: {}, app: { on() {} } } : orig.call(this, req, ...rest); };
  let ipc;
  try { ipc = require('../src/main/ipc'); } finally { Module._load = orig; }
  assert.strictEqual(ipc.cleanAnswer('\x1b[38;5;141m> \x1b[0mみかん\n\x1b[?25h'), 'みかん');
  // ターンの起動条件は画面から届いたものが勝ち、無ければ会話の既定
  const sess = { cli: 'claude', model: 'm1', readonly: false, autoApprove: false };
  assert.deepStrictEqual(ipc.turnSpec(sess, { prompt: ' p ' }), { cli: 'claude', model: 'm1', readonly: false, autoApprove: false, text: 'p' });
  assert.deepStrictEqual(ipc.turnSpec(sess, { prompt: 'p', cli: 'Codex', model: '', readonly: true, autoApprove: true }), { cli: 'codex', model: '', readonly: true, autoApprove: true, text: 'p' });
  assert.throws(() => ipc.turnSpec(sess, { prompt: ' ' }), /空/);
  assert.strictEqual(ipc.turnSpec(sess, { prompt: '', attachments: [{ rel: 'a' }] }).text, '', '添付だけの依頼は通す');
  const config = settings.normalize({});
  config.execution.tiers.small = { cli: 'codex', model: 'small-model' };
  assert.deepStrictEqual(ipc.executionSpec(sess, { prompt: ' p ', policy: 'saving' }, config), {
    cli: 'codex', model: 'small-model', readonly: false, autoApprove: false, text: 'p',
    policy: 'saving', tier: 'small', source: 'policy',
  });
  assert.deepStrictEqual(ipc.executionSpec(sess, { prompt: 'p', policy: 'direct', cli: 'kiro', model: 'm2', readonly: true }, config), {
    cli: 'kiro', model: 'm2', readonly: true, autoApprove: false, text: 'p',
    policy: 'direct', tier: '', source: 'direct',
  });
  assert.ok(ipc.sameLaunch({ cli: 'a', model: '', readonly: false }, { cli: 'a', readonly: 0 }));
  assert.ok(!ipc.sameLaunch({ cli: 'a', model: 'x' }, { cli: 'a', model: 'y' }));
  assert.ok(!ipc.sameLaunch(null, { cli: 'a' }));
  // 添付: 写したファイルはホスト側のパスで、作業フォルダの中のファイルは相対パスで本文に添える
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-'));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-repo-'));
  fs.writeFileSync(path.join(repo, 'a.ts'), 'x');
  const staged = attachments.stage(ud, 'shot.png', Buffer.from([1, 2, 3]));
  const w = ipc.withAttachments(ud, '依頼', [{ id: staged.id, name: 'shot.png' }, { rel: './a.ts' }, { rel: 'a.ts' }], { fsDir: repo });
  assert.deepStrictEqual(w.atts, [{ id: staged.id, name: 'shot.png', size: 3 }, { rel: 'a.ts', name: 'a.ts' }, { rel: 'a.ts', name: 'a.ts' }]);
  assert.deepStrictEqual(w.files, [require('../src/main/host').toHostPath(attachments.pathOf(ud, staged.id, 'shot.png'))]);
  assert.ok(w.prompt.startsWith('依頼\n\n添付ファイル') && w.prompt.includes('- a.ts（作業フォルダの中）') && w.prompt.includes(`/${staged.id}/shot.png`), w.prompt);
  assert.strictEqual(ipc.withAttachments(ud, '依頼', [], { fsDir: repo }).prompt, '依頼');
  assert.ok(ipc.withAttachments(ud, '', [{ rel: 'a.ts' }], { fsDir: repo }).prompt.startsWith('添付ファイル'));
  assert.throws(() => ipc.withAttachments(ud, 'p', [{ rel: '../etc/passwd' }], { fsDir: repo }), /外/);
  assert.throws(() => ipc.withAttachments(ud, 'p', [{ id: staged.id, name: 'other.png' }], { fsDir: repo }), /見つかりません/);
  assert.strictEqual(ipc.stripAnsi('plain'), 'plain');
  assert.strictEqual(ipc.cleanAnswer('> 引用ではなく入力欄\n本文'), '引用ではなく入力欄\n本文');
  const legacyAider = ipc.presentSession({ messages: [{
    role: 'assistant', cli: 'aider', text: '► **THINKING**\n\n考えた\n\n---\n► **ANSWER**\n\n答え\n\nTokens: 1k sent, 2 received.',
    parts: { thinking: [], information: [{ title: 'aider の対話セッション' }] },
  }] });
  assert.strictEqual(legacyAider.messages[0].text, '答え');
  assert.deepStrictEqual(legacyAider.messages[0].parts.thinking, [{ text: '考えた', status: 'done' }]);
  assert.strictEqual(legacyAider.messages[0].parts.information.length, 1);
  assert.strictEqual(text.stripAnsi('\x1b]0;title\x07x\x1b]8;;http://a\x1b\\y'), 'xy', 'OSC は BEL でも ST でも閉じる');
  // Windows のヘッドレスは wsl.exe に載せ、cwd を WSL 表記へ直す
  const spec = ipc.spawnSpec('claude', ['-p'], { cwd: 'C:\\work\\repo', env: { A: '1' }, distro: 'Ubuntu' });
  if (process.platform === 'win32') {
    assert.strictEqual(spec.command, 'wsl.exe');
    assert.ok(spec.args.join(' ').includes("cd '/mnt/c/work/repo'"));
  } else {
    assert.strictEqual(spec.command, 'claude');
  }
});

test('host.toWslPath: WSL へ渡すと決めた場面の変換は、この端末の OS を見ない', () => {
  const host = require('../src/main/host');
  assert.strictEqual(host.toWslPath('C:\\work\\repo'), '/mnt/c/work/repo');
  assert.strictEqual(host.toWslPath('\\\\wsl$\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(host.toWslPath('\\\\wsl.localhost\\Ubuntu\\home\\me'), '/home/me');
  // すでに WSL 表記のもの・相対パス・空はそのまま
  assert.strictEqual(host.toWslPath('/home/me/repo'), '/home/me/repo');
  assert.strictEqual(host.toWslPath('.statemachine/x/workflow.yaml'), '.statemachine/x/workflow.yaml');
  assert.strictEqual(host.toWslPath(''), '');
});

test('host.wslArgv: WSL ログインシェル経由の 1 回起動 argv を組む（cwd は WSL 表記へ直す）', () => {
  const host = require('../src/main/host');
  // ディストロ指定あり・env の上書きあり
  const withDistro = host.wslArgv('agent-loop', ['inspect', '--json'], {
    cwd: 'C:\\work\\repo', env: { FOO: 'a b' }, distro: 'Ubuntu',
  });
  assert.strictEqual(withDistro.command, 'wsl.exe');
  assert.deepStrictEqual(withDistro.args.slice(0, 2), ['-d', 'Ubuntu']);
  assert.deepStrictEqual(withDistro.args.slice(2, 5), ['-e', 'bash', '-lc']);
  const script = withDistro.args[5];
  assert.ok(script.includes("export FOO='a b';"), script);
  assert.ok(script.includes("exec 'agent-loop' 'inspect' '--json'"), script);
  assert.ok(script.includes("cd '/mnt/c/work/repo'"), script);
  // 引数はここでは直さない（どれがパスかは呼ぶ側しか知らない）
  assert.ok(host.wslArgv('agent-loop', ['--dir', 'C:\\work\\repo'], { cwd: 'C:\\work\\repo' })
    .args[3].includes("exec 'agent-loop' '--dir' 'C:\\work\\repo'"));
  // ディストロ未指定（既定）・env の上書きなし → -d を付けず、export も無い
  const withoutDistro = host.wslArgv('agent-flow', ['patterns', '--json'], { cwd: '/home/me/repo' });
  assert.deepStrictEqual(withoutDistro.args.slice(0, 3), ['-e', 'bash', '-lc']);
  assert.ok(!withoutDistro.args.join(' ').includes('export'));
  assert.ok(withoutDistro.args[3].includes("cd '/home/me/repo'"), withoutDistro.args[3]);
});

test('automation: WSL へ載せ替えるときは --dir / --bus の値も WSL 表記へ直す', () => {
  const { hostPathArgs } = automationIpc;
  // agent-loop / agent-herd の作業対象
  assert.deepStrictEqual(
    hostPathArgs(['inspect', '--json', '--dir', 'C:\\work\\repo']),
    ['inspect', '--json', '--dir', '/mnt/c/work/repo'],
  );
  assert.deepStrictEqual(
    hostPathArgs(['statemachine', '--workflow', '.statemachine/x/workflow.yaml', '--dir', '\\\\wsl$\\Ubuntu\\home\\me\\repo']),
    ['statemachine', '--workflow', '.statemachine/x/workflow.yaml', '--dir', '/home/me/repo'],
  );
  // agent-flow の共有 bus
  assert.deepStrictEqual(
    hostPathArgs(['--bus', 'C:\\Users\\me\\.agents\\flow\\bus', 'cancel', 'app-1', '--reason', '中止']),
    ['--bus', '/mnt/c/Users/me/.agents/flow/bus', 'cancel', 'app-1', '--reason', '中止'],
  );
  // 自由文は触らない——依頼文が偶然パスの形をしていても、パスのオプションの値でなければそのまま
  assert.deepStrictEqual(
    hostPathArgs(['run', 'C:\\work\\repo の状況をまとめて', '--agent-cli', 'aider']),
    ['run', 'C:\\work\\repo の状況をまとめて', '--agent-cli', 'aider'],
  );
  assert.deepStrictEqual(hostPathArgs(['-p', 'C:\\work\\repo']), ['-p', 'C:\\work\\repo']);
  // 値の無い末尾のオプション・空の引数でも壊れない
  assert.deepStrictEqual(hostPathArgs(['inspect', '--dir']), ['inspect', '--dir']);
  assert.deepStrictEqual(hostPathArgs([]), []);
});

test('automation: agent-herd / agent-loop / agent-flow だけを Windows で WSL ログインシェル経由に載せ替える', () => {
  const adapter = fs.readFileSync(path.join(SRC, 'main/automation/ipc.js'), 'utf8');
  // 名前の一族と、platform ゲート・host.wslArgv への配線を、ソースの形として確かめる
  // （Windows 実機でしか実行時の分岐を通せないため）。
  assert.match(adapter, /HERD_FAMILY_COMMANDS\s*=\s*new Set\(\['agent-herd',\s*'agent-loop',\s*'agent-flow'\]\)/);
  assert.match(adapter, /function herdCommandSpawnSpec\(/);
  assert.match(adapter, /host\.hostOf\(cwd,\s*store\.loadConfig\(userData\(\)\)\.wslDistro\)\.distro/);
  assert.match(adapter, /host\.wslArgv\(command,\s*hostPathArgs\(args\),\s*\{\s*cwd,\s*distro\s*\}\)/);
  assert.match(adapter, /HOST_PATH_OPTIONS\s*=\s*new Set\(\['--dir',\s*'--bus'\]\)/);
  // bus へ書く workspace.local（WSL の中で `git -C` に渡る）もホストの表記で渡す
  assert.match(adapter, /hostPath:\s*host\.toHostPath,/);
  assert.match(adapter, /process\.platform === 'win32' && \(onHost \|\| HERD_FAMILY_COMMANDS\.has\(name\)\)/);

  const userData = () => require('os').tmpdir();
  const route = automationIpc.makeTaskCommandSpawnSpec(userData);
  // 一族以外（python / playwright-cli / winauto など）は載せ替えない
  assert.strictEqual(route('python'), undefined);
  assert.strictEqual(route('playwright-cli'), undefined);
  assert.strictEqual(route('winauto'), undefined);
  if (process.platform === 'win32') {
    for (const name of ['agent-herd', 'agent-loop', 'agent-flow']) {
      assert.strictEqual(typeof route(name), 'function', `${name} は WSL 経由に載せ替える`);
    }
    // 呼ぶ側が host: true と言えば、一族の名前でなくても WSL 経由（CLI を直接起こす AI 支援・手動実行）
    assert.strictEqual(typeof route('claude', { host: true }), 'function');
    assert.strictEqual(typeof route('python3', { host: true }), 'function');
  } else {
    assert.strictEqual(route('claude', { host: true }), undefined);
    // WSL 経由に載せ替えるのは Windows だけ（他の OS はもともとネイティブに実体がある）
    for (const name of ['agent-herd', 'agent-loop', 'agent-flow']) assert.strictEqual(route(name), undefined);
  }
});

test('ERE → RegExp: POSIX のブラケットクラスを写す', () => {
  const re = text.ereToRegExp('^[[:space:]]*[>?❯›][[:space:]]*$|│[[:space:]]*[>❯›]');
  assert.ok(re.test('  > '));
  assert.ok(re.test('│ ❯ 依頼を書く'));
  assert.ok(!re.test('> 本文がある'));
  assert.ok(text.ereToRegExp('^[[:blank:]]*[>?❯›]([[:blank:]].*)?$').test('> foo'));
  assert.strictEqual(text.ereToRegExp('('), null);
});

test('kiro の一覧から、このターン以後に更新された最新を選ぶ', () => {
  const json = JSON.stringify([{ cwd: '/r', sessions: [
    { sessionId: 'old', updatedAt: '2026-01-01T00:00:00Z' },
    { sessionId: 'new', updatedAt: '2026-09-05T00:00:10Z' },
    { sessionId: 'newer', updatedAt: '2026-09-05T00:00:20Z' },
  ] }, { cwd: '/other', sessions: [{ sessionId: 'x', updatedAt: '2026-09-06T00:00:00Z' }] }]);
  assert.strictEqual(agentCli.pickListedSession(json, '/r', Date.parse('2026-09-05T00:00:00Z')), 'newer');
  assert.strictEqual(agentCli.pickListedSession(json, '/r', Date.parse('2026-09-07T00:00:00Z')), '');
  assert.strictEqual(agentCli.pickListedSession('not json', '/r', 0), '');
});

test('定義の一覧は同名先勝ちで、command[0] の有無を印にする', () => {
  const all = agentCli.list('');
  const names = all.map((a) => a.name);
  assert.ok(names.includes('claude') && names.includes('copilot') && names.includes('kiro'));
  assert.strictEqual(new Set(names).size, names.length);
  assert.strictEqual(all.find((a) => a.name === 'kiro').session, 'list');
});

test('店: 会話の作成・追記・一覧・更新・削除', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-'));
  store.addRepo(ud, '/repo/a');
  assert.ok(store.isRegistered(ud, '/repo/a') && !store.isRegistered(ud, '/repo/b'));
  const s = store.createSession(ud, { repo: '/repo/a', cli: 'claude', readonly: true, policy: 'saving', tier: 'small' });
  assert.strictEqual(s.transport, 'tmux', '既定は tmux');
  store.appendMessage(ud, s.id, { role: 'user', text: '最初の依頼\n2 行目' });
  store.appendMessage(ud, s.id, { role: 'assistant', text: '答え', code: 0 });
  store.updateSession(ud, s.id, { cli: 'codex', model: 'm', policy: 'quality', tier: 'large', ignored: 'no', transport: 'headless', worktree: 'other', live: { cli: 'codex', model: 'm', readonly: false } });
  store.setCliEntry(ud, s.id, 'claude', { id: 'X' });
  store.setCliEntry(ud, s.id, 'claude', { seen: 2 });
  store.setCliEntry(ud, s.id, 'claude', { setupApplied: true });
  const got = store.readSession(ud, s.id);
  assert.strictEqual(got.worktree, '', '作業フォルダは会話を作ったあとは変えられない');
  assert.strictEqual(got.title, '最初の依頼');
  assert.deepStrictEqual([got.cli, got.model], ['codex', 'm'], 'エージェントとモデルは「次のターン」の既定として変えられる');
  assert.deepStrictEqual([got.policy, got.tier], ['quality', 'large']);
  assert.deepStrictEqual(store.cliEntry(got, 'claude'), { id: 'X', seen: 2, setupApplied: true }, 'CLI ごとにセッション ID・見た数・初回設定の適用を持つ');
  assert.strictEqual(store.cliEntry(got, 'codex'), null);
  assert.deepStrictEqual(got.live, { cli: 'codex', model: 'm', readonly: false });
  assert.strictEqual(got.transport, 'headless');
  assert.strictEqual(got.ignored, undefined);
  assert.strictEqual(got.messages.length, 2);
  assert.deepStrictEqual([store.listSessions(ud, '/repo/a')[0].cli, store.listSessions(ud, '/repo/a')[0].policy], ['codex', 'quality']);
  // 以前の形（cliSession 1 つ）は読むときに cliSessions へ写す
  const legacyId = '00000000-0000-4000-8000-000000000001';
  fs.writeFileSync(path.join(store.sessionsDir(ud), `${legacyId}.json`), JSON.stringify({ id: legacyId, repo: '/repo/a', cli: 'claude', cliSession: 'OLD', messages: [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }], updatedAt: '2026-01-01T00:00:00Z' }));
  const legacy = store.readSession(ud, legacyId);
  assert.deepStrictEqual(store.cliEntry(legacy, 'claude'), { id: 'OLD', seen: 2 });
  assert.strictEqual(legacy.cliSession, undefined);
  assert.deepStrictEqual(store.readAllSessions(ud).map((x) => x.id).sort(), [s.id, legacyId].sort());
  store.removeSession(ud, legacyId);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/a').map((x) => x.id), [s.id]);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/b'), []);
  // 作業フォルダ（worktree）を持つ会話は、名前とブランチを覚えて一覧にも出す
  const w = store.createSession(ud, { repo: '/repo/a', cli: 'claude', worktree: 'feature-x', branch: 'feature/x' });
  assert.deepStrictEqual([w.worktree, w.branch], ['feature-x', 'feature/x']);
  const listed = store.listSessions(ud, '/repo/a').find((x) => x.id === w.id);
  assert.deepStrictEqual([listed.worktree, listed.branch], ['feature-x', 'feature/x']);
  store.removeSession(ud, w.id);
  assert.throws(() => store.readSession(ud, '../etc'), /不正/);
  store.removeSession(ud, s.id);
  assert.deepStrictEqual(store.listSessions(ud, ''), []);
  const cfg = store.saveConfig(ud, { wslDistro: ' Ubuntu ', transport: 'bogus', view: 'files', lastFiles: { '/repo/a': 'README.md' } });
  assert.strictEqual(cfg.wslDistro, 'Ubuntu');
  assert.strictEqual(cfg.transport, 'tmux');
  assert.strictEqual(cfg.useWorktree, true, '作業フォルダの機能は既定で使える');
  assert.strictEqual(store.saveConfig(ud, { useWorktree: false }).useWorktree, false, '切ったら覚える');
  assert.strictEqual(cfg.view, 'files');
  assert.strictEqual(cfg.lastFiles['/repo/a'], 'README.md');
});

test('tmuxセッションは最終利用から24時間保持し旧形式も正規化する', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-terminal-session-'));
  const session = store.createSession(ud, { repo: '/repo/a', cli: 'cursor' });
  const now = new Date('2026-09-06T10:00:00.000Z');

  const saved = store.touchTerminalSession(ud, session.id, {
    name: 'agent-app-owned', state: 'active', ownerInstanceId: 'instance-a', cli: 'cursor', model: 'm',
  }, now);

  assert.strictEqual(saved.terminalSession.lastUsedAt, now.toISOString());
  assert.strictEqual(saved.terminalSession.expiresAt, '2026-09-07T10:00:00.000Z');
  assert.deepStrictEqual(saved.terminalSnapshots, []);
  assert.deepStrictEqual(store.staleTerminalSessions(ud, new Date('2026-09-07T09:59:59.000Z')), []);
  assert.strictEqual(store.staleTerminalSessions(ud, new Date('2026-09-07T10:00:01.000Z'))[0].id, session.id);
});

test('エージェント切替前の端末画面を上限付きスナップショットとして残す', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-terminal-snapshot-'));
  const session = store.createSession(ud, { repo: '/repo/a', cli: 'cursor' });
  for (let i = 0; i < 14; i += 1) {
    store.addTerminalSnapshot(ud, session.id, {
      agentCli: `agent-${i}`, model: 'm', reason: 'agent_switch', screenText: `screen-${i}`,
    });
  }
  const saved = store.readSession(ud, session.id);
  assert.strictEqual(saved.terminalSnapshots.length, 12);
  assert.strictEqual(saved.terminalSnapshots[0].agentCli, 'agent-2');
  assert.strictEqual(saved.terminalSnapshots.at(-1).screenText, 'screen-13');
});

function makeRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-git-'));
  const run = (args) => execFileSync('git', ['-C', repo, ...args], { stdio: 'pipe' });
  run(['init', '-q']);
  run(['config', 'user.email', 't@example.com']);
  run(['config', 'user.name', 't']);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
  run(['add', '.']);
  run(['commit', '-qm', 'init']);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'two\n');
  fs.writeFileSync(path.join(repo, 'b.txt'), 'new\n');
  return repo;
}

test('git: 作業ツリーの変更をホストのシェル経由で読む', async () => {
  const repo = makeRepo();
  const res = await git.changes(repo);
  assert.deepStrictEqual(res.files.map((f) => [f.file, f.label]), [['a.txt', '変更'], ['b.txt', '新規']]);
  assert.ok(res.diff.includes('-one') && res.diff.includes('+two'));
  assert.ok(res.branch, 'ブランチ名が付く');
  assert.ok((await git.fileDiff(repo, 'b.txt')).includes('+new'));
  const not = await git.changes(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-nogit-')));
  assert.ok(not.error);
  require('../src/main/host').closeAll();
});

test('ファイル: ツリー・本文・言語判定・外へ出ない', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-files-'));
  fs.mkdirSync(path.join(repo, 'src', 'deep'), { recursive: true });
  fs.mkdirSync(path.join(repo, '.git'));
  fs.writeFileSync(path.join(repo, 'README.md'), '# Title\n\n```mermaid\ngraph TD; A-->B\n```\n');
  fs.writeFileSync(path.join(repo, 'src', 'index.ts'), 'export const x = 1;\n');
  fs.writeFileSync(path.join(repo, 'src', 'deep', 'Dockerfile'), 'FROM node\n');
  fs.writeFileSync(path.join(repo, 'bin.dat'), Buffer.from([0, 1, 2, 3]));
  fs.writeFileSync(path.join(repo, 'pic.png'), Buffer.from('89504e470d0a1a0a', 'hex'));
  // 作業フォルダの置き場（.worktrees）はリポジトリの写しなので、本体のツリーには出さない
  fs.mkdirSync(path.join(repo, '.worktrees', 'feature-x', 'src'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.worktrees', 'feature-x', 'src', 'index.ts'), 'export const x = 1;\n');
  const root = await files.listDir(repo, '');
  assert.deepStrictEqual(root.entries.map((e) => e.name), ['src', 'bin.dat', 'pic.png', 'README.md'], 'ディレクトリ先・名前順・.git と .worktrees は出さない');
  assert.strictEqual(root.entries.find((e) => e.name === 'README.md').language, 'markdown');
  const src = await files.listDir(repo, 'src');
  assert.deepStrictEqual(src.entries.map((e) => e.rel), ['src/deep', 'src/index.ts']);
  const ts = await files.readFile(repo, 'src/index.ts');
  assert.strictEqual(ts.kind, 'text');
  assert.strictEqual(ts.language, 'typescript');
  assert.strictEqual(ts.lines, 2);
  assert.strictEqual((await files.readFile(repo, 'bin.dat')).kind, 'binary');
  assert.ok((await files.readFile(repo, 'pic.png')).dataUrl.startsWith('data:image/png;base64,'));
  assert.strictEqual(files.languageOf('src/deep/Dockerfile'), 'dockerfile');
  assert.strictEqual(files.languageOf('Makefile'), 'makefile');
  assert.strictEqual(files.languageOf('x.unknownext'), '');
  assert.strictEqual(files.languageOf('.gitignore'), 'plaintext');
  await assert.rejects(files.readFile(repo, '../../etc/passwd'), /外/);
  await assert.rejects(files.listDir(repo, '..'), /外/);
  assert.throws(() => files.resolveInside(repo, '..'), /外/);
  const { hits, truncated } = await files.find(repo, 'index');
  assert.deepStrictEqual(hits.map((h) => h.rel), ['src/index.ts'], '名前検索が worktree の分だけ重複しない');
  assert.strictEqual(truncated, false);
  // 作業フォルダ自身を根にすれば、その中は普通に見える
  const inWt = await files.listDir(path.join(repo, '.worktrees', 'feature-x'), '');
  assert.deepStrictEqual(inWt.entries.map((e) => e.name), ['src']);
});

test('添付: 写す・引く・消す。名前は 1 要素に丸め、外へ出ない', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-'));
  const a = attachments.stage(ud, '../../evil/../shot.png', Buffer.from('png'));
  assert.strictEqual(a.name, 'shot.png');
  assert.match(a.id, /^[0-9a-f-]{36}$/);
  const r = attachments.resolve(ud, a.id, a.name);
  assert.strictEqual(fs.readFileSync(r.path, 'utf8'), 'png');
  assert.ok(r.path.startsWith(path.join(ud, 'attachments', a.id)));
  assert.throws(() => attachments.resolve(ud, '../x', 'shot.png'), /不正/);
  assert.throws(() => attachments.resolve(ud, a.id, 'nope.png'), /見つかりません/);
  assert.throws(() => attachments.stage(ud, 'big.bin', Buffer.alloc(attachments.MAX_BYTES + 1)), /大きすぎます/);
  const src = path.join(ud, 'src.md');
  fs.writeFileSync(src, '# doc');
  const b = attachments.stageFile(ud, src);
  assert.strictEqual(b.name, 'src.md');
  assert.strictEqual(b.size, 5);
  assert.throws(() => attachments.stageFile(ud, ud), /ファイルではありません/);
  // 会話を消すと、そのメッセージが参照している添付も消える
  attachments.discardAll(ud, { messages: [{ role: 'user', attachments: [{ id: a.id, name: a.name }, { rel: 'x' }] }] });
  assert.ok(!fs.existsSync(path.join(ud, 'attachments', a.id)));
  assert.ok(fs.existsSync(path.join(ud, 'attachments', b.id)));
  attachments.discard(ud, b.id);
  assert.ok(!fs.existsSync(path.join(ud, 'attachments', b.id)));
  assert.strictEqual(attachments.discard(ud, b.id), true, '無くても失敗にしない');
  // 起動時の掃除: どの会話も参照していないものだけ消す
  const kept = attachments.stage(ud, 'kept.txt', Buffer.from('k'));
  const orphan = attachments.stage(ud, 'orphan.txt', Buffer.from('o'));
  fs.mkdirSync(path.join(ud, 'attachments', 'not-an-id'));
  assert.strictEqual(attachments.sweep(ud, [{ messages: [{ role: 'user', attachments: [{ id: kept.id, name: 'kept.txt' }] }] }]), 1);
  assert.ok(fs.existsSync(path.join(ud, 'attachments', kept.id)) && !fs.existsSync(path.join(ud, 'attachments', orphan.id)) && fs.existsSync(path.join(ud, 'attachments', 'not-an-id')));
  assert.strictEqual(attachments.sweep(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-none-')), []), 0);
});

test('ファイル: 名前検索は索引を使い回し、生成物のフォルダに潜らず、浅い前方一致を先に出す', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-find-'));
  const mk = (rel, body = '') => { fs.mkdirSync(path.dirname(path.join(repo, rel)), { recursive: true }); fs.writeFileSync(path.join(repo, rel), body); };
  mk('index.html');
  mk('src/index.ts');
  mk('src/deep/reindex.ts');
  mk('node_modules/pkg/index.js');
  mk('dist/index.js');
  mk('build');                                 // フォルダではないので「潜らない」対象にならない
  mk('.worktrees/wt/src/index.ts');
  const first = await files.find(repo, 'index');
  assert.deepStrictEqual(first.hits.map((h) => h.rel), ['index.html', 'src/index.ts', 'src/deep/reindex.ts'], '前方一致 → 部分一致、それぞれ浅い順。node_modules / dist / .worktrees は出ない');
  assert.strictEqual(first.truncated, false);
  assert.ok(first.indexed >= 5);
  // 索引は少しの間そのまま（新しいファイルはまだ見えない）。「更新」で作り直す
  mk('src/index2.ts');
  assert.deepStrictEqual((await files.find(repo, 'index2')).hits, []);
  assert.deepStrictEqual((await files.find(repo, 'index2', 200, { refresh: true })).hits.map((h) => h.rel), ['src/index2.ts']);
  // `/` を含めばパスで探せる
  assert.deepStrictEqual((await files.find(repo, 'deep/re')).hits.map((h) => h.rel), ['src/deep/reindex.ts']);
  assert.deepStrictEqual((await files.find(repo, 'build')).hits.map((h) => h.rel), ['build']);
  // 件数の上限で打ち切られても、浅い階層は必ず載る（幅優先）
  const small = await files.buildIndex(repo, { maxEntries: 3 });
  assert.strictEqual(small.truncated, true);
  assert.ok(small.entries.every((e) => !e.rel.includes('/')), `浅い階層だけ: ${small.entries.map((e) => e.rel)}`);
  assert.deepStrictEqual(files.searchIndex([{ rel: 'a/b.txt', name: 'b.txt', type: 'file', language: '' }], '', 10), []);
  files.forgetIndex(repo);
});

test('ファイル: main を止めない（同期 I/O をツリー・本文・検索に使わない）', () => {
  const src = fs.readFileSync(path.join(SRC, 'main/files.js'), 'utf8');
  const body = src.slice(src.indexOf('async function listDir'));
  assert.doesNotMatch(body.replace(/function forgetIndex[\s\S]*$/, ''), /readdirSync|statSync|readFileSync|openSync|readSync/, 'ツリー・本文・検索は fs.promises で読む');
  assert.match(src, /INDEX_TTL_MS/);
  assert.match(fs.readFileSync(path.join(SRC, 'renderer/files.js'), 'utf8'), /filterSeq/, '遅れて届いた検索結果は捨てる');
});

test('起動: ホストの確認と git を待たずに画面を出し、送信前にだけ待つ', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const initBody = renderer.slice(renderer.indexOf('async function init()'));
  assert.doesNotMatch(initBody.slice(0, initBody.indexOf('await selectRepo')), /await api\.hostInfo\(\)/, 'host:info を待ってから画面を組まない');
  assert.match(renderer, /state\.hostReady = api\.hostInfo\(\)/);
  assert.match(renderer, /await Promise\.all\(\[state\.agentsReady, state\.hostReady\]\)/, '送信は CLI の有無と tmux の有無が届いてから');
  assert.match(renderer, /listWorktrees\(state\.repo, \{ withStatus: false \}\)[\s\S]*listWorktrees\(state\.repo, \{ withStatus: true \}\)/, 'worktree の一覧を先に、変更数はあとから');
  assert.match(renderer, /repoToken/, '遅れて届いた返事は捨てる');
  const selectBody = renderer.slice(renderer.indexOf('async function selectRepo'), renderer.indexOf('async function refreshWorktrees'));
  assert.doesNotMatch(selectBody, /await api\.listAgents/, 'agents:list（ホストの PATH）を待って一覧を描かない');
  assert.doesNotMatch(selectBody, /await refreshWorktrees/, 'git worktree list / status を待って一覧を描かない');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  assert.match(preload, /listWorktrees: \(repo, opts\)/);
});

test('店: 会話一覧は変わっていないファイルを読み直さず、変わったものは反映する', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-list-'));
  const a = store.createSession(ud, { repo: '/r', cli: 'claude' });
  assert.strictEqual(store.listSessions(ud, '/r')[0].count, 0);
  store.appendMessage(ud, a.id, { role: 'user', text: 'hello' });
  assert.strictEqual(store.listSessions(ud, '/r')[0].count, 1, '追記は一覧の件数へ反映される');
  assert.strictEqual(store.listSessions(ud, '/r')[0].title, 'hello');
  store.removeSession(ud, a.id);
  assert.deepStrictEqual(store.listSessions(ud, '/r'), []);
});

test('ファイル: 索引は相対パスの並び（WSL 内の git ls-files）からも作れ、失敗すれば fs で歩く', async () => {
  const idx = files.indexFromPaths(['src/deep/b.ts', 'src/a.ts', 'README.md', './src/a.ts', 'dir\\win.txt', '']);
  assert.deepStrictEqual(idx.entries.map((e) => `${e.type}:${e.rel}`),
    ['dir:dir', 'file:README.md', 'dir:src', 'file:dir/win.txt', 'file:src/a.ts', 'dir:src/deep', 'file:src/deep/b.ts'], '途中のフォルダも載せ、浅い順 → 名前順（大文字小文字を無視）');
  assert.strictEqual(idx.entries.find((e) => e.rel === 'src/a.ts').language, 'typescript');
  assert.strictEqual(idx.truncated, false);
  assert.strictEqual(files.indexFromPaths(['a', 'b', 'c'], { maxEntries: 2 }).truncated, true);
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-lister-'));
  fs.writeFileSync(path.join(repo, 'real.txt'), '');
  const listed = await files.find(repo, 'ghost/', 200, { refresh: true, lister: async () => ['ghost/from-git.txt'] });
  assert.deepStrictEqual(listed.hits.map((h) => h.rel), ['ghost/from-git.txt'], 'lister の並びをそのまま索引にする（fs には無いパス）');
  const fallback = await files.find(repo, 'real', 200, { refresh: true, lister: async () => null });
  assert.deepStrictEqual(fallback.hits.map((h) => h.rel), ['real.txt'], 'lister が null なら fs で歩く');
  const failed = await files.find(repo, 'real', 200, { refresh: true, lister: async () => { throw new Error('git がない'); } });
  assert.deepStrictEqual(failed.hits.map((h) => h.rel), ['real.txt'], 'lister が失敗しても fs で歩く');
  files.forgetIndex(repo);
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  assert.match(ipc, /host\.isWslUnc\(dirs\.fsDir\)/, '\\\\wsl$\\ のリポジトリだけ WSL の中で git ls-files を撃つ');
  assert.match(ipc, /'ls-files', '--cached', '--others', '--exclude-standard', '-z'/);
});

test('「確認待ち」は答える場所（端末操作）への行き先で、答え方は決めない', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const css = fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8');
  // 状態の印は 1 つの span のまま。答えを並べるパネルは持たない
  assert.match(html, /<span id="phase" class="phase" hidden><\/span>/);
  assert.ok(!html.includes('phase-menu'), '確認待ちに答えのパネルを作らない');
  assert.ok(!/id="phase-(yes|no|enter)"/.test(html), 'はい・いいえのような答えのボタンを置かない');
  // `y` は効く CLI と効かない CLI があり、見分けるには文言を読むことになる（ADR-1）。
  // 仮想キー行にも足さない——足したキーは「送るキー」だけで、答えではない
  assert.ok(!/data-terminal-key="[yn]"/.test(html), '答えを当てにいくキーを仮想キー行へ足さない');
  // 押すと端末操作へ移る（焦点も端末へ）。答えられる phase のときだけ押せる
  assert.match(renderer, /node\.onclick = \(\) => setInputMode\('terminal'\)/);
  assert.match(renderer, /const answerable = ph\.phase === 'attention'/);
  assert.match(renderer, /node\.className = `phase \$\{ph\.phase\}\$\{answerable \? ' answerable' : ''\}`/);
  assert.match(css, /\.phase\.answerable \{[^}]*cursor: pointer/);
  // 一覧からも、開いてそのまま端末操作まで行く
  assert.match(renderer, /pick\.onclick = \(\) => openSession\(s\.id, \{ answer: answering \}\)/);
  assert.match(renderer, /await attaching;\s*\n\s*if \(state\.current && state\.current\.id === id\) setInputMode\('terminal'\)/);
  // ポップアップの種別は元の 2 つに戻る
  assert.match(renderer, /POPUP_MENU_SELECTOR = 'details\.more-menu\[open\], details\.run-settings\[open\]'/);
});

test('回答の下の「定型の依頼」と「入力欄に戻す」は入力欄に入れるだけで送らない', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  // 部品は既存の .message-actions / .message-action（「この依頼をタスクにする」と同じ）
  const quick = renderer.slice(renderer.indexOf('function quickRequestActions('), renderer.indexOf('// index … 会話の messages'));
  assert.match(quick, /el\('div', 'message-actions'\)/);
  assert.match(quick, /el\('button', 'message-action', request\.label\)/);
  // 出すのは最後の応答の下だけ。応答中は出さない
  assert.match(quick, /index !== cur\.messages\.length - 1/);
  assert.match(quick, /state\.running\.has\(cur\.id\) \|\| state\.pending\.has\(cur\.id\)/);
  // 押しても送らない（送信は利用者）
  assert.match(quick, /button\.onclick = \(\) => fillPrompt\(request\.text\)/);
  assert.ok(!/fillPrompt[\s\S]{0,400}sendPrompt\(\)/.test(renderer.slice(renderer.indexOf('function fillPrompt('))), 'fillPrompt は送信しない');
  // 依頼を入力欄へ戻すのは、本文と添付の両方
  assert.match(renderer, /again\.onclick = \(\) => fillPrompt\(m\.text, \{ attachments: m\.attachments \|\| \[\] \}\)/);
  // 設定の行は起動時アクションと同じ形
  assert.match(html, /id="quick-add" class="small">追加<\/button>[\s\S]*id="quick-requests" class="startup-actions"/);
  assert.match(renderer, /el\('div', 'startup-row quick-row'\)/);
});

test('前面に無いときの通知は、既に流している合図から出す（通知専用の経路を作らない）', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  assert.match(ipc, /channel === 'turn:done'[\s\S]*notifier\.show/);
  assert.match(ipc, /channel === 'term:phase' && payload && payload\.phase === 'attention'/);
  assert.match(ipc, /onRunExit: \(\{ name, mode, result \}\)/);
  assert.match(ipc, /enabled: \(\) => store\.loadConfig\(userData\(\)\)\.notify\.background !== false/);
  assert.match(preload, /onNotifyOpen: on\('notify:open'\)/);
  assert.match(html, /id="notify-background"[\s\S]*バックグラウンドで通知する/);
});

test('変更ビューの行からも、ファイルビュアーと同じ「会話に添付」ができる', () => {
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  // 添付の中身は 1 本（相対パスを次の依頼に添えるだけで、写さない）
  assert.match(renderer, /function attachRepoFile\(rel\) \{[\s\S]*addAttachments\(\[\{ rel, name: rel\.split\('\/'\)\.pop\(\) \}\]\)/);
  assert.match(renderer, /attach\.onclick = \(event\) => \{ event\.stopPropagation\(\); attachRepoFile\(f\.file\); \}/);
  // 消えたファイルは添えられない
  assert.match(renderer, /if \(f\.label !== '削除'\) \{/);
});

test('会話は名前で絞り込め、名前を変えられる', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer/renderer.js'), 'utf8');
  // 絞り込みの 1 行はファイルツリーと同じ形（.tree-tools + input）
  assert.match(html, /<div id="session-filter-row" class="tree-tools">[\s\S]*id="session-filter" placeholder="名前で絞り込み"/);
  assert.match(renderer, /state\.sessions\.filter\(\(s\) => String\(s\.title \|\| ''\)\.toLowerCase\(\)\.includes\(needle\)\)/);
  assert.match(renderer, /\$\('session-filter-row'\)\.hidden = state\.area !== 'conversation'/);
  // 名前の変更は「その他」の 1 行（新しいダイアログは作らない）
  assert.match(html, /id="session-rename" hidden>会話名を変更</);
  assert.match(renderer, /api\.updateSession\(cur\.id, \{ title \}\)/);
});
