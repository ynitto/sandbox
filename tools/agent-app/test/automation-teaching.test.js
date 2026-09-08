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

test('最初の依頼文は保存先・statemachine-use の作成モード・見本の依頼の作法・記録がこの端末側で取られることを伝える', () => {
  const win = teaching.prompt({ machine: 'monthly', purpose: '毎月の売上を集計する', skillDir: '/mnt/c/repo/.github/skills/statemachine-use', platform: 'win32', tools: { browser: true, windows: true } });
  assert.match(win, /\.statemachine\/monthly\//);
  assert.match(win, /statemachine-use/);
  assert.match(win, /run_machine\.py \.statemachine\/monthly\/workflow\.yaml --dry-run/);
  assert.match(win, /@record browser <開始 URL>/);
  assert.match(win, /@record windows <アプリ名>/);
  assert.match(win, /WSL の tmux で動いていて、利用者の画面（ブラウザ・Windows アプリ）は Windows 側/);
  assert.match(win, /あなた自身は playwright-cli \/ winauto の記録を起こさない/);
  assert.match(win, /ブラウザ（playwright-cli）・Windows アプリ（winauto）/);
  assert.match(win, /利用者の目的:\n毎月の売上を集計する/);
  assert.match(win, /`playwright-cli` スキル[\s\S]*`windows-app-automation` スキル/);
  const linux = teaching.prompt({ machine: 'monthly', existing: true, platform: 'linux', tools: { browser: false, windows: false } });
  assert.match(linux, /今の工程を短く要約してから、利用者に変更したい点を聞いて/);
  assert.doesNotMatch(linux, /利用者の目的:/);
  assert.match(linux, /見本の記録は利用者の端末（このアプリ）が取ります/);
  assert.match(linux, /見本を取る道具が見つかっていません/);
  assert.match(linux, /python \.github\/skills\/statemachine-use\/scripts\/run_machine\.py/);
  const follow = teaching.demonstrationPrompt({ machine: 'monthly', hostPath: '/home/me/repo/.statemachine/monthly/recordings/r.md', source: 'browser', target: 'https://a.test', steps: 2, parameters: ['month'] });
  assert.match(follow, /\/home\/me\/repo\/\.statemachine\/monthly\/recordings\/r\.md を読んで/);
  assert.match(follow, /2 工程の候補[\s\S]*month/);
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

test('タスクの会話は agent-app の会話基盤で開き、見本の記録はこの端末で取って所在を WSL 表記で送る', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main', 'ipc.js'), 'utf8');
  const preload = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const renderer = fs.readFileSync(path.join(SRC, 'renderer', 'taskTeaching.js'), 'utf8');
  const html = fs.readFileSync(path.join(SRC, 'renderer', 'index.html'), 'utf8');
  for (const channel of ['automation:teach:start', 'automation:teach:session', 'automation:teach:demonstration']) {
    assert.ok(ipc.includes(`handle('${channel}'`), channel);
    assert.ok(preload.includes(`invoke('${channel}'`), channel);
  }
  assert.match(ipc, /kind: 'task', task: \{ machine \}/, 'タスクの会話は kind: task');
  assert.match(ipc, /await guardedRunTurn\(session\.id, \{\s*prompt,/, '最初の依頼は会話と同じターンの経路で送る');
  assert.match(ipc, /const hostPath = host\.toHostPath\(saved\.file\)/, '記録の所在は WSL 表記へ直してから AI へ');
  assert.match(ipc, /agentCli\.resolvePath\('playwright-cli'\)/, '見本を取る道具はこの端末の PATH で見る');
  assert.match(renderer, /api\.termOpen\(session\.id/);
  assert.match(renderer, /api\.automation\.recordingStart\(/);
  assert.match(renderer, /api\.automation\.teachDemonstration\(/);
  assert.match(renderer, /TeachingProtocol\.parseRecordRequest\(message && message\.text\)/, 'AI の @record 行で見本のカードを開く');
  assert.match(renderer, /Windows 側）で記録します。AI は WSL の tmux/);
  assert.match(html, /<div slot="teaching" id="task-teaching" hidden>/);
  assert.match(html, /id="task-term-host"/);
  assert.match(html, /id="task-mode-terminal"/);
  assert.match(html, /id="task-record-stop"[^>]*>終了してAIへ渡す</);
  const term = fs.readFileSync(path.join(SRC, 'renderer', 'term.js'), 'utf8');
  assert.match(term, /window\.TaskTerm = createTerm\(\)/, '会話とタスクで別の端末ミラーを持つ');
});
