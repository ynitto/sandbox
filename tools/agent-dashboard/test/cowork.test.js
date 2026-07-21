'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const cowork = require('../src/features/cowork/main/cowork');
const cowork_loopProvider = require('../src/features/cowork/main/loopProvider');
const {
  makeLoopProvider, winDriveToWsl, toWslCwd, sh: providerSh,
} = cowork_loopProvider;

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-repo-'));
  spawnSync('git', ['init', '-b', 'main'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['config', 'user.email', 'cowork@example.test'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['config', 'user.name', 'Cowork Test'], { cwd: repo, encoding: 'utf8' });
  fs.writeFileSync(path.join(repo, 'README.md'), '# repo\n');
  spawnSync('git', ['add', 'README.md'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });
  return repo;
}

test('itemsOf は cowork.items だけを正として扱い旧 loopJobs/stateMachines は読まない', () => {
  const items = cowork.itemsOf({
    items: [{ id: 'flat', type: 'loop', repo: '/repo-a' }],
    loopJobs: [{ id: 'legacy-loop', cwd: '/repo-b' }],
    stateMachines: [{ id: 'legacy-sm', cwd: '/repo-c' }],
  });
  assert.deepStrictEqual(items.map((x) => x.id), ['flat']);
});

test('overview は複数リポジトリの作業をフラットに並べる', () => {
  const repoA = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-a-'));
  const repoB = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-b-'));
  fs.mkdirSync(path.join(repoA, '.kiro-loop', 'logs'), { recursive: true });
  fs.mkdirSync(path.join(repoB, '.statemachine-use', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repoA, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  fs.writeFileSync(path.join(repoB, '.statemachine-use', 'logs', 'flow.log'), 'idle\n');
  const ov = cowork.overview({ cowork: { items: [
    { id: 'daily', type: 'loop', repo: repoA },
    { id: 'release', type: 'state-machine', repo: repoB, workflow: 'release.yaml' },
  ] } });
  assert.deepStrictEqual(ov.items.map((x) => x.repo), [repoA, repoB]);
  assert.deepStrictEqual(ov.items.map((x) => x.type), ['loop', 'state-machine']);
});

test('overview は statusFile を作らず既存ログとプロセス由来の state を返す', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  const ov = cowork.overview({ cowork: { items: [{ id: 'daily', type: 'loop', repo }] } });
  assert.strictEqual(ov.items.length, 1);
  assert.strictEqual(ov.items[0].state.status, 'done');
  assert.ok(ov.items[0].state.lastLog.endsWith('run.log'));
  assert.ok(!fs.existsSync(path.join(repo, 'status.json')));
});

test('saveWork は複数リポジトリそれぞれに git 保存処理を試みる', () => {
  const repoA = tmpRepo();
  const repoB = tmpRepo();
  const saved = [];
  const res = cowork.saveWork({}, (cfg) => { saved.push(cfg); return cfg; }, {
    items: [
      { id: 'a', type: 'loop', repo: repoA },
      { id: 'b', type: 'state-machine', repo: repoB },
    ],
  });
  assert.strictEqual(saved.length, 1);
  assert.deepStrictEqual(res.git.map((x) => x.repo).sort(), [repoA, repoB].sort());
  assert.ok(res.git.every((x) => x.result.ok));
});

test('wslPath は WSL UNC を Linux パスへ変換する', () => {
  assert.strictEqual(cowork.wslPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(cowork.wslPath('/home/me/repo'), '/home/me/repo');
});

test('decodeCliOutput は不正 UTF-8 を Shift_JIS として読む', () => {
  // CP932 の「あ」(0x82 0xA0)
  const buf = Buffer.from([0x82, 0xa0]);
  assert.strictEqual(cowork.decodeCliOutput(buf), 'あ');
  assert.strictEqual(cowork.decodeCliOutput(Buffer.from('ok', 'utf8')), 'ok');
});

test('loop 実行は kiro-loop の send サブコマンドでプロンプト名を送る（run は存在しない）', () => {
  // command を echo に差し替えて、組み立てられた引数だけを検証する
  const r = makeLoopProvider({ loopCommand: 'echo' }).run({ id: '毎朝レビュー', cwd: os.tmpdir() });
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send 毎朝レビュー');
});

test('loop 実行は送信先ペインを引けたら -s で明示する（複数ペインでも失敗しない）', () => {
  // kiro-loop の loop-state 参照をスタブ化して、名前 → ペインの解決だけを差し替える
  const tmux = require('../src/features/kiro-loop/main/tmux');
  const origFind = tmux.findPane;
  tmux.findPane = ({ name }) => (name === '毎朝レビュー' ? '%12' : '');
  try {
    const hit = makeLoopProvider({ loopCommand: 'echo' }).run({ id: '毎朝レビュー', cwd: os.tmpdir() });
    assert.strictEqual(hit.stdout, 'send -s %12 毎朝レビュー');
    // 引けないときは従来どおり CLI の自動解決に任せる
    const miss = makeLoopProvider({ loopCommand: 'echo' }).run({ id: '未知の作業', cwd: os.tmpdir() });
    assert.strictEqual(miss.stdout, 'send 未知の作業');
  } finally {
    tmux.findPane = origFind;
  }
});

test('loop 実行は明示 args があればそれを優先する', () => {
  const r = makeLoopProvider({ loopCommand: 'echo' }).run({ id: 'X', args: ['send', '-s', 'sess', 'X'], cwd: os.tmpdir() });
  assert.ok(r.ok);
  assert.strictEqual(r.stdout, 'send -s sess X');
});

test('winDriveToWsl は Windows ドライブパスを /mnt/<drive> に変換する', () => {
  assert.strictEqual(winDriveToWsl('C:\\proj\\アプリ'), '/mnt/c/proj/アプリ');
  assert.strictEqual(winDriveToWsl('D:/work/repo/'), '/mnt/d/work/repo');
  assert.strictEqual(winDriveToWsl('C:\\'), '/mnt/c');
  assert.strictEqual(winDriveToWsl('/home/me/repo'), '');          // POSIX は対象外
  assert.strictEqual(winDriveToWsl('\\\\wsl.localhost\\U\\home'), '');
});

test('toWslCwd は UNC/ドライブ/POSIX を WSL 側パスへ寄せる', () => {
  assert.strictEqual(toWslCwd('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(toWslCwd('C:\\proj'), '/mnt/c/proj');
  assert.strictEqual(toWslCwd('/home/me/repo'), '/home/me/repo');
});

test('win32 では Windows ドライブ上のリポジトリでも wsl.exe 経由で loop を実行する（直接 spawn しない）', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = providerSh('kiro-loop', ['send', 'x'], { cwd: 'C:\\proj\\app' });
    // Linux 上のテストでは wsl.exe が無く ENOENT になるが、その ENOENT が
    // kiro-loop ではなく wsl.exe を指していること＝WSL 経由であることを検証する。
    assert.ok(/wsl\.exe/.test(r.error), `wsl.exe を起動する: ${r.error}`);
    assert.ok(!/spawnSync kiro-loop/.test(r.error), 'kiro-loop を Windows 側で直接 spawn しない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('win32 の loop 実行は既定で別ウィンドウ（WSL tmux）起動になり、runWindow:false で従来動作に戻る', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const launched = makeLoopProvider({ loopCommand: 'kiro-loop' }).run({ id: '毎朝レビュー', cwd: 'C:\\proj\\app' });
    assert.strictEqual(launched.ok, true);
    assert.strictEqual(launched.launched, true, '新しいウィンドウでの起動として返る');
    assert.match(launched.message, /別ウィンドウ/);
    // GUI プロセスからの直接 spawn ではコンソールが割り当てられずウィンドウが出ない。
    // cmd の start で新しいコンソールを開かせる（スクリプト本文は一時ファイル経由）。
    assert.match(launched.windowCommand, /^cmd \/s \/c start "/, 'cmd の start でウィンドウを開く');
    assert.match(launched.windowCommand, /wsl\.exe .*-e bash -lc /, 'wsl.exe で bash ログインシェルを起動する');
    assert.ok(launched.scriptFile, '実行スクリプトを一時ファイルへ書く');
    assert.ok(fs.existsSync(launched.scriptFile), 'スクリプトファイルが実在する');
    assert.ok(
      fs.readFileSync(launched.scriptFile, 'utf8').includes("'kiro-loop' 'send' '毎朝レビュー'"),
      'スクリプト本文に send コマンドが入る'
    );
    const legacy = makeLoopProvider({ loopCommand: 'kiro-loop', runWindow: false })
      .run({ id: 'X', cwd: 'C:\\proj\\app' });
    assert.ok(/wsl\.exe/.test(legacy.error), `runWindow:false は従来の同期 wsl.exe 実行: ${legacy.error}`);
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('chatWindowScript は tmux セッション確保 → 起動待ち → paste-buffer 送信 → attach を組み立てる', () => {
  const script = cowork_loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat --trust-all-tools',
    cwd: '/mnt/c/proj/app',
    session: 'kiro-dash-abc12345',
    prompt: 'レビューして {{target}}',
  });
  assert.ok(script.includes('tmux has-session -t "$__ses"'), '既存セッションを再利用する');
  assert.ok(script.includes('tmux new-session -d -s "$__ses"'), '無ければ作成する');
  assert.ok(script.includes('exec ') && script.includes('kiro-cli') && script.includes('--trust-all-tools'),
    'chatCommand を argv 分解して起動する');
  assert.ok(script.includes('grep -qE'), 'kiro-cli の入力プロンプトを待つ');
  assert.ok(script.includes('tmux set-buffer') && script.includes('tmux paste-buffer -p'),
    'スラッシュ補完との競合を避けるためブラケットペースト（-p）で送る');
  assert.ok(script.includes("'レビューして {{target}}'"), 'プロンプト本文を引用して埋め込む');
  assert.ok(script.includes('tmux attach -t "$__ses"'), '送信後はアタッチして進行を見せる');
  assert.ok(script.includes('read _'), '終了時にウィンドウを即閉じしない');
  // 枠付き入力欄（Claude Code の `│ > │` 等）も検出する（取りこぼすと 60 秒待って極端に遅い）。
  assert.ok(script.includes('│[[:space:]]*[>❯›]'), '枠で囲うプロンプトも入力待ち判定に含める');
});

test('chatSessionName は kiro 接頭辞 + repo digest（端末タブの既定発見に載る）', () => {
  const name = cowork_loopProvider.chatSessionName('/home/me/app');
  assert.match(name, /^kiro-dash-[0-9a-f]{8}$/);
  assert.strictEqual(name, cowork_loopProvider.chatSessionName('/home/me/app'), '同じ repo なら安定');
});

test('CLIチャット用セッションはプロジェクトとCLIごとに安定し、空プロンプトなら接続だけ行う', () => {
  const kiro = cowork_loopProvider.chatSessionName('/home/me/app', 'kiro');
  const claude = cowork_loopProvider.chatSessionName('/home/me/app', 'claude');
  assert.match(kiro, /^agent-chat-kiro-[0-9a-f]{8}$/);
  assert.notStrictEqual(kiro, claude, 'CLI が違えば別セッションになる');
  assert.strictEqual(kiro, cowork_loopProvider.chatSessionName('/home/me/app', 'kiro'));
  const script = cowork_loopProvider.chatWindowScript({
    chatCommand: ['claude', '--model', 'sonnet'],
    cwd: '/home/me/app',
    session: claude,
    prompt: null,
  });
  assert.ok(script.includes('claude') && script.includes('--model') && script.includes('sonnet'));
  assert.ok(script.includes('tmux attach -t "$__ses"'));
  assert.ok(!script.includes('tmux new-session -d'), '対話CLIは端末へ接続してから起動する');
  assert.ok(!script.includes('grep -qE'), '接続だけなら入力待ちをしない');
  assert.ok(!script.includes('tmux set-buffer -b agentdash --'), '空プロンプトを送信しない');
});

test('terminalLaunchSpec は macOS のTerminalとLinuxの利用可能な端末を選ぶ', () => {
  const mac = cowork_loopProvider.terminalLaunchSpec('darwin', '/tmp/chat.command');
  assert.deepStrictEqual(mac, {
    command: 'open', args: ['-a', 'Terminal', '/tmp/chat.command'], terminal: 'Terminal',
  });
  const linux = cowork_loopProvider.terminalLaunchSpec(
    'linux', '/tmp/chat.sh', (name) => name === 'gnome-terminal' ? '/usr/bin/gnome-terminal' : ''
  );
  assert.deepStrictEqual(linux, {
    command: '/usr/bin/gnome-terminal', args: ['--', '/tmp/chat.sh'], terminal: 'gnome-terminal',
  });
  assert.throws(
    () => cowork_loopProvider.terminalLaunchSpec('linux', '/tmp/chat.sh', () => ''),
    /ターミナルが見つかりません/
  );
});

test('win32 で job.prompt があれば kiro-loop を介さず tmux + kiro-cli へ直接送るウィンドウを開く', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = makeLoopProvider({ loopCommand: 'kiro-loop' })
      .run({ id: '毎朝レビュー', cwd: 'C:\\proj\\app', prompt: 'レビューしてください' });
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.launched, true);
    assert.match(r.session || '', /^kiro-dash-/, '接続先セッション名を返す');
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('kiro-cli') && body.includes('--trust-all-tools'), '既定の chatCommand で起動する');
    assert.ok(body.includes('レビューしてください'), '解決済みプロンプト本文を送る');
    assert.ok(!/kiro-loop(?!\.yml)/.test(body.replace(/kiro-dash-[0-9a-f]+/g, '')), 'kiro-loop は実行しない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('resolveLoopPromptText は .kiro/kiro-loop.yml のブロックスカラ本文を名前で解決する', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-prompt-'));
  fs.mkdirSync(path.join(repo, '.kiro'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), [
    'prompts:',
    '  - name: "毎朝レビュー"',
    '    prompt: |',
    '      直近の変更をレビューしてください。',
    '      対象ブランチ: {{target_branch}}',
    '    interval_minutes: 60',
    '    enabled: true',
    '  - name: "別ジョブ"',
    '    prompt: 一行プロンプト',
    '',
  ].join('\n'), 'utf8');
  const text = cowork.resolveLoopPromptText(repo, '毎朝レビュー');
  assert.ok(text.includes('直近の変更をレビューしてください。'), `本文を解決する: ${text}`);
  assert.ok(text.includes('{{target_branch}}'), 'プレースホルダーもそのまま残す');
  assert.strictEqual(cowork.resolveLoopPromptText(repo, '別ジョブ'), '一行プロンプト');
  assert.strictEqual(cowork.resolveLoopPromptText(repo, '存在しない'), '');
});

test('runLoop は win32 ウィンドウ実行で kiro-loop.yml の本文 + 入力補助を直接送る', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-runwin-'));
  fs.mkdirSync(path.join(repo, '.kiro'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), [
    'prompts:',
    '  - name: "毎朝レビュー"',
    '    prompt: レビューしてください {{target}}',
    '    interval_minutes: 60',
    '',
  ].join('\n'), 'utf8');
  const config = { cowork: { items: [{ id: 'daily', name: '毎朝レビュー', type: 'loop', repo }] } };
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = cowork.runLoop(config, 'daily');
    assert.strictEqual(r.launched, true);
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('レビューしてください {{target}}'), 'yml のプロンプト本文を送る');
    assert.ok(body.includes('プレースホルダー'), '入力補助（質問してから実行）を付け加える');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('runStateMachine は win32 ウィンドウ実行で statemachine-use スキル発動文 + 入力補助を送る', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-smwin-'));
  const config = { cowork: {
    items: [{ id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo }],
  } };
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = cowork.runStateMachine(config, 'sm1', '');
    assert.strictEqual(r.launched, true);
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('statemachine-use スキルでreleaseステートマシンを実行して'), 'スキル発動文を送る');
    assert.ok(body.includes('入力'), '入力パラメータの補助を付け加える');
    const withInput = cowork.runStateMachine(config, 'sm1', 'v1.2');
    const body2 = fs.readFileSync(withInput.scriptFile, 'utf8');
    assert.ok(body2.includes('入力: v1.2'), '指定された入力はプロンプトへ含める');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('windowStartCommand は start のタイトル・distro・スクリプトパスを cmd 規則で組み立てる', () => {
  const line = cowork_loopProvider.windowStartCommand('Ubuntu', '/mnt/c/Users/dev/Temp/agent-dashboard/run.sh');
  assert.strictEqual(
    line,
    'start "定常業務 (agent-dashboard)" wsl.exe -d "Ubuntu" -e bash -lc ". \'/mnt/c/Users/dev/Temp/agent-dashboard/run.sh\'"'
  );
  const noDistro = cowork_loopProvider.windowStartCommand('', '/mnt/c/t/run.sh');
  assert.ok(!noDistro.includes('-d '), 'distro 未指定なら -d を付けない');
});

test('windowScript は cd → send 実行 → 送信先ペインのセッションへ tmux attach を組み立てる', () => {
  const script = cowork_loopProvider.windowScript('kiro-loop', ['send', '毎朝レビュー'], '/mnt/c/proj/app');
  assert.ok(script.includes("cd '/mnt/c/proj/app'"), 'プロジェクトルートへ cd する');
  assert.ok(script.includes("'kiro-loop' 'send' '毎朝レビュー'"), 'send をそのまま実行する');
  assert.ok(script.includes('tee'), '出力を表示しつつ送信先ペインの特定に使う');
  assert.ok(script.includes('tmux attach'), '送信後はセッションへアタッチして進行を見せる');
  assert.ok(script.includes('read _'), '特定できないときはウィンドウを残して原因を読めるようにする');
});

test('state-machine 実行は statemachine-use スキルを発動するプロンプトを kiro-loop send で送る', () => {
  const repo = os.tmpdir();
  const config = { cowork: {
    loopCommand: 'echo',
    items: [{ id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo }],
  } };
  const r = cowork.runStateMachine(config, 'sm1', '');
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send release ステートマシンを実行して');
  const withInput = cowork.runStateMachine(config, 'sm1', 'v1.2');
  assert.strictEqual(withInput.stdout, 'send release ステートマシンを実行して。入力: v1.2');
});

test('runLoop / runStateMachine は実行履歴（historyFile）へ記録し readHistory で新しい順に読める', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-hist-'));
  const historyFile = path.join(repo, 'history.jsonl');
  const config = { cowork: {
    loopCommand: 'echo',
    historyFile,
    items: [
      { id: 'daily', type: 'loop', name: '毎朝レビュー', repo },
      { id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo },
    ],
  } };
  assert.ok(cowork.runLoop(config, 'daily').ok);
  assert.ok(cowork.runStateMachine(config, 'sm1', '').ok);
  assert.ok(cowork.runLoop(config, 'daily').ok);

  const loopLogs = cowork.itemLogs(config, 'daily');
  assert.strictEqual(loopLogs.history.length, 2);
  assert.ok(loopLogs.history.every((h) => h.ok && h.name === '毎朝レビュー' && h.type === 'loop'));
  assert.ok(loopLogs.history[0].at >= loopLogs.history[1].at, '新しい順');
  const smLogs = cowork.itemLogs(config, 'sm1');
  assert.strictEqual(smLogs.history.length, 1);
  assert.strictEqual(smLogs.history[0].type, 'state-machine');
  assert.match(smLogs.history[0].message, /send release-runner|send release/);
});

test('itemLogs はリポジトリのログ候補を返し readLog は末尾を読む（候補外パスは拒否）', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-logs-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  const logFile = path.join(repo, '.kiro-loop', 'logs', 'run.log');
  fs.writeFileSync(logFile, `${'x'.repeat(3000)}TAIL-MARKER\n`);
  const secret = path.join(repo, 'secret.txt');
  fs.writeFileSync(secret, 'top secret');
  const config = { cowork: {
    historyFile: path.join(repo, 'history.jsonl'),
    items: [{ id: 'daily', type: 'loop', name: 'daily', repo }],
  } };
  const info = cowork.itemLogs(config, 'daily');
  assert.strictEqual(info.logs.length, 1);
  assert.strictEqual(info.logs[0].name, 'run.log');
  assert.ok(info.logs[0].size > 3000);
  const read = cowork.readLog(config, 'daily', info.logs[0].file, 2000);
  assert.ok(read.text.includes('TAIL-MARKER'), '末尾を読む');
  assert.ok(read.truncated, '上限超は truncated');
  assert.throws(() => cowork.readLog(config, 'daily', secret), /この作業のログではありません/);
});

test('実行履歴は上限を超えると新しい方だけ残して切り詰める', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-trim-'));
  const historyFile = path.join(dir, 'history.jsonl');
  const cfg = { historyFile };
  for (let i = 0; i < 1005; i += 1) {
    cowork.appendHistory(cfg, { at: new Date(2026, 0, 1, 0, 0, i % 60).toISOString(), key: 'k', ok: true, message: `run-${i}` });
  }
  const lines = fs.readFileSync(historyFile, 'utf8').split('\n').filter(Boolean);
  assert.ok(lines.length <= 600, `切り詰められている: ${lines.length}`);
  assert.ok(lines[lines.length - 1].includes('run-1004'), '最新は残る');
});

test('overview の既定はプロセス探査せず probed=false', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-light-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  const ov = cowork.overview({ cowork: { items: [{ id: 'daily', type: 'loop', repo }] } });
  assert.strictEqual(ov.items[0].state.probed, false);
  assert.strictEqual(ov.items[0].state.running, false);
  const probed = cowork.overview(
    { cowork: { items: [{ id: 'daily', type: 'loop', repo }] } },
    { probeProcess: true }
  );
  assert.strictEqual(probed.items[0].state.probed, true);
});

test('定型業務の作成指示は statemachine-use の作成モードと生成先を明示する', () => {
  const prompt = cowork.stateMachineCreationPrompt('リリース確認', 'release-check', '確認後に承認する');
  assert.match(prompt, /statemachine-use スキルの作成モード/);
  assert.match(prompt, /\.statemachine\/release-check\//);
  assert.match(prompt, /確認後に承認する/);
  assert.match(prompt, /実行はしない/);
});

console.log(`\n${passed} cowork tests passed`);
