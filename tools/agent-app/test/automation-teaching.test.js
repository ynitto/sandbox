'use strict';

// タスクを AI と作る tmux 会話の材料: 見本の依頼の約束事、下書き（sidecar）、最初の依頼文、
// 見本の記録（Markdown）、会話（kind: 'task'）の紐づけ。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const protocol = require('../src/renderer/teachingProtocol');
const teaching = require('../src/main/automation/teaching');
const machineStore = require('../src/main/automation/store');
const store = require('../src/main/store');

const SRC = path.join(__dirname, '..', 'src');

test('見本の依頼は @record の 1 行で拾う（最後の 1 件。引用や箇条書きの飾りは無視）', () => {
  assert.deepStrictEqual(protocol.parseRecordRequest('了解です。\n@record browser https://example.test/list\n'), { source: 'browser', target: 'https://example.test/list' });
  assert.deepStrictEqual(protocol.parseRecordRequest('- @record windows 勤怠管理'), { source: 'windows', target: '勤怠管理' });
  assert.deepStrictEqual(protocol.parseRecordRequest('> @record browser: `https://a.test`'), { source: 'browser', target: 'https://a.test' });
  assert.deepStrictEqual(protocol.parseRecordRequest('@record browser'), { source: 'browser', target: '' });
  assert.deepStrictEqual(protocol.parseRecordRequest('@record browser a\n@record windows b'), { source: 'windows', target: 'b' });
  assert.strictEqual(protocol.parseRecordRequest('record browser は書かない'), null);
  assert.strictEqual(protocol.parseRecordRequest(''), null);
  assert.strictEqual(protocol.recordLine('windows', '勤怠'), '@record windows 勤怠');
});

test('ブラウザの見本の固定文: 開始は接続先と attach/recording-start を、終了は recording-stop と保存先と detach を伝える', () => {
  const start = protocol.recordingStartMessage({ endpoint: 'http://localhost:9222', url: 'https://a.test/list', browser: 'Edge' });
  assert.match(start, /^@recording start\n/);
  assert.match(start, /ブラウザ（Edge）をリモートデバッグ付きで起動しました。接続先: http:\/\/localhost:9222/);
  assert.match(start, /開始 URL: https:\/\/a\.test\/list/);
  assert.match(start, /`playwright-cli attach --cdp=http:\/\/localhost:9222`/);
  assert.match(start, /`playwright-cli recording-start`/);
  assert.match(start, /利用者が操作している間は、あなたはブラウザを操作しない/);
  assert.match(protocol.recordingStartMessage({}), /接続先: http:\/\/localhost:9222[\s\S]*開始 URL: （未指定/);
  const stop = protocol.recordingStopMessage({ machine: 'monthly' });
  assert.match(stop, /^@recording stop\n/);
  assert.match(stop, /`playwright-cli recording-stop`/);
  assert.match(stop, /`\.statemachine\/monthly\/recordings\/<時刻>-browser\.md`/);
  assert.match(stop, /`playwright-cli detach`/);
  assert.match(stop, /パスワードらしい値は定義に残さない/);
  assert.strictEqual(protocol.DEFAULT_ENDPOINT, 'http://localhost:9222');
});

test('保存名は目的の 1 行目から作り、英数字にならなければ job-<乱数>', () => {
  assert.strictEqual(teaching.machineNameFor('Monthly Sales Report\n詳細'), 'monthly-sales-report');
  assert.match(teaching.machineNameFor('毎月の売上を集計する'), /^job-[0-9a-f]{8}$/);
  assert.match(teaching.machineNameFor(''), /^job-[0-9a-f]{8}$/);
});

test('下書きは .statemachine/<名前>/teaching.json に会話 ID と見本の控えだけを持ち、定義ができれば利用可能', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-teaching-'));
  assert.strictEqual(teaching.load(root, 'draft'), null);
  const saved = teaching.save(root, 'draft', { title: '売上集計', purpose: '毎月の売上を集計する', sessionId: '00000000-0000-4000-8000-000000000001' });
  assert.strictEqual(saved.version, 2);
  assert.strictEqual(saved.sessionId, '00000000-0000-4000-8000-000000000001');
  assert.ok(saved.createdAt && saved.updatedAt);
  assert.strictEqual(teaching.save(root, 'draft', { ...saved, sessionId: 'bogus' }).sessionId, '', '会話 ID は UUID だけ');
  assert.deepStrictEqual(teaching.list(root).map((item) => [item.machine, item.status, item.published]), [['draft', 'draft', false]]);
  machineStore.save(root, { name: '売上集計', machine: 'draft', purpose: '集計', steps: [{ kind: 'agent', title: '集計', detail: '集計する' }] });
  assert.deepStrictEqual(teaching.list(root).map((item) => [item.machine, item.status, item.published]), [['draft', 'ready', true]]);
  assert.deepStrictEqual(teaching.presentStatus({ published: true }), { status: 'ready', published: true, runnable: true });
  assert.deepStrictEqual(teaching.presentStatus({ published: false }), { status: 'draft', published: false, runnable: false });
  assert.throws(() => teaching.fileFor(root, '../x'), /識別名/);
});

test('最初の依頼文は保存先・statemachine-use の作成モード・見本の依頼の作法・ブラウザは固定文を待って CDP で記録することを伝える', () => {
  const win = teaching.prompt({ machine: 'monthly', purpose: '毎月の売上を集計する', skillDir: '/mnt/c/repo/.github/skills/statemachine-use', platform: 'win32', tools: { browser: true, windows: true } });
  assert.match(win, /\.statemachine\/monthly\//);
  assert.match(win, /statemachine-use/);
  assert.match(win, /run_machine\.py \.statemachine\/monthly\/workflow\.yaml --dry-run/);
  assert.match(win, /@record browser <開始 URL>/);
  assert.match(win, /@record windows <アプリ名>/);
  assert.match(win, /「操作の見本」の「記録を始める」を押すよう伝えて待ちます/);
  assert.match(win, /WSL の tmux で動いていて、利用者の画面（ブラウザ・Windows アプリ）は Windows 側/);
  assert.match(win, /Windows 側の playwright-cli を WSL から起こすことはできません/);
  // ブラウザ: アプリが Windows 側で Edge を起こし、固定文が届いてから AI が CDP で接続して記録する
  assert.match(win, /Windows 側で Edge をリモートデバッグ付き（http:\/\/localhost:9222）で起こし/);
  assert.match(win, /`@recording start` で始まる固定文/);
  assert.match(win, /`playwright-cli attach --cdp=http:\/\/localhost:9222`[\s\S]*`playwright-cli recording-start`/);
  assert.match(win, /`@recording stop` で始まる固定文[\s\S]*`playwright-cli recording-stop`[\s\S]*`\.statemachine\/monthly\/recordings\/<時刻>-browser\.md`[\s\S]*`playwright-cli detach`/);
  assert.match(win, /固定文が届く前に自分でブラウザを起こしたり記録を始めたりしない/);
  assert.match(win, /WSL のネットワークが mirrored でないと localhost が Windows 側に届きません/);
  // Windows アプリ: 記録はアプリが取る
  assert.match(win, /Windows アプリの見本: 記録はこのアプリが winauto で取り/);
  assert.match(win, /あなた自身は winauto の記録を起こさない/);
  assert.match(win, /ブラウザ（Edge）・Windows アプリ（winauto）/);
  assert.match(win, /利用者の目的:\n毎月の売上を集計する/);
  assert.match(win, /`playwright-cli` スキル[\s\S]*`windows-app-automation` スキル/);
  const linux = teaching.prompt({ machine: 'monthly', existing: true, platform: 'linux', tools: { browser: false, windows: false } });
  assert.match(linux, /今の工程を短く要約してから、利用者に変更したい点を聞いて/);
  assert.doesNotMatch(linux, /利用者の目的:/);
  assert.match(linux, /利用者の画面はこのアプリと同じ端末にあります/);
  assert.doesNotMatch(linux, /Windows 側で Edge|mirrored/);
  assert.match(linux, /見本を取る道具（Edge \/ winauto）が見つかっていません/);
  assert.match(linux, /python \.github\/skills\/statemachine-use\/scripts\/run_machine\.py/);
  const follow = teaching.demonstrationPrompt({ machine: 'monthly', hostPath: '/home/me/repo/.statemachine/monthly/recordings/r.md', source: 'browser', target: 'https://a.test', steps: 2, parameters: ['month'] });
  assert.match(follow, /\/home\/me\/repo\/\.statemachine\/monthly\/recordings\/r\.md を読んで/);
  assert.match(follow, /2 工程の候補[\s\S]*month/);
});

test('下書きの再開と既存タスクの編集は保存済みファイルから文脈を復元する依頼を作る', () => {
  const draft = teaching.resumePrompt({ machine: 'monthly', purpose: '毎月の売上を集計する' });
  assert.match(draft, /下書き作成を再開/);
  assert.match(draft, /\.statemachine\/monthly\//);
  assert.match(draft, /workflow\.yaml、actions\/\*\.md/);
  assert.match(draft, /タスクの目的: 毎月の売上を集計する/);
  assert.match(draft, /保存済みの下書きを踏まえ/);

  const edit = teaching.resumePrompt({ machine: 'monthly', existing: true, context: '工程 aggregate' });
  assert.match(edit, /タスクの編集を開始/);
  assert.match(edit, /今回の編集対象: 工程 aggregate/);
  assert.match(edit, /現在の定義を短く要約/);
  assert.match(edit, /まだファイルは変更しない/);
});

test('見本の記録は Markdown にして recordings/ へ置き、操作の行は本文と同じ形で書く（パスワードは残さない）', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-recording-'));
  const recording = {
    source: 'browser', url: 'https://a.test/login', parameters: ['user'],
    steps: [
      { kind: 'browser', title: 'ログインする', target: 'https://a.test/login', detail: '1. 「ユーザー」入力欄に {{user}} を入力する\n2. 「ログイン」ボタンを押す',
        recorded: [
          { op: 'goto', target: 'https://a.test/login', label: 'https://a.test/login' },
          { op: 'fill', target: "getByRole('textbox', { name: 'ユーザー' })", role: 'textbox', label: 'ユーザー', value: '{{user}}', example: 'me' },
          { op: 'click', target: "getByRole('button', { name: 'ログイン' })", role: 'button', label: 'ログイン' },
        ] },
    ],
  };
  const saved = teaching.saveRecording(root, 'login', recording);
  assert.match(saved.relative, /^\.statemachine\/login\/recordings\/\d{8}T\d{6}Z-browser\.md$/);
  const body = fs.readFileSync(saved.file, 'utf8');
  assert.match(body, /^# 操作の見本（ブラウザ）/);
  assert.match(body, /開始 URL: https:\/\/a\.test\/login/);
  assert.match(body, /毎回変わる値の候補: `\{\{user\}\}`/);
  assert.match(body, /### 1\. ログインする/);
  assert.match(body, /goto https:\/\/a\.test\/login\nfill getByRole\('textbox', \{ name: 'ユーザー' \}\) "\{\{user\}\}"\nclick getByRole\('button', \{ name: 'ログイン' \}\)/);
  assert.strictEqual(saved.sidecar.recordings.length, 1);
  assert.strictEqual(teaching.load(root, 'login').recordings[0].steps, 1);
  assert.throws(() => teaching.saveRecording(root, 'login', { source: 'browser', steps: [] }), /工程がありません/);
  const win = teaching.recordingMarkdown({ source: 'windows', target: '勤怠管理', steps: [{ title: '出力', recorded: [{ op: 'click', target: 'auto_id:=Export' }], check: 'winauto wait name:=完了 --app 勤怠管理' }] });
  assert.match(win, /`winauto` の 1 コマンド/);
  assert.match(win, /click "auto_id:=Export"/);
  assert.match(win, /完了確認の候補: `winauto wait name:=完了 --app 勤怠管理`/);
});

test('タスクの会話は kind: task で保存名に紐づき、会話一覧には出ない', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-task-session-'));
  const chat = store.createSession(ud, { repo: '/r', cli: 'claude' });
  const task = store.createSession(ud, { repo: '/r', cli: 'claude', kind: 'task', task: { machine: 'monthly' } });
  assert.throws(() => store.createSession(ud, { repo: '/r', cli: 'claude', kind: 'task' }), /保存名/);
  assert.deepStrictEqual(store.listSessions(ud, '/r').map((s) => s.id), [chat.id], '会話一覧はふつうの会話だけ');
  assert.deepStrictEqual(store.listSessions(ud, '/r', { kind: 'task' }).map((s) => [s.id, s.machine]), [[task.id, 'monthly']]);
  assert.strictEqual(store.findTaskSession(ud, '/r', 'monthly').id, task.id);
  assert.strictEqual(store.findTaskSession(ud, '/r', 'other'), null);
  assert.deepStrictEqual(store.readSession(ud, chat.id).task, null);
  assert.strictEqual(store.readSession(ud, chat.id).kind, 'conversation');
});

test('タスクの会話は agent-app の会話基盤で開き、ブラウザの見本は Edge を起こして固定文を tmux へ、Windows アプリの見本は所在を WSL 表記で送る', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main', 'ipc.js'), 'utf8');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer', 'taskTeaching.js'), 'utf8');
  const html = fs.readFileSync(path.join(SRC, 'renderer', 'index.html'), 'utf8');
  for (const channel of ['automation:teach:start', 'automation:teach:session', 'automation:teach:demonstration', 'automation:teach:browser']) {
    assert.ok(ipc.includes(`handle('${channel}'`), channel);
    assert.ok(preload.includes(`invoke('${channel}'`), channel);
  }
  assert.match(ipc, /kind: 'task', task: \{ machine \}/, 'タスクの会話は kind: task');
  assert.match(ipc, /await guardedRunTurn\(session\.id, \{\s*prompt,/, '最初の依頼は会話と同じターンの経路で送る');
  assert.match(ipc, /const liveTmux = !!conversation && !conversation\.closed && !\['dead', 'gone'\]\.includes\(conversation\.phase\)/,
    '既存の tmux が生きているかを終了状態まで含めて判定する');
  assert.match(ipc, /if \(!busy && !liveTmux\) \{[\s\S]*teaching\.resumePrompt/,
    '起動済み tmux へは固定の再開プロンプトを送らず、そのまま接続する');
  assert.match(ipc, /const hostPath = host\.toHostPath\(saved\.file\)/, '記録の所在は WSL 表記へ直してから AI へ');
  assert.match(ipc, /recordingBrowser\.findBrowser\(/, '見本を取るブラウザはこの端末で探す');
  assert.match(ipc, /recordingBrowser\.launchRecordingBrowser\(\{[\s\S]*profileDir: path\.join\(userData\(\), recordingBrowser\.PROFILE_DIR\)/, '記録用のプロファイルは userData の下');
  assert.doesNotMatch(ipc, /resolvePath\('playwright-cli'\)/, 'ブラウザの見本にこの端末の playwright-cli は要らない（AI 側が使う）');
  // 固定文は renderer が会話の送信経路（send / termSubmit = tmux）で送る。main は Edge を起こすだけ
  assert.match(renderer, /api\.automation\.teachBrowser\(rec\.target\)/);
  assert.match(renderer, /await sendText\(TeachingProtocol\.recordingStartMessage\(\{ endpoint: opened\.endpoint/);
  assert.match(renderer, /await sendText\(TeachingProtocol\.recordingStopMessage\(\{ machine: state\.machine \}\)\)/);
  assert.match(renderer, /if \(state\.running\) res = await api\.termSubmit\(sess\.id, text\);/, '応答中は端末へそのまま流す');
  assert.doesNotMatch(renderer, /source: rec\.source/, 'この端末の playwright-cli でブラウザを記録する経路は残さない');
  assert.match(renderer, /api\.termOpen\(session\.id/);
  assert.doesNotMatch(renderer, /else if \(state\.editing\) \{ await startTeaching\(token\); return; \}/, '編集画面を開いただけでは AI を起こさない');
  assert.match(renderer, /\$\('task-launch-start'\)\.onclick = \(\) => startTeaching\(\)/, '編集開始ボタンで tmux を開く');
  assert.match(renderer, /api\.automation\.teachPrepare\(/, '作成時は下書きとセッションを先に準備する');
  assert.match(renderer, /state\.autoStart = \{ repo: state\.repo, machine: view\.machine, options \}/, '作成した下書きの画面でセッションを自動起動する');
  assert.match(html, /id="task-create-agent"/);
  assert.match(html, /id="task-create-model"/);
  assert.match(html, /id="task-launch-agent"/);
  assert.match(html, /id="task-launch-model"/);
  assert.match(html, /id="task-create-start"[^>]*>作成開始</);
  assert.doesNotMatch(html, /id="task-create-cancel"/, '新規作成画面に戻るボタンを表示しない');
  assert.match(html, /class="task-save-name"[^>]*>[\s\S]*<strong>保存名<\/strong>/);
  assert.doesNotMatch(html, /<summary>保存名を指定<\/summary>/);
  assert.match(html, /class="run-settings task-execution-settings"[\s\S]*id="task-create-agent"[\s\S]*id="task-create-model"/,
    '新規作成は手動実行と同じ設定コントロールを使う');
  assert.match(html, /id="task-terminal-placeholder" class="terminal-stage"/, '編集開始前から黒い tmux プレースホルダーを表示する');
  assert.match(html, /id="task-composer-placeholder"/, '編集開始前から入力欄ぶんを予約し、tmux 領域の位置と寸法を固定する');
  assert.match(html, /id="task-launch-settings-summary"[\s\S]*id="task-launch-agent"[\s\S]*id="task-launch-model"/,
    '編集開始も新規作成と同じ設定コントロールを使う');
  assert.match(html, /id="task-launch-start"[^>]*>編集開始</);
  assert.match(renderer, /'起動中です\.\.\.'/, 'tmux の準備中は待機領域へ状態を表示する');
  assert.doesNotMatch(html, /id="task-launch-title"[^>]*>AIと編集</, '編集画面の中で「AIと編集」を繰り返さない');
  assert.match(renderer, /\$\('task-launch-heading'\)\.hidden = state\.published/, '公開済みタスクでは下書き用見出しも隠す');
  assert.match(renderer, /api\.automation\.recordingStart\(\{ root: state\.repo, source: 'windows'/);
  assert.match(renderer, /api\.automation\.teachDemonstration\(/);
  assert.match(renderer, /TeachingProtocol\.parseRecordRequest\(message && message\.text\)/, 'AI の @record 行で見本のカードを開く');
  assert.match(renderer, /記録はこの端末で取る・Windows では WSL へ渡す/, '仕組みの説明は画面に常駐させない（README にある）');
  assert.match(html, /<div slot="teaching" id="task-teaching" hidden>/);
  assert.match(html, /id="task-term-host"/);
  assert.match(html, /id="task-mode-terminal"/);
  assert.match(html, /id="task-record-open"[^>]*>操作の見本<\/button>/, 'AI編集の中から手動でも記録を開始できる');
  assert.match(html, /id="task-record-stop"[^>]*>終了してAIへ渡す</);
  // 端末と入力欄は会話画面と同じ実体を使う（見た目を作り直さない）
  assert.match(html, /id="task-terminal" class="terminal-stage"/);
  assert.match(html, /id="task-composer" class="composer-shell"/);
  const term = fs.readFileSync(path.join(SRC, 'renderer', 'term.js'), 'utf8');
  assert.match(term, /window\.TaskTerm = createTerm\(\)/, '会話とタスクで別の端末ミラーを持つ');
});
