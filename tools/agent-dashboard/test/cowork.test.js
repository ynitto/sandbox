'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const cowork = require('../src/features/cowork/main/cowork');
const cowork_loopProvider = require('../src/features/cowork/main/loopProvider');
const wslMain = require('../src/base/main/wsl');

// 定常業務の走査ルートには**常にユーザーホーム**が入る（`~/.agents/agent-loop.yml` を拾うため）。
// テストでは実機のホームを覗かせない——結果が実行環境の持ち物で変わってしまう。
const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'home-stub-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;
const {
  makeLoopProvider, winDriveToWsl, toWslCwd, sh: providerSh,
} = cowork_loopProvider;

// win32 の起動は「窓を開く前に wsl.exe を実地検査する」。テスト機に WSL は無いので
// 検査だけ差し替え、起動コマンドの組み立てとスクリプト本体を見る
// （検査そのものは専用テストで実物を直接呼ぶ）。
const realVerifyWslLaunch = wslMain.verifyWslLaunch;
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

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

function writeMachine(repo, name, workflow, files = {}) {
  const dir = path.join(repo, '.statemachine', name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'workflow.yaml'), workflow, 'utf8');
  for (const [relative, text] of Object.entries(files)) {
    const file = path.join(dir, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text, 'utf8');
  }
  return path.join(dir, 'workflow.yaml');
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
  fs.mkdirSync(path.join(repoA, '.agent-loop', 'logs'), { recursive: true });
  fs.mkdirSync(path.join(repoB, '.statemachine-use', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repoA, '.agent-loop', 'logs', 'run.log'), 'finished successfully\n');
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
  fs.mkdirSync(path.join(repo, '.agent-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agent-loop', 'logs', 'run.log'), 'finished successfully\n');
  const ov = cowork.overview({ cowork: { items: [{ id: 'daily', type: 'loop', repo }] } });
  assert.strictEqual(ov.items.length, 1);
  assert.strictEqual(ov.items[0].state.status, 'done');
  assert.ok(ov.items[0].state.lastLog.endsWith('run.log'));
  assert.ok(!fs.existsSync(path.join(repo, 'status.json')));
});

test('同じリポジトリの状態とログは選択した定常業務だけに絞る', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-scoped-logs-'));
  const logDir = path.join(repo, '.agent-loop', 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const dailyLog = path.join(logDir, 'daily-review.log');
  const weeklyLog = path.join(logDir, 'weekly-report.log');
  const ambiguousLog = path.join(logDir, 'latest-run.log');
  fs.writeFileSync(dailyLog, 'finished successfully\n');
  fs.writeFileSync(weeklyLog, 'failed with error\n');
  fs.writeFileSync(ambiguousLog, 'failed without a routine identifier\n');
  fs.utimesSync(dailyLog, new Date('2026-08-24T00:00:00Z'), new Date('2026-08-24T00:00:00Z'));
  fs.utimesSync(weeklyLog, new Date('2026-08-25T00:00:00Z'), new Date('2026-08-25T00:00:00Z'));
  fs.utimesSync(ambiguousLog, new Date('2026-08-26T00:00:00Z'), new Date('2026-08-26T00:00:00Z'));
  const config = { cowork: { items: [
    { id: 'daily-review', type: 'loop', name: '日次レビュー', repo },
    { id: 'weekly-report', type: 'loop', name: '週次レポート', repo },
  ] } };

  const overview = cowork.overview(config);
  assert.strictEqual(overview.items[0].state.status, 'done', '日次レビューは日次ログの状態を使う');
  assert.strictEqual(overview.items[1].state.status, 'failed', '週次レポートは週次ログの状態を使う');
  assert.deepStrictEqual(cowork.itemLogs(config, 'daily-review').logs.map((log) => log.file), [dailyLog]);
  assert.deepStrictEqual(cowork.itemLogs(config, 'weekly-report').logs.map((log) => log.file), [weeklyLog]);
  assert.throws(() => cowork.readLog(config, 'daily-review', weeklyLog), /この作業のログではありません/);
});

test('定型業務の汎用ログ名はログ本文の workflow から対応付ける', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-scoped-sm-logs-'));
  const logDir = path.join(repo, '.statemachine-use', 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const releaseLog = path.join(logDir, 'agent-loop-1000-1.jsonl');
  const deployLog = path.join(logDir, 'agent-loop-2000-1.jsonl');
  fs.writeFileSync(releaseLog, `${JSON.stringify({ event: 'start', argv: [path.join(repo, '.statemachine', 'release', 'workflow.yaml')] })}\nfinished successfully\n`);
  fs.writeFileSync(deployLog, `${JSON.stringify({ event: 'start', argv: [path.join(repo, '.statemachine', 'deploy', 'workflow.yaml')] })}\nfailed with error\n`);
  const config = { cowork: { items: [
    { id: 'release', type: 'state-machine', name: 'リリース', workflow: 'release', repo },
    { id: 'deploy', type: 'state-machine', name: 'デプロイ', workflow: 'deploy', repo },
  ] } };

  const overview = cowork.overview(config);
  assert.strictEqual(overview.items[0].state.status, 'done');
  assert.strictEqual(overview.items[1].state.status, 'failed');
  assert.deepStrictEqual(cowork.itemLogs(config, 'release').logs.map((log) => log.file), [releaseLog]);
  assert.deepStrictEqual(cowork.itemLogs(config, 'deploy').logs.map((log) => log.file), [deployLog]);
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

// 定常業務の実行は既定で別ウィンドウ。ここを win32 に絞っていたころは、macOS / Linux で
// main プロセスの spawnSync（最大 60 秒）へ落ちて画面が固まっていた（IPC が 1 つも返らない）。
test('別ウィンドウ実行は Windows 専用ではない（固まる同期実行へ落とさない）', () => {
  const { supportsRunWindow } = cowork_loopProvider;
  assert.strictEqual(supportsRunWindow('win32'), true);
  assert.strictEqual(supportsRunWindow('darwin'), true, 'macOS は Terminal で開ける');
  assert.strictEqual(supportsRunWindow('linux', (name) => (name === 'xterm' ? '/usr/bin/xterm' : '')), true);
  // 端末エミュレータが 1 つも無い環境（CI 等）だけ、従来の同期実行へ落とす
  assert.strictEqual(supportsRunWindow('linux', () => ''), false);
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'loopProvider.js'), 'utf8'
  );
  const run = src.slice(src.indexOf('    run(job) {'), src.indexOf('  };\n}\n\nmodule.exports'));
  assert.ok(run.includes('supportsRunWindow()'), '実行の分岐は「窓を開けるか」で決める');
  assert.ok(!/process\.platform === 'win32'/.test(run), 'OS 名で分岐しない');
});

test('loop 実行は agent-loop の send サブコマンドでプロンプト名を送る（run は存在しない）', () => {
  // command を echo に差し替えて、組み立てられた引数だけを検証する
  const r = makeLoopProvider({ loopCommand: 'echo', runWindow: false }).run({ id: '毎朝レビュー', cwd: os.tmpdir() });
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send 毎朝レビュー');
});

test('loop 実行は送信先ペインを引けたら -s で明示する（複数ペインでも失敗しない）', () => {
  // agent-loop の loop-state 参照をスタブ化して、名前 → ペインの解決だけを差し替える
const tmux = require('../src/features/routines/main/tmux');
  const origFind = tmux.findPane;
  tmux.findPane = ({ name }) => (name === '毎朝レビュー' ? '%12' : '');
  try {
    const hit = makeLoopProvider({ loopCommand: 'echo', runWindow: false }).run({ id: '毎朝レビュー', cwd: os.tmpdir() });
    assert.strictEqual(hit.stdout, 'send -s %12 毎朝レビュー');
    // 引けないときは従来どおり CLI の自動解決に任せる
    const miss = makeLoopProvider({ loopCommand: 'echo', runWindow: false }).run({ id: '未知の作業', cwd: os.tmpdir() });
    assert.strictEqual(miss.stdout, 'send 未知の作業');
  } finally {
    tmux.findPane = origFind;
  }
});

test('loop 実行は明示 args があればそれを優先する', () => {
  const r = makeLoopProvider({ loopCommand: 'echo', runWindow: false }).run({ id: 'X', args: ['send', '-s', 'sess', 'X'], cwd: os.tmpdir() });
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
    const r = providerSh('agent-loop', ['send', 'x'], { cwd: 'C:\\proj\\app' });
    // Linux 上のテストでは wsl.exe が無く ENOENT になるが、その ENOENT が
    // agent-loop ではなく wsl.exe を指していること＝WSL 経由であることを検証する。
    assert.ok(/wsl\.exe/.test(r.error), `wsl.exe を起動する: ${r.error}`);
    assert.ok(!/spawnSync agent-loop/.test(r.error), 'agent-loop を Windows 側で直接 spawn しない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('win32 の loop 実行は既定で別ウィンドウ（WSL tmux）起動になり、runWindow:false で従来動作に戻る', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const launched = makeLoopProvider({ loopCommand: 'agent-loop' }).run({ id: '毎朝レビュー', cwd: 'C:\\proj\\app' });
    assert.strictEqual(launched.ok, true);
    assert.strictEqual(launched.launched, true, '新しいウィンドウでの起動として返る');
    assert.match(launched.message, /別ウィンドウ/);
    // GUI プロセスからの直接 spawn ではコンソールが割り当てられずウィンドウが出ない。
    // cmd の start で新しいコンソールを開かせる（スクリプト本文は一時ファイル経由）。
    assert.match(launched.windowCommand, /^cmd\.exe \/d \/c start /, 'cmd の start でウィンドウを開く');
    assert.match(launched.windowCommand, /wsl\.exe .*-e bash -lc /, 'wsl.exe で bash ログインシェルを起動する');
    assert.ok(launched.scriptFile, '実行スクリプトを一時ファイルへ書く');
    assert.ok(fs.existsSync(launched.scriptFile), 'スクリプトファイルが実在する');
    assert.ok(!fs.existsSync(launched.scriptFile.replace(/\.sh$/, '.cmd')),
      '起動子ファイルは介さない（増やした組み立てが増やした失敗点だった）');
    assert.ok(
      fs.readFileSync(launched.scriptFile, 'utf8').includes("'agent-loop' 'send' '毎朝レビュー'"),
      'スクリプト本文に send コマンドが入る'
    );
    const legacy = makeLoopProvider({ loopCommand: 'agent-loop', runWindow: false })
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
  // tmux へは argv をそのまま渡す（シェル文字列 1 個へ畳まない）。畳むと
  // Node → cmd → wsl → bash → tmux → sh と段が重なるほど引用が崩れ、
  // 「セッションは作られたのに直後に消える」形で失敗する。
  assert.ok(script.includes("tmux new-session -d -s \"$__ses\" -c '/mnt/c/proj/app' 'kiro-cli' 'chat' '--trust-all-tools'"),
    'chatCommand を argv のまま tmux へ渡す（入れ子の引用を作らない）');
  assert.ok(!/'exec [^']*'"'"'/.test(script), 'シェル文字列へ畳んだ二重引用を作らない');
  assert.ok(script.includes('grep -qiE'), 'kiro-cli の入力プロンプトを待つ');
  assert.ok(script.includes('tmux send-keys -t "$__ses" -l -- ')
    && script.includes('tmux send-keys -t "$__ses" Enter;'),
  '本文と Enter を別々の send-keys で送る');
  assert.ok(!script.includes('paste-buffer'), '一括ペースト（paste-buffer）は使わない');
  assert.ok(script.includes("'レビューして {{target}}'"), 'プロンプト本文を引用して埋め込む');
  assert.ok(script.includes('tmux attach -t "$__ses"'), '送信後はアタッチして進行を見せる');
  assert.ok(script.includes('read _'), '終了時にウィンドウを即閉じしない');
  // 検出＋送信はバックグラウンドに回し、前面はすぐアタッチする（"起動を待っています" で固まらない）。
  assert.ok(/\)\s*&\s/.test(script), '検出＋送信はバックグラウンド、前面はすぐアタッチする');
  // 枠付き入力欄（Claude Code の `│ > │` 等）も検出する（取りこぼすと待たされ続けて遅い）。
  assert.ok(script.includes('│[[:space:]]*[>❯›]'), '枠で囲うプロンプトも入力待ち判定に含める');
  // kiro-cli の入力プレースホルダ（`>` を出さず「Ask a question or describe a task」を表示）も検出する。
  assert.ok(/grep -qiE/.test(script) && script.includes('ask a question'),
    'kiro-cli の入力プレースホルダ（大小無視）も入力待ち判定に含める');
});

test('窓を開く前に WSL を検査し、落ちたら開かずに理由を画面へ返す', () => {
  // 段が深い（Node → cmd /c start "" → wsl.exe → bash → tmux → CLI）ので、
  // 手前で落ちるとコンソールは一瞬で閉じて原因を持ち去る。tmux から先は
  // スクリプト側の生存チェックが受け持ち、その手前をここで確かめる。
  const okRes = realVerifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu',
    () => ({ status: 0, stdout: '', stderr: '', error: '' }));
  assert.strictEqual(okRes.ok, true);
  // 検査は起動と同じ argv の形で撃つ（形が違うと「検査は通るのに本番だけ落ちる」）
  assert.deepStrictEqual(wslMain.launchArgs('Ubuntu', '/mnt/c/t/run.sh').slice(0, 5),
    ['-d', 'Ubuntu', '-e', 'bash', '-lc']);

  // /mnt が見えない（automount 無効）— 何を確認すべきかまで書く
  const missing = realVerifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu',
    () => ({ status: wslMain.SCRIPT_MISSING_EXIT, stdout: '', stderr: '', error: '' }));
  assert.strictEqual(missing.ok, false);
  assert.ok(missing.error.includes('automount'));

  // ディストロが違う — 推測で別のディストロへ倒さず、そのまま報告する
  // （別ディストロで開いても対象フォルダは無い）
  const bad = realVerifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu', (cmd, args) => (args[0] === '--list'
    ? { status: 0, stdout: '  NAME   STATE    VERSION\n* Ubuntu Running  2', stderr: '', error: '' }
    : { status: 1, stdout: '', stderr: '指定された名前のディストリビューションはありません。', error: '' }));
  assert.strictEqual(bad.ok, false);
  assert.ok(bad.error.includes('-d Ubuntu'), 'どの指定で失敗したかを書く');
  assert.ok(bad.error.includes('指定された名前の'), 'wsl 自身のメッセージをそのまま出す');
  assert.ok(bad.error.includes('* Ubuntu'), 'インストール済み一覧を添える');

  // 検査が落ちたら窓を開かない
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  wslMain.verifyWslLaunch = () => ({ ok: false, error: 'WSL を起動できません（検査）' });
  try {
    const res = cowork_loopProvider.launchWindowScript('echo hi', { cwd: 'C:\\proj\\app' });
    assert.strictEqual(res.ok, false, '検査が落ちたら窓を開かない');
    assert.ok(res.error.includes('WSL を起動できません'), '理由をそのまま返す');
  } finally {
    wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('セッションが作成直後に消えたら検知し、CLI を直接実行して原因を見せる', () => {
  // これが今回の症状の芯。`tmux new-session -d` は起動するコマンドが存在しなくても
  // exit 0 を返し、セッションだけが直後に消える（tmux 3.4 で実測）。作成の戻り値しか
  // 見ていないと「作成できた」と誤認して attach に進み、原因が何も残らないまま窓が閉じる。
  const script = cowork_loopProvider.chatWindowScript({
    chatCommand: ['kiro-cli', 'chat', '--trust-all-tools'],
    cwd: '/mnt/c/proj/app', session: 's', prompt: 'p',
  });
  const createAt = script.indexOf('tmux new-session -d');
  const liveAt = script.indexOf('if ! tmux has-session -t "$__ses" 2>/dev/null; then', createAt);
  assert.ok(liveAt > createAt, '作成の直後に生存を確かめる（戻り値だけを信じない）');
  const attachAt = script.indexOf('tmux attach -t "$__ses"');
  assert.ok(liveAt < attachAt, 'attach より前に確かめる');
  // 消えていたら、同じコマンドを窓の中で直接実行して CLI 自身のエラーを見せる
  const fallback = script.slice(liveAt, attachAt);
  assert.ok(fallback.includes('tmux セッションが起動直後に終了しました'), '何が起きたかを述べる');
  assert.ok(fallback.includes("'kiro-cli' 'chat' '--trust-all-tools'; __rc=$?"),
    '同じコマンドを直接実行して原因を表示する');
  assert.ok(fallback.includes('read _'), '原因を読めるようウィンドウを閉じない');
  assert.ok(fallback.includes('exit 1'), 'attach へ進まず抜ける');
});

test('チャットも診断も同じ生存チェックを通る（片方だけ直さない）', () => {
  // 送るものが無い経路（CLIチャットの手動オープン）も同じ形に揃える。
  for (const prompt of ['p', null]) {
    const script = cowork_loopProvider.chatWindowScript({
      chatCommand: ['claude'], cwd: '/w', session: 's', prompt,
    });
    assert.ok(script.includes('tmux new-session -d'), `detached で作る (prompt=${prompt})`);
    assert.ok(script.includes('セッション生存 ok'), `生存チェックを通る (prompt=${prompt})`);
    assert.ok(script.includes('エージェントCLIが exit $__rc で終了しました'),
      `失敗時のフォールバック表示を持つ (prompt=${prompt})`);
  }
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
  // 送るものが無い経路も detached 作成 → 生存チェック → attach に揃える。
  // 直接 attach で起動すると、CLI が即死したときエラーが流れて消え、原因が残らない。
  assert.ok(script.includes('tmux new-session -d'), 'detached で作ってから生存を確かめる');
  assert.ok(!script.includes('grep -qiE'), '接続だけなら入力待ちをしない');
  assert.ok(!script.includes('tmux set-buffer -b agentdash --'), '空プロンプトを送信しない');
});

test('診断セッションは接頭辞で名前空間を分ける（作業用セッションと混ざらない）', () => {
  // 読み取り専用のつもりの窓が作業セッションに合流すると、そこから書き込みができてしまう
  // （S9 §6-2 の決着）。名前を分けるのがその実体。
  const work = cowork_loopProvider.chatSessionName('/home/me/app', 'kiro');
  const doctor = cowork_loopProvider.chatSessionName('/home/me/app', 'doctor:kiro:T-1', 'agent-doctor');
  assert.match(doctor, /^agent-doctor-/);
  assert.notStrictEqual(work, doctor);
  assert.strictEqual(doctor,
    cowork_loopProvider.chatSessionName('/home/me/app', 'doctor:kiro:T-1', 'agent-doctor'),
    '同じ need の再診断は同じセッションへ attach する');
  assert.notStrictEqual(doctor,
    cowork_loopProvider.chatSessionName('/home/me/app', 'doctor:kiro:T-2', 'agent-doctor'),
    'need が違えば別セッション');
});

test('promptOnNewOnly は既存セッションへブリーフを送り直さない', () => {
  // 会話が続いているところへ同じブリーフを再投入すると文脈が二重になる（S9-4）。
  const once = cowork_loopProvider.chatWindowScript({
    chatCommand: ['claude'], cwd: '/home/me/app', session: 'agent-doctor-x',
    prompt: 'ブリーフ', promptOnNewOnly: true,
  });
  assert.match(once, /if \[ \$__new -eq 1 \]; then __wait_ready \|\| exit 0; tmux send-keys/,
    '新規作成時だけ、入力受付を待ってから送る');
  const always = cowork_loopProvider.chatWindowScript({
    chatCommand: ['claude'], cwd: '/home/me/app', session: 'agent-chat-x', prompt: '業務プロンプト',
  });
  assert.ok(always.includes('tmux send-keys -t "$__ses" -l -- '), '既定は毎回送る（従来動作）');
  assert.ok(!/if \[ \$__new -eq 1 \]; then __wait_ready \|\| exit 0; tmux send-keys/.test(always));
});

test('エージェントコマンドと業務プロンプトは 1 送信ごとに入力受付（ready）を待ち直す', () => {
  // CLI はコマンドをキューしない。前のコマンドの実行中に次を送ると黙って捨てられるため、
  // 「エージェントに送る」×N → 業務プロンプト の各送信の前で必ず __wait_ready を挟む。
  const script = cowork_loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat --trust-all-tools',
    cwd: '/mnt/c/proj/app',
    session: 'kiro-dash-abc12345',
    prompt: '本題のプロンプト',
    sessionCommands: [
      { id: 'c1', mode: 'chat', run: '/first command' },
      { id: 'c2', mode: 'chat', run: '/second command' },
    ],
  });
  assert.ok(script.includes('__wait_ready() {'), 'ready 待ちを関数として定義する');
  const waits = (script.match(/__wait_ready \|\| exit 0; /g) || []).length;
  assert.strictEqual(waits, 3, 'チャットコマンド2件 + 業務プロンプトの計3送信すべての前で待つ');
  const firstAt = script.indexOf('/first command');
  const secondAt = script.indexOf('/second command');
  const promptAt = script.indexOf('本題のプロンプト');
  assert.ok(firstAt < secondAt && secondAt < promptAt, '送信順は開始コマンド → 業務プロンプト');
  const between = script.slice(firstAt, secondAt);
  assert.ok(between.includes('__wait_ready || exit 0; '),
    '前のコマンドの完了（入力受付の再表示）を待ってから次を送る');
});

test('terminalLaunchSpec は macOS のTerminalとLinuxの利用可能な端末を選ぶ', () => {
  const mac = cowork_loopProvider.terminalLaunchSpec('darwin', '/tmp/chat.command');
  assert.deepStrictEqual(mac, {
    command: 'open', args: ['-na', 'Terminal', '/tmp/chat.command'], terminal: 'Terminal',
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

test('定常業務も ⚙ 設定のディストロで POSIX パスを解決する（既定へ丸めない）', () => {
  // 「相談ボタンからは開けるのに定常業務からは開けない」の正体。
  // engine / nodeRepos 経由（プロジェクト・CLIチャット・対話診断）は toViewerPath に
  // engine.distro を渡していたが、cowork だけ渡しておらず WSL の既定へ丸まっていた。
  // 既定が設定と違う環境（docker-desktop 等）では別のディストロを指し、そこには
  // bash も対象フォルダも無いので、開いたウィンドウが即座に閉じる。
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  const prevEnv = process.env.WSL_DISTRO_NAME;
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  process.env.WSL_DISTRO_NAME = 'docker-desktop';   // WSL の既定（設定とは別物）
  try {
    const config = { engine: { distro: 'Ubuntu' } };
    assert.strictEqual(cowork.viewerRepo('/home/me/app', config),
      '\\\\wsl.localhost\\Ubuntu\\home\\me\\app', '設定のディストロで解決する');
    assert.strictEqual(cowork.viewerRepo('/home/me/app', { engine: {} }),
      '\\\\wsl.localhost\\docker-desktop\\home\\me\\app', '設定が空なら従来どおり既定へ');
    // UNC / ドライブ表記はそのまま（変換対象は POSIX 絶対パスだけ）
    assert.strictEqual(cowork.viewerRepo('C:\\proj\\app', config), 'C:\\proj\\app');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
    if (prevEnv === undefined) delete process.env.WSL_DISTRO_NAME;
    else process.env.WSL_DISTRO_NAME = prevEnv;
  }
});

test('win32 で job.prompt があれば agent-loop を介さず tmux + kiro-cli へ直接送るウィンドウを開く', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = makeLoopProvider({ loopCommand: 'agent-loop' })
      .run({ id: '毎朝レビュー', cwd: 'C:\\proj\\app', prompt: 'レビューしてください' });
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.launched, true);
    assert.match(r.session || '', /^kiro-dash-/, '接続先セッション名を返す');
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('kiro-cli') && body.includes('--trust-all-tools'), '既定の chatCommand で起動する');
    assert.ok(body.includes('レビューしてください'), '解決済みプロンプト本文を送る');
    assert.ok(!/agent-loop(?!\.yml)/.test(body.replace(/kiro-dash-[0-9a-f]+/g, '')), 'agent-loop は実行しない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('resolveLoopPromptText は .agents/agent-loop.yml のブロックスカラ本文を名前で解決する', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-prompt-'));
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'agent-loop.yml'), [
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

test('runLoop は必要な入力を検証し、置換済み本文を直接送る', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-runwin-'));
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'agent-loop.yml'), [
    'prompts:',
    '  - name: "毎朝レビュー"',
    '    prompt: レビューしてください {target}',
    '    interval_minutes: 60',
    '',
  ].join('\n'), 'utf8');
  const config = { cowork: { items: [{ id: 'daily', name: '毎朝レビュー', type: 'loop', repo }] } };
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    assert.deepStrictEqual(cowork.overview(config).items[0].parameters, ['target']);
    assert.throws(() => cowork.runLoop(config, 'daily'), /入力してください: target/);
    const r = cowork.runLoop(config, 'daily', { target: 'main' });
    assert.strictEqual(r.launched, true);
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('レビューしてください main'), '入力をプログラム側で置換する');
    assert.ok(!body.includes('{target}'), '未置換の入力をLLMへ渡さない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('runStateMachine は入力値を構造化した実行指示へ組み込む', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-smwin-'));
  writeMachine(repo, 'release', [
    'name: リリース',
    'initial_state: start',
    'context:',
    '  version: ""',
    'states:',
    '  start:',
    '    action: "{{input}} / {{context.version}}"',
    '    terminal: true',
    'transitions: []',
    '',
  ].join('\n'));
  const config = { cowork: {
    items: [{ id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo }],
  } };
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    assert.deepStrictEqual(cowork.overview(config).items[0].parameters, ['input', 'context.version']);
    assert.throws(() => cowork.runStateMachine(config, 'sm1', { input: 'app' }), /context.version/);
    const r = cowork.runStateMachine(config, 'sm1', { input: 'app', 'context.version': 'v1.2' });
    assert.strictEqual(r.launched, true);
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('statemachine-use スキルでreleaseステートマシンを実行して'), 'スキル発動文を送る');
    assert.ok(body.includes('- input: "app"') && body.includes('- context.version: "v1.2"'),
      '入力値をキー付きで渡す');
    assert.ok(!body.includes('先に必要な入力を箇条書きで私に質問'), '旧LLM入力促進文を送らない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('stateMachineInputSpec は外部ファイルを含む実参照だけを入力にする', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-sminput-'));
  const wf = writeMachine(repo, 'release', [
    'name: リリース',
    'initial_state: start',
    'context:',
    '  version: ""',
    '  channel: "stable"',
    '  unused: ""',
    'states:',
    '  start:',
    '    action_file: actions/start.md',
    '    output_key: result',
    'transitions:',
    '  - from: start',
    '    to: done',
    '    condition_file: conditions/start_to_done.md',
    '',
  ].join('\n'), {
    'actions/start.md': '{{input}} を {{version}} / {{context.channel}} へ反映。{{result}} は実行時出力',
    'conditions/start_to_done.md': '{{context.ticket}} があれば完了',
  });
  const spec = cowork.stateMachineInputSpec(wf);
  assert.deepStrictEqual(spec.keys, ['input', 'version', 'context.ticket']);
  assert.deepStrictEqual(spec.defaults, { 'context.channel': 'stable' }, '既定値済みは入力不要');
  assert.ok(!spec.keys.includes('context.unused'), '参照されない空 context は入力にしない');
  assert.ok(!spec.keys.includes('result'), 'output_key は実行時に生成されるので入力にしない');
  assert.match(cowork.stateMachineInputSpec(path.join(repo, 'none.yaml')).error, /読めません|ENOENT/);
});

test('stateMachineInputSpec は実行器が注入する変数を必須入力にしない', () => {
  // 分類の正典: statemachine-use の references/schema.md「Context Variable Reference」。
  // 実行器が実行開始時／ステート実行中に自分で作る値は、workflow の context: に無くても
  // 人に入力させない。人しか渡せない外部入力（input と未宣言の自由変数）だけを残す。
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-sm-runtime-'));
  const wf = writeMachine(repo, 'digest', [
    'name: ダイジェスト',
    'initial_state: fetch',
    'context:',
    '  source: ""',
    'states:',
    '  fetch:',
    '    action: "{{today}} 時点の {{source}} を {{now}} に取得（{{current_state}} / {{step_count}}）"',
    '    output_key: fetch_result',
    '    check: "pytest -q"',
    '  write:',
    '    action: "{{last_output}} と {{history.fetch}} と {{fetch_result}} から {{audience}} 向けに書く"',
    '    on_enter: "{{check_output}} を確認"',
    '    on_exit: "{{history}} を保存"',
    'transitions:',
    '  - from: fetch',
    '    to: write',
    '    condition_rule: "equals:check_ok:true"',
    '  - from: write',
    '    to: done',
    '    condition_rule: "startswith:check_status:0;equals:fetch_result:OK"',
    '',
  ].join('\n'));
  const spec = cowork.stateMachineInputSpec(wf);

  // 外部入力だけが必須になる: 宣言済みだが空の context（source）と、どこにも無い自由変数（audience）。
  assert.deepStrictEqual(spec.keys, ['source', 'audience']);

  // 組み込み変数（実行開始時に注入）
  for (const key of ['today', 'now', 'current_state', 'step_count', 'last_output', 'history', 'context']) {
    assert.ok(!spec.keys.includes(key), `組み込み変数を入力にしない: ${key}`);
  }
  // ステート実行中に注入される決定的検査の結果（condition_rule / on_enter からの参照を含む）
  for (const key of ['check_ok', 'check_status', 'check_output']) {
    assert.ok(!spec.keys.includes(key), `検査結果を入力にしない: ${key}`);
  }
  // 履歴変数とステート出力変数
  assert.ok(!spec.keys.includes('history.fetch'), '履歴変数を入力にしない');
  assert.ok(!spec.keys.includes('fetch_result'), 'ステート出力変数を入力にしない');
  // 実行器が注入する値は既定値としても出さない（人が上書きする面ではない）
  assert.deepStrictEqual(spec.defaults, {});
});

test('stateMachineInputSpec は context: で上書きされた組み込み変数も入力にしない', () => {
  // today / now は「組み込みだが context: で上書き可」。宣言されていても実行器が値を
  // 用意するので、人への要求にも既定値の提示にも出さない。
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-sm-builtin-override-'));
  const wf = writeMachine(repo, 'fixed-date', [
    'name: 固定日',
    'initial_state: work',
    'context:',
    '  today: "2000-01-01"',
    'states:',
    '  work:',
    '    action: "{{today}} の分を {{topic}} で書く"',
    '',
  ].join('\n'));
  const spec = cowork.stateMachineInputSpec(wf);

  assert.deepStrictEqual(spec.keys, ['topic']);
  assert.ok(!Object.prototype.hasOwnProperty.call(spec.defaults, 'today'),
    '組み込み変数は既定値として提示しない');
});

test('stateMachineInputSpec は input を実行器の注入と混同しない', () => {
  // input だけは実行器ではなく人が渡す（--input）。組み込み扱いで落としてはいけない。
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-sm-input-'));
  const wf = writeMachine(repo, 'ask', [
    'name: 依頼',
    'initial_state: work',
    'states:',
    '  work:',
    '    action: "{{input}} を {{last_output}} と突き合わせる"',
    '',
  ].join('\n'));

  assert.deepStrictEqual(cowork.stateMachineInputSpec(wf).keys, ['input']);
});

test('入力値は不足・未知キーを拒否し、宣言済みキーだけを置換する', () => {
  const spec = { keys: ['target'], defaults: {}, error: '' };
  assert.throws(() => cowork.validateParameters(spec, {}), /target/);
  assert.throws(() => cowork.validateParameters(spec, { target: 'main', extra: 'x' }), /extra/);
  const values = cowork.validateParameters(spec, { target: ' main ' });
  assert.deepStrictEqual(values, { target: 'main' });
  assert.strictEqual(cowork.applyParameters('対象={{ target }}', values), '対象=main');
});

test('windowStartArgs は argv を返す（コマンドラインを自前で組み立てない）', () => {
  // 自前の文字列を cmd.exe の引用規則へ合わせようとして 3 通り壊した
  // （3 段の cmd /c 入れ子・起動子 .cmd・verbatim な 1 本の文字列）。
  // argv を返して Node に引用させれば、引用の責任が 1 か所に寄る。
  const args = cowork_loopProvider.windowStartArgs(
    'Ubuntu', '/mnt/c/Users/dev/Temp/agent-dashboard/run.sh'
  );
  assert.deepStrictEqual(args, [
    '/d', '/c', 'start', '',
    'wsl.exe', '-d', 'Ubuntu',
    '-e', 'bash', '-lc', ". '/mnt/c/Users/dev/Temp/agent-dashboard/run.sh'",
  ]);
  // 空タイトルは Node が "" として渡す。start に「次が実行ファイル」と確定させるため。
  assert.strictEqual(args[3], '', 'タイトルは空文字の引数として渡す');
  assert.strictEqual(args[4], 'wsl.exe', 'タイトルの次が実行ファイル');
  // 引用符は 1 つも自前で書かない（Node が必要な分だけ付ける）
  assert.ok(args.every((a) => !a.includes('"')), '引用符を自前で埋め込まない');
  const noDistro = cowork_loopProvider.windowStartArgs('', '/mnt/c/t/run.sh');
  assert.ok(!noDistro.includes('-d'), 'distro 未指定なら -d を付けない');
  assert.strictEqual(noDistro[4], 'wsl.exe');
  // 窓のタイトルはコマンドラインではなくスクリプト側（エスケープシーケンス）で付ける
  const esc = cowork_loopProvider.titleEscape('定常業務 (agent-dashboard)');
  assert.ok(esc.includes('\\033]0;') && esc.includes("'定常業務 (agent-dashboard)'"));
  assert.strictEqual(cowork_loopProvider.titleEscape(''), '', 'タイトル未指定なら何も足さない');
});

test('win32 の起動は windowsVerbatimArguments に依存しない', () => {
  // verbatim は「自前で組み立てた 1 本の文字列をそのまま渡す」ための指定。
  // 引用を Node に任せる以上、これに依存してはいけない（依存が残ると自前組み立てへ戻る）。
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'loopProvider.js'), 'utf8'
  );
  const win32Branch = src.slice(src.indexOf("if (platform === 'win32') {"), src.indexOf('} else {'));
  assert.ok(!/windowsVerbatimArguments:\s*true/.test(win32Branch),
    'win32 分岐で verbatim を有効にしない');
  assert.ok(win32Branch.includes('windowStartArgs('), 'argv 組み立てを使う');
});

test('win32 の -d は cwd（WSL UNC）から取れるときだけ付ける（推測を渡さない）', () => {
  // 一覧から名前を推測して -d に渡す実装を入れたが、取り違えると
  // 「指定された名前のディストリビューションはありません。」で即死した。
  // 確実に分かるときだけ指定し、分からないときは wsl の既定へ委ねる。
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const unc = cowork_loopProvider.launchWindowScript('echo hi',
      { cwd: '\\\\wsl.localhost\\Ubuntu\\home\\me\\app' });
    assert.ok(unc.windowCommand.includes('-d Ubuntu'), 'UNC から取れた名前は指定する');
    const drive = cowork_loopProvider.launchWindowScript('echo hi', { cwd: 'C:\\proj\\app' });
    assert.ok(!/ -d /.test(drive.windowCommand),
      'Windows ドライブ上のときは -d を付けず既定に任せる');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('実行スクリプトは足跡（.log）を残す — stdout/stderr は奪わない', () => {
  // ウィンドウが「一瞬出て閉じる」とき画面に何も残らない。どこまで進んだかを時刻つきで
  // 残しておかないと、cd・tmux 作成・attach・read のどれで落ちたかを区別できない。
  const pre = cowork_loopProvider.tracePreamble('/tmp/x.log');
  assert.ok(pre.includes("__log='/tmp/x.log'"), 'ログ先を持つ');
  assert.ok(pre.includes('__t()'), '追記用の関数を定義する');
  assert.ok(pre.includes('>> "$__log"'), '追記だけ（リダイレクトで stdout を奪わない）');
  assert.ok(!/\bexec\s*>/.test(pre) && !pre.includes('| tee'),
    'stdout/stderr を奪わない（tmux attach が tty を失うと動かなくなる）');
  assert.ok(pre.includes('tty='), 'tty の有無を残す（read が即戻る症状の切り分け）');

  const script = cowork_loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat', cwd: '/mnt/c/proj/app', session: 's', prompt: 'p',
  });
  for (const mark of ['cd ok', 'tmux セッション作成 ok', 'attach 開始', 'attach 終了 status=$?',
    'Enter 待ち', '=== 終了 ===']) {
    assert.ok(script.includes(mark), `足跡を残す: ${mark}`);
  }
  assert.strictEqual(cowork_loopProvider.windowLogPath('/tmp/a/cowork-run-1.sh'),
    '/tmp/a/cowork-run-1.log', 'ログは実行スクリプトと対で置く');
});

test('windowScript は cd → send 実行 → 送信先ペインのセッションへ tmux attach を組み立てる', () => {
  const script = cowork_loopProvider.windowScript('agent-loop', ['send', '毎朝レビュー'], '/mnt/c/proj/app');
  assert.ok(script.includes("cd '/mnt/c/proj/app'"), 'プロジェクトルートへ cd する');
  assert.ok(script.includes("'agent-loop' 'send' '毎朝レビュー'"), 'send をそのまま実行する');
  assert.ok(script.includes('tee'), '出力を表示しつつ送信先ペインの特定に使う');
  assert.ok(script.includes('tmux attach'), '送信後はセッションへアタッチして進行を見せる');
  assert.ok(script.includes('read _'), '特定できないときはウィンドウを残して原因を読めるようにする');
});

test('state-machine 実行は statemachine-use スキルを発動するプロンプトを agent-loop send で送る', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-sm-send-'));
  writeMachine(repo, 'release', [
    'name: リリース',
    'initial_state: done',
    'states:',
    '  done:',
    '    terminal: true',
    'transitions: []',
    '',
  ].join('\n'));
  const config = { cowork: {
    loopCommand: 'echo',
    runWindow: false,   // 引数の組み立てを見るテスト（窓を開く経路は別テスト）
    items: [{ id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo }],
  } };
  const r = cowork.runStateMachine(config, 'sm1');
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send release ステートマシンを実行して');
});

test('runLoop / runStateMachine は実行履歴（historyFile）へ記録し readHistory で新しい順に読める', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-hist-'));
  const historyFile = path.join(repo, 'history.jsonl');
  writeMachine(repo, 'release', [
    'name: リリース',
    'initial_state: done',
    'states:',
    '  done:',
    '    terminal: true',
    'transitions: []',
    '',
  ].join('\n'));
  const config = { cowork: {
    loopCommand: 'echo',
    runWindow: false,
    historyFile,
    items: [
      { id: 'daily', type: 'loop', name: '毎朝レビュー', repo },
      { id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo },
    ],
  } };
  assert.ok(cowork.runLoop(config, 'daily').ok);
  assert.ok(cowork.runStateMachine(config, 'sm1').ok);
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
  fs.mkdirSync(path.join(repo, '.agent-loop', 'logs'), { recursive: true });
  const logFile = path.join(repo, '.agent-loop', 'logs', 'run.log');
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
  fs.mkdirSync(path.join(repo, '.agent-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agent-loop', 'logs', 'run.log'), 'finished successfully\n');
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
