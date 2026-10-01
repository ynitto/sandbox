'use strict';
// テストケースファイル（YAML / JSON）の読み込みと検査。
// 書式の説明は src/format-reference.md（エージェントへのプロンプトにもそのまま入る）。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

// 画面を操作するステップ（screenshot: step のとき、この後に撮る）
const ACTION_STEPS = ['goto', 'click', 'dblclick', 'fill', 'press', 'select', 'check', 'uncheck', 'hover', 'upload', 'eval', 'reload'];
// 待つ・確かめる・撮るステップ
const OTHER_STEPS = ['wait', 'expect', 'screenshot'];
const STEP_KINDS = [...ACTION_STEPS, ...OTHER_STEPS];
// ステップに添えてよい補助キー
const STEP_EXTRA_KEYS = ['note', 'timeout'];

const TARGET_KEYS = ['css', 'role', 'name', 'label', 'text', 'placeholder', 'testId', 'alt', 'title', 'exact', 'nth'];
const EXPECT_KEYS = ['target', 'visible', 'hidden', 'text', 'contains', 'notContains', 'value', 'count', 'enabled', 'disabled', 'checked', 'url', 'title'];
const MOCK_KEYS = ['url', 'method', 'status', 'body', 'json', 'headers', 'contentType', 'delayMs', 'abort', 'times'];
const SCREENSHOT_MODES = ['step', 'failure', 'off'];
const BROWSERS = ['chromium', 'firefox', 'webkit'];

const SUITE_KEYS = ['suite', 'baseUrl', 'browser', 'viewport', 'locale', 'timezone', 'colorScheme', 'screenshot', 'timeout', 'setup', 'variants', 'cases'];
// 同じケースを条件を変えて繰り返す組（言語・画面幅など）
const VARIANT_KEYS = ['name', 'locale', 'timezone', 'viewport', 'colorScheme', 'localStorage', 'sessionStorage', 'headers'];
const SETUP_KEYS = ['localStorage', 'sessionStorage', 'cookies', 'mocks', 'steps', 'headers'];
const CASE_KEYS = ['id', 'title', 'requirement', 'tags', 'skip', 'envs', 'variants', 'viewport', 'locale', 'timezone', 'colorScheme', ...SETUP_KEYS];

function parseText(text, file = '') {
  const ext = path.extname(file).toLowerCase();
  if (ext === '.json') return JSON.parse(text);
  return YAML.parse(text);
}

function loadFile(file) {
  const text = fs.readFileSync(file, 'utf8');
  let data;
  try {
    data = parseText(text, file);
  } catch (e) {
    return { suite: null, errors: [`${file}: 読み込めません: ${e.message}`] };
  }
  const { suite, errors } = normalize(data);
  if (suite) suite.file = path.resolve(file);
  return { suite, errors: errors.map((e) => `${file}: ${e}`) };
}

// ディレクトリなら中の *.yaml / *.yml / *.json（webui-test-results などは除く）を名前順に集める
function collectFiles(inputs) {
  const out = [];
  for (const input of inputs) {
    const stat = fs.statSync(input);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(input).sort()) {
        const p = path.join(input, name);
        if (name.startsWith('.') || name === 'node_modules' || name === 'webui-test-results' || /^webui-test\.config\.(ya?ml|json)$/.test(name)) continue;
        if (fs.statSync(p).isDirectory()) out.push(...collectFiles([p]));
        else if (/\.(ya?ml|json)$/i.test(name)) out.push(p);
      }
    } else {
      out.push(input);
    }
  }
  return out;
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function unknownKeys(obj, allowed, where, errors) {
  for (const k of Object.keys(obj)) {
    if (!allowed.includes(k)) errors.push(`${where}: 知らないキー「${k}」（使えるのは ${allowed.join(', ')}）`);
  }
}

function checkTarget(t, where, errors) {
  if (typeof t === 'string') {
    if (!t.trim()) errors.push(`${where}: 対象が空です`);
    return;
  }
  if (!isPlainObject(t)) {
    errors.push(`${where}: 対象は文字列（セレクタ）か { role, name } / { label } / { text } などのオブジェクトで書きます`);
    return;
  }
  unknownKeys(t, TARGET_KEYS, where, errors);
  const primary = ['css', 'role', 'label', 'text', 'placeholder', 'testId', 'alt', 'title'].filter((k) => t[k] !== undefined);
  if (primary.length !== 1) errors.push(`${where}: 対象の指定（css / role / label / text / placeholder / testId / alt / title）はちょうど 1 つ書きます`);
}

function checkMocks(mocks, where, errors) {
  if (!Array.isArray(mocks)) {
    errors.push(`${where}.mocks: 配列で書きます`);
    return;
  }
  mocks.forEach((m, i) => {
    const w = `${where}.mocks[${i}]`;
    if (!isPlainObject(m)) return errors.push(`${w}: オブジェクトで書きます`);
    unknownKeys(m, MOCK_KEYS, w, errors);
    if (typeof m.url !== 'string' || !m.url) errors.push(`${w}: url（例: "**/api/users"）が要ります`);
    if (m.status !== undefined && !Number.isInteger(m.status)) errors.push(`${w}: status は整数です`);
  });
}

function checkStep(step, where, errors) {
  if (!isPlainObject(step)) return errors.push(`${where}: ステップは「- click: ...」のような 1 つのキーを持つオブジェクトです`);
  const kinds = Object.keys(step).filter((k) => STEP_KINDS.includes(k));
  const extra = Object.keys(step).filter((k) => !STEP_KINDS.includes(k) && !STEP_EXTRA_KEYS.includes(k));
  if (extra.length) errors.push(`${where}: 知らないキー「${extra.join(', ')}」（ステップは ${STEP_KINDS.join(', ')}）`);
  if (kinds.length !== 1) return errors.push(`${where}: ステップの種類はちょうど 1 つ書きます`);
  const kind = kinds[0];
  const v = step[kind];
  const w = `${where}.${kind}`;
  switch (kind) {
    case 'goto':
      if (typeof v !== 'string' || !v) errors.push(`${w}: URL かパスを書きます`);
      break;
    case 'reload':
      break;
    case 'click': case 'dblclick': case 'check': case 'uncheck': case 'hover':
      checkTarget(v, w, errors);
      break;
    case 'fill': case 'select':
      if (!isPlainObject(v)) { errors.push(`${w}: { target, value } で書きます`); break; }
      unknownKeys(v, ['target', 'value'], w, errors);
      checkTarget(v.target, `${w}.target`, errors);
      if (v.value === undefined) errors.push(`${w}: value が要ります`);
      break;
    case 'press':
      if (typeof v === 'string') break;
      if (!isPlainObject(v) || typeof v.key !== 'string') { errors.push(`${w}: キー名（例: Enter）か { target, key } で書きます`); break; }
      unknownKeys(v, ['target', 'key'], w, errors);
      if (v.target !== undefined) checkTarget(v.target, `${w}.target`, errors);
      break;
    case 'upload':
      if (!isPlainObject(v)) { errors.push(`${w}: { target, files } で書きます`); break; }
      checkTarget(v.target, `${w}.target`, errors);
      if (!v.files) errors.push(`${w}: files が要ります`);
      break;
    case 'eval':
      if (typeof v !== 'string') errors.push(`${w}: ページで実行する JavaScript を文字列で書きます`);
      break;
    case 'wait':
      if (typeof v === 'number') break;
      if (isPlainObject(v) && (v.url !== undefined || v.load !== undefined || v.hidden !== undefined || v.visible !== undefined)) {
        unknownKeys(v, ['url', 'load', 'visible', 'hidden'], w, errors);
        if (v.visible !== undefined) checkTarget(v.visible, `${w}.visible`, errors);
        if (v.hidden !== undefined) checkTarget(v.hidden, `${w}.hidden`, errors);
        if (v.load !== undefined && !['load', 'domcontentloaded', 'networkidle'].includes(v.load)) errors.push(`${w}.load: load / domcontentloaded / networkidle のどれかです`);
        break;
      }
      checkTarget(v, w, errors); // 対象が見えるまで待つ
      break;
    case 'expect': {
      if (!isPlainObject(v)) { errors.push(`${w}: { visible: 対象 } や { target, text } で書きます`); break; }
      unknownKeys(v, EXPECT_KEYS, w, errors);
      const checks = EXPECT_KEYS.filter((k) => k !== 'target' && v[k] !== undefined);
      if (!checks.length) errors.push(`${w}: 確かめる内容（visible / hidden / text / contains / notContains / value / count / enabled / disabled / checked / url / title）が要ります`);
      if (v.visible !== undefined && v.visible !== true) checkTarget(v.visible, `${w}.visible`, errors);
      if (v.hidden !== undefined && v.hidden !== true) checkTarget(v.hidden, `${w}.hidden`, errors);
      const needsTarget = ['text', 'contains', 'notContains', 'value', 'count', 'enabled', 'disabled', 'checked'].filter((k) => v[k] !== undefined);
      const needsTargetBool = (v.visible === true || v.hidden === true);
      if ((needsTarget.length || needsTargetBool) && v.target === undefined) errors.push(`${w}: ${[...needsTarget, needsTargetBool ? 'visible/hidden: true' : ''].filter(Boolean).join(', ')} には target が要ります`);
      if (v.target !== undefined) checkTarget(v.target, `${w}.target`, errors);
      if (v.count !== undefined && !Number.isInteger(v.count)) errors.push(`${w}.count: 整数です`);
      break;
    }
    case 'screenshot':
      if (typeof v === 'string' && v) break;
      if (!isPlainObject(v)) { errors.push(`${w}: 名前（文字列）か { name, fullPage, target, path } で書きます`); break; }
      unknownKeys(v, ['name', 'fullPage', 'target', 'path', 'mask'], w, errors);
      if (typeof v.name !== 'string' || !v.name) errors.push(`${w}: name が要ります`);
      if (v.target !== undefined) checkTarget(v.target, `${w}.target`, errors);
      if (v.mask !== undefined) {
        if (!Array.isArray(v.mask)) errors.push(`${w}.mask: 対象の配列で書きます`);
        else v.mask.forEach((m, i) => checkTarget(m, `${w}.mask[${i}]`, errors));
      }
      break;
    default:
      break;
  }
  if (step.timeout !== undefined && !(Number.isFinite(step.timeout) && step.timeout > 0)) errors.push(`${where}.timeout: ミリ秒の正の数です`);
}

function checkSetupLike(obj, where, errors) {
  for (const key of ['localStorage', 'sessionStorage', 'headers']) {
    if (obj[key] !== undefined && !isPlainObject(obj[key])) errors.push(`${where}.${key}: キーと値の組で書きます`);
  }
  if (obj.cookies !== undefined && !Array.isArray(obj.cookies)) errors.push(`${where}.cookies: 配列で書きます`);
  if (obj.mocks !== undefined) checkMocks(obj.mocks, where, errors);
  if (obj.steps !== undefined) {
    if (!Array.isArray(obj.steps)) errors.push(`${where}.steps: 配列で書きます`);
    else obj.steps.forEach((s, i) => checkStep(s, `${where}.steps[${i}]`, errors));
  }
}

function checkViewport(v, where, errors) {
  if (v === undefined) return;
  if (!isPlainObject(v) || !Number.isInteger(v.width) || !Number.isInteger(v.height)) errors.push(`${where}.viewport: { width, height } を整数で書きます`);
}

// 読んだデータを検査し、既定値を補った suite を返す。エラーがあれば errors に並べる。
function normalize(data) {
  const errors = [];
  if (!isPlainObject(data)) return { suite: null, errors: ['トップレベルは suite / cases を持つオブジェクトです'] };
  unknownKeys(data, SUITE_KEYS, 'トップレベル', errors);
  if (!Array.isArray(data.cases) || data.cases.length === 0) errors.push('cases: テストケースを 1 つ以上書きます');
  if (data.browser !== undefined && !BROWSERS.includes(data.browser)) errors.push(`browser: ${BROWSERS.join(' / ')} のどれかです`);
  if (data.screenshot !== undefined && !SCREENSHOT_MODES.includes(data.screenshot)) errors.push(`screenshot: ${SCREENSHOT_MODES.join(' / ')} のどれかです`);
  if (data.timeout !== undefined && !(Number.isFinite(data.timeout) && data.timeout > 0)) errors.push('timeout: ミリ秒の正の数です');
  checkViewport(data.viewport, 'トップレベル', errors);
  if (data.setup !== undefined) {
    if (!isPlainObject(data.setup)) errors.push('setup: オブジェクトで書きます');
    else { unknownKeys(data.setup, SETUP_KEYS, 'setup', errors); checkSetupLike(data.setup, 'setup', errors); }
  }
  const variantNames = new Set();
  if (data.variants !== undefined) {
    if (!Array.isArray(data.variants) || !data.variants.length) errors.push('variants: 配列で 1 つ以上書きます');
    else data.variants.forEach((v, i) => {
      const w = `variants[${i}]`;
      if (!isPlainObject(v)) return errors.push(`${w}: オブジェクトで書きます`);
      unknownKeys(v, VARIANT_KEYS, w, errors);
      if (typeof v.name !== 'string' || !v.name) errors.push(`${w}: name（例: ja / en-narrow）が要ります`);
      else if (variantNames.has(v.name)) errors.push(`${w}: name「${v.name}」が重複しています`);
      else variantNames.add(v.name);
      checkViewport(v.viewport, w, errors);
      checkSetupLike(v, w, errors);
    });
  }
  const ids = new Set();
  (Array.isArray(data.cases) ? data.cases : []).forEach((c, i) => {
    const where = `cases[${i}]`;
    if (!isPlainObject(c)) return errors.push(`${where}: オブジェクトで書きます`);
    unknownKeys(c, CASE_KEYS.concat(['steps']), where, errors);
    if (typeof c.id !== 'string' && typeof c.id !== 'number') errors.push(`${where}: id（例: TC-001）が要ります`);
    else if (ids.has(String(c.id))) errors.push(`${where}: id「${c.id}」が重複しています`);
    else ids.add(String(c.id));
    if (typeof c.title !== 'string' || !c.title) errors.push(`${where}: title が要ります`);
    if (!Array.isArray(c.steps) || c.steps.length === 0) errors.push(`${where}: steps を 1 つ以上書きます`);
    checkViewport(c.viewport, where, errors);
    checkSetupLike(c, where, errors);
    if (c.envs !== undefined && (!Array.isArray(c.envs) || !c.envs.every((x) => typeof x === 'string'))) errors.push(`${where}.envs: 環境名の配列で書きます（例: [local]）`);
    if (c.variants !== undefined) {
      if (!Array.isArray(c.variants)) errors.push(`${where}.variants: variants の name の配列で書きます`);
      else for (const n of c.variants) if (!variantNames.has(n)) errors.push(`${where}.variants: 「${n}」は variants にありません`);
    }
  });
  if (errors.length) return { suite: null, errors };

  const suite = {
    suite: data.suite ? String(data.suite) : 'テスト',
    baseUrl: data.baseUrl || '',
    browser: data.browser || 'chromium',
    viewport: data.viewport || { width: 1280, height: 800 },
    locale: data.locale,
    timezone: data.timezone,
    colorScheme: data.colorScheme,
    screenshot: data.screenshot || 'step',
    timeout: data.timeout || 10000,
    setup: data.setup || {},
    variants: data.variants || null,
    cases: data.cases.map((c) => ({ ...c, id: String(c.id) })),
  };
  return { suite, errors: [] };
}

function stepKind(step) {
  return Object.keys(step).find((k) => STEP_KINDS.includes(k));
}

module.exports = { isPlainObject, loadFile, collectFiles, normalize, parseText, stepKind, ACTION_STEPS, STEP_KINDS };
