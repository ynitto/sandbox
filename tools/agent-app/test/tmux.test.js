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

test('画面の判定: ready / busy / unknown', () => {
  const claude = tmux.compilePatterns({ readyPattern: '^[[:space:]]*[>?❯›][[:space:]]*$|│[[:space:]]*[>❯›]|\\? for shortcuts', busyPattern: 'esc to interrupt', readyTailLines: 3 });
  assert.strictEqual(tmux.classify('⏺ 考え中… (esc to interrupt)\n\n╭──╮\n│ > │\n╰──╯', claude), 'busy');
  assert.strictEqual(tmux.classify('⏺ 答え\n\n╭──╮\n│ > │\n╰──╯\n  ? for shortcuts', claude), 'ready');
  assert.strictEqual(tmux.classify('何か出力している途中\nまだ続く', claude), 'unknown');
  const kiro = tmux.compilePatterns({});
  assert.strictEqual(tmux.classify('本文\n\n> ', kiro), 'ready', '既定の ready パターン');
  assert.strictEqual(kiro.readyTimeoutSec, 60);
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
    // 疑似 CLI は行ごとに読むので 2 回答える。依頼の echo（"> lines"）は落ちる
    assert.strictEqual(second.text, '⏺ echo: two\n  detail line\n⏺ echo: lines\n  detail line');

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
