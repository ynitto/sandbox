'use strict';

// 道具の確認と、statemachine-use スキルの所在。LLM を使わない診断コマンドだけを呼ぶ。
//   playwright-cli --version      … ブラウザ操作（記録・実行）
//   winauto doctor --output json  … Windows アプリ操作（Windows 上でのみ意味がある）
//   python --version              … スキルのスクリプト（検証・実行）を動かす
// スキルのスクリプトは「選んだフォルダから上へ辿って .github/skills/statemachine-use を探す →
// このアプリが置かれたリポジトリ → 設定で指定したパス」の順で見つける。

const fs = require('fs');
const path = require('path');

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

// `capture(command, args, { cwd, timeoutMs })` → { ok, status, stdout, stderr, error }
async function toolStatus({ cwd = '', capture, skillDir = '' } = {}) {
  if (typeof capture !== 'function') throw new Error('道具の確認に使う実行関数がありません');
  const out = [];
  const py = await findPython(capture);
  out.push({
    id: 'python', label: 'Python（スキルのスクリプトを動かす）', ok: !!py,
    summary: py ? `利用可能（${py.command}: ${py.version}）` : 'python / python3 を起動できません',
    hint: py ? '' : 'Python 3 を入れて PATH に通してください。検証・実行はスキルの Python スクリプトが担います。',
  });
  out.push({
    id: 'skill', label: 'statemachine-use スキル（scripts/run_machine.py）', ok: !!skillDir,
    summary: skillDir ? `見つかりました（${skillDir}）` : '見つかりません',
    hint: skillDir ? '' : '選んだフォルダの上位に .github/skills/statemachine-use が無いときは、設定でスキルのフォルダを指定してください。',
  });
  const pw = await capture('playwright-cli', ['--version'], { cwd, timeoutMs: 20000 });
  out.push({
    id: 'playwright-cli', label: 'ブラウザ操作（playwright-cli）', ok: !!(pw && pw.ok),
    summary: pw && pw.ok ? `利用可能（${firstLine(pw) || 'version 不明'}）` : `起動できません: ${(pw && (pw.error || firstLine(pw))) || 'playwright-cli'}`,
    hint: pw && pw.ok ? '' : '`npm install -g @playwright/cli` で入れます（ブラウザの実体は `playwright-cli install-browser`）。',
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

module.exports = { SKILL_REL, findSkillDir, findPython, toolStatus, summarizeDoctor, isDir };
