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
function writeWindowScript(script) {
  const fs = require('fs');
  const os = require('os');
  const path = require('path');
  const dir = path.join(os.tmpdir(), 'agent-dashboard');
  fs.mkdirSync(dir, { recursive: true });
  // 古い実行スクリプトの掃除（1 日以上前のもの。失敗しても実行は続ける）
  try {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    for (const f of fs.readdirSync(dir)) {
      if (!/^cowork-run-.*\.sh$/.test(f)) continue;
      const p = path.join(dir, f);
      try { if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p); } catch { /* 掃除失敗は無視 */ }
    }
  } catch { /* 掃除失敗は無視 */ }
  const file = path.join(dir, `cowork-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.sh`);
  fs.writeFileSync(file, `${script.replace(/\r\n/g, '\n')}\n`, 'utf8');
  return file;
}

// `cmd /s /c start "<title>" wsl.exe …` のコマンドライン。windowsVerbatimArguments で
// そのまま渡すため自前で組み立てる（Node の既定の引用は cmd.exe の規則と一致しない）。
function windowStartCommand(distro, wslScriptPath, title = '定常業務 (agent-dashboard)') {
  const d = distro ? `-d "${distro}" ` : '';
  return `start "${title}" wsl.exe ${d}-e sh -lc ". '${wslScriptPath}'"`;
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
  const distro = wslDistro(options.cwd);
  const script = windowScript(command, (args || []).map(String), cwd);
  let scriptFile;
  try {
    scriptFile = writeWindowScript(script);
  } catch (e) {
    return { ok: false, status: -1, stdout: '', stderr: '', error: `実行スクリプトを書けません: ${e.message}` };
  }
  // C:\Users\...\Temp\... → /mnt/c/users/.../temp/...（テスト等で変換できなければそのまま）
  const wslScriptPath = winDriveToWsl(scriptFile) || scriptFile.replace(/\\/g, '/');
  const cmdline = windowStartCommand(distro, wslScriptPath);
  try {
    const child = spawn('cmd.exe', ['/d', '/s', '/c', cmdline], {
      stdio: 'ignore',
      windowsHide: true,              // 隠すのは cmd 自身。start が開く新ウィンドウは表示される
      windowsVerbatimArguments: true, // cmdline を Node に再引用させずそのまま渡す
      detached: true,
    });
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
    message: '別ウィンドウ（WSL tmux）で実行を開始しました',
    windowCommand: `cmd /s /c ${cmdline}`,
    scriptFile,
  };
}

function makeLoopProvider(cfg) {
  const provider = cfg.loopProvider || 'kiro-loop';
  const command = cfg.loopCommand || provider;
  return {
    provider,
    command,
    replacementHint: cfg.nextLoopProvider || 'agent-loop',
    run(job) {
      // kiro-loop / agent-loop に `run` サブコマンドは無い。単発実行は
      // `send <プロンプト名>` — cwd（ワークスペース）の .kiro/kiro-loop.* から
      // 定期プロンプト名を解決してセッションへ送信する（送信のみで応答は待たない）。
      const args = Array.isArray(job.args) ? job.args : ['send', job.id || job.name].filter(Boolean);
      // Windows では既定で新しいウィンドウの WSL tmux 上で実行する（cowork.runWindow: false で
      // 従来の非表示 spawnSync に戻せる）。
      if (process.platform === 'win32' && cfg.runWindow !== false) {
        return runInWindow(command, args, { cwd: job.cwd || job.repo });
      }
      return sh(command, args, { cwd: job.cwd || job.repo, timeoutMs: job.timeoutMs || 60000 });
    },
  };
}

module.exports = {
  makeLoopProvider, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, shellQuote, sh,
  decodeCliOutput, windowScript, windowStartCommand, writeWindowScript, runInWindow,
  splitCommand, quoteToken, expandHome,
};
