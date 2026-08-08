'use strict';

// agent-audit CLI をヘッドレスで 1 回呼ぶ。Windows では WSL（wsl.exe）経由、
// Linux ではローカルの bash で実行する（kiro-loop の exec と同じ流儀）。
//
// ダッシュボードから呼ぶのは LLM を使わない決定的なサブコマンドだけ
// （collect / usage / stats / doctor）。extract / distill などの LLM 段は呼ばない —
// LLM の消費リズムは agent-audit 側の間隔・蓄積ゲート設定が正で、GUI から
// 不用意に駆動しない。usage / stats は --json の出力をそのまま契約として読む
// （集計ロジックをこちらへ複製しない）。
const { spawn } = require('child_process');

const exec = require('../../kiro-loop/main/exec');

const PERIODS = new Set(['day', 'month', 'total']);
const GROUP_KEYS = new Set(['workload', 'tool', 'agent_cli', 'model', 'ref', 'node']);

// collect は audit ディレクトリの state.json を最後の書き手勝ちで上書きするため
// 多重実行に弱い（agent-audit 側にロックは無い）。このプロセス発の collect を
// ここで直列化し、実行中の再要求はビジーとして返す。
let collectInFlight = null;
let lastCollect = null;

function auditSettings(cfg) {
  const a = (cfg && cfg.agentAudit) || {};
  return {
    command: String(a.command || '').trim(),
    distro: String(a.distro || '').trim(),
    configPath: String(a.configPath || '').trim(),
    auditDir: String(a.auditDir || '').trim(),
    collectIntervalMin: Number(a.collectIntervalMin || 0) || 0,
  };
}

// グローバル引数（--config / --audit-dir）はサブコマンドより前に置く
// （agent-audit の argparse はサブコマンド後のグローバル引数を受け付けない）。
function buildScript(settings, subArgs) {
  const args = [];
  if (settings.configPath) args.push('--config', settings.configPath);
  if (settings.auditDir) args.push('--audit-dir', settings.auditDir);
  args.push(...subArgs);
  const quoted = args.map((value) => exec.shellQuote(value)).join(' ');
  if (settings.command) return `${settings.command} ${quoted}`;
  return 'bin=$(command -v agent-audit) || { '
    + 'echo "agent-audit が PATH に見つかりません。監査タブの設定でコマンドを指定してください" >&2; exit 127; }; '
    + `"$bin" ${quoted}`;
}

// exec.shInWsl と同じ argv 構成の非同期版。collect は数十秒かかりうるので、
// main プロセスを spawnSync で塞がない。
function defaultRunShell(script, timeoutMs, distro) {
  const wrapped = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${script}`;
  const [cmd, args] = process.platform === 'win32'
    ? ['wsl.exe', distro ? ['-d', distro, '-e', 'bash', '-lc', wrapped] : ['-e', 'bash', '-lc', wrapped]]
    : ['bash', ['-lc', wrapped]];
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(cmd, args, { windowsHide: true });
    } catch (error) {
      resolve({ ok: false, status: -1, stdout: '', stderr: '', error: error.message });
      return;
    }
    const out = [];
    const errOut = [];
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs);
    let spawnError = '';
    child.on('error', (error) => { spawnError = error.message; });
    if (child.stdout) child.stdout.on('data', (chunk) => out.push(chunk));
    if (child.stderr) child.stderr.on('data', (chunk) => errOut.push(chunk));
    child.on('close', (status) => {
      clearTimeout(timer);
      resolve({
        ok: status === 0,
        status,
        stdout: exec.decodeCliOutput(Buffer.concat(out)).trimEnd(),
        stderr: exec.decodeCliOutput(Buffer.concat(errOut)).trimEnd(),
        error: timedOut ? `時間切れです（${Math.round(timeoutMs / 1000)}秒）` : spawnError,
      });
    });
  });
}

function detailOf(r) {
  return `${r.stdout || ''}\n${r.stderr || ''}`.trim().slice(0, 4000);
}

function failureMessage(r, fallback) {
  return r.stderr || r.error || `${fallback}（exit ${r.status}）`;
}

// --json の stdout から JSON 本体を取り出す。bash のログインシェル（profile の
// echo など）の出力が先に混ざっても、最初の '{' から最後の '}' までを本体とみなす。
function parseJson(stdout) {
  const s = String(stdout || '');
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  if (start < 0 || end <= start) {
    throw new Error('agent-audit の応答に JSON がありません');
  }
  return JSON.parse(s.slice(start, end + 1));
}

function collect(cfg, runShell = defaultRunShell) {
  if (collectInFlight) {
    return { ok: false, busy: true, error: '収集を実行中です', lastCollect };
  }
  const running = (async () => {
    const settings = auditSettings(cfg);
    const r = await runShell(buildScript(settings, ['collect']), 120000, settings.distro);
    lastCollect = {
      at: new Date().toISOString(),
      ok: r.ok,
      status: r.status,
      detail: detailOf(r),
      error: r.ok ? '' : failureMessage(r, '収集に失敗しました'),
    };
    return { ...lastCollect, busy: false, lastCollect };
  })();
  collectInFlight = running.finally(() => { collectInFlight = null; });
  return collectInFlight;
}

async function usage(cfg, period, by, runShell = defaultRunShell) {
  const p = PERIODS.has(period) ? period : 'month';
  const b = GROUP_KEYS.has(by) ? by : 'workload';
  const settings = auditSettings(cfg);
  const r = await runShell(
    buildScript(settings, ['usage', '--period', p, '--by', b, '--json']), 60000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, 'トークン利用量を集計できませんでした'));
  const data = parseJson(r.stdout);
  return { ...data, lastCollect };
}

// 全体設定「利用状況」の 1 枚を組み立てるための集計。機能別（workload）とエージェント別
// （agent_cli）を 1 往復で取り、合計もここで畳む。
//
// **合計をここで足すのは「同じ行を 2 度数えない」ため**。usage --json の行は台帳行 +
// 未結合セッション行で、どの粒度で切っても母集合は同じ——だから機能別の合計＝
// エージェント別の合計になる。画面が 2 つの表から別々に合計を出すと、片方の取得だけが
// 失敗したときに食い違った数字が並ぶ。集計ロジック自体は agent-audit が正で、ここは
// 行の足し合わせしかしない（列の意味づけ——実測と推定を合算しない——も向こうの契約）。
async function summary(cfg, period, runShell = defaultRunShell) {
  const p = PERIODS.has(period) ? period : 'month';
  const [byWorkload, byAgent] = [
    await usage(cfg, p, 'workload', runShell),
    await usage(cfg, p, 'agent_cli', runShell),
  ];
  const rows = (byWorkload && byWorkload.rows) || [];
  const totals = rows.reduce((acc, row) => ({
    runs: acc.runs + (Number(row.runs) || 0),
    seconds: acc.seconds + (Number(row.seconds) || 0),
    measuredIn: acc.measuredIn + (Number(row.measured_in) || 0),
    measuredOut: acc.measuredOut + (Number(row.measured_out) || 0),
    estimated: acc.estimated + (Number(row.estimated_tokens) || 0),
    unmeasuredRuns: acc.unmeasuredRuns + (Number(row.unmeasured_runs) || 0),
    usd: acc.usd + (Number(row.usd) || 0),
  }), { runs: 0, seconds: 0, measuredIn: 0, measuredOut: 0, estimated: 0, unmeasuredRuns: 0, usd: 0 });
  totals.measured = totals.measuredIn + totals.measuredOut;
  totals.total = totals.measured + totals.estimated;
  return {
    period: p,
    workloads: rows,
    agents: (byAgent && byAgent.rows) || [],
    totals,
    lastCollect,
  };
}

async function stats(cfg, period, runShell = defaultRunShell) {
  const p = PERIODS.has(period) ? period : 'month';
  const settings = auditSettings(cfg);
  const r = await runShell(
    buildScript(settings, ['stats', '--period', p, '--json']), 60000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, '実行品質を集計できませんでした'));
  return parseJson(r.stdout);
}

// CLI ネイティブセッションの検索・本文取得（flow ノードの「会話を見る」導線）。
// セッションストアの読みは agent-audit の 1 実装が正で、ここは引数を渡すだけ。
// 会話はローカルの記録なので、他端末で実行されたノードでは空が返る（呼び出し側が示す）。
async function sessions(cfg, { cli, sinceIso, untilIso, cwdContains, nativeId, limit } = {}, runShell = defaultRunShell) {
  const settings = auditSettings(cfg);
  const sub = ['sessions'];
  if (cli) sub.push('--cli', String(cli));
  if (sinceIso) sub.push('--since', String(sinceIso));
  if (untilIso) sub.push('--until', String(untilIso));
  if (cwdContains) sub.push('--cwd-contains', String(cwdContains));
  if (nativeId) sub.push('--messages', String(nativeId));
  if (limit) sub.push('--limit', String(limit));
  const r = await runShell(buildScript(settings, sub), 60000, settings.distro);
  if (!r.ok) throw new Error(failureMessage(r, '会話の記録を取得できませんでした'));
  return parseJson(r.stdout);
}

// doctor は設定不備でも非ゼロ終了しうるので、失敗も throw せず本文ごと返す
// （点検結果そのものが見たい情報）。
async function doctor(cfg, runShell = defaultRunShell) {
  const settings = auditSettings(cfg);
  const r = await runShell(buildScript(settings, ['doctor']), 60000, settings.distro);
  return { ok: r.ok, status: r.status, detail: detailOf(r), error: r.ok ? '' : failureMessage(r, '点検を実行できませんでした') };
}

module.exports = { auditSettings, buildScript, parseJson, collect, usage, summary, stats, sessions, doctor };
