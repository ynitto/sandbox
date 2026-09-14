'use strict';

// 自動更新。外部サービスを使わず、利用者が用意した**更新元**（共有フォルダか社内の HTTP）に
// 置いた配布物を見に行く。
//
//   更新元/
//     manifest.json             … scripts/publish-update.js が書く（版・ファイル名・sha256）
//     agent-app-<版>.exe        … npm run dist:portable の成果物
//     agent-tools-<版>.tar.gz   … リポジトリの tools/ を git archive したもの（WSL 側の agent-tools）
//
// 確認は 3 つの契機（起動時・定期・手動）で同じ check() を呼ぶだけで、**取り込みは apply() を
// 利用者が押したときだけ**行う。黙って差し替えない。
//
// アプリ本体（Windows の portable 版）は、動いている exe を自分では上書きできないので、
// 新しい exe を隣に置き、終了後に入れ替えて起動し直す小さな cmd を切り離して走らせる
// （applyScript）。portable 版の元の exe は electron-builder が PORTABLE_EXECUTABLE_FILE で教える
// （process.execPath は一時展開先なので使わない）。それ以外の起動形態（開発起動・NSIS 版）では
// 本体の更新は案内だけにとどめ、agent-tools の更新だけを行う。
//
// agent-tools は CLI と同じホスト（Windows なら WSL）で tar を展開して install.sh を叩く。入れ直すのは
// agent-app が呼ぶ 3 本（agent-herd / agent-loop / agent-flow）だけで、agent-project などは触らない。
// 入れた版は $HOME/.local/share/agent-app/agent-tools.version に残し、次の確認はそれと比べる。

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { sq } = require('./host');

const MANIFEST = 'manifest.json';
const TOOLS_STAMP = '$HOME/.local/share/agent-app/agent-tools.version';
const FETCH_TIMEOUT_MS = 30000;
const TOOLS_INSTALL_TIMEOUT_MS = 15 * 60 * 1000;
const MAX_REDIRECTS = 5;

// ---- 版の比較 -------------------------------------------------------------------

// 1.2.3 / 1.2.3-beta.1 のような版を数の並びで比べる（不明な形は文字列比較）。
//   a > b なら 1、a < b なら -1、同じなら 0
function compareVersions(a, b) {
  const parse = (v) => {
    const m = String(v || '').trim().match(/^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?$/);
    return m ? { nums: m[1].split('.').map(Number), pre: m[2] || '' } : null;
  };
  const pa = parse(a); const pb = parse(b);
  if (!pa || !pb) return String(a || '').localeCompare(String(b || ''));
  const len = Math.max(pa.nums.length, pb.nums.length);
  for (let i = 0; i < len; i += 1) {
    const x = pa.nums[i] || 0; const y = pb.nums[i] || 0;
    if (x !== y) return x > y ? 1 : -1;
  }
  if (pa.pre === pb.pre) return 0;
  if (!pa.pre) return 1;        // 正式版 > 先行版
  if (!pb.pre) return -1;
  return pa.pre.localeCompare(pb.pre);
}

// ---- 更新元 ---------------------------------------------------------------------

function sourceKind(source) {
  const s = String(source || '').trim();
  if (!s) return '';
  return /^https?:\/\//i.test(s) ? 'url' : 'dir';
}

function joinSource(source, name) {
  const s = String(source || '').trim();
  if (sourceKind(s) === 'url') return `${s.replace(/\/+$/, '')}/${name}`;
  return path.join(s, name);
}

function httpGet(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    const lib = /^https:/i.test(url) ? require('https') : require('http');
    const req = lib.get(url, { timeout: FETCH_TIMEOUT_MS }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location) {
        res.resume();
        if (redirects >= MAX_REDIRECTS) { reject(new Error('リダイレクトが多すぎます')); return; }
        resolve(httpGet(new URL(res.headers.location, url).toString(), redirects + 1));
        return;
      }
      if (status !== 200) { res.resume(); reject(new Error(`更新元が ${status} を返しました: ${url}`)); return; }
      resolve(res);
    });
    req.on('timeout', () => req.destroy(new Error(`更新元の応答がありません: ${url}`)));
    req.on('error', reject);
  });
}

async function readText(source, name) {
  const target = joinSource(source, name);
  if (sourceKind(source) === 'url') {
    const res = await httpGet(target);
    const chunks = [];
    for await (const c of res) chunks.push(c);
    return Buffer.concat(chunks).toString('utf8');
  }
  return fs.promises.readFile(target, 'utf8');
}

function entry(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const version = String(raw.version || '').trim();
  const file = String(raw.file || '').trim();
  if (!version || !file || /[\\/]/.test(file)) return null;   // ファイル名だけ（更新元の外を指させない）
  return {
    version,
    file,
    sha256: String(raw.sha256 || '').trim().toLowerCase(),
    size: Number.isFinite(Number(raw.size)) ? Number(raw.size) : 0,
  };
}

function normalizeManifest(raw) {
  const m = raw && typeof raw === 'object' ? raw : {};
  return {
    app: entry(m.app),
    tools: entry(m.tools),
    notes: String(m.notes || '').trim().slice(0, 400),
    publishedAt: String(m.publishedAt || '').trim(),
  };
}

async function readManifest(source) {
  if (!sourceKind(source)) throw new Error('更新元が設定されていません（設定 > アプリ）');
  let text;
  try { text = await readText(source, MANIFEST); } catch (err) {
    throw new Error(`更新元を読めません: ${err.message}`);
  }
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error(`更新元の ${MANIFEST} が壊れています`); }
  return normalizeManifest(parsed);
}

function sha256Of(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    fs.createReadStream(file).on('data', (d) => hash.update(d)).on('end', () => resolve(hash.digest('hex'))).on('error', reject);
  });
}

// 更新元のファイルを dest へ写す（URL なら取得）。sha256 が manifest にあれば照合する。
async function fetchToFile(source, item, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const temp = `${dest}.part`;
  try {
    if (sourceKind(source) === 'url') {
      const res = await httpGet(joinSource(source, item.file));
      await new Promise((resolve, reject) => {
        const out = fs.createWriteStream(temp);
        res.pipe(out);
        res.on('error', reject);
        out.on('error', reject);
        out.on('finish', resolve);
      });
    } else {
      await fs.promises.copyFile(joinSource(source, item.file), temp);
    }
    if (item.sha256) {
      const actual = await sha256Of(temp);
      if (actual !== item.sha256) throw new Error(`${item.file} の内容が manifest と一致しません（sha256）`);
    }
    await fs.promises.rename(temp, dest);
  } finally {
    try { fs.unlinkSync(temp); } catch { /* rename 済み、または未作成 */ }
  }
  return dest;
}

// ---- 何を更新できるか ----------------------------------------------------------

//   manifest       … normalizeManifest の結果
//   appVersion     … いま動いている本体の版
//   canApplyApp    … 本体を入れ替えられる起動形態か（Windows の portable 版）
//   tools          … { version, installed }（ホストで読んだ印と、3 本のどれかが PATH にあるか）
function plan({ manifest, appVersion, canApplyApp, tools }) {
  const m = manifest || {};
  const app = { current: String(appVersion || ''), next: '', available: false, applicable: !!canApplyApp };
  if (m.app && compareVersions(m.app.version, appVersion) > 0) { app.next = m.app.version; app.available = true; }
  const t = tools || {};
  const toolsPlan = { current: String(t.version || ''), installed: !!t.installed, next: '', available: false, applicable: true };
  if (m.tools && m.tools.version !== toolsPlan.current) { toolsPlan.next = m.tools.version; toolsPlan.available = true; }
  return {
    app,
    tools: toolsPlan,
    notes: m.notes || '',
    any: (app.available && app.applicable) || toolsPlan.available,
  };
}

// ---- 本体の入れ替え（Windows portable）---------------------------------------------

// 終了を待って exe を入れ替え、起動し直す cmd。動いている exe は名前を変えられる（削除と上書きは
// できない）ので、まず .old へ退かし、新しい exe を元の名前へ移し、最後に .old を消す。
// 消せなくても（まだ掴んでいる）次回の更新で消す。
function applyScript({ target, staged, pid, log }) {
  const old = `${target}.old`;
  return [
    '@echo off',
    'setlocal',
    `set "TARGET=${target}"`,
    `set "STAGED=${staged}"`,
    `set "OLD=${old}"`,
    `set "LOG=${log}"`,
    'set N=0',
    `echo [%date% %time%] waiting for pid ${pid} >> "%LOG%"`,
    ':wait',
    `tasklist /FI "PID eq ${pid}" 2>NUL | find " ${pid} " >NUL`,
    'if not errorlevel 1 ( timeout /t 1 /nobreak >NUL & goto wait )',
    'timeout /t 1 /nobreak >NUL',
    ':retry',
    'if exist "%OLD%" del /f /q "%OLD%" >NUL 2>&1',
    'move /y "%TARGET%" "%OLD%" >> "%LOG%" 2>&1',
    'if errorlevel 1 (',
    '  set /a N+=1',
    '  if %N% lss 60 ( timeout /t 1 /nobreak >NUL & goto retry )',
    '  echo [%date% %time%] could not move the running exe >> "%LOG%"',
    '  goto fail',
    ')',
    'move /y "%STAGED%" "%TARGET%" >> "%LOG%" 2>&1',
    'if errorlevel 1 (',
    '  echo [%date% %time%] could not place the new exe, restoring >> "%LOG%"',
    '  move /y "%OLD%" "%TARGET%" >> "%LOG%" 2>&1',
    '  goto fail',
    ')',
    'echo [%date% %time%] replaced, starting >> "%LOG%"',
    'start "" "%TARGET%"',
    'del /f /q "%OLD%" >NUL 2>&1',
    'del /f /q "%~f0" >NUL 2>&1',
    'exit /b 0',
    ':fail',
    'if exist "%STAGED%" del /f /q "%STAGED%" >NUL 2>&1',
    'start "" "%TARGET%"',
    'exit /b 1',
    '',
  ].join('\r\n');
}

// ---- agent-tools（ホスト側）---------------------------------------------------------

// agent-app が呼ぶ 3 本。install.sh の --only で入れ直す対象（agent-loop は install.sh が常に一緒に入れ直す）
const TOOLS = ['agent-herd', 'agent-loop', 'agent-flow'];
const INSTALL_ONLY = 'agent-flow,agent-herd';

// ホストの印と 3 本の有無を 1 回で読む
function toolsProbeScript() {
  const probes = TOOLS.map((name) => `${name}=%s`).join('\\n');
  const args = TOOLS.map((name) => `"$(command -v ${name} || true)"`).join(' ');
  return `printf 'version=%s\\n${probes}\\n' "$(cat ${TOOLS_STAMP} 2>/dev/null | head -1 || true)" ${args}`;
}

function parseToolsProbe(output) {
  const info = { version: '', installed: false };
  for (const line of String(output || '').split('\n')) {
    const m = line.match(/^(version|agent-herd|agent-loop|agent-flow)=(.*)$/);
    if (!m) continue;
    if (m[1] === 'version') info.version = m[2].trim();
    else if (m[2].trim()) info.installed = true;
  }
  return info;
}

// tar を展開して install.sh を叩き、成功したら印を書く。archive はホスト表記（/mnt/c/… など）。
function toolsInstallScript({ archive, version }) {
  return [
    'set -e',
    `archive=${sq(archive)}`,
    'dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-tools-update.XXXXXX")"',
    'tar xzf "$archive" -C "$dir"',
    'test -f "$dir/tools/agent-tools/install.sh" || { echo "配布物に tools/agent-tools/install.sh がありません"; exit 1; }',
    `bash "$dir/tools/agent-tools/install.sh" --only ${INSTALL_ONLY} </dev/null`,
    `mkdir -p "$(dirname ${TOOLS_STAMP})"`,
    `printf '%s\\n' ${sq(version)} > ${TOOLS_STAMP}`,
    'rm -rf "$dir"',
  ].join('\n');
}

// ---- まとめ役 -------------------------------------------------------------------

// deps:
//   userData    … 置き場（取得したファイルは <userData>/updates/）
//   appVersion  … 本体の版
//   loadConfig  … 設定（update.source / wslDistro）を読む
//   shellFor    … (distro) => HostShell（agent-tools の確認と更新）
//   post        … (channel, payload) => renderer へ知らせる
//   quit        … 本体の入れ替え前に呼ぶ（app.quit）
//   platform / portableFile / pid / spawnFn / tmpdir … 試験で差し替える
class Updater {
  constructor({
    userData, appVersion, loadConfig, shellFor, post = () => {}, quit = () => {},
    platform = process.platform, portableFile = process.env.PORTABLE_EXECUTABLE_FILE || '',
    pid = process.pid, spawnFn = spawn, tmpdir = os.tmpdir(),
  }) {
    Object.assign(this, { userData, appVersion, loadConfig, shellFor, post, quit, platform, portableFile, pid, spawnFn, tmpdir });
    this.checking = false;
    this.applying = false;
    this.progress = '';
    this.lastCheckAt = 0;
    this.lastError = '';
    this.manifest = null;
    this.plan = null;
    this.timer = null;
    this.startTimer = null;
  }

  canApplyApp() {
    return this.platform === 'win32' && !!this.portableFile && fs.existsSync(this.portableFile);
  }

  config() {
    const cfg = this.loadConfig() || {};
    return { ...(cfg.update || {}), distro: this.platform === 'win32' ? String(cfg.wslDistro || '') : '' };
  }

  status() {
    const cfg = this.config();
    return {
      source: cfg.source || '',
      appVersion: this.appVersion,
      portable: this.canApplyApp(),
      checking: this.checking,
      applying: this.applying,
      progress: this.progress,
      lastCheckAt: this.lastCheckAt,
      error: this.lastError,
      plan: this.plan,
    };
  }

  notifyChanged(extra = {}) {
    this.post('update:changed', { ...this.status(), ...extra });
  }

  // 更新の有無を見る。手動（manual）なら失敗を投げ、自動なら記録だけして黙る。
  async check({ manual = false } = {}) {
    const cfg = this.config();
    if (!cfg.source) {
      if (manual) throw new Error('更新元が設定されていません（設定 > アプリ）');
      return this.plan;
    }
    if (this.checking) return this.plan;
    this.checking = true;
    this.notifyChanged();
    try {
      const manifest = await readManifest(cfg.source);
      const probe = await this.shellFor(cfg.distro).run(toolsProbeScript(), { timeoutMs: 20000 });
      const tools = probe.ok ? parseToolsProbe(probe.output) : { version: '', installed: false, error: probe.error };
      this.manifest = manifest;
      this.plan = plan({ manifest, appVersion: this.appVersion, canApplyApp: this.canApplyApp(), tools });
      this.lastError = '';
      this.lastCheckAt = Date.now();
      return this.plan;
    } catch (err) {
      this.lastError = err.message;
      this.lastCheckAt = Date.now();
      if (manual) throw err;
      return this.plan;
    } finally {
      this.checking = false;
      this.notifyChanged({ trigger: manual ? 'manual' : 'auto' });
    }
  }

  step(text) {
    this.progress = text;
    this.notifyChanged();
  }

  // 利用者が承認した分だけ取り込む。agent-tools → 本体の順（本体は入れ替えのために終了する）。
  async apply({ app: doApp = false, tools: doTools = false } = {}) {
    if (this.applying) throw new Error('更新を適用しています');
    if (!this.manifest || !this.plan) throw new Error('先に更新を確認してください');
    const cfg = this.config();
    if (!cfg.source) throw new Error('更新元が設定されていません（設定 > アプリ）');
    const dir = path.join(this.userData, 'updates');
    const result = { tools: '', app: '' };
    this.applying = true;
    try {
      if (doTools && this.plan.tools.available && this.manifest.tools) {
        const item = this.manifest.tools;
        this.step(`agent-tools ${item.version} を取得しています…`);
        const archive = await fetchToFile(cfg.source, item, path.join(dir, item.file));
        this.step('agent-tools を入れ直しています（数分かかります）…');
        const { toHostPath } = require('./host');
        const r = await this.shellFor(cfg.distro).run(toolsInstallScript({ archive: toHostPath(archive), version: item.version }), { timeoutMs: TOOLS_INSTALL_TIMEOUT_MS });
        try { fs.unlinkSync(archive); } catch { /* 残っても次で上書き */ }
        if (!r.ok) {
          const tail = String(r.output || r.error || '').split('\n').filter(Boolean).slice(-8).join('\n');
          throw new Error(`agent-tools の更新に失敗しました\n${tail}`);
        }
        this.plan = { ...this.plan, tools: { ...this.plan.tools, current: item.version, next: '', available: false, installed: true } };
        this.plan.any = this.plan.app.available && this.plan.app.applicable;
        result.tools = item.version;
      }
      if (doApp && this.plan.app.available && this.manifest.app) {
        if (!this.canApplyApp()) throw new Error('この起動形態では本体を入れ替えられません（portable 版だけ）');
        const item = this.manifest.app;
        this.step(`Agent App ${item.version} を取得しています…`);
        const staged = await fetchToFile(cfg.source, item, `${this.portableFile}.new`);
        const script = path.join(this.tmpdir, `agent-app-update-${process.pid}.cmd`);
        const log = path.join(this.tmpdir, 'agent-app-update.log');
        fs.writeFileSync(script, applyScript({ target: this.portableFile, staged, pid: this.pid, log }));
        this.step('入れ替えのために終了します…');
        const child = this.spawnFn('cmd.exe', ['/c', script], { detached: true, stdio: 'ignore', windowsHide: true });
        if (child && child.unref) child.unref();
        result.app = item.version;
        setTimeout(() => this.quit(), 300);
      }
      this.progress = '';
      return result;
    } catch (err) {
      this.progress = '';
      throw err;
    } finally {
      this.applying = false;
      this.notifyChanged();
    }
  }

  // 起動時と定期の確認。設定が変わったら呼び直す（間隔だけ読み直す）。
  schedule({ startupDelayMs = 15000, tickMs = 5 * 60 * 1000 } = {}) {
    this.unschedule();
    const cfg = this.config();
    if (cfg.onStartup !== false && cfg.source) {
      this.startTimer = setTimeout(() => { this.check().catch(() => {}); }, startupDelayMs);
      if (this.startTimer.unref) this.startTimer.unref();
    }
    this.timer = setInterval(() => {
      const c = this.config();
      const hours = Number(c.intervalHours) || 0;
      if (!c.source || hours <= 0 || this.checking || this.applying) return;
      if (Date.now() - this.lastCheckAt >= hours * 3600 * 1000) this.check().catch(() => {});
    }, tickMs);
    if (this.timer.unref) this.timer.unref();
  }

  unschedule() {
    if (this.timer) clearInterval(this.timer);
    if (this.startTimer) clearTimeout(this.startTimer);
    this.timer = null;
    this.startTimer = null;
  }
}

module.exports = {
  MANIFEST, TOOLS_STAMP, TOOLS, INSTALL_ONLY, compareVersions, sourceKind, joinSource, normalizeManifest, readManifest, fetchToFile, sha256Of,
  plan, applyScript, toolsProbeScript, parseToolsProbe, toolsInstallScript, Updater,
};
