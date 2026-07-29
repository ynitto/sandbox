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

// コマンド設定（例 `python3 ~/tools/kiro-loop/kiro-loop.py`）を argv 配列へ分解する。
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

function sh(command, args, options = {}) {
  const argv = (args || []).map(String);
  const tokens = splitCommand(command);
  if (process.platform === 'win32') {
    // kiro-loop / agent-loop（と statemachine-use を発動するプロンプト送信）は WSL 側にしか
    // 無い想定。リポジトリが Windows ドライブ上でも wsl.exe 経由でプロジェクトルートから
    // 実行する（Windows で直接 spawn すると ENOENT になる）。
    const cwd = toWslCwd(options.cwd);
    const distro = wslDistro(options.cwd);
    // LANG を明示しないと WSL 側のロケールで日本語 stderr が化けることがある。
    const cd = cwd ? `cd ${shellQuote(cwd)} && ` : '';
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}${tokens.map(quoteToken).join(' ')} ${argv.map(shellQuote).join(' ')}`;
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script];
    const res = spawnSync('wsl.exe', wslArgs, {
      encoding: 'buffer',
      timeout: options.timeoutMs || 30000,
      windowsHide: true,
    });
    return resultOf(res);
  }
  // shell:true は cmd.exe 経由で日本語引数・出力を壊す（agent-project/actions.js と同方針）。
  const res = spawnSync(expandHome(tokens[0] || command), [...tokens.slice(1), ...argv], {
    cwd: options.cwd || process.cwd(),
    encoding: 'buffer',
    shell: false,
    timeout: options.timeoutMs || 30000,
    windowsHide: true,
  });
  return resultOf(res);
}

// 実行を「見える」ようにする WSL 側スクリプト。send の出力を表示しつつ tee で拾い、
// 出力中のペイン ID（%N。kiro-loop send が送信先を stderr に出す）からセッションを
// 特定できたらそのまま tmux attach する——実行の様子を同じウィンドウで見続けられる。
// 特定できない・失敗したときはウィンドウを開いたまま（read）にして原因を読めるようにする。
function windowScript(command, argv, cwd) {
  const cd = cwd ? `cd ${shellQuote(cwd)} || { __t "cd 失敗: ${cwd}"; echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 0; }; __t "cd ok: ${cwd}"; ` : '';
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

// `cmd /s /c start "<title>" wsl.exe [-d "<distro>"] -e bash -lc ". '<script>'"` のコマンドライン。
// windowsVerbatimArguments でそのまま渡すため自前で組み立てる（Node の既定の引用は
// cmd.exe の規則と一致しない）。
//
// **引用符は 1 段に保ち、入れ子を作らない。** 過去に 2 通りの入れ子を試して両方壊した:
//   ・`start "<題>" cmd /d /s /c "wsl.exe -d "<distro>" … || pause"`（3 段）
//   ・`start "<題>" "<起動子.cmd>" "<script>" "<distro>"`（起動子をファイル化）
// cmd.exe の引用・パス解決を Windows 無しで検証する手段が無い以上、**組み立てを増やさない**
// のが唯一の防御になる。この形（title と -lc の引数だけが引用され、入れ子にならない）は
// 定常業務ウィンドウが元から使っていた形で、wsl.exe の起動までは到達していた実績がある。
//
// 起動が失敗したときの説明は、消えるコンソールではなく **起動前の検査**（base/main/wsl.js の
// verifyWslLaunch）が担い、アプリの画面へ返す。
//
// ログインシェルは bash を使う（sh=dash だと利用者の ~/.bashrc / profile にある
// bash 構文 `[[ … ]]` が `sh: N: [[: not found` になり、そこで止まると venv 有効化も
// 走らず「No virtual environment found」のままエージェントが起動できない）。
function windowStartCommand(distro, wslScriptPath, title = '定常業務 (agent-dashboard)') {
  const d = distro ? `-d "${distro}" ` : '';
  return `start "${title}" wsl.exe ${d}-e bash -lc ". '${wslScriptPath}'"`;
}

// スクリプトを新しいコンソールウィンドウ（WSL）で起動する共通処理。
// 成否は「ウィンドウ起動の受付」まで（実行結果はウィンドウ内で人が見る）。
function launchWindowScript(script, options = {}) {
  const platform = process.platform;
  let scriptFile;
  try {
    scriptFile = writeWindowScript(script, platform);
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
    const cmdline = windowStartCommand(distro, wslScriptPath, options.title);
    command = 'cmd.exe';
    args = ['/d', '/s', '/c', cmdline];
    windowCommand = `cmd /s /c ${cmdline}`;
    terminal = 'WSL';
    spawnOptions = { ...spawnOptions, windowsHide: true, windowsVerbatimArguments: true };
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
// インタラクティブ実行（kiro-loop を介さない直接 tmux + kiro-cli）
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
// kiro-loop は実行しない: セッションが無ければ作り、kiro-cli の入力プロンプト
// （`> ` / `!>` 等 — kiro-loop の _PROMPT_RE と同じ判定）を待ってから
// set-buffer + paste-buffer + Enter（kiro-loop の _send_to_pane と同じ安全送信）で
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
      ? `{ echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗したため起動しません"; read _; exit 0; }`
      : `echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗しました（続行します）"`;
    return `echo "[agent-dashboard] セッション開始コマンド: ${e.id}"; ${run} || ${onFail}; `;
  }).join('');
}

// 送信テキストは 1 行へ畳む（kiro-loop の送信と同じ）。改行を含めて送ると kiro-cli 等が
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
  const cd = cwd ? `cd ${shellQuote(cwd)} || { __t "cd 失敗: ${cwd}"; echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 0; }; __t "cd ok: ${cwd}"; ` : '';
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
  // CLI はログインシェル経由で起動する（tmux サーバが別の文脈——kiro-loop デーモン等——で
  // 先に立っていると、サーバ環境の PATH に ~/.local/bin 等が無く CLI が即死しうる）。
  const create = `tmux new-session -s "$__ses" ${cwd ? `-c ${shellQuote(cwd)} ` : ''}${shellQuote(`exec bash -lc ${shellQuote(`exec ${chat}`)}`)}`;
  if (!sendPrompt && !chatLines) {
    return (
      `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
      `__ses=${shellQuote(ses)}; ` +
      `if ! tmux has-session -t "$__ses" 2>/dev/null; then ` +
      preLines +
      `echo "[agent-dashboard] tmux セッション $__ses を作成してエージェントCLIへ接続します（Ctrl+b d で離脱）"; ` +
      `__t "セッション作成して接続"; ${create} || echo "[agent-dashboard] tmux セッションを開始できませんでした"; ` +
      `else echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; __t "既存セッションへ attach"; tmux attach -t "$__ses"; fi; ` +
      `__t "attach 終了 status=$?"; ` +
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
    `${create.replace('new-session', 'new-session -d')} || { __t "tmux セッション作成に失敗"; echo "[agent-dashboard] tmux セッション作成に失敗しました"; read _; exit 0; }; ` +
    `__t "tmux セッション作成 ok"; ` +
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
        // 業務プロンプトも kiro-loop と同じ send-keys（テキストと Enter を分けて）で送る。
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
    message: message || '別ウィンドウ（WSL tmux / kiro-cli）で実行を開始しました',
  });
  return res.ok ? { ...res, session } : res;
}

// `send <プロンプト名>` の引数を組む。ペインが複数動いていると送信先を省略した send は
// 「複数のペインが動作中です」で失敗するため、名前からペインを引けたら -s で明示する。
function sendArgsFor(job) {
  if (Array.isArray(job.args)) return job.args;
  const name = job.id || job.name;
  if (!name) return ['send'];
  let pane;
  try {
    pane = require('../../kiro-loop/main/tmux').findPane({ repo: job.cwd || job.repo, name });
  } catch {
    pane = '';   // 解決できなければ従来どおり CLI の自動解決に任せる
  }
  return pane ? ['send', '-s', pane, name] : ['send', name];
}

function makeLoopProvider(cfg) {
  const provider = cfg.loopProvider || 'kiro-loop';
  const command = cfg.loopCommand || provider;
  return {
    provider,
    command,
    replacementHint: cfg.nextLoopProvider || 'agent-loop',
    run(job) {
      // Windows では既定で新しいウィンドウの WSL tmux 上で実行する（cowork.runWindow: false で
      // 従来の非表示 spawnSync に戻せる）。
      if (process.platform === 'win32' && cfg.runWindow !== false) {
        // job.prompt があれば kiro-loop を介さず、tmux + kiro-cli（インタラクティブ）へ
        // プロンプトを直接送る。呼び出し側（cowork.runLoop / runStateMachine）が
        // kiro-loop.yml の本文やステートマシン実行文を解決して渡してくる。
        if (job.prompt) {
          // 起動する対話 CLI は agent_cli 設定（＝CLI 定義）から解決する。
          // cfg.chatCommand は明示上書きとして残る（S9-3）。
          const { coworkChatLaunch } = require('./cowork');
          return runChatWindow({
            ...coworkChatLaunch({ cowork: cfg }, job.cwd || job.repo),
            prompt: job.prompt,
            cwd: job.cwd || job.repo,
            sessionCommands: job.sessionCommands,
          });
        }
        // 明示 args（レガシー）の項目は従来どおり <loopCommand> をウィンドウで実行する
        return runInWindow(command, sendArgsFor(job), { cwd: job.cwd || job.repo });
      }
      // kiro-loop / agent-loop に `run` サブコマンドは無い。単発実行は
      // `send <プロンプト名>` — cwd（ワークスペース）の .kiro/kiro-loop.* から
      // 定期プロンプト名を解決してセッションへ送信する（送信のみで応答は待たない）。
      return sh(command, sendArgsFor(job), { cwd: job.cwd || job.repo, timeoutMs: job.timeoutMs || 60000 });
    },
  };
}

module.exports = {
  DEFAULT_READY_PATTERN, DEFAULT_READY_TIMEOUT_SEC,
  makeLoopProvider, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, shellQuote, sh,
  decodeCliOutput, windowScript, windowStartCommand, windowLogPath, tracePreamble,
  writeWindowScript,
  runInWindow,
  chatWindowScript, chatSessionName, runChatWindow, launchWindowScript,
  sessionProcessLines, sessionChatLines,
  splitCommand, quoteToken, expandHome, findExecutable, terminalLaunchSpec,
};
