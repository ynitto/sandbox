'use strict';

// tmux の駆動。純粋な部分（判定・差分・キー変換・コマンド文字列）は常に、
// 実物の tmux を使う統合テストは tmux がある環境でだけ走る。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const tmux = require('../src/main/tmux');
const host = require('../src/main/host');

test('WSL のパス変換', () => {
  assert.strictEqual(host.wslPath('\\\\wsl$\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(host.wslPath('\\\\wsl.localhost\\Ubuntu-22.04\\'), '/');
  assert.strictEqual(host.wslDistro('\\\\wsl$\\Ubuntu\\home'), 'Ubuntu');
  assert.strictEqual(host.wslDistro('C:\\x'), '');
  assert.strictEqual(host.winDriveToWsl('C:\\Users\\me\\repo\\'), '/mnt/c/Users/me/repo');
  assert.strictEqual(host.winDriveToWsl('D:/a/b'), '/mnt/d/a/b');
  assert.strictEqual(host.winDriveToWsl('/already/posix'), '');
  assert.strictEqual(host.joinHost('/mnt/c/repo/', 'src\\a.ts'), '/mnt/c/repo/src/a.ts');
  assert.strictEqual(host.sq("it's"), `'it'"'"'s'`);
  if (process.platform !== 'win32') assert.strictEqual(host.toHostPath('/x/y'), '/x/y');
});

test('端末スクロールは tmux copy-mode の履歴を操作する', () => {
  const up = tmux.cmdScroll('agent-app-test', -7);
  assert.match(up, /copy-mode -e/);
  assert.match(up, /-X -N 7 scroll-up/);
  assert.match(tmux.cmdScroll('agent-app-test', 3), /-X -N 3 scroll-down/);
  assert.match(tmux.cmdCancelCopy('agent-app-test'), /-X cancel/);
});

test('画面の判定: ready / busy / unknown', () => {
  const claude = tmux.compilePatterns({ readyPattern: '^[[:space:]]*[>?❯›][[:space:]]*$|│[[:space:]]*[>❯›]|\\? for shortcuts', busyPattern: 'esc to interrupt', readyTailLines: 3 });
  assert.strictEqual(tmux.classify('⏺ 考え中… (esc to interrupt)\n\n╭──╮\n│ > │\n╰──╯', claude), 'busy');
  assert.strictEqual(tmux.classify('⏺ 答え\n\n╭──╮\n│ > │\n╰──╯\n  ? for shortcuts', claude), 'ready');
  assert.strictEqual(tmux.classify('何か出力している途中\nまだ続く', claude), 'unknown');
  const kiro = tmux.compilePatterns({});
  assert.strictEqual(tmux.classify('本文\n\n> ', kiro), 'ready', '既定の ready パターン');
  assert.strictEqual(kiro.readyTimeoutSec, 60);
});

test('Kiroの点字ロゴを処理中スピナーと誤認しない', () => {
  const kiro = tmux.compilePatterns({
    readyPattern: 'ask a question|describe a task',
  });
  const screen = `
    ⣀⣴⣶⣶⣦⣀   ⣀⣴⣶⣦⣄⣀
    ⢸⣿⣉⣁⣈⢻   ⢸⣿⣉⣁⣈⢻

An early release of Kiro CLI V3 is now available!
▸ Credits: 0.13 • Time: 2s
Trust All Tools active, confirmations are off
kiro_default · auto · ◔ 4%
ask a question or describe a task ↵`;
  assert.strictEqual(tmux.classify(screen, kiro), 'ready');
});

test('Codexのヘッダーはreadyではなく、Copilotの許可画面はattention', () => {
  const codex = tmux.compilePatterns({
    readyPattern: '^[[:blank:]]*[>?❯›][[:blank:]]*$|│[[:blank:]]*[>❯›][[:blank:]]*│?[[:blank:]]*$',
    busyPattern: 'esc to interrupt',
  });
  const loading = '╭───────────────────────────────────────╮\n│ >_ OpenAI Codex (v0.153.4)            │\n│ model: loading   /model to change      │\n╰───────────────────────────────────────╯\n  Resuming session…';
  assert.strictEqual(tmux.classify(loading, codex), 'busy');
  assert.strictEqual(tmux.classify(`${loading}\n\n› `, codex), 'ready');
  assert.strictEqual(tmux.classify('› Ask Codex to do anything\n\n  gpt-5.6-sol medium · ~/repo', codex), 'ready');

  const approval = '│ Do you want to run this command? │\n│ ❯ 1. Yes │\n│   2. Yes, and don\'t ask again │\n│ enter to select · esc to cancel │';
  assert.strictEqual(tmux.classify(approval, tmux.compilePatterns({})), 'attention');
  assert.strictEqual(tmux.classify(`${approval}\n\n› `, tmux.compilePatterns({})), 'ready', '確認後の入力欄が出たら古い確認表示を引きずらない');

  const copilot = tmux.compilePatterns({
    readyPattern: '^[[:space:]]*┃[[:space:]]*$',
    busyPattern: 'pending.*ctrl\\+c to cancel|working[[:space:]]+esc interrupt',
    readyTailLines: 8,
  });
  const copilotReady = '● 了解。簡潔に回答。\n\n ~/repo [⎇ main%]  Session: 0.99 AIC used\n╻▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n┃         \n╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n ← open sidebar · / commands · ? help · tab next tab  Auto → gpt-5.6-luna';
  assert.strictEqual(tmux.classify(copilotReady, copilot), 'ready');
  const copilotWorking = '❯ 今日の天気 (pending · ctrl+c to cancel)\n ~/repo Session: 0 AIC used\n╻▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n┃\n╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n ◎ Working esc interrupt  Auto → mai-code-1.1-flash';
  assert.strictEqual(tmux.classify(copilotWorking, copilot), 'busy');
  const copilotWorkingWithBytes = '● skill(ponytail)\n╻▄▄▄▄▄▄▄▄\n┃\n╹▀▀▀▀▀▀▀▀\n ◉ Working · 25 B esc interrupt  Auto → gpt-5.6-luna';
  assert.strictEqual(tmux.classify(copilotWorkingWithBytes, copilot), 'busy');

  const copilotQuestion = `● Asking user
╭──────────────────╮
│ どの地域の天気ですか？ │
│ ❯ 1. 東京             │
│   2. 大阪             │
│   3. Other (type your answer) │
│ Use ↑↓ or number keys to select, Enter to confirm, Esc to cancel │
╰──────────────────╯`;
  assert.strictEqual(tmux.classify(copilotQuestion, copilot), 'attention');
  assert.match(tmux.attentionDetail(copilotQuestion), /どの地域の天気ですか？/);
  assert.match(tmux.attentionDetail(copilotQuestion), /1\. 東京/);
});

test('Cursor Agent の Add a follow-up 画面をターン完了と判定する', () => {
  // 配布済みの ~/.agents/agents/cursor.json が古く、完了画面しか持たない場合も動くこと。
  const cursor = tmux.compilePatterns({
    readyPattern: 'add a follow-up',
    busyPattern: '^[[:space:]]*[⠀-⣿]+[[:space:]]+.+$',
  });
  const startup = 'Cursor Agent\nTip: Use /plan to plan execution and reach the right outcome faster.\n\n→ Plan, search, build anything\n\nAuto\n~/Workspace/sandbox-test · main';
  assert.strictEqual(tmux.classify(startup, cursor), 'ready');
  const working = 'Cursor Agent\n\n⠀⠰⠰ Working\nTip: Use /config to customize Cursor settings and behavior.\n\n→ Add a follow-up\nAuto · 9.3%\n~/Workspace/sandbox-test · main';
  assert.strictEqual(tmux.classify(working, cursor), 'busy');
  const alternateSpinner = 'Cursor Agent\n\n⠠⠜ Working\nTip: Use /debug to instrument and debug complex problems.\n\n→ Add a follow-up\nAuto · 9.7%\n~/Workspace/sandbox-test · main';
  assert.strictEqual(tmux.classify(alternateSpinner, cursor), 'busy');
  const reading = 'Cursor Agent\n\n⠘⠆ Reading  71 tokens\nTip: Use /run-everything to skip all approvals.\n\n→ Add a follow-up\nAuto · 9.5%\n~/Workspace/sandbox-test · main';
  assert.strictEqual(tmux.classify(reading, cursor), 'busy');
  const screen = 'Hello. What would you like to work on?\n\n  → Add a follow-up\n\n  Auto · 9.3%\n  ~/Workspace/sandbox-test · main';
  assert.strictEqual(tmux.classify(screen, cursor), 'ready');
  assert.strictEqual(tmux.extractReply('Cursor Agent\n\n→ Add a follow-up', `Cursor Agent\n\n> hello\n\nHello. What would you like to work on?\n\n→ Add a follow-up\n\nAuto · 9.3%\n~/Workspace/sandbox-test · main`, 'hello'), 'Hello. What would you like to work on?');
});

test('Claude Code の workspace 信頼確認を attention として検出する', () => {
  const screen = `Accessing workspace:\n\n/Users/me/repo\n\nQuick safety check: Is this a project you created or one you trust?\n\n❯ No, exit\n  Yes, I trust this folder\n\nEnter to confirm · Esc to cancel`;
  assert.match(screen, tmux.ATTENTION);
});

test('waitReady は起動中の attention に依頼を送らず、確認後の ready を待つ', async () => {
  const conv = new tmux.Conversation({ id: 'trust', shell: {}, cwd: '/tmp', argv: [], patterns: tmux.compilePatterns({}) });
  conv.patterns.readyTimeoutSec = 0.01;
  conv.phase = 'attention';
  let settled = false;
  const waiting = conv.waitReady().then(() => { settled = true; });
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.strictEqual(settled, false);
  conv.phase = 'ready';
  await waiting;
  assert.strictEqual(settled, true);
});

test('send-keys は複数行を1回の入力へ畳む', async () => {
  const calls = [];
  const shell = { run: async (command) => { calls.push(command); return { ok: true, output: '' }; } };
  const conv = new tmux.Conversation({ id: 'one-line', shell, cwd: '/tmp', argv: [], patterns: tmux.compilePatterns({}) });
  conv.phase = 'ready';
  conv.historyText = async () => '';
  conv.schedule = () => {};
  await conv.send('開始指示\n\nhello', () => {});
  assert.strictEqual(calls.length, 2);
  assert.match(calls[0], /send-keys/);
  assert.match(calls[0], /開始指示 hello/);
  assert.doesNotMatch(calls[0], /set-buffer|paste-buffer/);
});

test('候補確定型のスキル入力はEnterを2回送る', async () => {
  const calls = [];
  const shell = { run: async (command) => { calls.push(command); return { ok: true, output: '' }; } };
  const conv = new tmux.Conversation({ id: 'skill-enter', shell, cwd: '/tmp', argv: [], patterns: tmux.compilePatterns({}) });
  conv.phase = 'ready';
  conv.historyText = async () => '';
  conv.schedule = () => {};
  await conv.send('$caveman', () => {}, { enterCount: 2 });
  assert.strictEqual(calls.length, 3);
  assert.strictEqual(calls.filter((command) => /'Enter'/.test(command)).length, 2);
});

test('応答中でもElectron入力欄から文章回答をtmuxへ送れる', async () => {
  const calls = [];
  const shell = { run: async (command) => { calls.push(command); return { ok: true, output: '' }; } };
  const conv = new tmux.Conversation({ id: 'followup', shell, cwd: '/tmp', argv: [], patterns: tmux.compilePatterns({}) });
  conv.phase = 'attention';
  conv.turn = { prompt: '質問', startedAt: Date.now(), before: '', sawBusy: true, readyCount: 0, stopped: false, done: () => {} };
  conv.schedule = () => {};

  const result = await conv.submit('東京');

  assert.strictEqual(result.accepted, true);
  assert.strictEqual(calls.length, 2);
  assert.match(calls[0], /'東京'/);
  assert.match(calls[1], /'Enter'/);
  assert.ok(conv.turn, '実行中ターンは維持する');
});

test('応答の抽出: 入力欄・フッター・依頼の echo を除いた差分', () => {
  const before = 'Welcome to CLI\n\n╭────────╮\n│ >      │\n╰────────╯\n  ? for shortcuts';
  const after = 'Welcome to CLI\n\n> こんにちは\n\n⏺ やあ。何を手伝う？\n\n  - 1 つ目\n  - 2 つ目\n\n╭────────╮\n│ >      │\n╰────────╯\n  ? for shortcuts';
  assert.strictEqual(tmux.extractReply(before, after, 'こんにちは'), '⏺ やあ。何を手伝う？\n\n  - 1 つ目\n  - 2 つ目');
  // 複数行の依頼も echo として落ちる
  const after2 = 'x\n> 行 1\n  行 2\n答え\n> ';
  assert.strictEqual(tmux.extractReply('x\n> ', after2, '行 1\n行 2'), '答え');
  assert.strictEqual(tmux.extractReply('a\nb', 'a\nb', 'p'), '');
});

test('Copilotの枠とステータスを除き回答本文だけを抽出する', () => {
  const before = 'Copilot v1.0.83 uses AI.\n\n● 前の回答\n\n ~/repo [⎇ main%] Session: 0.5 AIC used\n╻▄▄▄▄▄▄▄▄▄▄▄▄\n┃\n╹▀▀▀▀▀▀▀▀▀▀▀▀\n ← open sidebar · / commands · ? help · tab next tab  Auto → model';
  const after = `${before}\n ▄▄▄▄▄▄▄▄▄▄▄▄▄\n  ❯ 今日の天気  09:02\n ▀▀▀▀▀▀▀▀▀▀▀▀▀\n ● 天気不明。場所名教えろ。今日の予報出す。\n\n ~/repo [⎇ main%] Session: 0.81 AIC used\n╻▄▄▄▄▄▄▄▄▄▄▄▄\n┃\n╹▀▀▀▀▀▀▀▀▀▀▀▀\n ← open sidebar · / commands · ? help · tab next tab  Auto → model`;
  assert.strictEqual(tmux.extractReply(before, after, '今日の天気'), '● 天気不明。場所名教えろ。今日の予報出す。');
  const current = `${before}\n ● skill(ponytail)\n ▄▄▄▄▄▄▄▄\n  ❯ 今日の天気 09:13\n ▀▀▀▀▀▀▀▀\n ● Fetching web content https://wttr.in/Tokyo ┃\n ● 東京の今日： ┃\n   • 天気：雨 ┃\n\n ~/repo [⎇ main%] Session: 1.4 AIC used\n╻▄▄▄▄▄▄▄▄\n┃\n╹▀▀▀▀▀▀▀▀\n ◉ Working · 25 B esc interrupt Auto → model`;
  assert.strictEqual(tmux.extractReply(before, current, '今日の天気'), '● skill(ponytail)\n● Fetching web content https://wttr.in/Tokyo\n● 東京の今日：\n  • 天気：雨');
});

test('xterm のキー入力を send-keys の引数へ', () => {
  assert.deepStrictEqual(tmux.keysToArgs('ab\r'), [['-l', '--', 'ab'], ['--', 'Enter']]);
  assert.deepStrictEqual(tmux.keysToArgs('\x1b[A\x1b'), [['--', 'Up'], ['--', 'Escape']]);
  assert.deepStrictEqual(tmux.keysToArgs('\x03'), [['--', 'C-c']]);
  assert.deepStrictEqual(tmux.keysToArgs('\x7fあ'), [['--', 'BSpace'], ['-l', '--', 'あ']]);
  assert.deepStrictEqual(tmux.keysToArgs('\x01'), [['--', 'C-a']]);
});

test('tmux コマンド文字列は自前のソケットを使い、引用が壊れない', () => {
  const s = tmux.cmdNew({ name: 'agent-app-x', cwd: "/tmp/it's", argv: ['claude', '--session-id', 'S'], cols: 100, rows: 30 });
  assert.ok(s.startsWith(`tmux -L agent-app new-session -d -s 'agent-app-x' -c '/tmp/it'"'"'s' -x 100 -y 30 bash -lc`));
  // bash -lc の引数は一重引用で包み、その中の argv も一重引用なので '"'"' で閉じ直す
  assert.ok(s.includes(`exec '"'"'claude'"'"' '"'"'--session-id'"'"' '"'"'S'"'"''`), s);
  assert.ok(s.includes('remain-on-exit on') && s.includes('window-size manual'));
  assert.ok(tmux.cmdScreen('n').includes('capture-pane -p -e -t'));
  assert.ok(tmux.cmdScreen('n', { history: true }).includes('-J -S -'));
  const parsed = tmux.parseScreen('3|1|80|24|0||12|0\n\x1eline1\nline2');
  assert.strictEqual(tmux.parseScreen('0|19|80|20|1|7|1|0\n\x1e\nPane is dead').deadStatus, 7);
  assert.deepStrictEqual(parsed.cursor, { x: 3, y: 1 });
  assert.strictEqual(parsed.cols, 80);
  assert.strictEqual(parsed.dead, false);
  assert.strictEqual(parsed.deadStatus, null);
  assert.strictEqual(parsed.historySize, 12);
  assert.strictEqual(parsed.text, 'line1\nline2');
  assert.strictEqual(tmux.sessionName('0123abcd-ef00-1111-2222-333344445555'), 'agent-app-0123abcdef00');
});

test('常駐シェル: 逐次にコマンドを流し、終了コードと出力を返す', async () => {
  const sh = new host.HostShell({ platform: 'linux' });
  try {
    const a = await sh.run('echo hello; echo err >&2');
    assert.strictEqual(a.ok, true);
    assert.strictEqual(a.output, 'hello\nerr');
    const b = await sh.run('printf "no newline"');
    assert.strictEqual(b.output, 'no newline');
    const c = await sh.run('exit 3');
    assert.strictEqual(c.ok, false);
    assert.strictEqual(c.status, 3);
    const d = await sh.exec(['printf', '%s|%s', "it's", 'a b']);
    assert.strictEqual(d.output, "it's|a b");
    const many = await Promise.all([1, 2, 3].map((i) => sh.run(`echo ${i}`)));
    assert.deepStrictEqual(many.map((r) => r.output), ['1', '2', '3']);
    const t = await sh.run('sleep 5', { timeoutMs: 300 });
    assert.strictEqual(t.ok, false);
    assert.match(t.error, /タイムアウト/);
    const after = await sh.run('echo again');                  // 詰まったシェルは捨てて起こし直す
    assert.strictEqual(after.output, 'again');
  } finally { sh.close(); }
});

const hasTmux = process.platform !== 'win32' && spawnSync('tmux', ['-V']).status === 0;

// 疑似 CLI: `> ` で入力を待ち、1 行受けると「thinking (esc to interrupt)」→ 答え → また `> `。
// 「bye」で終了する。claude 風の busy_pattern / ready_pattern で駆動できることを見る。
const STUB = `#!/usr/bin/env bash
# 本物の TUI と同じく echo は自前で出す（tty の echo に任せると貼り付けの echo と Enter の改行が
# 処理より先に画面へ出て、行の消去位置がずれる）
stty -echo 2>/dev/null
printf 'stub cli ready\\n'
while true; do
  printf '> '
  IFS= read -r line || exit 0
  printf '%s\\n' "$line"
  case "$line" in
    bye) printf 'bye!\\n'; exit 7 ;;
    fail) printf 'FATAL: boom\\n'; exit 1 ;;
  esac
  printf 'thinking… (esc to interrupt)\\n'
  sleep 0.6
  printf '\\033[1A\\r\\033[2K'
  printf '\\033[32m⏺\\033[0m echo: %s\\n' "$line"
  printf '  detail line\\n'
done
`;

test('統合: tmux 上の疑似 CLI と会話する', { skip: !hasTmux && 'tmux が無い' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-tmux-'));
  const stub = path.join(dir, 'stub-cli');
  fs.writeFileSync(stub, STUB, { mode: 0o755 });
  const sh = new host.HostShell({ platform: 'linux' });
  const events = [];
  let conv = new tmux.Conversation({
    id: `t${Date.now().toString(36)}`, shell: sh, cwd: dir, argv: [stub], cols: 100, rows: 24,
    patterns: tmux.compilePatterns({ readyPattern: '^[[:space:]]*>[[:space:]]*$', busyPattern: 'esc to interrupt', readyTailLines: 3, readyTimeoutSec: 10 }),
    emit: (ch, p) => events.push([ch, p]),
  });
  try {
    const opened = await conv.open();
    assert.strictEqual(opened.reused, false);
    assert.ok((await tmux.listSessions(sh)).includes(conv.name));
    await new Promise((resolve) => { const tick = () => (conv.phase === 'ready' ? resolve() : setTimeout(tick, 100)); tick(); });

    const reply = await new Promise((resolve, reject) => { conv.send('hello there', resolve).catch(reject); });
    assert.strictEqual(reply.error, '');
    assert.strictEqual(reply.text, '⏺ echo: hello there\n  detail line');
    assert.ok(events.some(([ch, p]) => ch === 'term:phase' && p.phase === 'busy'), '応答中の phase が流れる');

    // 端末ミラー: 見ている間だけ画面が流れ、キー入力が届く
    conv.watch();
    await new Promise((r) => setTimeout(r, 700));
    const screen = events.filter(([ch]) => ch === 'term:screen').at(-1);
    assert.ok(screen && screen[1].text.includes('echo: hello there') && screen[1].text.includes('\x1b['), '色付きの画面');
    assert.ok(screen[1].tail.includes('> '));
    conv.unwatch();

    const second = await new Promise((resolve, reject) => { conv.send('two\nlines', resolve).catch(reject); });
    // 行入力型 CLI にも複数行の依頼を 1 ターンとして渡す。
    assert.strictEqual(second.text, '⏺ echo: two lines\n  detail line');

    // 同じ名前のセッションへ再接続できる
    const again = new tmux.Conversation({ id: conv.id, shell: sh, cwd: dir, argv: [stub], patterns: conv.patterns, emit() {} });
    assert.strictEqual((await again.open()).reused, true);
    again.detach();

    // reuse=false は、残っているセッションを消して起動し直す（別のモデルや CLI で続けるとき）
    conv.detach();
    const fresh = new tmux.Conversation({ id: conv.id, shell: sh, cwd: dir, argv: [stub], patterns: conv.patterns, emit() {}, launch: { cli: 'stub', model: 'm2', readonly: false } });
    assert.strictEqual((await fresh.open({ reuse: false })).reused, false);
    assert.deepStrictEqual(fresh.launch, { cli: 'stub', model: 'm2', readonly: false });
    await new Promise((resolve) => { const tick = () => (fresh.phase === 'ready' ? resolve() : setTimeout(tick, 100)); tick(); });
    assert.ok(!(await fresh.historyText()).includes('echo: hello there'), '前の画面は残っていない');
    conv = fresh;

    // CLI が終わるとペインは残り、dead になる
    const last = await new Promise((resolve, reject) => { conv.send('bye', resolve).catch(reject); });
    assert.ok(last.error.includes('終了コード 7'), last.error);
    assert.strictEqual(conv.phase, 'dead');
    await assert.rejects(conv.send('x', () => {}), /終了/);
  } finally {
    await conv.kill();
    assert.ok(!(await tmux.listSessions(sh)).includes(conv.name));
    sh.close();
  }
});

test('統合: 停止（Esc）と resize', { skip: !hasTmux && 'tmux が無い' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-tmux-'));
  const stub = path.join(dir, 'slow-cli');
  fs.writeFileSync(stub, `#!/usr/bin/env bash\nstty -echo 2>/dev/null\nwhile true; do printf '> '; IFS= read -r l || exit 0; printf '%s\\nworking (esc to interrupt)\\n' "$l"; sleep 3; printf '\\033[1A\\r\\033[2Kdone\\n'; done\n`, { mode: 0o755 });
  const sh = new host.HostShell({ platform: 'linux' });
  const conv = new tmux.Conversation({
    id: `s${Date.now().toString(36)}`, shell: sh, cwd: dir, argv: [stub], cols: 80, rows: 20,
    patterns: tmux.compilePatterns({ readyPattern: '^>[[:space:]]*$', busyPattern: 'esc to interrupt', readyTimeoutSec: 10 }),
  });
  try {
    await conv.open();
    await new Promise((resolve) => { const tick = () => (conv.phase === 'ready' ? resolve() : setTimeout(tick, 100)); tick(); });
    await conv.resize(90, 30);
    const cap = await conv.capture();
    assert.strictEqual(cap.screen.cols, 90);
    assert.strictEqual(cap.screen.rows, 30);
    const p = new Promise((resolve, reject) => { conv.send('go', resolve).catch(reject); });
    await new Promise((r) => setTimeout(r, 600));
    assert.strictEqual(await conv.stop(), true);         // busy_pattern に esc とあるので Escape を送る（疑似 CLI は無視して 3 秒後に戻る）
    const msg = await p;
    assert.strictEqual(msg.stopped, true);
  } finally {
    await conv.kill();
    sh.close();
  }
});
