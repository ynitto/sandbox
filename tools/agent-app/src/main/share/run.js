'use strict';

// 共有で引き受けた依頼を、この PC の CLI で 1 回だけ走らせる口。会話（session）は作らない。
// tmux が使えて対話定義のある CLI なら画面つき（依頼者がその画面を見られる）、無ければヘッドレス。
// 依頼の workspace と登録リポジトリを突き合わせる origin URL の表もここが持つ。
// 呼ぶのは share/（participant）と ipc。tmux が使えるかは起動時に ipc が setTmuxAvailable で渡す。

const fs = require('fs');
const { spawn } = require('child_process');
const store = require('../store');
const host = require('../host');
const agentCli = require('../agentCli');
const tmux = require('../tmux');
const response = require('../response');
const { stripAnsi, cleanAnswer, lineEmitter } = require('../text');
const { spawnSpec, killTree } = require('../proc');
const { userData, dirsOf, distroFor } = require('../paths');

// ---- 共有（LAN の参加者として CLI を 1 回起こす） --------------------------------------------
//
// 会話を持たない単発。セッション ID も履歴も無く、定義に no_session_args があれば付けて
// 参加者の CLI にセッションを残さない。読み取り専用で起こす。
//   { cli, prompt, model, readonly, cwd, files, timeoutMs, onLine }
//   → { done: Promise<{ text, code, stopped, error, errorClass, quotaKind, elapsedMs, usage }>, stop(reason) }
function runPrompt({ cli, prompt, model = '', readonly = true, cwd, files = [], timeoutMs = 0, onLine = () => {}, repo = '' }) {
  const cfg = store.loadConfig(userData());
  const distro = host.hostOf(repo || cwd, cfg.wslDistro).distro;
  const spec = agentCli.load(cli, repo);
  const cmd = agentCli.turnCmd(spec, { prompt, model, readonly, cliSession: '', history: [], files });
  let argv = cmd.argv;
  if (spec.noSessionArgs && spec.noSessionArgs.length) argv = agentCli.insertAfterSubcommand(argv, spec.noSessionArgs);
  const startedAt = Date.now();
  const spec2 = spawnSpec(argv[0], argv.slice(1), { cwd: host.toHostPath(cwd), env: cmd.env, distro });
  let child;
  let stopped = false;
  let stopReason = '';
  const done = new Promise((resolve) => {
    try {
      child = spawn(spec2.command, spec2.args, { windowsHide: true, ...spec2.extra });
    } catch (err) {
      resolve({ text: '', code: 1, stopped: false, error: `起動できません: ${(err && err.message) || err}`, errorClass: 'env', quotaKind: '', elapsedMs: 0, usage: null });
      return;
    }
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
    child.stdout.on('data', lineEmitter((line) => onLine('stdout', line)));
    child.stderr.on('data', lineEmitter((line) => onLine('stderr', line)));
    child.on('error', (err) => { stderr += `\n起動エラー: ${(err && err.message) || err}`; });
    child.stdin.on('error', () => { /* 先に終わった CLI へ書いた EPIPE */ });
    child.stdin.end(cmd.stdin == null ? '' : cmd.stdin);
    const timer = timeoutMs > 0 ? setTimeout(() => { stopped = true; stopReason = '時間切れ'; killTree(child); }, timeoutMs) : null;
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      let answer = '';
      if (cmd.outputFile) {
        try { answer = fs.readFileSync(cmd.outputFile, 'utf8'); } catch { /* 書かれなかった */ }
        try { fs.unlinkSync(cmd.outputFile); } catch { /* 無ければよい */ }
      } else {
        answer = stdout;
      }
      answer = response.parseTranscript(cli, cleanAnswer(answer)).text;
      const failed = stopped || code !== 0 || !answer;
      const rule = failed ? agentCli.classifyError(spec, `${stripAnsi(stderr)}\n${stdout}`) : null;
      resolve({
        text: answer, code, stopped, elapsedMs: Date.now() - startedAt, usage: null,
        error: !failed ? '' : (stopped ? stopReason : (rule ? rule.hint : (stripAnsi(stderr).trim() || stdout.trim()).split('\n').slice(-6).join('\n'))),
        errorClass: !failed ? '' : (stopped ? 'transient' : (rule && rule.cls ? rule.cls : 'cli')),
        quotaKind: rule && rule.quotaKind ? rule.quotaKind : '',
      });
    });
  });
  return { done, stop(reason) { stopped = true; stopReason = reason || '止めた'; if (child) killTree(child); } };
}

// ---- 共有（引き受けた依頼を tmux の画面で走らせる） ------------------------------------------
//
// 依頼者は自分の端末ミラーで「他人の PC で何が起きているか」を見ながら待つ。そのため引き受けた
// 側は会話と同じ tmux セッション（agent-app-share-<依頼 id>）で CLI を起こし、画面が変わるたびに
// onScreen で渡す（participant が心拍に載せて依頼者へ送る）。tmux が無い PC ではヘッドレスに倒す。
let shareRunSeq = 0;              // 引き受けた依頼の tmux 名を分ける連番（`tmux.sharePaneId`）
let shareTmuxOk = false;

function shareOutcome(message, { cli, spec, conv, startedAt, stopped = false }) {
  const structured = response.parseTranscript(cli, message.text || '');
  const halted = stopped || !!message.stopped;
  const failed = halted || !!message.error || !structured.text;
  const rule = failed ? agentCli.classifyError(spec, conv.lastText || '') : null;
  return {
    text: structured.text, code: failed ? 1 : 0, stopped: halted, elapsedMs: Date.now() - startedAt, usage: null,
    error: failed ? (message.error || '画面から答えを読み取れませんでした') : '',
    errorClass: !failed ? '' : (halted ? 'transient' : (rule && rule.cls ? rule.cls : 'cli')),
    quotaKind: rule && rule.quotaKind ? rule.quotaKind : '',
  };
}

function runPromptTmux(opts) {
  const { cli, prompt, model = '', cwd, timeoutMs = 0, onScreen = () => {}, shareId } = opts;
  const cfg = store.loadConfig(userData());
  const spec = agentCli.load(cli, '');
  const { shell } = host.hostOf('', cfg.wslDistro);
  const cmd = agentCli.interactiveCmd(spec, { model, readonly: true, autoApprove: false, cliSession: '', history: [] });
  shareRunSeq = (shareRunSeq + 1) % 1000;
  const id = tmux.sharePaneId(shareId, shareRunSeq);
  const startedAt = Date.now();
  let stopped = false;
  const conv = new tmux.Conversation({
    id, shell, cwd: host.toHostPath(cwd), argv: cmd.argv, patterns: tmux.compilePatterns(spec.interactive),
    launch: { cli, model, readonly: true, autoApprove: false },
    emit: (channel, payload) => { if (channel === 'term:screen') onScreen(payload.text); },
  });
  conv.watchers = 1;                       // 依頼者が見ているので、画面は常に取る
  let timer = null;
  const cleanup = async () => {
    if (timer) clearTimeout(timer);
    await conv.kill().catch(() => {});
  };
  const done = new Promise((resolve) => {
    (async () => {
      try {
        await conv.open({ reuse: false });
        await conv.waitReady();
        if (timeoutMs > 0) timer = setTimeout(() => { stopped = true; conv.stop().catch(() => {}); }, timeoutMs);
        await conv.send(prompt, (message) => resolve(shareOutcome(message, { cli, spec, conv, startedAt, stopped })));
      } catch (err) {
        resolve({ text: '', code: 1, stopped: false, error: `起動できません: ${(err && err.message) || err}`, errorClass: 'env', quotaKind: '', elapsedMs: Date.now() - startedAt, usage: null });
      }
    })();
  }).then(async (outcome) => { await cleanup(); return outcome; });
  return {
    done,
    // 引き受けた人だけが打てる（自分の PC の自分の CLI）。依頼者には送れない
    keys(data) { conv.keys(data).catch(() => {}); },
    // 止めるときは生成を止めてから tmux ごと終わらせる（kill が待っているターンも閉じる）
    stop() {
      stopped = true;
      conv.stop().catch(() => {}).then(() => cleanup()).catch(() => {});
    },
  };
}

// 共有で 1 件を走らせる。tmux が使えて対話定義のある CLI なら画面つき、無ければヘッドレス。
function runSharedPrompt(opts) {
  const spec = opts.cli ? agentCli.load(opts.cli, '') : null;
  const cfg = store.loadConfig(userData());
  if (shareTmuxOk && opts.shareId && spec && spec.interactive && cfg.transport !== 'headless') {
    try { return runPromptTmux(opts); } catch { /* 定義や tmux の都合で作れなければヘッドレス */ }
  }
  return runPrompt(opts);
}

// 登録リポジトリの origin URL（共有の依頼の workspace と突き合わせる）。60 秒ごとに引き直す。
const repoUrls = new Map();
function normalizeRepoUrl(url) {
  return String(url || '').trim().toLowerCase()
    .replace(/\/+$/, '')            // 末尾の /
    .replace(/\.git$/, '')          // .git
    .replace(/^[a-z+]+:\/\//, '')   // scheme://
    .replace(/^[^@/:]+@/, '')       // user@
    .replace(/^([^/:]+):/, '$1/');  // host:path → host/path
}
async function refreshRepoUrls() {
  const cfg = store.loadConfig(userData());
  const next = new Map();
  for (const repo of cfg.repos) {
    try {
      const dirs = dirsOf(repo, '');
      const r = await host.shellFor(distroFor(repo)).exec(['git', '-C', dirs.hostDir, 'remote', 'get-url', 'origin'], { timeoutMs: 5000 });
      if (r.ok && r.output.trim()) next.set(normalizeRepoUrl(r.output), repo);
    } catch { /* origin が無いリポジトリは突き合わせの対象外 */ }
  }
  repoUrls.clear();
  for (const [k, v] of next) repoUrls.set(k, v);
  return repoUrls;
}
function repoFor(url) { return repoUrls.get(normalizeRepoUrl(url)) || ''; }

// tmux があるか（起動時にホストを調べた結果）。無い PC では常にヘッドレスで走らせる。
function setTmuxAvailable(ok) { shareTmuxOk = !!ok; }

module.exports = { runPrompt, runPromptTmux, runSharedPrompt, shareOutcome, normalizeRepoUrl, refreshRepoUrls, repoFor, setTmuxAvailable };
