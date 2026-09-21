'use strict';

// Routing belongs to agent-tools (`agent-herd route`; design in
// docs/plans/2026-09-21-agent-app-judge-request-routing-design.md). The app only narrows the
// candidates it can name (the repository's tasks, workflows and the configured skills), runs the
// command, and validates the answer before acting on it. Anything undecided falls back to
// today's behaviour: run the request in the conversation, pick skills by text match.
const fs = require('fs');
const path = require('path');
const skillSelection = require('./skillSelection');

const LIMITS = { tasks: 8, flows: 8, skills: 6 };
const TIMEOUT_MS = 30000;
const STAGES = new Set(['jev', 'judge']);
const HANDLINGS = new Set(['answer', 'converse', 'task', 'flow']);
// 先頭のスラッシュ行（`/sm name` など）。起動形は slashroute が決めるので振り分けない。
const SLASH_LINE = /^\/[a-z0-9][a-z0-9._-]*(?:[ \t]|$)/;

// 振り分けを呼ばない理由（LLM の前に決定的に決まるもの）。'' なら呼ぶ。
function skipReason({ text = '', mode = 'auto', skillMode = 'auto', quickRequests = [] } = {}) {
  if (mode === 'off') return 'off';
  if (skillMode === 'manual') return 'manual-skills';
  const body = String(text || '').trim();
  if (!body) return 'empty';
  if (SLASH_LINE.test(body)) return 'slash';
  if ((Array.isArray(quickRequests) ? quickRequests : []).some((item) => item && String(item.text || '').trim() === body)) return 'quick';
  return '';
}

function ranked(text, items, limit) {
  return (Array.isArray(items) ? items : [])
    .filter((item) => item && item.name)
    .map((item) => ({ item, score: skillSelection.relevance(text, { name: item.name, description: item.description, tags: item.tags || [] }) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || String(a.item.name).localeCompare(String(b.item.name)))
    .slice(0, limit)
    .map((entry) => entry.item);
}

// 候補の 1 枚（agent-herd route の --candidates）。tasks / flows は名前と説明の一致で上位だけ、
// skills は自動選択の候補から、依頼に名前が出ているもの（明示。判定に訊かない）を除いて上位だけ。
function candidates({ text = '', tasks = [], flows = [], skills = [], repo = '', attachments = [], readonly = false } = {}) {
  const explicit = new Set(skillSelection.mentioned(text, skills).map((skill) => skill.name));
  const trim = (item) => ({ id: String(item.id), name: String(item.name || item.id), description: String(item.description || '').slice(0, 200) });
  return {
    tasks: ranked(text, tasks, LIMITS.tasks).map(trim),
    flows: ranked(text, flows, LIMITS.flows).map(trim),
    skills: ranked(text, skills.filter((skill) => !explicit.has(skill.name)), LIMITS.skills)
      .map((skill) => ({ name: String(skill.name), description: String(skill.description || '').slice(0, 200) })),
    context: { repo: String(repo || ''), attachments: attachments.map(String).filter(Boolean), readonly: !!readonly },
  };
}

const EMPTY = { decided: false, stage: null, handling: null, task: null, flow: null, target: null, skills: null, routine: null, hold: false };

function validate(value, cands) {
  if (!value || typeof value !== 'object' || !STAGES.has(value.stage)) return { ...EMPTY, reason: 'invalid' };
  const number = (raw) => (Number.isFinite(Number(raw)) ? Number(raw) : null);
  const handling = value.handling && HANDLINGS.has(value.handling.choice)
    ? { choice: value.handling.choice, confidence: number(value.handling.confidence) } : null;
  const pick = (answer, list) => {
    const found = answer && list.find((item) => item.id === answer.choice);
    return found ? { id: found.id, name: found.name, confidence: number(answer.confidence) } : null;
  };
  const task = pick(value.task, cands.tasks);
  const flow = pick(value.flow, cands.flows);
  const skills = (Array.isArray(value.skills) ? value.skills : [])
    .filter((item) => item && cands.skills.some((skill) => skill.name === item.name))
    .map((item) => ({ name: item.name, probability: number(item.probability) || 0 }));
  const routine = value.routine && typeof value.routine.value === 'boolean'
    ? { value: value.routine.value, probability: number(value.routine.probability) } : null;
  const target = handling && handling.choice === 'task' ? task : handling && handling.choice === 'flow' ? flow : null;
  return { decided: true, stage: value.stage, handling, task, flow, target, skills, routine, hold: !!(value.hold && target), reason: '' };
}

// agent-herd route を起こす。候補はファイルで渡す（説明文に引用符や改行があっても argv の
// 引用に依存しない）。file はこの PC のパス、toHostPath は CLI が動く側（Windows なら WSL）の表記。
async function route({ text, candidates: cands, cwd, capture, signal, file, toHostPath = (p) => p }) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(cands), 'utf8');
  let result;
  try {
    result = await capture('agent-herd', ['route', '--candidates', toHostPath(file)], { cwd, input: text, signal, timeoutMs: TIMEOUT_MS });
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return { ...EMPTY, reason: 'unavailable' };
  } finally {
    try { fs.unlinkSync(file); } catch { /* 消えていればよい */ }
  }
  if (signal?.aborted) return { ...EMPTY, reason: 'aborted' };
  let value;
  try { value = JSON.parse(result?.stdout); } catch { /* 旧版や失敗の出力は答えではない */ }
  if (!result?.ok) {
    const noise = `${result?.stderr || ''} ${result?.error || ''}`;
    const unavailable = result?.status === 127 || result?.status === 2
      || /ENOENT|command not found|not recognized|No module named|未知のサブコマンド/i.test(noise);
    return { ...EMPTY, reason: unavailable ? 'unavailable' : String((value && value.reason) || result?.error || 'undecided') };
  }
  return validate(value, cands);
}

// ---- 流用時の入力値（実行条件）を依頼から写す ---------------------------------------------
// 日付の語は決定的に写す（タスク画面の自動入力と同じ `@date:*`）。残りのキーだけ、ローカル LLM の
// `extract`（`--format json`。文法で JSON オブジェクトを強制）に「依頼文の言葉をそのまま」写させ、
// 宣言にあるキーの文字列だけを受ける。値の採否は機械、確認は人（実行のボタンは押さない）。
const DATE_WORDS = [['@date:previous-month', /前月|先月/], ['@date:month', /今月/], ['@date:yesterday', /昨日/], ['@date:today', /今日|本日/]];
const DATE_KEY = /date|day|month|week|period|期間|日付|年月|月|日/i;

function dateWord(text) {
  const hit = DATE_WORDS.find(([, re]) => re.test(String(text || '')));
  return hit ? hit[0] : '';
}

function extractionPrompt(text, keys) {
  return ['依頼文から次の入力値を抜き出し、JSON オブジェクトだけを返す。', `キー: ${keys.join(', ')}`,
    '依頼文に書かれていないキーは null にする。値は依頼文の言葉をそのまま短く写し、推測で補わない。',
    '', '依頼文:', String(text || '')].join('\n');
}

async function extractInputs({ text = '', parameters = [], cwd, capture, signal } = {}) {
  const keys = [...new Set((Array.isArray(parameters) ? parameters : []).map(String).filter(Boolean))];
  const values = {};
  if (!keys.length) return values;
  const fromText = dateWord(text);
  const rest = [];
  for (const key of keys) {
    if (fromText && DATE_KEY.test(key)) values[key] = fromText;
    else rest.push(key);
  }
  if (!rest.length || typeof capture !== 'function') return values;
  let result;
  try {
    result = await capture('agent-herd', ['--purpose', 'extract', '--readonly', '-p', extractionPrompt(text, rest)], { cwd, signal, timeoutMs: TIMEOUT_MS });
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return values;
  }
  if (!result?.ok || signal?.aborted) return values;
  let parsed;
  try {
    const raw = String(result.stdout || '');
    parsed = JSON.parse(raw.slice(raw.indexOf('{')));
  } catch { return values; }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return values;
  for (const key of rest) {
    const value = parsed[key];
    if (value == null || typeof value === 'object' || typeof value === 'boolean') continue;
    const str = String(value).trim().slice(0, 200);
    if (!str || /^(null|none|不明|なし)$/i.test(str)) continue;
    values[key] = DATE_KEY.test(key) && dateWord(str) ? dateWord(str) : str;
  }
  return values;
}

const DATE_LABELS = { '@date:today': '今日', '@date:yesterday': '昨日', '@date:month': '今月', '@date:previous-month': '前月' };

function inputsLine(inputs) {
  const pairs = Object.entries(inputs || {}).map(([key, value]) => `${key}=${DATE_LABELS[value] || value}`);
  return pairs.length ? { type: 'status', title: `入力：${pairs.join(' · ')}`, status: 'success', detail: '依頼から写した値。タスクを開いて確認してから実行' } : null;
}

const METHOD = { jev: 'Jev', judge: 'ローカル判定' };

function information(result) {
  if (!result.decided) {
    return { type: 'status', title: '振り分け：決めず（会話で実行）', status: 'success', detail: result.reason ? `理由：${result.reason}` : '' };
  }
  const handling = result.handling;
  const label = !handling ? '読み取り専用のまま'
    : handling.choice === 'answer' ? '答えるだけ（実行しない）'
      : handling.choice === 'converse' ? '会話で実行'
        : result.target ? `${handling.choice === 'flow' ? 'ワークフロー' : 'タスク'}「${result.target.name}」を流用できます`
          : '会話で実行（流用先を決められず）';
  const confidence = handling && handling.confidence != null ? ` ${handling.confidence.toFixed(2)}` : '';
  return { type: 'status', title: `振り分け：${label}`, status: 'success', detail: `選択方法：${METHOD[result.stage] || result.stage}${confidence}` };
}

function routineInformation() {
  return { type: 'status', title: '繰り返せる依頼です。••• → この作業を定型化', status: 'success' };
}

// 会話を止めたときに会話へ残す案内（役割 routing）。本文と添付は入力欄に残るので、案内が持つのは
// 開く先と、そのまま会話で実行するときの本文だけ。
function heldMessage(result, { text = '', attachments = [], inputs = {} } = {}) {
  const kind = result.handling.choice;
  const noun = kind === 'flow' ? 'ワークフロー' : 'タスク';
  const name = result.target.name;
  const values = inputs && typeof inputs === 'object' ? inputs : {};
  return {
    message: {
      role: 'routing', text: `${noun}「${name}」を流用できます。`,
      parts: { information: [information(result), inputsLine(values)].filter(Boolean) },
      routing: { kind, id: result.target.id, name, request: String(text), attachments: Array.isArray(attachments) ? attachments : [], inputs: values },
    },
    notice: `${noun}「${name}」を流用できます（入力は保持）`,
  };
}

module.exports = { LIMITS, TIMEOUT_MS, skipReason, candidates, validate, route, information, routineInformation, heldMessage, dateWord, extractionPrompt, extractInputs, inputsLine };
