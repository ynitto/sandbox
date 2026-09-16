'use strict';

// 監査（agent-audit）の周期・申告・要約。
//
// 役割分担は docs/plans/2026-09-16-agent-app-agent-audit-split-and-artifact-sharing-design.md。
//   読解と保存と判定 … agent-audit（ホスト側の Python）。**数字はここで作らない**
//   周期と申告と表示 … このファイル。自分の出来事を台帳の形で 1 行ずつ足し、連鎖を回す
//
// 境界（Windows ↔ WSL）を渡るものは 3 つだけにする。
//   1. 追記専用の台帳    userData/audit-feed/<YYYYMMDD>.jsonl（agent-audit の ledger_dirs が読む）
//   2. 単発の JSON 出力  usage --json / stats --json / qualify --json
//   3. ストアの中身      userData/audit/（書き手は agent-audit 1 本・読むのはこちら）
// 再帰 glob と SQLite は渡さない（9p 越しに遅く、途中で切れる）。
//
// 台帳の行は node-budget と同じ形にする。reader を増やさないためで、追加したのは
// `artifact`（成果物の種別・名前・出所）だけ——これが成果物の適格性の集計鍵になる。

const fs = require('fs');
const os = require('os');
const path = require('path');

const host = require('./host');

const FEED_DIR = 'audit-feed';
const STORE_DIR = 'audit';
const CONFIG_NAME = 'audit-config.json';
// 成果物の種別。定型化（shared/reuse.js）の種類とそろえる——内部の綴り
// （ステートマシン）を混ぜない。
const ARTIFACT_KINDS = ['skill', 'task', 'workflow'];
// agent-audit が終端として数える status。これ以外は集計に入らない。
const STATUSES = ['done', 'failed', 'cancelled', 'escalate'];

// 連鎖の 6 段。agent-loop の audit-calibrate-hook.py と同じ並び・同じ許容終了コード。
// 本体に複合コマンドを持たない決定（仕様 §5）の裏返しで、この表は hook と app の
// 2 か所にある。どちらも「前段の成功を暗黙に保証しない」ので、終了コードで止める。
const STEPS = [
  { key: 'collect', args: ['collect'], allow: [0], label: '収集' },
  { key: 'qualify', args: ['qualify', '--apply'], allow: [0], label: '適格性' },
  { key: 'calibrate', args: ['calibrate', '--write'], allow: [0], label: '較正' },
  { key: 'extract', args: ['extract'], allow: [0, 1], label: '抽出' },
  { key: 'distill', args: ['distill', '--review'], allow: [0, 1], label: '蒸留' },
  { key: 'tune', args: ['tune', '--apply'], allow: [0], label: '還流' },
];

const STEP_TIMEOUT_MS = 10 * 60 * 1000;

function feedDir(userData) { return path.join(userData, FEED_DIR); }
function storeDir(userData) { return path.join(userData, STORE_DIR); }
function configFile(userData) { return path.join(userData, CONFIG_NAME); }

function dayKey(now) {
  return new Date(now).toISOString().slice(0, 10).replace(/-/g, '');
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function artifactOf(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const kind = String(raw.kind || '');
  const name = String(raw.name || '').trim();
  if (!ARTIFACT_KINDS.includes(kind) || !name) return null;
  return { kind, name, origin: String(raw.origin || '') };
}

// 台帳 1 行。`ts` が無い行は agent-audit が読み飛ばすので、ここで必ず刻む。
function row(raw, { now = Date.now(), node = os.hostname() } = {}) {
  const out = {
    ts: new Date(now).toISOString(),
    node: String(raw.node || node || ''),
    tool: 'agent-app',
    workload: String(raw.workload || ''),
    ref: String(raw.ref || ''),
    purpose: String(raw.purpose || raw.workload || ''),
    agent_cli: String(raw.agent_cli || ''),
    model: String(raw.model || ''),
    seconds: Math.max(0, Math.round((num(raw.seconds) || 0) * 10) / 10),
    tokens_in: num(raw.tokens_in),
    tokens_out: num(raw.tokens_out),
    status: STATUSES.includes(raw.status) ? raw.status : 'failed',
  };
  if (raw.error_class) out.error_class = String(raw.error_class);
  if (raw.session_id) out.session_id = String(raw.session_id);
  if (raw.task_id) out.task_id = String(raw.task_id);
  if (raw.run_id) out.run_id = String(raw.run_id);
  if (raw.mode) out.mode = String(raw.mode);
  const artifact = artifactOf(raw.artifact);
  if (artifact) out.artifact = artifact;
  return out;
}

// 1 行を足す。**この呼び出しが失敗しても本体の処理は止めない**——監査は副産物で、
// 会話やタスクの成否を左右してはいけない（呼ぶ側は try で包まずに済む）。
function feed(userData, raw, options = {}) {
  try {
    const rec = row(raw, options);
    const dir = feedDir(userData);
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, `${dayKey(options.now || Date.now())}.jsonl`),
      `${JSON.stringify(rec)}\n`, 'utf8');
    return rec;
  } catch {
    return null;
  }
}

// 会話のターン 1 回。トークンは CLI が申告したときだけ載せる（推定は書かない）。
function feedTurn(userData, { session = {}, message = {}, sessionId = '' } = {}, options = {}) {
  if (!message || message.role !== 'assistant') return null;
  const failed = !!message.error || message.stopped || (message.code != null && message.code !== 0);
  return feed(userData, {
    workload: 'chat',
    ref: String(session.id || ''),
    purpose: 'chat',
    agent_cli: String(message.cli || session.cli || ''),
    model: String(message.model || session.model || ''),
    seconds: (num(message.elapsedMs) || 0) / 1000,
    tokens_in: message.tokensIn,
    tokens_out: message.tokensOut,
    status: message.stopped ? 'cancelled' : failed ? 'failed' : 'done',
    error_class: failed && message.error ? 'answer' : '',
    session_id: sessionId,
  }, options);
}

// タスク・ワークフローの手動実行 1 回。成果物の名前が付くのはここだけなので、
// 適格性（artifacts.json）の材料もこの行から入る。
function feedRun(userData, { root = '', record = {}, kind = 'task' } = {}, options = {}) {
  const name = String(record.machine || record.taskId || '');
  if (!name) return null;
  const status = record.escalate ? 'escalate' : record.ok ? 'done' : 'failed';
  const started = Date.parse(record.startedAt || '');
  const finished = Date.parse(record.finishedAt || '');
  return feed(userData, {
    workload: 'task',
    ref: name,
    purpose: kind,
    agent_cli: String(record.agentCli || ''),
    model: String(record.model || ''),
    seconds: Number.isFinite(started) && Number.isFinite(finished)
      ? Math.max(0, (finished - started) / 1000) : 0,
    status,
    error_class: status === 'done' ? '' : String(record.errorClass || 'verify'),
    task_id: String(record.taskId || ''),
    run_id: String(record.runId || ''),
    artifact: { kind, name, origin: root ? `repo:${path.basename(root)}` : '' },
  }, { ...options, now: Number.isFinite(finished) ? finished : options.now });
}

// 共有（LAN）で引き受けた依頼 1 件。share/ledger の行を台帳の形へ写す。
function feedShare(userData, ledgerRow = {}, options = {}) {
  const status = ledgerRow.status === 'lost' ? 'cancelled' : ledgerRow.status;
  return feed(userData, {
    workload: 'shared',
    ref: String(ledgerRow.posted_by || ''),
    purpose: 'shared',
    agent_cli: String(ledgerRow.cli || ''),
    model: String(ledgerRow.model || ''),
    seconds: num(ledgerRow.seconds) || 0,
    tokens_in: ledgerRow.tokens_in,
    tokens_out: ledgerRow.tokens_out,
    status: STATUSES.includes(status) ? status : 'failed',
    error_class: String(ledgerRow.error_class || ''),
    mode: ledgerRow.mode,
    run_id: String(ledgerRow.id || ''),
  }, options);
}

// ---- 生成する設定 --------------------------------------------------------------

// agent-audit は環境変数を見ない（設定ファイルと引数だけ）。app が持つ 3 つ
// （書き先・追加の台帳・追加のホーム）を JSON で渡す。YAML を書かないので PyYAML も要らない。
// 利用者が自分の設定で全部決めたいときは settings の audit.configFile を指す（そのときは
// ledger_dirs と extra_homes も利用者の責任になる）。
function generateConfig(userData, { platform = process.platform, env = process.env } = {}) {
  const toHost = (p) => (platform === 'win32' ? host.toWslPath(p) : String(p || ''));
  const homes = [];
  if (platform === 'win32' && env.USERPROFILE) homes.push(host.toWslPath(env.USERPROFILE));
  const config = {
    _generated_by: 'agent-app',
    audit_dir: toHost(storeDir(userData)),
    // 申告は 1 本に絞る。共有の台帳（share/ledger）を直接読ませない——あちらは行の形が
    // 違い（`ts` を持たない）、feedShare が同じ出来事を台帳の形へ写している。
    ledger_dirs: [toHost(feedDir(userData))],
    extra_homes: homes,
    // 本文の検索は session-index.db が持つ。写しをストアに増やさない。
    with_transcripts: false,
  };
  fs.mkdirSync(userData, { recursive: true });
  const file = configFile(userData);
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(config, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, config };
}

// 本人の操作を邪魔しないための前置き。ionice が無いホストでも動く形にする。
function stepScript(argv) {
  const quoted = host.quoteArgv(argv);
  return 'IO=""; command -v ionice >/dev/null 2>&1 && IO="ionice -c 3"; '
    + `nice -n 19 $IO ${quoted}`;
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

// ---- 表示のための読み取り（数字は作らない） --------------------------------------

function artifacts(userData) {
  const doc = readJson(path.join(storeDir(userData), 'artifacts.json'));
  const rows = doc && Array.isArray(doc.artifacts) ? doc.artifacts : [];
  return { revision: doc ? Number(doc.revision) || 0 : 0, generatedAt: doc ? String(doc.generated_at || '') : '', items: rows };
}

// 洞察とレポートは agent-audit が書いたファイルをそのまま見せる（ホストが止まっていても読める）。
function insights(userData, { limit = 20 } = {}) {
  const dir = path.join(storeDir(userData), 'insights');
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => n.endsWith('.json')); } catch { return []; }
  const items = [];
  for (const name of names) {
    const doc = readJson(path.join(dir, name));
    if (doc) items.push(doc);
  }
  return items
    .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')))
    .slice(0, limit);
}

function reports(userData, { limit = 10 } = {}) {
  const dir = path.join(storeDir(userData), 'reports');
  try {
    return fs.readdirSync(dir).filter((n) => n.endsWith('.md')).sort().reverse().slice(0, limit)
      .map((name) => ({ name, path: path.join(dir, name) }));
  } catch { return []; }
}

// ---- 周期 ----------------------------------------------------------------------

class Auditor {
  constructor({
    userData, loadConfig, shellFor, post = () => {}, busy = () => false,
    platform = process.platform, env = process.env, now = () => Date.now(),
  }) {
    Object.assign(this, { userData, loadConfig, shellFor, post, busy, platform, env, now });
    this.running = false;
    this.available = null;      // null = 未確認
    this.step = '';
    this.lastRunAt = 0;
    this.lastError = '';
    this.lastSteps = [];
    this.deferred = 0;
    this.timer = null;
  }

  config() {
    const cfg = this.loadConfig() || {};
    return { ...(cfg.audit || {}), distro: this.platform === 'win32' ? String(cfg.wslDistro || '') : '' };
  }

  shell() { return this.shellFor(this.config().distro); }

  status() {
    const cfg = this.config();
    const art = artifacts(this.userData);
    return {
      enabled: cfg.enabled !== false,
      intervalMinutes: Number(cfg.intervalMinutes) || 0,
      shareRepo: String(cfg.shareRepo || ''),
      running: this.running,
      step: this.step,
      available: this.available,
      lastRunAt: this.lastRunAt,
      lastError: this.lastError,
      steps: this.lastSteps,
      deferred: this.deferred,
      store: storeDir(this.userData),
      artifacts: { revision: art.revision, generatedAt: art.generatedAt, count: art.items.length },
    };
  }

  notifyChanged(extra = {}) { this.post('audit:changed', { ...this.status(), ...extra }); }

  // agent-audit が入っているか。無ければ「任意の機能が使えない」だけで本体は止めない（ADR-11）。
  async probe({ force = false } = {}) {
    if (this.available != null && !force) return this.available;
    const r = await this.shell().run('command -v agent-audit >/dev/null 2>&1 && echo yes || echo no',
      { timeoutMs: 20000 });
    this.available = r.ok && /yes/.test(r.output || '');
    return this.available;
  }

  // 連鎖を 1 周。単一飛行で、ターンが動いている間は回さない（本人の操作を邪魔しない）。
  async run({ manual = false } = {}) {
    if (this.running) return { skipped: 'running' };
    const cfg = this.config();
    if (!manual && cfg.enabled === false) return { skipped: 'disabled' };
    if (!manual && this.busy()) {
      this.deferred += 1;
      this.notifyChanged();
      return { skipped: 'busy' };
    }
    if (!(await this.probe())) {
      this.lastError = 'agent-audit がホストにありません（任意。agent-tools の install.sh で入ります）';
      this.notifyChanged();
      if (manual) throw new Error(this.lastError);
      return { skipped: 'unavailable' };
    }
    this.running = true;
    this.lastError = '';
    this.lastSteps = [];
    this.notifyChanged();
    try {
      const base = ['agent-audit', '--config', this.configPath()];
      const shell = this.shell();
      for (const step of STEPS) {
        this.step = step.label;
        this.notifyChanged();
        const r = await shell.run(stepScript([...base, ...step.args]), { timeoutMs: STEP_TIMEOUT_MS });
        // ホストのシェルは終了コードを status として返す（run の ok は status===0）。
        const status = r.ok ? 0 : Number(r.status);
        const allowed = r.ok || step.allow.includes(status);
        this.lastSteps.push({
          key: step.key, label: step.label, status: Number.isFinite(status) ? status : -1, ok: allowed,
          output: String(r.output || r.error || '').split('\n').slice(-6).join('\n').slice(-1200),
        });
        if (!allowed) {
          this.lastError = `${step.label}で止まりました（終了コード ${Number.isFinite(status) ? status : '?'}）`;
          break;
        }
      }
      this.lastRunAt = this.now();
      return { steps: this.lastSteps, error: this.lastError };
    } finally {
      this.running = false;
      this.step = '';
      this.notifyChanged();
    }
  }

  // 設定で自前の agent-audit.yaml を指したときは、そちらを渡して app は何も生成しない。
  configPath() {
    const own = String(this.config().configFile || '').trim();
    if (own) return this.platform === 'win32' ? host.toWslPath(own) : own;
    const generated = generateConfig(this.userData, { platform: this.platform, env: this.env });
    return this.platform === 'win32' ? host.toWslPath(generated.file) : generated.file;
  }

  // 集計は agent-audit に訊く（--json をそのまま渡す）。
  async summary({ by = 'agent_cli', period = 'month' } = {}) {
    if (!(await this.probe())) return { available: false, usage: null, quality: null };
    const base = ['agent-audit', '--config', this.configPath()];
    const shell = this.shell();
    const [usage, quality, limits] = await Promise.all([
      shell.run(host.quoteArgv([...base, 'usage', '--by', by, '--period', period, '--json']), { timeoutMs: 120000 }),
      shell.run(host.quoteArgv([...base, 'stats', '--period', period, '--json']), { timeoutMs: 120000 }),
      // 利用枠は期間・内訳の選択によらず最新の観測を読む。
      shell.run(host.quoteArgv([...base, 'usage', '--by', 'agent_cli', '--period', 'total', '--json']), { timeoutMs: 120000 }),
    ]);
    const parse = (r) => {
      if (!r.ok) return null;
      try { return JSON.parse(String(r.output || '').slice(String(r.output).indexOf('{'))); } catch { return null; }
    };
    const usageData = parse(usage);
    // agent-dashboard と同じく、CLI が重複排除済みの行を一度だけ足す。
    const totals = usageData && Array.isArray(usageData.rows) ? usageData.rows.reduce((sum, row) => {
      for (const key of ['measured_in', 'measured_out', 'estimated_tokens', 'unmeasured_runs', 'runs']) {
        sum[key] += Number(row[key]) || 0;
      }
      return sum;
    }, { measured_in: 0, measured_out: 0, estimated_tokens: 0, unmeasured_runs: 0, runs: 0 }) : null;
    return {
      available: true,
      totals,
      agentLimits: (parse(limits) || {}).agent_limits || [],
      limitsError: !limits.ok || !parse(limits),
      by,
      period,
      usage: usageData,
      quality: parse(quality),
      error: [usage.ok ? '' : (usage.error || usage.output || ''), quality.ok ? '' : (quality.error || quality.output || '')]
        .filter(Boolean).join('\n').split('\n').slice(-4).join('\n'),
    };
  }

  schedule({ startupDelayMs = 90000, tickMs = 60 * 1000 } = {}) {
    this.unschedule();
    const cfg = this.config();
    if (cfg.enabled === false) return;
    this.startTimer = setTimeout(() => { this.run().catch(() => {}); }, startupDelayMs);
    if (this.startTimer.unref) this.startTimer.unref();
    this.timer = setInterval(() => {
      const c = this.config();
      const minutes = Number(c.intervalMinutes) || 0;
      if (c.enabled === false || minutes <= 0 || this.running) return;
      if (this.now() - this.lastRunAt >= minutes * 60 * 1000) this.run().catch(() => {});
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
  FEED_DIR, STORE_DIR, CONFIG_NAME, ARTIFACT_KINDS, STATUSES, STEPS,
  feedDir, storeDir, configFile, row, feed, feedTurn, feedRun, feedShare,
  generateConfig, stepScript, artifacts, insights, reports, Auditor,
};
