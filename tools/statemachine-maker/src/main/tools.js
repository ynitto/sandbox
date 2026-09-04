'use strict';

// 実行環境の確認と、statemachine-use スキルの所在。LLM を使わない診断コマンドだけを呼ぶ。
//   playwright-cli --version      … ブラウザ操作（記録・実行）
//   winauto doctor --output json  … Windows アプリ操作（Windows 上でのみ意味がある）
//   python --version              … スキルの構成確認スクリプトを動かす
//   agent-herd defs --json        … agent-tools の AI 定義を列挙する
// スキルのスクリプトは「選んだフォルダから上へ辿って .github/skills/statemachine-use を探す →
// このアプリが置かれたリポジトリ → 設定で指定したパス」の順で見つける。

const fs = require('fs');
const path = require('path');
const recording = require('./recording');

const SKILL_REL = path.join('.github', 'skills', 'statemachine-use');

function isFile(p) {
  try { return fs.statSync(p).isFile(); } catch { return false; }
}

function isDir(p) {
  try { return fs.statSync(p).isDirectory(); } catch { return false; }
}

// スキルのフォルダを探す。見つかった順に 1 つ返す（無ければ ''）。
function findSkillDir({ root = '', configured = '', appRoot = '' } = {}) {
  const candidates = [];
  if (configured) candidates.push(configured);
  let cur = root ? path.resolve(root) : '';
  for (let i = 0; cur && i < 8; i += 1) {
    candidates.push(path.join(cur, SKILL_REL));
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  if (appRoot) candidates.push(path.resolve(appRoot, '..', '..', SKILL_REL));
  for (const c of candidates) {
    if (c && isFile(path.join(c, 'scripts', 'run_machine.py'))) return c;
  }
  return '';
}

const PYTHONS = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python'];

async function findPython(capture) {
  for (const cmd of PYTHONS) {
    const res = await capture(cmd, ['--version'], { timeoutMs: 10000 });
    if (res && res.ok) return { command: cmd, version: String(res.stdout || res.stderr || '').trim() };
  }
  return null;
}

function summarizeDoctor(stdout, stderr) {
  const raw = String(stdout || '').trim();
  try {
    const obj = JSON.parse(raw.slice(raw.indexOf('{')));
    const checks = Array.isArray(obj.checks) ? obj.checks : [];
    const failed = checks.filter((c) => c && c.ok === false);
    if (obj.ok === true && !failed.length) return { ok: true, summary: `診断 ${checks.length} 項目すべて OK` };
    const names = failed.map((c) => String(c.name || c.id || c.detail || c.message || '')).filter(Boolean);
    return { ok: false, summary: names.length ? `不備: ${names.join(' / ')}` : '診断に失敗した項目があります' };
  } catch {
    const line = (String(stderr || '').trim() || raw).split(/\r?\n/).find(Boolean) || '';
    return { ok: false, summary: line.slice(0, 200) || '診断の出力を読めませんでした' };
  }
}

function firstLine(res) {
  return (String((res && (res.stdout || res.stderr)) || '').trim().split(/\r?\n/).find(Boolean) || '').slice(0, 120);
}

async function agentDefinitions({ cwd = '', capture } = {}) {
  if (typeof capture !== 'function') throw new Error('agent-tools の定義を確認する実行関数がありません');
  const res = await capture('agent-herd', ['defs', '--json'], { cwd, timeoutMs: 20000 });
  if (!res || !res.ok) {
    throw new Error(`agent-tools（agent-herd）を起動できません: ${(res && (res.error || firstLine(res))) || '原因不明'}`);
  }
  let parsed;
  try {
    parsed = JSON.parse(String(res.stdout || ''));
  } catch {
    throw new Error('agent-tools の定義一覧を読み取れませんでした');
  }
  const definitions = Array.isArray(parsed.definitions) ? parsed.definitions : [];
  return [...new Set(definitions.map((name) => String(name || '').trim()).filter(Boolean))];
}

function agentHerdRunSpec({ workflow, root, agent, model = '', input = '', context = {} } = {}) {
  const args = [
    'harness', 'statemachine',
    '--workflow', String(workflow || ''),
    '--agent-cli', String(agent || ''),
    '--dir', String(root || ''),
  ];
  if (model) args.push('--model', String(model));
  if (input) args.push('--input', String(input));
  for (const [key, value] of Object.entries(context || {})) args.push('--param', `${key}=${value}`);
  return { command: 'agent-herd', args };
}

// `capture(command, args, { cwd, timeoutMs })` → { ok, status, stdout, stderr, error }
async function toolStatus({ cwd = '', capture, skillDir = '' } = {}) {
  if (typeof capture !== 'function') throw new Error('道具の確認に使う実行関数がありません');
  const out = [];
  const py = await findPython(capture);
  out.push({
    id: 'python', label: 'Python（構成確認）', ok: !!py,
    summary: py ? `利用可能（${py.command}: ${py.version}）` : 'python / python3 を起動できません',
    hint: py ? '' : 'Python 3 を入れて PATH に通してください。構成確認に使用します。',
  });
  out.push({
    id: 'skill', label: 'statemachine-use スキル（scripts/run_machine.py）', ok: !!skillDir,
    summary: skillDir ? `見つかりました（${skillDir}）` : '見つかりません',
    hint: skillDir ? '' : '選んだフォルダの上位に .github/skills/statemachine-use が無いときは、設定でスキルのフォルダを指定してください。',
  });
  try {
    const definitions = await agentDefinitions({ cwd, capture });
    out.push({
      id: 'agent-tools', label: 'AI 実行（agent-tools）', ok: definitions.length > 0,
      summary: definitions.length ? `${definitions.length} 件の AI 定義を利用できます` : 'AI 定義がありません',
      hint: definitions.length ? '' : 'agents/*.json に AI 定義を追加してください。',
    });
  } catch (err) {
    out.push({
      id: 'agent-tools', label: 'AI 実行（agent-tools）', ok: false,
      summary: err.message,
      hint: 'tools/agent-tools/install.sh を実行し、agent-herd を PATH に通してください。',
    });
  }
  const pw = await capture('playwright-cli', ['--version'], { cwd, timeoutMs: 20000 });
  const pwHelp = pw && pw.ok
    ? await capture('playwright-cli', ['--help'], { cwd, timeoutMs: 20000 })
    : null;
  const pwVersion = firstLine(pw) || 'version 不明';
  const pwHelpOk = !!(pwHelp && pwHelp.ok);
  const pwRecording = pwHelpOk
    && recording.supportsPlaywrightRecording(`${pwHelp.stdout || ''}\n${pwHelp.stderr || ''}`);
  let pwSummary;
  let pwHint;
  if (!pw || !pw.ok) {
    pwSummary = `起動できません: ${(pw && (pw.error || firstLine(pw))) || 'playwright-cli'}`;
    pwHint = '`npm install -g @playwright/cli@latest` で入れます（ブラウザの実体は `playwright-cli install-browser`）。';
  } else if (!pwHelpOk) {
    pwSummary = `利用できません（${pwVersion}: 操作の記録機能を確認できません）`;
    pwHint = '`npm install -g @playwright/cli@latest` で更新してから、もう一度確認してください。';
  } else if (!pwRecording) {
    pwSummary = `利用できません（${pwVersion}: 操作の記録に未対応）`;
    pwHint = '`npm install -g @playwright/cli@latest` で更新してから、もう一度確認してください。';
  } else {
    pwSummary = `利用可能（${pwVersion}）`;
    pwHint = '';
  }
  out.push({
    id: 'playwright-cli', label: 'ブラウザ操作（playwright-cli）', ok: !!(pw && pw.ok && pwRecording),
    summary: pwSummary,
    hint: pwHint,
  });
  if (process.platform === 'win32') {
    const wa = await capture('winauto', ['doctor', '--output', 'json'], { cwd, timeoutMs: 30000 });
    const verdict = wa && !wa.error ? summarizeDoctor(wa.stdout, wa.stderr) : { ok: false, summary: `起動できません: ${(wa && wa.error) || 'winauto'}` };
    out.push({
      id: 'winauto', label: 'Windows アプリ操作（winauto）', ok: verdict.ok, summary: verdict.summary,
      hint: verdict.ok ? '' : 'tools/winauto/install.py を実行し、winauto doctor で確認します。',
    });
  } else {
    out.push({
      id: 'winauto', label: 'Windows アプリ操作（winauto）', ok: false,
      summary: 'この OS では使えません（Windows 上でのみ動きます）',
      hint: 'Windows アプリの工程を含む定義は Windows 上で実行してください。記録は別の端末で取って貼り付けられます。',
    });
  }
  return out;
}

module.exports = {
  SKILL_REL, findSkillDir, findPython, agentDefinitions, agentHerdRunSpec,
  toolStatus, summarizeDoctor, isDir,
};
