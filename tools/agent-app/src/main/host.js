'use strict';

// tmux と git を動かす「ホスト」への窓口。
//
//   Linux / macOS … このプロセスと同じ OS で bash -l を 1 本常駐させ、そこへコマンドを流す
//   Windows       … CLI は WSL に入っている前提。wsl.exe [-d <distro>] -e bash -l を常駐させる
//
// 毎回 spawn しない理由: 端末ミラーは 0.3〜0.5 秒ごとに capture-pane を撃つ。Windows で
// その都度 wsl.exe を起こすと 1 回あたり数十〜数百 ms かかり、画面が追いつかない。
// ログインシェル（-l）なのは、nvm / pipx など利用者の PATH で CLI を引くため。
//
// パス表記の変換（Windows ↔ WSL）は agent-dashboard の base/main/wsl.js と同じ規則。
// 登録したリポジトリは Windows 表記（C:\… / \\wsl$\Ubuntu\…）でも、tmux の cwd と
// git の -C には必ず WSL 表記（/mnt/c/… / /home/…）へ直して渡す。

const { spawn } = require('child_process');
const crypto = require('crypto');

// ---- パス変換 -----------------------------------------------------------------

function isWslUnc(p) {
  return /^\\\\wsl(?:\$|\.localhost)\\/i.test(String(p || '').replace(/\//g, '\\'));
}

// \\wsl$\Ubuntu\home\me → /home/me（UNC でなければ入力のまま）
function wslPath(p) {
  const s = String(p || '');
  const unc = s.replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (unc) return (unc[1] || '').replace(/\\/g, '/').replace(/\/+$/, '') || '/';
  return s;
}

// \\wsl$\Ubuntu\… → Ubuntu（UNC でなければ ''＝既定のディストロ）
function wslDistro(p) {
  const unc = String(p || '').replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  return unc ? unc[1] : '';
}

// C:\Users\me\repo → /mnt/c/Users/me/repo（ドライブパスでなければ ''）
function winDriveToWsl(p) {
  const m = String(p || '').replace(/\//g, '\\').match(/^([A-Za-z]):(\\.*)?$/);
  if (!m) return '';
  const rest = (m[2] || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return `/mnt/${m[1].toLowerCase()}${rest}`;
}

// ホスト（tmux / git が走る側）から見たパス。Windows 以外は入力のまま。
function toHostPath(p) {
  if (process.platform !== 'win32') return String(p || '');
  if (isWslUnc(p)) return wslPath(p);
  return winDriveToWsl(p) || String(p || '');
}

// ホスト側の相対パス連結（区切りは常に /）
function joinHost(base, rel) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = String(rel || '').replace(/\\/g, '/').replace(/^\/+/, '');
  return r ? `${b}/${r}` : b;
}

function sq(s) {
  return `'${String(s).replace(/'/g, `'"'"'`)}'`;
}

function quoteArgv(argv) {
  return argv.map(sq).join(' ');
}

// ---- 常駐シェル ---------------------------------------------------------------

// 1 コマンド = 開始マーカー + 本体 + 終了マーカー（終了コード付き）。stdout と stderr は
// 本体の中で 2>&1 に束ねる（順序が要るのは表示だけで、判定は終了コードで行う）。
class HostShell {
  constructor({ distro = '', platform = process.platform, spawnFn = spawn } = {}) {
    this.distro = String(distro || '');
    this.platform = platform;
    this.spawnFn = spawnFn;
    this.child = null;
    this.queue = [];
    this.current = null;
    this.buf = '';
    this.spawnError = '';
  }

  argv() {
    const env = 'export LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=xterm-256color; ';
    if (this.platform === 'win32') {
      return ['wsl.exe', [...(this.distro ? ['-d', this.distro] : []), '-e', 'bash', '-l', '-c', `${env}exec bash`]];
    }
    return ['bash', ['-l', '-c', `${env}exec bash`]];
  }

  ensure() {
    if (this.child && this.child.exitCode == null && !this.child.killed) return this.child;
    const [cmd, args] = this.argv();
    const child = this.spawnFn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    this.child = child;
    this.buf = '';
    this.spawnError = '';
    child.stdout.on('data', (d) => this.onData(d));
    child.stderr.on('data', () => { /* プロファイルの雑音。本体の stderr は 2>&1 で stdout 側に来る */ });
    child.on('error', (err) => { this.spawnError = (err && err.message) || String(err); this.fail(`ホストのシェルを起動できません: ${this.spawnError}`); });
    child.on('close', () => { if (this.child === child) { this.child = null; this.fail(this.spawnError ? `ホストのシェルを起動できません: ${this.spawnError}` : 'ホストのシェルが終了しました'); } });
    child.stdin.on('error', () => { /* close 側で拾う */ });
    return child;
  }

  fail(message) {
    const cur = this.current;
    this.current = null;
    if (cur) { clearTimeout(cur.timer); cur.resolve({ ok: false, status: -1, output: '', error: message }); }
    const rest = this.queue.splice(0);
    for (const q of rest) q.resolve({ ok: false, status: -1, output: '', error: message });
  }

  onData(chunk) {
    this.buf += chunk.toString('utf8');
    const cur = this.current;
    if (!cur) { if (this.buf.length > 1 << 20) this.buf = this.buf.slice(-65536); return; }
    const start = this.buf.indexOf(cur.startMark);
    if (start < 0) return;
    const endAt = this.buf.indexOf(cur.endMark, start + cur.startMark.length);
    if (endAt < 0) return;
    const body = this.buf.slice(start + cur.startMark.length, endAt);
    const tail = this.buf.slice(endAt + cur.endMark.length);
    const m = tail.match(/^(\d+)\n/);
    if (!m) return;                                 // 終了コードの行がまだ届いていない
    this.buf = tail.slice(m[0].length);
    this.current = null;
    clearTimeout(cur.timer);
    const status = Number(m[1]);
    cur.resolve({ ok: status === 0, status, output: body.replace(/\n$/, ''), error: status === 0 ? '' : body.trim() });
    this.next();
  }

  next() {
    if (this.current || !this.queue.length) return;
    const cur = this.queue.shift();
    this.current = cur;
    let child;
    try { child = this.ensure(); } catch (err) { this.fail(`ホストのシェルを起動できません: ${err.message}`); return; }
    // 本体はサブシェルで走らせる。`{ }` だと本体の exit で常駐シェルごと終わる。
    const script = `printf '%s' ${sq(cur.startMark)}; ( ${cur.script}\n) 2>&1; __rc=$?; printf '%s%s\\n' ${sq(cur.endMark)} "$__rc"\n`;
    cur.timer = setTimeout(() => {
      // 応答が無い＝シェルごと詰まっている。落として次から起こし直す。
      try { child.kill(); } catch { /* 既に終了 */ }
      this.child = null;
      this.fail(`ホストのコマンドがタイムアウトしました（${Math.round(cur.timeoutMs / 1000)} 秒）`);
    }, cur.timeoutMs);
    try { child.stdin.write(script); } catch (err) { this.fail(`ホストのシェルへ書けません: ${err.message}`); }
  }

  // シェルスクリプト 1 つを実行して { ok, status, output, error } を返す。
  run(script, { timeoutMs = 15000 } = {}) {
    return new Promise((resolve) => {
      const tok = crypto.randomBytes(6).toString('hex');
      this.queue.push({ script: String(script), startMark: `\u001e${tok}>`, endMark: `\u001e${tok}<`, timeoutMs, resolve });
      this.next();
    });
  }

  // argv をそのまま 1 コマンドとして実行する（引用はこちらで行う）。
  exec(argv, opts) {
    return this.run(quoteArgv(argv), opts);
  }

  close() {
    const c = this.child;
    this.child = null;
    if (c) { try { c.stdin.end(); } catch { /* 済 */ } try { c.kill(); } catch { /* 済 */ } }
    this.fail('ホストのシェルを閉じました');
  }
}

// ディストロごとに 1 本。Linux / macOS では distro は常に ''。
const shells = new Map();

function shellFor(distro = '') {
  const key = process.platform === 'win32' ? String(distro || '') : '';
  if (!shells.has(key)) shells.set(key, new HostShell({ distro: key }));
  return shells.get(key);
}

function closeAll() {
  for (const s of shells.values()) s.close();
  shells.clear();
}

// リポジトリのパスから、それを扱うホスト（ディストロ）と WSL 表記のパスを決める。
//   defaultDistro … UNC でも ドライブでもない（=既定のディストロ）ときに使う設定値
function hostOf(repo, defaultDistro = '') {
  const distro = process.platform === 'win32' ? (wslDistro(repo) || String(defaultDistro || '')) : '';
  return { distro, cwd: toHostPath(repo), shell: shellFor(distro) };
}

// ホストに tmux / git があるか（結果はディストロごとに少しの間だけ覚える）
const probeCache = new Map();
async function probe(distro = '', { force = false } = {}) {
  const key = String(distro || '');
  const hit = probeCache.get(key);
  if (hit && !force && Date.now() - hit.at < 60000) return hit.info;
  const shell = shellFor(distro);
  const r = await shell.run('printf "tmux=%s\\ngit=%s\\nhome=%s\\n" "$(command -v tmux || true)" "$(command -v git || true)" "$HOME"; tmux -V 2>/dev/null || true', { timeoutMs: 20000 });
  const info = { ok: r.ok, tmux: '', git: '', home: '', tmuxVersion: '', error: r.ok ? '' : r.error };
  for (const line of r.output.split('\n')) {
    const m = line.match(/^(tmux|git|home)=(.*)$/);
    if (m) info[m[1]] = m[2].trim();
    else if (/^tmux \d/.test(line)) info.tmuxVersion = line.trim();
  }
  probeCache.set(key, { at: Date.now(), info });
  return info;
}

module.exports = {
  isWslUnc, wslPath, wslDistro, winDriveToWsl, toHostPath, joinHost, sq, quoteArgv,
  HostShell, shellFor, closeAll, hostOf, probe,
};
