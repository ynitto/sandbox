'use strict';

// ブラウザの見本のために、**この端末**で Edge（無ければ Chrome）をリモートデバッグ付きで起こす。
//
// Windows では AI は WSL の tmux にいて、Windows 側の playwright-cli を WSL から起こすことは
// できない。代わりに、このアプリが Windows 側で Edge を `--remote-debugging-port` 付きで起こし、
// 起動できたことを固定文（renderer/teachingProtocol.js）で AI に伝える。AI は WSL 側の
// playwright-cli でその CDP エンドポイントに接続して記録を取る。
//
// ここは Electron に触れない（起動・確認の関数は引数で受ける）ので、Node からそのまま試せる。

const fs = require('fs');
const http = require('http');
const path = require('path');

const PORT = 9222;
const PROFILE_DIR = 'recording-browser-profile';

// 起動の候補。Edge を先に、無ければ Chrome。Windows は既定の置き場、それ以外は PATH で探す。
function candidates(platform = process.platform, env = process.env) {
  if (platform === 'win32') {
    const roots = [env['ProgramFiles(x86)'], env.ProgramFiles, env.LOCALAPPDATA].filter(Boolean);
    const rel = [['Microsoft', 'Edge', 'Application', 'msedge.exe'], ['Google', 'Chrome', 'Application', 'chrome.exe']];
    return rel.flatMap((parts) => roots.map((root) => path.win32.join(root, ...parts)));
  }
  if (platform === 'darwin') {
    return ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];
  }
  return ['microsoft-edge', 'microsoft-edge-stable', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'];
}

// `resolvePath(name)` は PATH から実行ファイルを探す関数（agentCli.resolvePath と同じ契約）。
function findBrowser({ platform = process.platform, env = process.env, exists = fs.existsSync, resolvePath = null } = {}) {
  for (const candidate of candidates(platform, env)) {
    if (/[\\/]/.test(candidate)) { if (exists(candidate)) return candidate; continue; }
    const found = resolvePath ? resolvePath(candidate) : '';
    if (found) return found;
  }
  return '';
}

function browserLabel(file) {
  return /msedge|Microsoft Edge|microsoft-edge/i.test(String(file || '')) ? 'Edge' : 'Chrome';
}

function endpointFor(port = PORT) {
  return `http://localhost:${port}`;
}

// DevTools の /json/version に答えがあれば、そのポートでブラウザがリモートデバッグを受け付けている。
function probeDevTools(port = PORT, { timeoutMs = 1500 } = {}) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/json/version', timeout: timeoutMs }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        let browser = '';
        try { browser = String(JSON.parse(body).Browser || ''); } catch { /* 本文が読めなくても応答があれば十分 */ }
        resolve({ ok: res.statusCode === 200, browser });
      });
    });
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, browser: '' }); });
    req.on('error', () => resolve({ ok: false, browser: '' }));
  });
}

function launchArgs({ port = PORT, profileDir, url = '' } = {}) {
  return [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    url || 'about:blank',
  ];
}

// 起こして、リモートデバッグに応答するまで待つ。
//   profileDir … 記録専用のプロファイル。近年の Edge / Chrome は既定のプロファイルではリモート
//                デバッグを受け付けないので、別のプロファイルを必ず渡す（ログインは 1 回目に済ませれば残る）。
//   spawn      … child_process.spawn 互換。detached で起こし、待たずに切り離す。
//   probe      … probeDevTools 互換。
// 既にそのポートで応答があるときも起動はする（同じプロファイルなら既存の窓に新しいタブが開く）。
async function launchRecordingBrowser({
  url = '', port = PORT, profileDir = '', platform = process.platform, env = process.env,
  exists = fs.existsSync, resolvePath = null, spawn = require('child_process').spawn,
  probe = probeDevTools, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), timeoutMs = 20000, mkdir = fs.mkdirSync,
} = {}) {
  const file = findBrowser({ platform, env, exists, resolvePath });
  if (!file) throw new Error('記録に使うブラウザ（Edge か Chrome）が見つかりません。Microsoft Edge を入れてください');
  if (!profileDir) throw new Error('記録用のプロファイルの置き場がありません');
  const target = String(url || '').trim().slice(0, 500);
  if (target && !/^https?:\/\//i.test(target) && !/^about:/i.test(target)) throw new Error('開始 URL は http:// か https:// で始めてください');
  const before = await probe(port);
  try { mkdir(profileDir, { recursive: true }); } catch { /* 作れなければブラウザが自分で作る */ }
  let child;
  try {
    child = spawn(file, launchArgs({ port, profileDir, url: target }), { detached: true, stdio: 'ignore', windowsHide: false });
    if (child && typeof child.unref === 'function') child.unref();
    if (child && typeof child.on === 'function') child.on('error', () => { /* 起動失敗は下の待ちで分かる */ });
  } catch (err) {
    throw new Error(`ブラウザを起動できませんでした: ${(err && err.message) || err}`, { cause: err });
  }
  const deadline = Date.now() + timeoutMs;
  let alive = before.ok ? before : null;
  while (!alive) {
    if (Date.now() >= deadline) {
      throw new Error(`ブラウザがリモートデバッグ（ポート ${port}）に応答しません。同じポートを別のブラウザが使っていないか確かめてください`);
    }
    await sleep(500);
    const now = await probe(port);
    if (now.ok) alive = now;
  }
  return { ok: true, browser: browserLabel(file), file, version: alive.browser, port, endpoint: endpointFor(port), url: target, reused: before.ok };
}

module.exports = { PORT, PROFILE_DIR, candidates, findBrowser, browserLabel, endpointFor, probeDevTools, launchArgs, launchRecordingBrowser };
