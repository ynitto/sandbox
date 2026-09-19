'use strict';

// 設定 > 実行制御「遷移や振り分けの判定」。
//
// 値はこのアプリの config.json ではなく、agent-herd の設定ファイル（python が動く側の
// ~/.agents/agent-herd.yaml。Windows では WSL の home）にある——判定 AI を呼ぶのは
// agent-herd / agent-loop / agent-flow で、あちらが読める場所でなければ意味が無い。
// だから読み書きは `agent-herd config` に頼み、このモジュールはその出入りの形だけを持つ。
//
//   auto  … ローカルの AI（aider / ollama）で作業しているときだけ判定 AI を使う（agent-herd の既定）
//   model … いつも、指定したローカルモデルの判定 AI を使う（クラウドの AI で作業していても判定だけ逃がす）
//   off   … 判定 AI を使わない

const MODES = ['auto', 'model', 'off'];
const DEFAULT_MODEL = 'gemma4:e4b';

function normalize(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const mode = MODES.includes(source.mode) ? source.mode : 'auto';
  const model = String(source.model || '').trim();
  return { mode: mode === 'model' && !model ? 'auto' : mode, model: mode === 'model' ? model : '' };
}

// `agent-herd config --json` の出力（{"path", "judge": {"mode": auto|pinned|off, "model"}}）を画面の値へ。
function parseStatus(stdout) {
  const raw = String(stdout || '').trim();
  let parsed;
  try {
    parsed = JSON.parse(raw.slice(raw.indexOf('{')));
  } catch {
    throw new Error('agent-herd の設定を読み取れませんでした');
  }
  const judge = parsed && parsed.judge && typeof parsed.judge === 'object' ? parsed.judge : {};
  if (judge.mode === 'pinned') return normalize({ mode: 'model', model: judge.model });
  if (judge.mode === 'off') return normalize({ mode: 'off' });
  return normalize({ mode: 'auto' });
}

// 画面の値を `agent-herd config set judge.model <値>` の引数へ。
function setArgs(value) {
  const v = normalize(value);
  const word = v.mode === 'model' ? v.model : v.mode;
  return ['config', 'set', 'judge.model', word];
}

function firstLine(res) {
  return (String((res && (res.stderr || res.stdout)) || '').trim().split(/\r?\n/).find(Boolean) || '').slice(0, 160);
}

// capture(command, args, { timeoutMs }) → { ok, status, stdout, stderr, error }
async function read({ capture } = {}) {
  if (typeof capture !== 'function') throw new Error('agent-herd を起こす実行関数がありません');
  const res = await capture('agent-herd', ['config', '--json'], { timeoutMs: 20000 });
  if (!res || !res.ok) {
    return { available: false, value: normalize({}), error: (res && (res.error || firstLine(res))) || 'agent-herd を起動できません' };
  }
  return { available: true, value: parseStatus(res.stdout), error: '' };
}

async function write({ capture, value } = {}) {
  if (typeof capture !== 'function') throw new Error('agent-herd を起こす実行関数がありません');
  const res = await capture('agent-herd', setArgs(value), { timeoutMs: 20000 });
  if (!res || !res.ok) {
    throw new Error(`判定の設定を保存できません: ${(res && (res.error || firstLine(res))) || 'agent-herd を起動できません'}`);
  }
  return normalize(value);
}

module.exports = { MODES, DEFAULT_MODEL, normalize, parseStatus, setArgs, read, write };
