'use strict';

const { spawn, spawnSync } = require('child_process');

function shellQuote(s) {
  return `'${String(s).replace(/'/g, `'"'"'`)}'`;
}

function isWslPath(p) {
  const s = String(p || '');
  return /^\\\\wsl(?:\$|\.localhost)\\/i.test(s) || /^\//.test(s);
}

function wslPath(p) {
  const s = String(p || '');
  const unc = s.replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (unc) return (unc[1] || '').replace(/\\/g, '/') || '/';
  return s;
}

function wslDistro(p) {
  const s = String(p || '');
  const unc = s.replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  return unc ? unc[1] : '';
}

// Windows ドライブパス（C:\foo\bar）→ WSL の /mnt/c/foo/bar。該当しなければ ''。
function winDriveToWsl(p) {
  const m = String(p || '').replace(/\//g, '\\').match(/^([A-Za-z]):(\\.*)?$/);
  if (!m) return '';
  const rest = (m[2] || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return `/mnt/${m[1].toLowerCase()}${rest}`;
}

// cwd（WSL UNC / POSIX / Windows ドライブ）を WSL 側の Linux パスへ寄せる。
function toWslCwd(p) {
  if (isWslPath(p)) return wslPath(p);
  return winDriveToWsl(p);
}

// コマンド設定（例 `python3 ~/tools/agent-loop/agent-loop.py`）を argv 配列へ分解する。
// クォート（"…" / '…'）で空白入りパスも表せる（agent-project/actions.js と同じ規則）。
// 全体を 1 トークンとして引用すると `'python3 /path/…': not found` になり実行できない。
function splitCommand(command) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(String(command || '').trim()))) {
    out.push(m[1] != null ? m[1] : m[2] != null ? m[2] : m[3]);
  }
  return out;
}

// シェルへ埋め込むトークンの引用。先頭の ~ は WSL 側の $HOME で展開されるよう
// 引用の外に出す（クォートすると ~ 展開されず not found になる）。
function quoteToken(t) {
  const s = String(t);
  if (s === '~') return '"$HOME"';
  if (s.startsWith('~/')) return `"$HOME"${shellQuote(s.slice(1))}`;
  return shellQuote(s);
}

// 非 win32 の直接 spawn 用: 先頭トークンの ~ を homedir で展開する（shell:false では
// ~ 展開が起きない）。
function expandHome(t) {
  const s = String(t || '');
  if (s === '~') return require('os').homedir();
  if (s.startsWith('~/')) return require('path').join(require('os').homedir(), s.slice(2));
  return s;
}

// Windows ネイティブ CLI は CP932、WSL は UTF-8。encoding:'utf8' 固定だと日本語が文字化けする。
// buffer で受け取り、UTF-8 → だめなら Shift_JIS（CP932 系）へフォールバックする。
function decodeCliOutput(buf) {
  if (buf == null) return '';
  if (typeof buf === 'string') return buf;
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
  if (!b.length) return '';
  const utf8 = b.toString('utf8');
  if (!utf8.includes('\uFFFD')) return utf8;
  try {
    return new TextDecoder('shift_jis').decode(b);
  } catch {
    return utf8;
  }
}

function resultOf(res) {
  return {
    ok: res.status === 0,
    status: res.status,
    stdout: decodeCliOutput(res.stdout).trim(),
    stderr: decodeCliOutput(res.stderr).trim(),
    error: res.error ? res.error.message : '',
  };
}

// ループ系 CLI 1 回分の起動仕様（同期 sh / 非同期 runCommandCapture の共通部分）。
//
// **win32 は必ず wsl.exe 経由**。agent-loop（と statemachine-use を発動するプロンプト送信、
// statemachine ハーネス）は WSL 側にしか無い想定で、Windows から直接 spawn すると ENOENT に
// なる。cwd も Windows パス（UNC / ドライブ）のままでは子プロセスへ渡せないので、
// WSL 側の `cd` としてスクリプトへ畳み込む。
// LANG を明示しないと WSL 側のロケールで日本語 stderr が化けることがある。
function cliSpawnSpec(command, args, cwd) {
  const tokens = splitCommand(command);
  const argv = (args || []).map(String);
  if (process.platform === 'win32') {
    const linuxCwd = toWslCwd(cwd);
    const distro = wslDistro(cwd);
    const cd = linuxCwd ? `cd ${shellQuote(linuxCwd)} && ` : '';
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}${tokens.map(quoteToken).join(' ')} ${argv.map(shellQuote).join(' ')}`;
    return {
      command: 'wsl.exe',
      args: distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script],
      // cwd は渡さない（Windows 側に存在しないパスを渡すと spawn 自体が失敗する）。
      options: { windowsHide: true },
    };
  }
  // shell:true は cmd.exe 経由で日本語引数・出力を壊す（agent-project/actions.js と同方針）。
  return {
    command: expandHome(tokens[0] || command),
    args: [...tokens.slice(1), ...argv],
    options: { cwd: cwd || process.cwd(), shell: false, windowsHide: true },
  };
}

function sh(command, args, options = {}) {
  const spec = cliSpawnSpec(command, args, options.cwd);
  const res = spawnSync(spec.command, spec.args, {
    ...spec.options,
    encoding: 'buffer',
    timeout: options.timeoutMs || 30000,
  });
  return resultOf(res);
}

// 実行を「見える」ようにする WSL 側スクリプト。send の出力を表示しつつ tee で拾い、
// 出力中のペイン ID（%N。agent-loop send が送信先を stderr に出す）からセッションを
// 特定できたらそのまま tmux attach する——実行の様子を同じウィンドウで見続けられる。
// 特定できない・失敗したときはウィンドウを開いたまま（read）にして原因を読めるようにする。
function windowScript(command, argv, cwd) {
  const cd = cwd ? `cd ${shellQuote(cwd)} || { __t "cd 失敗: ${cwd}"; echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 1; }; __t "cd ok: ${cwd}"; ` : '';
  const run = `${splitCommand(command).map(quoteToken).join(' ')} ${argv.map(shellQuote).join(' ')}`;
  return (
    `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
    `__out=$(mktemp 2>/dev/null || echo /tmp/agent-dashboard-run.$$); ` +
    `${run} 2>&1 | tee "$__out"; ` +
    `__pane=$(grep -o "%[0-9][0-9]*" "$__out" | head -1); rm -f "$__out"; ` +
    `if [ -n "$__pane" ]; then ` +
    `__sess=$(tmux display-message -p -t "$__pane" "#{session_name}" 2>/dev/null); ` +
    // exec にはしない: attach が失敗（tty 無し等）するとウィンドウが即閉じて原因が読めない。
    // attach から戻ったら（離脱・失敗とも）Enter 待ちに落として window を人が閉じる。
    `if [ -n "$__sess" ]; then echo; echo "[agent-dashboard] tmux セッション $__sess にアタッチします（Ctrl+b d で離脱）"; sleep 1; tmux attach -t "$__sess"; fi; ` +
    `else echo; echo "[agent-dashboard] tmux セッションを特定できませんでした"; fi; ` +
    `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; __t "Enter 待ち"; read _; __t "=== 終了 ==="`
  );
}

// 実行スクリプトと対の実行ログ（同じ場所・拡張子だけ .log）。
function windowLogPath(scriptFile) {
  return `${String(scriptFile).replace(/\.(?:sh|command)$/i, '')}.log`;
}

// 実行の足跡を残すシェル片。**stdout / stderr には触らない**——ここを tee やリダイレクトで
// 奪うと `tmux attach` が tty を失って動かなくなる。追記だけの `__t` を用意し、各段階で
// 呼ぶ（呼ぶ側は windowScript / chatWindowScript）。
//
// なぜ要るか: ウィンドウが「一瞬出て閉じる」とき、画面に何も残らないので、cd で落ちたのか・
// tmux が作れなかったのか・attach が即座に戻ったのか・そもそも read が待たずに返ったのかを
// 区別できない。時刻つきの足跡があれば、次の 1 回で切り分けられる。
function tracePreamble(logPath) {
  return (
    `__log=${shellQuote(logPath)}; `
    + '__t() { printf "%s %s\\n" "$(date +%H:%M:%S 2>/dev/null)" "$*" >> "$__log" 2>/dev/null || true; }; '
    + '__t "=== 起動 ==="; '
    // tty の有無は「read が待たずに返る」＝ウィンドウが一瞬で閉じる症状の決め手になる。
    // `tty` は tty でないとき stdout に "not a tty" と出して非 0 を返すので、それをそのまま拾う。
    + '__t "shell=$0 tty=$(tty 2>/dev/null)"; '
  );
}

// 実行スクリプトの一時ファイル置き場。%TEMP%\agent-dashboard\ に書き、WSL からは
// /mnt/<drive> 経由で読む。スクリプト本文を cmd.exe のコマンドラインに載せない
// （' % ^ & 等の引用規則で本文が化ける）ためのワンクッション。
function writeWindowScript(script, platform = process.platform) {
  const fs = require('fs');
  const os = require('os');
  const path = require('path');
  const dir = path.join(os.tmpdir(), 'agent-dashboard');
  fs.mkdirSync(dir, { recursive: true });
  // 古い実行スクリプト・ログの掃除（1 日以上前のもの。失敗しても実行は続ける）
  try {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    for (const f of fs.readdirSync(dir)) {
      if (!/^cowork-run-.*\.(?:sh|command|log)$/.test(f)) continue;
      const p = path.join(dir, f);
      try { if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p); } catch { /* 掃除失敗は無視 */ }
    }
  } catch { /* 掃除失敗は無視 */ }
  const ext = platform === 'darwin' ? 'command' : 'sh';
  const file = path.join(dir, `cowork-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`);
  // ログはスクリプト側（WSL）から書くので、win32 では WSL から見えるパスを渡す。
  const logPath = windowLogPath(file);
  const logForScript = platform === 'win32'
    ? (winDriveToWsl(logPath) || logPath.replace(/\\/g, '/'))
    : logPath;
  fs.writeFileSync(file,
    `#!/bin/sh\n${tracePreamble(logForScript)}${script.replace(/\r\n/g, '\n')}\n`, 'utf8');
  if (platform !== 'win32') fs.chmodSync(file, 0o700);
  return file;
}

function findExecutable(name) {
  const fs = require('fs');
  const path = require('path');
  for (const dir of String(process.env.PATH || '').split(path.delimiter).filter(Boolean)) {
    const file = path.join(dir, name);
    try {
      fs.accessSync(file, fs.constants.X_OK);
      return file;
    } catch { /* 次の PATH 候補へ */ }
  }
  return '';
}

// この端末で「別ウィンドウ実行」が使えるか。
//
// **ここを win32 に絞っていたのが「定常業務を実行すると固まる」の正体**だった。非 Windows は
// ウィンドウ経路を素通りして main プロセスの `spawnSync`（最大 60 秒）へ落ちる。その間
// Electron main は IPC を 1 つも返せないので、画面はボタンが押されたまま無反応になり、
// 実行の様子も見えない。ウィンドウ経路の中身（スクリプトを書いて端末で開く）は最初から
// OS 非依存で、macOS は Terminal、Linux は見つかった端末エミュレータで同じものを開ける。
function supportsRunWindow(platform = process.platform, which = findExecutable) {
  if (platform === 'win32' || platform === 'darwin') return true;
  if (platform !== 'linux') return false;
  try {
    terminalLaunchSpec(platform, '', which);
    return true;
  } catch {
    return false;   // 端末エミュレータが 1 つも無い環境（CI 等）は従来の同期実行へ落とす
  }
}

function terminalLaunchSpec(platform, scriptFile, which = findExecutable) {
  if (platform === 'darwin') {
    return { command: 'open', args: ['-a', 'Terminal', scriptFile], terminal: 'Terminal' };
  }
  if (platform !== 'linux') throw new Error(`未対応のOSです: ${platform}`);
  const candidates = [
    ['x-terminal-emulator', ['-e', scriptFile]],
    ['gnome-terminal', ['--', scriptFile]],
    ['konsole', ['-e', scriptFile]],
    ['xfce4-terminal', ['-e', scriptFile]],
    ['kitty', [scriptFile]],
    ['alacritty', ['-e', scriptFile]],
    ['xterm', ['-e', scriptFile]],
  ];
  for (const [name, args] of candidates) {
    const command = which(name);
    if (command) return { command, args, terminal: name };
  }
  throw new Error('利用できる外部ターミナルが見つかりません');
}

// Windows でウィンドウを開く argv。経路は **Node → cmd /d /c start "" → wsl.exe** の 1 本。
//
// ここに至るまでに 3 通りの組み立てを試し、3 通りとも壊した:
//   ・`start "<題>" cmd /d /s /c "wsl.exe -d "<distro>" … || pause"`（cmd の 3 段入れ子）
//   ・`start "<題>" "<起動子.cmd>" "<script>" "<distro>"`（起動子をファイル化）
//   ・`start "" wsl.exe …` を 1 本の文字列にして windowsVerbatimArguments で渡す
// 共通する敗因は「cmd.exe の引用規則に合う文字列を自前で組み立てようとした」こと。
// Windows 実機なしにその正しさを確かめる手段が無く、段が増えるほど崩れた。
//
// ログインシェルは bash を使う（sh=dash だと利用者の ~/.bashrc / profile にある
// bash 構文 `[[ … ]]` が `sh: N: [[: not found` になり、そこで止まると venv 有効化も
// 走らず「No virtual environment found」のままエージェントが起動できない）。
//
// 窓のタイトルはスクリプト側のエスケープシーケンスで付ける（コマンドラインに載せない）。
// 起動が失敗したときの手掛かりは、消えるコンソールではなく実行ログ（tracePreamble）に残す。
// **コマンドラインを自前で組み立てない。** argv を返して Node に引用させる
// （windowsVerbatimArguments は使わない）。自前の文字列を cmd.exe の引用規則へ
// 合わせ続けるのは無理があり、実際に何度も壊した。argv 方式なら引用の責任が 1 か所
// （Node）に寄り、こちらは「何を渡すか」だけを決める。
//
// 空タイトル '' は Node が "" として渡す。start は最初の引用済みトークンをタイトルと
// 見なすため、これを明示すると「タイトルは空・次のトークンが実行ファイル」と一意に決まる。
function windowStartArgs(distro, wslScriptPath) {
  return [
    '/d', '/c', 'start', '',
    'wsl.exe',
    ...(distro ? ['-d', distro] : []),
    '-e', 'bash', '-lc', `. '${wslScriptPath}'`,
  ];
}

// 窓のタイトルを付けるシェル片（OSC 0）。cmd のコマンドラインには載せない。
function titleEscape(title) {
  const t = String(title || '').trim();
  return t ? `printf '\\033]0;%s\\007' ${shellQuote(t)}; ` : '';
}

// スクリプトを新しいコンソールウィンドウ（WSL）で起動する共通処理。
// 成否は「ウィンドウ起動の受付」まで（実行結果はウィンドウ内で人が見る）。
function launchWindowScript(script, options = {}) {
  const platform = process.platform;
  let scriptFile;
  try {
    // 窓のタイトルはスクリプト側で付ける（cmd のコマンドラインに載せず、引用の段を増やさない）。
    scriptFile = writeWindowScript(`${titleEscape(options.title)}${script}`, platform);
  } catch (e) {
    return { ok: false, status: -1, stdout: '', stderr: '', error: `実行スクリプトを書けません: ${e.message}` };
  }
  let command;
  let args;
  let windowCommand;
  let terminal;
  let spawnOptions = { stdio: 'ignore', detached: true };
  if (platform === 'win32') {
    // ディストロは cwd（WSL UNC）から取れるときだけ指定する。取れないとき（リポジトリが
    // C:\ 配下）は **指定しない** ＝ wsl の既定に任せる。
    // 一度 `wsl --list` から名前を推測する実装を入れたが、推測を渡す方が壊れた
    // （名前を取り違えると「指定された名前のディストリビューションはありません。」）。
    // 確実に分かるときだけ指定し、分からないときは既定に委ねる。
    const distro = wslDistro(options.cwd);
    // C:\Users\...\Temp\... → /mnt/c/users/.../temp/...（変換できなければそのまま）
    const wslScriptPath = winDriveToWsl(scriptFile) || scriptFile.replace(/\\/g, '/');
    // **窓を開く前の事前検査**（wsl.exe / ディストロ / bash / スクリプトの可視性）。
    // ここで落ちる失敗はコンソールが一瞬で閉じて原因を持ち去るので、窓を開かず画面へ返す。
    // tmux から先（セッションが作成直後に消える）はスクリプト側の生存チェックが受け持つ。
    const check = require('../../../base/main/wsl').verifyWslLaunch(wslScriptPath, distro);
    if (!check.ok) {
      return { ok: false, status: -1, stdout: '', stderr: '', error: check.error, scriptFile };
    }
    command = 'cmd.exe';
    args = windowStartArgs(distro, wslScriptPath);
    // 表示用（実際に渡すのは argv。ここを実行に使わない）
    windowCommand = ['cmd.exe', ...args].join(' ');
    terminal = 'WSL';
    // windowsVerbatimArguments は付けない——引用は Node に任せる。
    spawnOptions = { ...spawnOptions, windowsHide: true };
  } else {
    let spec;
    try {
      spec = terminalLaunchSpec(platform, scriptFile);
    } catch (e) {
      return { ok: false, status: -1, stdout: '', stderr: '', error: e.message, scriptFile };
    }
    ({ command, args, terminal } = spec);
    windowCommand = [command, ...args].map(shellQuote).join(' ');
  }
  try {
    const child = spawn(command, args, spawnOptions);
    child.on('error', () => {}); // 起動失敗（ENOENT 等）で main プロセスを落とさない
    child.unref();
  } catch (e) {
    return { ok: false, status: -1, stdout: '', stderr: '', error: e.message };
  }
  return {
    ok: true,
    status: 0,
    launched: true,
    stdout: '',
    stderr: '',
    error: '',
    message: options.message || `別ウィンドウ（${terminal} / tmux）で実行を開始しました`,
    windowCommand,
    terminal,
    scriptFile,
    // ウィンドウが一瞬で閉じたときに「どこまで進んだか」を読む先。起動の受付は成功
    // していても中で落ちることがあり、その足跡はここにしか残らない。
    logFile: windowLogPath(scriptFile),
  };
}

// 新しいコンソールウィンドウで WSL 上のコマンドを実行する（Windows のみ）。
// 従来の spawnSync（非表示・60 秒でタイムアウト kill）では、セッション未起動時の
// kiro-cli 立ち上げ待ちで失敗し、失敗理由も見えなかった。見えるウィンドウで実行し、
// 送信後はそのまま tmux にアタッチして「動いている様子」を見られるようにする。
//
// GUI プロセス（Electron main）からコンソールアプリを直接 spawn しても、対話できる
// コンソールは割り当てられない（stdio が NUL になり read / tmux attach が失敗し、
// ウィンドウも表示されない）。cmd.exe の `start` に新しいコンソールを割り当てさせる。
function runInWindow(command, args, options = {}) {
  const cwd = toWslCwd(options.cwd);
  const script = windowScript(command, (args || []).map(String), cwd);
  return launchWindowScript(script, { cwd: options.cwd });
}

// ---------------------------------------------------------------------------
// インタラクティブ実行（agent-loop を介さない直接 tmux + kiro-cli）
// ---------------------------------------------------------------------------

// リポジトリごとに安定した tmux セッション名。'kiro' 接頭辞なので端末タブの
// 既定発見（sessionPrefix: 'kiro'）にもそのまま載る。
// prefix は用途の名前空間。作業用（`agent-chat-`）と診断（`agent-doctor-`）を分けるのは
// S9 §6-2 の決着——診断で開いた読み取り専用の窓が作業セッションに合流すると、そこから
// 書き込みができてしまう。
function chatSessionName(linuxCwd, cli = '', prefix = 'agent-chat') {
  const key = String(cli || '');
  const digest = require('crypto').createHash('sha1')
    .update(key ? `${key}\0${String(linuxCwd || '')}` : String(linuxCwd || ''))
    .digest('hex').slice(0, 8);
  if (!key) return `kiro-dash-${digest}`;
  const safeCli = key.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').slice(0, 20) || 'agent';
  const safePrefix = String(prefix || 'agent-chat').toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-') || 'agent-chat';
  return `${safePrefix}-${safeCli}-${digest}`;
}

// kiro-cli をインタラクティブ起動した tmux セッションへプロンプトを直接送るスクリプト。
// agent-loop は実行しない: セッションが無ければ作り、kiro-cli の入力プロンプト
// （`> ` / `!>` 等 — agent-loop の _PROMPT_RE と同じ判定）を待ってから
// set-buffer + paste-buffer + Enter（agent-loop の _send_to_pane と同じ安全送信）で
// 送信し、そのままアタッチして実行の様子を見せる。
// セッション開始コマンド（agent-session-commands）の process モードを、tmux セッションを
// **新規作成するときだけ** 走らせるシェル片。cwd が WSL 側にあるため、Electron main では
// なく起動スクリプトの中で実行する。on_error='fail' はセッションを作らずに抜ける。
function sessionProcessLines(entries) {
  return (entries || []).filter((e) => e.mode === 'process' && !e.skip).map((e) => {
    const body = e.cwd ? `cd ${shellQuote(e.cwd)} && ${e.run}` : e.run;
    const seconds = Number(e.timeout) || 60;
    // bash で走らせる（sh=dash だと `source` や `[[ … ]]` 等の bash 構文が not found になる）。
    const run = `{ if command -v timeout >/dev/null 2>&1; then timeout ${seconds} bash -c ${shellQuote(body)}; else bash -c ${shellQuote(body)}; fi; }`;
    const onFail = e.on_error === 'fail'
      ? `{ echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗したため起動しません"; read _; exit 1; }`
      : `echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗しました（続行します）"`;
    return `echo "[agent-dashboard] セッション開始コマンド: ${e.id}"; ${run} || ${onFail}; `;
  }).join('');
}

// 送信テキストは 1 行へ畳む（agent-loop の送信と同じ）。改行を含めて送ると kiro-cli 等が
// 途中で確定してしまうため、複数行は空白でつなぐ。
function oneLine(s) {
  return String(s == null ? '' : s).replace(/\r?\n/g, ' ').trim();
}

function sendKeysLine(s) {
  const text = oneLine(s);
  const sendEnter = `tmux send-keys -t "$__ses" Enter; `;
  // Codex は文字列と Enter の瞬時投入を改行扱いする。補完確定後の Enter も分ける。
  const submit = /^\$[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(text)
    ? `${sendEnter}sleep 1; ${sendEnter}`
    : sendEnter;
  return `tmux send-keys -t "$__ses" -l -- ${shellQuote(text)}; sleep 1; ${submit}`;
}

// chat モード（「エージェントに送る」）は、CLI が入力を受け付けてから業務プロンプトより先に送る。
// paste-buffer は補完メニューと競合するため使わず、文字列と Enter を別々に送る。
// 各コマンドの前で必ず __wait_ready（入力受付待ち）を挟む——エージェントコマンドは CLI 側で
// キューされないため、前のコマンドの実行中に次を送ると黙って捨てられる。ready が戻らなければ
// exit 0 で残りの送信ごと諦める（実行中のセッションへ後続を投げ込まない）。
function sessionChatLines(entries) {
  return (entries || []).filter((e) => e.mode === 'chat' && !e.skip).map((e) => (
    `__wait_ready || exit 0; ${sendKeysLine(e.run)}sleep 1; `
  )).join('');
}

// 入力受付の既定パターン（定義に interactive.ready_pattern が無い CLI 用）。
//   ・行全体が素のプロンプト（`>` `❯` `›` `?`）だけ／`!>`／枠付き入力欄（`│ > │`）
//   ・kiro-cli の入力プレースホルダ「Ask a question or describe a task」
//     （kiro-cli は入力欄に `>` ではなくゴーストテキストを出すため、素のプロンプト判定に
//      当たらない。入力受付中にだけ出るので準備完了の合図として最適）
// CLI ごとの差分は agents/<name>.json の interactive.ready_pattern で宣言する（S9）。
const DEFAULT_READY_PATTERN =
  '^[[:space:]]*[>?❯›][[:space:]]*$|!>|│[[:space:]]*[>❯›]|ask a question|describe a task';
const DEFAULT_READY_TIMEOUT_SEC = 60;

function chatWindowScript({ chatCommand, cwd, session, prompt, sessionCommands,
                            readyPattern, readyTimeoutSec, promptOnNewOnly = false }) {
  const chatTokens = Array.isArray(chatCommand)
    ? chatCommand.map(String)
    : splitCommand(chatCommand || 'kiro-cli chat --trust-all-tools');
  const chat = chatTokens.map(quoteToken).join(' ');
  const ses = String(session || 'kiro-dash');
  const sendPrompt = prompt !== null && prompt !== undefined && String(prompt) !== '';
  const cd = cwd ? `cd ${shellQuote(cwd)} || { __t "cd 失敗: ${cwd}"; echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 1; }; __t "cd ok: ${cwd}"; ` : '';
  const preLines = sessionProcessLines(sessionCommands);
  const chatLines = sessionChatLines(sessionCommands);
  // 検出は 0.5 秒間隔。画面全体を見る（末尾数行に絞ると、入力欄の下にステータス行や
  // ヒントを出す CLI で取りこぼし、送信されなくなる）。
  const pattern = String(readyPattern || DEFAULT_READY_PATTERN);
  const waitTicks = Math.max(1, Math.round(Number(readyTimeoutSec || DEFAULT_READY_TIMEOUT_SEC) * 2));
  // 入力受付（ready）待ち。初回だけでなく**各送信の間**でも呼ぶ——エージェントコマンドは
  // CLI 側でキューされないため、前のコマンドの完了を待たずに次を送ると取りこぼされる
  // （以前は最初に 1 回待ったあと sleep 1 だけで連投していた）。
  const waitReady =
    `__wait_ready() { __i=0; while [ $__i -lt ${waitTicks} ]; do ` +
    `if tmux capture-pane -p -t "$__ses" 2>/dev/null | grep -qiE ${shellQuote(pattern)}; then return 0; fi; ` +
    `sleep 0.5; __i=$((__i+1)); done; return 1; }; `;
  // **tmux へは argv をそのまま渡す**（シェル文字列 1 個に畳まない）。
  // 畳むと `shellQuote('exec ' + 各トークンを quote した文字列)` という二重引用になり、
  // Windows → wsl.exe → bash → tmux → sh と段が重なるほど解釈が崩れて、
  // 「セッションは作られたのに直後に消える」形で失敗する。argv 形式なら引用は 1 段で済み、
  // tmux が exec するので余分な sh も挟まらない（空白入りの引数もそのまま届く）。
  const cwdArg = cwd ? `-c ${shellQuote(cwd)} ` : '';
  const create = `tmux new-session -d -s "$__ses" ${cwdArg}${chat}`;
  // **生存チェック**。`tmux new-session -d` は起動するコマンドが存在しなくても exit 0 を返し、
  // セッションだけが直後に消える（実機で確認済み）。作成の戻り値だけを見ていると、
  // 「作成できた」と誤認したまま attach に進み、原因が何も残らないまま窓が閉じる。
  // 消えていたら **同じコマンドを窓の中で直接実行**して、CLI 自身のエラーを人に見せる。
  const cliShown = shellQuote(chatTokens.join(' '));
  const liveCheck =
    `sleep 1; ` +
    `if ! tmux has-session -t "$__ses" 2>/dev/null; then ` +
    `__t "セッションが起動直後に消えた"; ` +
    `echo; echo "[agent-dashboard] tmux セッションが起動直後に終了しました（エージェントCLIを起動できていません）"; ` +
    `echo "[agent-dashboard] 同じコマンドをこの窓で直接実行して原因を表示します:"; ` +
    `echo "  $(printf %s ${cliShown})"; echo; ` +
    `${chat}; __rc=$?; ` +
    `__t "CLI 直接実行 exit=$__rc"; ` +
    `echo; echo "[agent-dashboard] エージェントCLIが exit $__rc で終了しました"; ` +
    `echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; read _; __t "=== 終了(CLI起動失敗) ==="; exit 1; ` +
    `fi; __t "セッション生存 ok"; `;
  const createChecked =
    `${create} || { __t "tmux セッション作成に失敗"; echo "[agent-dashboard] tmux セッション作成に失敗しました"; read _; exit 1; }; `
    + `__t "tmux セッション作成 ok"; ` + liveCheck;
  if (!sendPrompt && !chatLines) {
    return (
      `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
      `__ses=${shellQuote(ses)}; ` +
      `if ! tmux has-session -t "$__ses" 2>/dev/null; then ` +
      preLines +
      `echo "[agent-dashboard] tmux セッション $__ses を作成してエージェントCLIへ接続します（Ctrl+b d で離脱）"; ` +
      `__t "セッション作成"; ${createChecked}` +
      `else echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; __t "既存セッションへ接続"; fi; ` +
      `__t "attach 開始"; tmux attach -t "$__ses"; __t "attach 終了 status=$?"; ` +
      `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; __t "Enter 待ち"; read _; __t "=== 終了 ==="`
    );
  }
  return (
    `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
    `__ses=${shellQuote(ses)}; __new=0; ` +
    `if ! tmux has-session -t "$__ses" 2>/dev/null; then ` +
    `__new=1; ` +
    // セッションを新しく作るときだけ前準備を走らせる（既存セッションへの送信では走らせない）
    preLines +
    `echo "[agent-dashboard] tmux セッション $__ses を作成してエージェントCLIを起動します"; ` +
    createChecked +
    `fi; ` +
    `__t "セッション: $__ses new=$__new"; ` +
    // 送るものがあるときは、プロンプト検出＋送信を **バックグラウンド**に回し、前面はすぐ
    // アタッチして「起動の様子」を見せる。従来は検出が終わるまで最大 60 秒 "起動を待っています"
    // で固まって見えた（CLI の起動自体が遅いと、その間ずっと真っ黒な待ち画面だった）。
    // 判定パターンとタイムアウトは CLI 定義（interactive.ready_pattern /
    // ready_timeout_sec）から来る。大文字小文字は問わない（-i）。
    // 各送信は __wait_ready を挟んで逐次化する（前のコマンドの完了 = 入力受付の再表示を
    // 待ってから次を送る。CLI はコマンドをキューしないため、連投すると捨てられる）。
    (sendPrompt || chatLines
      ? waitReady +
        `( ` +
        // 「エージェントに送る」開始コマンドの送信タイミング:
        //   ・業務プロンプトを送る定常ループ … 前準備の二重送信を避け、新規セッション時だけ送る。
        //   ・CLIチャットの手動オープン（業務プロンプト無し）… 開くたびに毎回送る（常駐セッションへ
        //     再接続したときも設定した「エージェントに送る」を適用する）。
        (chatLines
          ? (sendPrompt ? `if [ $__new -eq 1 ]; then ${chatLines}fi; ` : chatLines)
          : '') +
        // 業務プロンプトも agent-loop と同じ send-keys（テキストと Enter を分けて）で送る。
        // promptOnNewOnly は「既存セッションへは送らない」（S9-4 の対話診断）——会話が
        // 続いているところへ同じブリーフを再投入すると文脈が二重になる。
        (sendPrompt
          ? (promptOnNewOnly
            ? `if [ $__new -eq 1 ]; then __wait_ready || exit 0; ${sendKeysLine(prompt)}fi; `
            : `__wait_ready || exit 0; ${sendKeysLine(prompt)}`)
          : '') +
        `) & ` +
        `echo "[agent-dashboard] エージェントCLIに接続します（起動後に自動で送信します・Ctrl+b d で離脱）"; ` +
        `sleep 1; __t "attach 開始"; tmux attach -t "$__ses"; __t "attach 終了 status=$?"; `
      : `echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; sleep 1; __t "attach 開始"; tmux attach -t "$__ses"; __t "attach 終了 status=$?"; `) +
    `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; __t "Enter 待ち"; read _; __t "=== 終了 ==="`
  );
}

// プロンプトを新しいウィンドウの tmux + kiro-cli セッションへ直接送る実行経路。
function runChatWindow({ chatCommand, prompt, cwd, sessionCommands, sessionKey, title, message,
                        readyPattern, readyTimeoutSec, promptOnNewOnly, sessionPrefix }) {
  const linuxCwd = toWslCwd(cwd);
  const session = chatSessionName(linuxCwd || cwd, sessionKey, sessionPrefix);
  const script = chatWindowScript({
    chatCommand, cwd: linuxCwd, session,
    prompt: prompt === null || prompt === undefined ? null : String(prompt), sessionCommands,
    readyPattern, readyTimeoutSec, promptOnNewOnly,
  });
  const res = launchWindowScript(script, {
    cwd,
    title,
    message: message || '別ウィンドウ（ターミナル / tmux）でエージェントCLIを起動しました',
  });
  return res.ok ? { ...res, session } : res;
}

// 一回限りのコマンド（agent-loop statemachine 等）を実行するセッションの名前空間。
// 実行ごとに一意な名前を使い、チャット（`agent-chat-`）や診断（`agent-doctor-`）の
// 常駐セッションへ合流させない。
const COMMAND_SESSION_PREFIX = 'agent-sm';

// 一回限りのコマンドを tmux セッションの中で実行して見せるスクリプト。
// チャット経路（chatWindowScript）と違い ready 判定も send-keys も無く、コマンド自身が
// 完走して終わる。ウィンドウを閉じても tmux 側の実行は続く。
//
// チャット経路と**意図的に違える点**（どちらも実機の tmux で確かめた上での判断）:
//
//   1. **失敗しても同じコマンドを窓で再実行しない**。chatWindowScript は「セッションが
//      起動直後に消えていたら同じ CLI を窓で直接実行して原因を見せる」が、あれは何度
//      起動してもよい対話 CLI だから成り立つ。ステートマシンは 1 回の実行でファイルを
//      書き換えるので、再実行すると同じ工程を二重に走らせてしまう。代わりに、
//      起動できない唯一の実質的な原因（PATH に無い）を **事前に** 確かめる
//      （agent-loop 側の cmd_send が which で確かめているのと同じ）。
//   2. **実行の記録はペインではなくファイルに採る**。tmux の pane は、実行が終わった
//      あとに人がアタッチすると、そのときのリサイズで内容が消える（remain-on-exit で
//      pane を残しても同じ。実機で確認済み）。短時間で終わる実行ほど「見に行ったら
//      何も無い」になるので、`pipe-pane` で出力をファイルへ写し、アタッチから戻った
//      あとに窓へ出す。runInWindow が tee で出力を拾っているのと同じ考え方。
//      pipe-pane は placeholder のうちに繋いでおき、`respawn-pane` で本命へ差し替える
//      ——本命をいきなり起動すると、繋ぐ前に出た行を取りこぼす。
function commandWindowScript({ command, args, cwd, session }) {
  const tokens = splitCommand(command);
  const run = [...tokens.map(quoteToken), ...(args || []).map(shellQuote)].join(' ');
  const bin = quoteToken(tokens[0] || command);
  const cd = cwd ? `cd ${shellQuote(cwd)} || { __t "cd 失敗: ${cwd}"; echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 1; }; __t "cd ok: ${cwd}"; ` : '';
  const cwdArg = cwd ? `-c ${shellQuote(cwd)} ` : '';
  const fail = (trace, message) => `{ __t ${shellQuote(trace)}; echo ${shellQuote(`[agent-dashboard] ${message}`)}; read _; exit 1; }`;
  return (
    `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
    `__ses=${shellQuote(session)}; ` +
    // 実行ファイルの事前確認。tmux は exec に失敗しても何も残さず pane ごと消えるので、
    // ここで断らないと「一瞬で終わって理由が分からない」だけになる。
    `command -v ${bin} >/dev/null 2>&1 || `
    + fail('実行ファイルが PATH に無い', `${tokens[0] || command} が PATH に見つかりません（WSL 側にインストールされているか確認してください）`) + '; ' +
    `__out=$(mktemp 2>/dev/null || echo /tmp/agent-dashboard-sm.$$); ` +
    `echo "[agent-dashboard] tmux セッション $__ses で実行します（Ctrl+b d で離脱しても実行は続きます）"; ` +
    `tmux new-session -d -s "$__ses" ${cwdArg}sleep 300 || `
    + fail('tmux セッション作成に失敗', 'tmux セッションを作成できませんでした') + '; ' +
    `tmux pipe-pane -o -t "$__ses" "cat >> '$__out'"; ` +
    `tmux respawn-pane -k -t "$__ses" ${cwdArg}${run} || `
    + `{ tmux kill-session -t "$__ses" 2>/dev/null; `
    + `__t "respawn-pane に失敗"; echo "[agent-dashboard] tmux ペインでコマンドを起動できませんでした"; read _; exit 1; }; ` +
    `__t "実行開始 $__ses"; ` +
    // 終了済みなら attach は失敗する（セッションはもう無い）。それは異常ではないので、
    // 下で記録を出す。
    `sleep 1; __t "attach 開始"; tmux attach -t "$__ses" 2>/dev/null; __t "attach 終了 status=$?"; ` +
    `if tmux has-session -t "$__ses" 2>/dev/null; then ` +
    `echo; echo "[agent-dashboard] 実行は継続中です。再接続: tmux attach -t $__ses"; __t "実行継続"; ` +
    `else echo; echo "[agent-dashboard] 実行が終了しました。出力（末尾）:"; `
    + `tail -n 30 "$__out" 2>/dev/null; rm -f "$__out"; __t "実行終了"; fi; ` +
    `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; __t "Enter 待ち"; read _; __t "=== 終了 ==="`
  );
}

// 一回限りのコマンドを新しいウィンドウの tmux セッションで実行する。セッション名は
// 実行ごとに一意（前回の実行ウィンドウが残っていても合流・誤送信しない）。
// cwd は **WSL 側の Linux パスへ翻訳してから** tmux と cd に渡す（Windows パスのまま
// 渡すと `-c` も `cd` も刺さらず、コマンドが別の場所で走る）。
function runCommandWindow({ command, args, cwd, sessionKey, title, message }) {
  const linuxCwd = toWslCwd(cwd);
  const base = chatSessionName(linuxCwd || cwd, sessionKey, COMMAND_SESSION_PREFIX);
  const session = `${base}-${Date.now().toString(36)}`;
  const script = commandWindowScript({ command, args, cwd: linuxCwd, session });
  const res = launchWindowScript(script, {
    cwd,
    title,
    message: message || '別ウィンドウ（ターミナル / tmux）で実行を開始しました',
  });
  return res.ok ? { ...res, session } : res;
}

// 一回限りのコマンドを非表示・非同期で実行し、結果を Promise で返す（ウィンドウ非対応
// 環境用のフォールバック。同期 spawnSync は main プロセスを実行時間ぶん止めるので使わない）。
// 出力末尾の `RESULT {json}` 行（agent-loop statemachine の結果契約）があれば解析して返す。
function runCommandCapture(command, args, options = {}) {
  // 起動仕様は同期の sh() と同じ（win32 は wsl.exe 経由）。ここを直接 spawn にすると、
  // Windows のダッシュボードから WSL 側の agent-loop を呼べない。
  const spec = cliSpawnSpec(command, args, options.cwd);
  return new Promise((resolve) => {
    const out = [];
    const err = [];
    let timedOut = false;
    let child;
    try {
      child = spawn(spec.command, spec.args, spec.options);
    } catch (e) {
      resolve({ ok: false, status: -1, stdout: '', stderr: '', error: e.message });
      return;
    }
    const timer = setTimeout(() => {
      timedOut = true;
      try { child.kill(); } catch { /* 終了済みなら無視 */ }
    }, Math.max(1000, options.timeoutMs || 1800000));
    child.stdout.on('data', (chunk) => { out.push(chunk); });
    child.stderr.on('data', (chunk) => { err.push(chunk); });
    child.on('error', (e) => {
      clearTimeout(timer);
      resolve({ ok: false, status: -1, stdout: '', stderr: '', error: e.message });
    });
    child.on('close', (status) => {
      clearTimeout(timer);
      const stdout = decodeCliOutput(Buffer.concat(out));
      const stderr = decodeCliOutput(Buffer.concat(err));
      if (timedOut) {
        resolve({ ok: false, status, stdout: stdout.trim(), stderr: stderr.trim(), error: '実行がタイムアウトしました' });
        return;
      }
      const line = stdout.split(/\r?\n/).reverse().find((l) => l.startsWith('RESULT '));
      if (line) {
        try {
          const parsed = JSON.parse(line.slice('RESULT '.length));
          resolve({ status, stderr: stderr.trim(), ...parsed, ok: parsed.ok === true });
          return;
        } catch { /* RESULT 行が壊れていれば exit code で判定へ */ }
      }
      resolve({ ok: status === 0, status, stdout: stdout.trim(), stderr: stderr.trim(), error: '' });
    });
  });
}

// `send <プロンプト名>` の引数を組む。ペインが複数動いていると送信先を省略した send は
// 「複数のペインが動作中です」で失敗するため、名前からペインを引けたら -s で明示する。
function sendArgsFor(job) {
  if (Array.isArray(job.args)) return job.args;
  const name = job.id || job.name;
  if (!name) return ['send'];
  let pane;
  try {
    pane = require('../../routines/main/tmux').findPane({ repo: job.cwd || job.repo, name });
  } catch {
    pane = '';   // 解決できなければ従来どおり CLI の自動解決に任せる
  }
  return pane ? ['send', '-s', pane, name] : ['send', name];
}

// cfg は cowork セクション、config は**アプリ設定の全体**。全体を受け取るのは、起動する
// 対話 CLI の解決が cowork の外（全体設定の実行制御 / ⚙ アシスタント設定）に依っているから。
// 以前はここで `{ cowork: cfg }` を組み直しており、その時点で orchestration も agent も
// 落ちていた＝どう設定しても定常業務は既定の CLI で起動していた。
function makeLoopProvider(cfg, config = null) {
  const appConfig = config || { cowork: cfg };
  // 実体はコマンド 1 つ。`loopProvider` は種類欄を廃止する前の設定（旧 config.json）を
  // 読むためだけに残す。未設定は agent-loop。
  const command = cfg.loopCommand || cfg.loopProvider || 'agent-loop';
  const provider = command;
  return {
    provider,
    command,
    run(job) {
      // 既定は新しいウィンドウの tmux 上での実行（Windows は WSL、macOS は Terminal、
      // Linux は見つかった端末）。`cowork.runWindow: false` で従来の非表示 spawnSync に戻せる
      // ——ただしそちらは main プロセスを最大 60 秒止めるので、既定にはしない。
      if (cfg.runWindow !== false && supportsRunWindow()) {
        // job.prompt があれば agent-loop を介さず、tmux + kiro-cli（インタラクティブ）へ
        // プロンプトを直接送る。呼び出し側（cowork.runLoop / runStateMachine）が
        // agent-loop.yml の本文やステートマシン実行文を解決して渡してくる。
        if (job.prompt) {
          // 起動する対話 CLI は全体設定（実行制御の workloads.routine）→ ⚙ アシスタント設定
          // → プロジェクト設定の順で解決する。cfg.chatCommand は明示上書き（S9-3）。
          // 呼び出し側（cowork.runLoop / runStateMachine）が解決済みなら再解決しない
          // ——開始コマンドの計画と同じ CLI でなければ、送る先と送る中身がずれる。
          const { coworkChatLaunch } = require('./cowork');
          return runChatWindow({
            ...(job.launch || coworkChatLaunch(appConfig, job.cwd || job.repo)),
            prompt: job.prompt,
            cwd: job.cwd || job.repo,
            sessionCommands: job.sessionCommands,
          });
        }
        // 明示 args（レガシー）の項目は従来どおり <loopCommand> をウィンドウで実行する
        return runInWindow(command, sendArgsFor(job), { cwd: job.cwd || job.repo });
      }
      // agent-loop / agent-loop に `run` サブコマンドは無い。単発実行は
      // `send <プロンプト名>` — cwd（ワークスペース）の .kiro/agent-loop.* から
      // 定期プロンプト名を解決してセッションへ送信する（送信のみで応答は待たない）。
      return sh(command, sendArgsFor(job), { cwd: job.cwd || job.repo, timeoutMs: job.timeoutMs || 60000 });
    },
  };
}

module.exports = {
  DEFAULT_READY_PATTERN, DEFAULT_READY_TIMEOUT_SEC,
  makeLoopProvider, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, shellQuote, sh,
  decodeCliOutput, windowScript, windowStartArgs, titleEscape, windowLogPath, tracePreamble,
  writeWindowScript,
  runInWindow,
  chatWindowScript, chatSessionName, runChatWindow, launchWindowScript,
  commandWindowScript, runCommandWindow, runCommandCapture, cliSpawnSpec,
  COMMAND_SESSION_PREFIX,
  sessionProcessLines, sessionChatLines,
  splitCommand, quoteToken, expandHome, findExecutable, terminalLaunchSpec, supportsRunWindow,
};
