'use strict';

// 応答と実行の評価（設計: docs/plans/2026-09-19-agent-app-opik-equivalent-observability-design.md）。
//
// 人が画面で点を付ける口は持たない。評価は 2 つの契機で起きる:
//   自動      … 応答（turn:done）と実行（run-history.append）の直後。ローカルの判定 AI
//               （agent-herd judge）にだけ訊く。標本は設定（sample / all / off）で決め、失敗した
//               応答・実行は標本に関係なく必ず評価する
//   まとめて  … 「会話を検索」で選んだ会話。利用者が明示的に押すので、判定 AI のほかに
//               選んだ AI のヘッドレス 1 回（読み取り専用）でもよい
// 結果は audit-feed に `workload: evaluation` の 1 行（audit.feedEvaluation）。評価された側の行は
// 書き換えない。観測への変換・洞察への畳み込み・集計は agent-audit（extract / distill / usage）が
// 行い、ここは数字を作らない。確度が足りなければ（judge が棄権したら）行を書かない。
//
// 問いは固定文で、人が編集する設定にはしない（§18.2 の連鎖と同じ作法）。`issue` の選択肢は
// agent-audit の観測の種類（OBSERVATION_KINDS）と同じ語彙。

const fs = require('fs');
const path = require('path');
const audit = require('./audit');

// まとめて評価の記録（userData/evaluation/batches.json。最新 20 件）。受信箱が「終わった」を未読として
// 出すための正典で、進み具合そのものは Evaluator が持つ。
const BATCH_DIR = 'evaluation';
const BATCH_FILE = 'batches.json';
const BATCH_KEEP = 20;
function batchFile(userData) { return path.join(userData, BATCH_DIR, BATCH_FILE); }
function readBatches(userData) {
  try {
    const value = JSON.parse(fs.readFileSync(batchFile(userData), 'utf8'));
    return Array.isArray(value) ? value.filter((b) => b && typeof b === 'object' && b.id) : [];
  } catch { return []; }
}
function appendBatch(userData, batch) {
  const file = batchFile(userData);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const next = [batch, ...readBatches(userData).filter((b) => b.id !== batch.id)].slice(0, BATCH_KEEP);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(next));
  fs.renameSync(`${file}.tmp`, file);
  return next;
}

const QUESTIONS = {
  quality: {
    type: 'score',
    instructions: '依頼に対して、回答と実行が要点を押さえ、誤りや無駄が無いか',
    criteria: {
      1: '要点を外している、誤りがある、または途中で止まっている',
      2: '概ね応えているが、抜けや無駄がある',
      3: '要点を押さえ、誤りも無駄も無い',
    },
  },
  issue: {
    type: 'choice',
    instructions: '改善すべき点があるなら、その原因はどこにあるか',
    criteria: {
      none: '改善すべき点は無い',
      'skill-gap': 'スキル（手順書）の手順が足りない、または合っていない',
      'prompt-issue': '依頼の書き方か受入基準が曖昧',
      'tool-failure': '使っているツールやコマンドの失敗で作業が止まった',
      'config-issue': '設定の不足か食い違い',
      avoid: '同じ失敗の繰り返し',
    },
  },
};
const ISSUES = Object.keys(QUESTIONS.issue.criteria);
const MODES = ['sample', 'all', 'off'];
const MIN_CONFIDENCE = 0.55;       // これに届かない問いは judge が棄権する（行を書かない）
const SAMPLE_EVERY = 5;            // sample のとき、成功した応答・実行は 5 件に 1 件
const STATE_CHARS = 6000;          // 判定 AI に渡す本文の上限（依頼の先頭と回答の末尾を残す）
const BATCH_LIMIT = 200;           // まとめて評価の 1 回の上限（検索の 2000 件と同じ作法で 1 行で伝える）
const QUEUE_LIMIT = 200;
const JUDGE_TIMEOUT_MS = 120000;
const HEADLESS_TIMEOUT_MS = 5 * 60 * 1000;
const RETRY_MS = 30000;

function clip(text, limit, { tail = false } = {}) {
  const s = String(text == null ? '' : text);
  if (s.length <= limit) return s;
  return tail ? `…${s.slice(-limit)}` : `${s.slice(0, limit)}…`;
}

// 標本の判定。失敗は必ず、成功は mode に従う（counter は評価の候補になった成功の通し番号）。
function shouldEvaluate(mode, { failed = false, counter = 0 } = {}) {
  const m = MODES.includes(mode) ? mode : 'sample';
  if (m === 'off') return false;
  if (failed) return true;
  if (m === 'all') return true;
  return counter % SAMPLE_EVERY === 0;
}

// 判定 AI に渡す「状態」。会話 1 往復（依頼と回答）か、実行の記録を人が読める形にする。
function stateText({ prompt = '', answer = '', information = [], used = null, status = '', error = '' } = {}) {
  const half = Math.floor(STATE_CHARS / 2);
  const lines = ['## 依頼', clip(prompt, half), '', '## 回答', clip(answer, half, { tail: true })];
  const commands = (Array.isArray(information) ? information : [])
    .filter((item) => item && item.type === 'command')
    .map((item) => `- ${clip(item.title || item.detail || '', 120)}${item.status === 'error' ? '（失敗）' : ''}`)
    .slice(0, 20);
  if (commands.length) lines.push('', '## 実行したコマンド', ...commands);
  if (used && typeof used === 'object') {
    const parts = [];
    if (Array.isArray(used.skills) && used.skills.length) parts.push(`スキル: ${used.skills.join(', ')}`);
    if (Array.isArray(used.tools) && used.tools.length) parts.push(`ツール: ${used.tools.join(', ')}`);
    if (parts.length) lines.push('', '## 使ったもの', ...parts.map((p) => `- ${p}`));
  }
  if (status) lines.push('', `## 結果: ${status}${error ? ` — ${clip(error, 400)}` : ''}`);
  return lines.join('\n');
}

// 会話の保存形（sessions/<id>.json の messages、または検索の record.messages）から、最後の往復を取る。
function lastExchange(messages) {
  const list = Array.isArray(messages) ? messages : [];
  let answerAt = -1;
  for (let i = list.length - 1; i >= 0; i -= 1) if (list[i] && list[i].role === 'assistant') { answerAt = i; break; }
  if (answerAt < 0) return null;
  let promptAt = -1;
  for (let i = answerAt - 1; i >= 0; i -= 1) if (list[i] && list[i].role === 'user') { promptAt = i; break; }
  const answer = list[answerAt];
  const prompt = promptAt >= 0 ? list[promptAt] : null;
  return { prompt: prompt ? prompt.text : '', answer: answer.text || '', message: answer };
}

// agent-herd judge の出力（JSON 1 行 + @agent-usage）→ 評価。棄権していれば null。
function parseJudge(stdout, { model = '' } = {}) {
  const raw = String(stdout || '');
  const at = raw.indexOf('{');
  if (at < 0) return null;
  let doc;
  try { doc = JSON.parse(raw.slice(at, raw.lastIndexOf('}') + 1)); } catch { return null; }
  const answers = doc && doc.answers && typeof doc.answers === 'object' ? doc.answers : {};
  const held = Array.isArray(doc.abstained) ? doc.abstained : [];
  if (held.length) return null;
  const q = answers.quality || {};
  const i = answers.issue || {};
  const score = Number(q.score);
  const bucket = Number(q.bucket);
  const quality = Number.isFinite(score) ? Math.max(1, Math.min(3, Math.round(score))) : (Number.isFinite(bucket) ? bucket : null);
  const issue = ISSUES.includes(i.choice) ? i.choice : 'none';
  if (quality == null) return null;
  const confidence = Math.min(Number(q.confidence) || 0, Number(i.confidence) || 0);
  return {
    quality, issue, confidence: Math.round(confidence * 1000) / 1000,
    method: String(q.method || i.method || ''), judge_model: String(model || ''),
  };
}

// 選んだ AI のヘッドレス 1 回に渡す依頼文。judge と同じ 2 つの問いに JSON で答えさせる。
function headlessPrompt(state) {
  return [
    'あなたは AI エージェントの作業を評価する係です。次の「依頼」と「回答」を読み、JSON だけを出力してください。',
    '説明文やコードフェンスは付けないでください。',
    '',
    '{"quality": 1|2|3, "issue": "none|skill-gap|prompt-issue|tool-failure|config-issue|avoid", "note": "<1 文。任意>"}',
    '',
    `quality: ${QUESTIONS.quality.instructions}（${Object.entries(QUESTIONS.quality.criteria).map(([k, v]) => `${k}=${v}`).join(' / ')}）`,
    `issue: ${QUESTIONS.issue.instructions}（${Object.entries(QUESTIONS.issue.criteria).map(([k, v]) => `${k}=${v}`).join(' / ')}）`,
    '',
    '---',
    state,
  ].join('\n');
}

function parseHeadless(text, { model = '' } = {}) {
  const raw = String(text || '');
  const m = raw.match(/\{[^{}]*"quality"[^{}]*\}/);
  if (!m) return null;
  let doc;
  try { doc = JSON.parse(m[0]); } catch { return null; }
  const quality = Number(doc.quality);
  if (![1, 2, 3].includes(quality)) return null;
  return {
    quality, issue: ISSUES.includes(doc.issue) ? doc.issue : 'none', confidence: 1, method: 'text',
    judge_model: String(model || ''), note: String(doc.note || '').slice(0, 400),
  };
}

// 課題を会話へ渡すときの最初の依頼文。改善策はこの会話で決める（agent-app は作らない）。
const TARGET_LABEL = { skill: 'スキル', task: 'タスク', workflow: 'ワークフロー', tool: 'ツール' };
function handoffPrompt(issue = {}) {
  const target = issue.target && issue.target.kind ? `${TARGET_LABEL[issue.target.kind] || issue.target.kind}「${issue.target.name}」` : '全体';
  const lines = [
    `評価で見つかった課題について、改善策を一緒に決めたいです。まだ直さなくてよいので、まず原因の見立てと、取りうる改善策を 2〜3 案、それぞれの利点と注意点つきで挙げてください。`,
    '',
    `## 対象`, target,
    '',
    '## 課題', String(issue.statement || '').trim(),
  ];
  const facts = [];
  if (issue.occurrences) facts.push(`観測 ${issue.occurrences} 件`);
  if (issue.confidence) facts.push(`確度 ${issue.confidence}`);
  if (issue.kind) facts.push(`種類 ${issue.kind}`);
  if (facts.length) lines.push('', '## 根拠', `- ${facts.join(' · ')}`);
  const evidence = Array.isArray(issue.evidence) ? issue.evidence.filter(Boolean) : [];
  if (evidence.length) lines.push(`- 観測: ${evidence.slice(0, 10).join(', ')}${evidence.length > 10 ? ' …' : ''}`);
  if (issue.id) lines.push(`- 洞察: ${issue.id}`);
  return lines.join('\n');
}

// 評価の実行役。1 件ずつ背景で回し、ターンや端末が動いている間は延期する（監査の連鎖と同じ）。
//   capture(name, args, { input, timeoutMs }) … agent-herd / agent-audit を起こす（Windows では WSL 経由）
//   runPrompt({ cli, prompt, model, readonly, cwd })  … 選んだ AI のヘッドレス 1 回（share/run.js）
//   readRecord(key)                                  … 検索の record（sessionBrowser.read）
class Evaluator {
  constructor({
    userData, loadConfig, capture, runPrompt = null, readRecord = null,
    feed = audit.feedEvaluation, busy = () => false, post = () => {}, now = () => Date.now(),
    setTimer = setTimeout, clearTimer = clearTimeout,
  }) {
    Object.assign(this, { userData, loadConfig, capture, runPrompt, readRecord, feed, busy, post, now, setTimer, clearTimer });
    this.queue = [];
    this.running = false;
    this.counter = 0;
    this.available = null;
    this.lastError = '';
    this.evaluated = 0;
    this.issues = 0;
    this.batch = null;    // { total, done, issues, skipped, cli, model, running }
    this.timer = null;
  }

  mode() {
    const cfg = this.loadConfig() || {};
    return cfg.evaluation && MODES.includes(cfg.evaluation.mode) ? cfg.evaluation.mode : 'sample';
  }

  status() {
    return {
      mode: this.mode(), available: this.available, running: this.running, queued: this.queue.length,
      evaluated: this.evaluated, issues: this.issues, lastError: this.lastError,
      batch: this.batch ? { ...this.batch } : null,
    };
  }

  notifyChanged() { this.post('evaluation:changed', this.status()); }

  // agent-herd（判定 AI）があるか。無ければ自動評価は何もしない（ADR-11）。
  async probe({ force = false } = {}) {
    if (this.available != null && !force) return this.available;
    const res = await this.capture('agent-herd', ['config', '--json'], { timeoutMs: 20000 });
    this.available = !!(res && res.ok);
    return this.available;
  }

  // 応答 1 件（ipc の turn:done の直前。feedTurn と同じ材料）。
  noteTurn({ session = {}, message = {}, sessionId = '' } = {}) {
    if (!message || message.role !== 'assistant') return false;
    const failed = !!message.error || !!message.stopped || (message.code != null && message.code !== 0);
    if (!this.take(failed)) return false;
    const exchange = lastExchange([...(Array.isArray(session.messages) ? session.messages : []), message]);
    const used = audit.usedOf(message);
    this.enqueue({
      kind: 'auto',
      evaluated: {
        workload: 'chat', ref: String(session.id || ''), session_id: sessionId,
        agent_cli: String(message.cli || session.cli || ''), model: String(message.model || session.model || ''), used,
      },
      state: stateText({
        prompt: exchange ? exchange.prompt : '', answer: message.text || '',
        information: message.parts && message.parts.information, used,
        status: message.stopped ? '停止' : failed ? '失敗' : '完了', error: message.error || '',
      }),
    });
    return true;
  }

  // 実行 1 件（audit.feedRun の聞き手。record は run-history の記録）。
  noteRun({ root = '', record = {}, kind = 'task' } = {}) {
    const name = String(record.machine || record.taskId || '');
    if (!name) return false;
    const failed = !record.ok;
    if (!this.take(failed)) return false;
    const summary = Object.fromEntries(Object.entries(record)
      .filter(([k, v]) => ['machine', 'taskId', 'ok', 'escalate', 'errorClass', 'error', 'summary', 'output', 'inputs', 'agentCli', 'model'].includes(k) && v != null)
      .map(([k, v]) => [k, typeof v === 'string' ? clip(v, 1500, { tail: k === 'output' }) : v]));
    this.enqueue({
      kind: 'auto',
      evaluated: {
        workload: 'task', ref: name, agent_cli: String(record.agentCli || ''), model: String(record.model || ''),
        artifact: { kind: kind === 'workflow' ? 'workflow' : 'task', name, origin: root ? `repo:${root.split(/[\\/]/).filter(Boolean).pop()}` : '' },
      },
      state: stateText({
        prompt: `${kind === 'workflow' ? 'ワークフロー' : 'タスク'}「${name}」の実行`,
        answer: JSON.stringify(summary, null, 1),
        status: record.escalate ? '人への差し戻し' : record.ok ? '完了' : '失敗', error: String(record.error || record.errorClass || ''),
      }),
    });
    return true;
  }

  take(failed) {
    const mode = this.mode();
    if (mode === 'off') return false;
    if (!failed) this.counter += 1;
    return shouldEvaluate(mode, { failed, counter: failed ? 0 : this.counter - 1 });
  }

  enqueue(item) {
    this.queue.push(item);
    while (this.queue.length > QUEUE_LIMIT) this.queue.shift();
    this.schedule(0);
  }

  schedule(delay = 0) {
    if (this.timer) this.clearTimer(this.timer);
    this.timer = this.setTimer(() => { this.timer = null; this.drain().catch(() => {}); }, delay);
    if (this.timer && this.timer.unref) this.timer.unref();
  }

  // 1 件ずつ。ターンや端末が動いていれば後で（本人の作業を邪魔しない）。
  // 動いている最中に呼ばれたら、その回の終わりを返す（呼ぶ側が待てる）。
  drain() {
    if (this.running) return this.inflight || Promise.resolve();
    if (!this.queue.length) return Promise.resolve();
    if (this.busy()) { this.schedule(RETRY_MS); return Promise.resolve(); }
    this.inflight = this.drainNow().finally(() => { this.inflight = null; });
    return this.inflight;
  }

  async drainNow() {
    this.running = true;
    this.notifyChanged();
    try {
      while (this.queue.length && !this.busy()) {
        const item = this.queue.shift();
        await this.evaluateOne(item);
      }
    } finally {
      this.running = false;
      if (this.batch && this.batch.running && !this.queue.some((q) => q.kind === 'batch')) this.finishBatch();
      this.notifyChanged();
      if (this.queue.length) this.schedule(RETRY_MS);
    }
  }

  async evaluateOne(item) {
    let evaluation = null;
    try {
      if (item.cli) {
        if (!this.runPrompt) throw new Error('この AI でまとめて評価する口がありません');
        const state = item.scrubbed ? item.state : await this.scrub(item.state);
        const run = this.runPrompt({ cli: item.cli, model: item.model || '', prompt: headlessPrompt(state), readonly: true, cwd: item.cwd || this.userData, repo: item.cwd || '', timeoutMs: HEADLESS_TIMEOUT_MS });
        const outcome = await run.done;
        if (outcome.error && !outcome.text) throw new Error(outcome.error);
        evaluation = parseHeadless(outcome.text, { model: `${item.cli}${item.model ? `:${item.model}` : ''}` });
      } else {
        if (!(await this.probe())) { this.lastError = 'agent-herd がホストにありません（任意。agent-tools の install.sh で入ります）'; return; }
        const res = await this.capture('agent-herd',
          ['judge', '--questions', JSON.stringify(QUESTIONS), '--min-confidence', String(MIN_CONFIDENCE)],
          { input: item.state, timeoutMs: JUDGE_TIMEOUT_MS });
        if (!res || (!res.ok && res.status !== 1)) throw new Error((res && (res.error || String(res.stderr || '').trim().split('\n').pop())) || 'judge を起動できません');
        const modelLine = String(res.stderr || '').match(/model[=:]\s*(\S+)/);
        evaluation = parseJudge(res.stdout, { model: modelLine ? modelLine[1] : '' });
      }
      this.lastError = '';
    } catch (err) {
      this.lastError = String((err && err.message) || err);
      if (this.batch && item.kind === 'batch') this.batch.skipped += 1;
      return;
    }
    if (item.kind === 'batch' && this.batch) this.batch.done += 1;
    if (!evaluation) { if (item.kind === 'batch' && this.batch) this.batch.skipped += 1; return; }   // 棄権
    const rec = this.feed(this.userData, { evaluated: item.evaluated, evaluation }, { now: this.now() });
    if (!rec) return;
    this.evaluated += 1;
    if (evaluation.issue !== 'none') {
      this.issues += 1;
      if (item.kind === 'batch' && this.batch) this.batch.issues += 1;
    }
  }

  // まとめて評価が終わった。記録に残す（受信箱が未読として出す）。書けなくても評価は無かったことにしない。
  finishBatch() {
    if (!this.batch || !this.batch.running) return;
    this.batch.running = false;
    this.batch.finishedAt = new Date(this.now()).toISOString();
    try { appendBatch(this.userData, { ...this.batch }); } catch { /* 記録は副産物 */ }
  }

  // クラウドの AI に渡す前の伏せ字化は agent-audit の規則で行う（規則を JS へ写さない）。
  async scrub(text) {
    const res = await this.capture('agent-audit', ['scrub'], { input: text, timeoutMs: 20000 });
    if (!res || !res.ok) throw new Error('伏せ字化に agent-audit が要ります（agent-tools の install.sh で入ります）');
    return String(res.stdout || '');
  }

  // まとめて評価。keys は検索の record の key（app:<id> / 外の会話 / public:）。
  async startBatch({ keys = [], cli = '', model = '' } = {}) {
    const list = [...new Set((Array.isArray(keys) ? keys : []).map((k) => String(k || '')).filter(Boolean))];
    if (!list.length) throw new Error('評価する会話を選んでください');
    if (list.length > BATCH_LIMIT) throw new Error(`一度に評価できるのは ${BATCH_LIMIT} 件までです。検索の条件を絞ってください`);
    if (this.batch && this.batch.running) throw new Error('まとめて評価が進んでいます。終わってから始めてください');
    if (!this.readRecord) throw new Error('会話を読む口がありません');
    const useCli = String(cli || '').trim();
    if (useCli && useCli !== 'herd' && !this.runPrompt) throw new Error('この AI でまとめて評価する口がありません');
    if (!useCli || useCli === 'herd') { if (!(await this.probe({ force: true }))) throw new Error('agent-herd がホストにありません（任意。agent-tools の install.sh で入ります）'); }
    this.batch = { id: `batch-${this.now()}`, total: list.length, done: 0, issues: 0, skipped: 0, cli: useCli || 'herd', model: String(model || ''), running: true, startedAt: new Date(this.now()).toISOString(), finishedAt: '' };
    for (const key of list) {
      let record;
      try { record = await this.readRecord(key); } catch { this.batch.skipped += 1; continue; }
      const exchange = lastExchange(record && record.messages);
      if (!exchange) { this.batch.skipped += 1; continue; }
      const used = audit.usedOf(exchange.message);
      this.enqueue({
        kind: 'batch', cli: useCli && useCli !== 'herd' ? useCli : '', model: String(model || ''), cwd: record.repo || '',
        evaluated: {
          workload: 'chat', ref: record.appId ? String(record.appId) : String(key), session_id: String(record.nativeId || ''),
          agent_cli: String(record.agent || ''), model: String(record.model || ''), used,
        },
        state: stateText({ prompt: exchange.prompt, answer: exchange.answer, information: exchange.message.parts && exchange.message.parts.information, used, status: '完了' }),
      });
    }
    if (!this.queue.some((q) => q.kind === 'batch')) this.finishBatch();
    this.notifyChanged();
    return this.status();
  }
}

module.exports = {
  QUESTIONS, ISSUES, MODES, MIN_CONFIDENCE, SAMPLE_EVERY, BATCH_LIMIT, STATE_CHARS,
  shouldEvaluate, stateText, lastExchange, parseJudge, parseHeadless, headlessPrompt, handoffPrompt, Evaluator,
  readBatches, appendBatch, batchFile,
};
