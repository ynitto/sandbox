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
  const cd = cwd ? `cd ${shellQuote(cwd)} || { echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 1; }; ` : '';
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
    `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; read _`
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
  // 古い実行スクリプトの掃除（1 日以上前のもの。失敗しても実行は続ける）
  try {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    for (const f of fs.readdirSync(dir)) {
      if (!/^cowork-run-.*\.(?:sh|command)$/.test(f)) continue;
      const p = path.join(dir, f);
      try { if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p); } catch { /* 掃除失敗は無視 */ }
    }
  } catch { /* 掃除失敗は無視 */ }
  const ext = platform === 'darwin' ? 'command' : 'sh';
  const file = path.join(dir, `cowork-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`);
  fs.writeFileSync(file, `#!/bin/sh\n${script.replace(/\r\n/g, '\n')}\n`, 'utf8');
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

// `cmd /s /c start "<title>" wsl.exe …` のコマンドライン。windowsVerbatimArguments で
// そのまま渡すため自前で組み立てる（Node の既定の引用は cmd.exe の規則と一致しない）。
function windowStartCommand(distro, wslScriptPath, title = '定常業務 (agent-dashboard)') {
  const d = distro ? `-d "${distro}" ` : '';
  return `start "${title}" wsl.exe ${d}-e sh -lc ". '${wslScriptPath}'"`;
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
  let terminal = '';
  let spawnOptions = { stdio: 'ignore', detached: true };
  if (platform === 'win32') {
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
function chatSessionName(linuxCwd, cli = '') {
  const key = String(cli || '');
  const digest = require('crypto').createHash('sha1')
    .update(key ? `${key}\0${String(linuxCwd || '')}` : String(linuxCwd || ''))
    .digest('hex').slice(0, 8);
  if (!key) return `kiro-dash-${digest}`;
  const safeCli = key.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').slice(0, 20) || 'agent';
  return `agent-chat-${safeCli}-${digest}`;
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
    const run = `{ if command -v timeout >/dev/null 2>&1; then timeout ${seconds} sh -c ${shellQuote(body)}; else sh -c ${shellQuote(body)}; fi; }`;
    const onFail = e.on_error === 'fail'
      ? `{ echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗したため起動しません"; read _; exit 1; }`
      : `echo "[agent-dashboard] セッション開始コマンド ${e.id} が失敗しました（続行します）"`;
    return `echo "[agent-dashboard] セッション開始コマンド: ${e.id}"; ${run} || ${onFail}; `;
  }).join('');
}

// chat モードは、kiro-cli が入力を受け付けてから業務プロンプトより先に送る。
function sessionChatLines(entries) {
  return (entries || []).filter((e) => e.mode === 'chat' && !e.skip).map((e, i) => (
    `tmux set-buffer -b agentdash-s${i} -- ${shellQuote(e.run)}; ` +
    `tmux paste-buffer -t "$__ses" -b agentdash-s${i}; ` +
    `tmux delete-buffer -b agentdash-s${i} 2>/dev/null; ` +
    `tmux send-keys -t "$__ses" Enter; sleep 1; `
  )).join('');
}

function chatWindowScript({ chatCommand, cwd, session, prompt, sessionCommands }) {
  const chatTokens = Array.isArray(chatCommand)
    ? chatCommand.map(String)
    : splitCommand(chatCommand || 'kiro-cli chat --trust-all-tools');
  const chat = chatTokens.map(quoteToken).join(' ');
  const ses = String(session || 'kiro-dash');
  const sendPrompt = prompt !== null && prompt !== undefined && String(prompt) !== '';
  const cd = cwd ? `cd ${shellQuote(cwd)} || { echo "[agent-dashboard] cd 失敗: ${cwd}"; read _; exit 1; }; ` : '';
  const preLines = sessionProcessLines(sessionCommands);
  const chatLines = sessionChatLines(sessionCommands);
  const create = `tmux new-session -s "$__ses" ${cwd ? `-c ${shellQuote(cwd)} ` : ''}${shellQuote(`exec ${chat}`)}`;
  if (!sendPrompt && !chatLines) {
    return (
      `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${cd}` +
      `__ses=${shellQuote(ses)}; ` +
      `if ! tmux has-session -t "$__ses" 2>/dev/null; then ` +
      preLines +
      `echo "[agent-dashboard] tmux セッション $__ses を作成してエージェントCLIへ接続します（Ctrl+b d で離脱）"; ` +
      `${create} || echo "[agent-dashboard] tmux セッションを開始できませんでした"; ` +
      `else echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; tmux attach -t "$__ses"; fi; ` +
      `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; read _`
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
    `${create.replace('new-session', 'new-session -d')} || { echo "[agent-dashboard] tmux セッション作成に失敗しました"; read _; exit 1; }; ` +
    `fi; ` +
    // 入力プロンプトの検出待ちは「何かを送るとき」だけ行う。業務プロンプトが無くても、
    // 新規セッションに chat モードの開始コマンドがあるなら送る必要があるので待つ
    // （接続するだけの起動で何も送らないときは、従来どおり待たずにアタッチする）。
    (sendPrompt || chatLines
      ? `echo "[agent-dashboard] エージェントCLIの起動を待っています…"; ` +
        `__i=0; __ok=0; ` +
        `while [ $__i -lt 60 ]; do ` +
        `if tmux capture-pane -p -t "$__ses" 2>/dev/null | grep -qE "^[[:space:]]*[>?❯›][[:space:]]*$|!>"; then __ok=1; break; fi; ` +
        `sleep 1; __i=$((__i+1)); ` +
        `done; ` +
        `if [ $__ok -eq 1 ]; then ` +
        (chatLines ? `if [ $__new -eq 1 ]; then ${chatLines}fi; ` : '') +
        (sendPrompt
          // 複数行プロンプトを崩さず送るため send-keys ではなく paste-buffer を使う
          ? `tmux set-buffer -b agentdash -- ${shellQuote(prompt)}; ` +
            `tmux paste-buffer -t "$__ses" -b agentdash; ` +
            `tmux delete-buffer -b agentdash 2>/dev/null; ` +
            `tmux send-keys -t "$__ses" Enter; ` +
            `echo "[agent-dashboard] プロンプトを送信しました。アタッチします（Ctrl+b d で離脱）"; `
          : `echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; `) +
        `else ` +
        `echo "[agent-dashboard] エージェントCLIの入力プロンプトを検出できませんでした。アタッチして状態を確認してください"; ` +
        `fi; ` +
        `sleep 1; tmux attach -t "$__ses"; `
      : `echo "[agent-dashboard] CLIチャットへ接続します（Ctrl+b d で離脱）"; sleep 1; tmux attach -t "$__ses"; `) +
    `echo; echo "[agent-dashboard] Enter でこのウィンドウを閉じます"; read _`
  );
}

// プロンプトを新しいウィンドウの tmux + kiro-cli セッションへ直接送る実行経路。
function runChatWindow({ chatCommand, prompt, cwd, sessionCommands, sessionKey, title, message }) {
  const linuxCwd = toWslCwd(cwd);
  const session = chatSessionName(linuxCwd || cwd, sessionKey);
  const script = chatWindowScript({
    chatCommand, cwd: linuxCwd, session,
    prompt: prompt === null || prompt === undefined ? null : String(prompt), sessionCommands,
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
  let pane = '';
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
          return runChatWindow({
            chatCommand: cfg.chatCommand || 'kiro-cli chat --trust-all-tools',
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
  makeLoopProvider, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, shellQuote, sh,
  decodeCliOutput, windowScript, windowStartCommand, writeWindowScript, runInWindow,
  chatWindowScript, chatSessionName, runChatWindow, launchWindowScript,
  sessionProcessLines, sessionChatLines,
  splitCommand, quoteToken, expandHome, findExecutable, terminalLaunchSpec,
};
