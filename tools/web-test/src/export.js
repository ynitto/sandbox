'use strict';
// テストケースファイル（YAML）を Playwright Test の .spec.ts に書き出す。
// 書き出したものは `npx playwright test` でそのまま動き、trace viewer・リトライ・分割実行など
// Playwright Test の機能を使える。組み立ては自前の実行と同じ plan.js を通すので、ケースの意味は変わらない。

const fs = require('fs');
const path = require('path');
const { planSuite } = require('./plan');
const { stepKind, ACTION_STEPS } = require('./casefile');
const { describeStep, slug, resolveUrl } = require('./runner');

const q = (s) => JSON.stringify(String(s));
const isRegex = (s) => typeof s === 'string' && /^\/.*\/[a-z]*$/.test(s);

function regexCode(s) {
  const m = s.match(/^\/(.*)\/([a-z]*)$/);
  return `new RegExp(${q(m[1])}${m[2] ? `, ${q(m[2])}` : ''})`;
}

function escapeRegex(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 空白をまとめて比べる自前の判定に合わせる（Playwright の toHaveText も空白を正規化する）
function textMatcher(s) {
  return isRegex(s) ? regexCode(s) : q(s);
}

function locatorCode(t) {
  if (typeof t === 'string') return `page.locator(${q(t)})`;
  const exact = t.exact !== undefined ? `, exact: ${!!t.exact}` : '';
  const opt = (extra = '') => (extra || exact ? `, { ${[extra, exact.replace(/^, /, '')].filter(Boolean).join(', ')} }` : '');
  let code;
  if (t.css) code = `page.locator(${q(t.css)})`;
  else if (t.role) code = `page.getByRole(${q(t.role)}${opt(t.name !== undefined ? `name: ${q(t.name)}` : '')})`;
  else if (t.label) code = `page.getByLabel(${q(t.label)}${opt()})`;
  else if (t.text) code = `page.getByText(${q(t.text)}${opt()})`;
  else if (t.placeholder) code = `page.getByPlaceholder(${q(t.placeholder)}${opt()})`;
  else if (t.testId) code = `page.getByTestId(${q(t.testId)})`;
  else if (t.alt) code = `page.getByAltText(${q(t.alt)}${opt()})`;
  else if (t.title) code = `page.getByTitle(${q(t.title)}${opt()})`;
  if (t.nth !== undefined) code += `.nth(${Number(t.nth)})`;
  return code;
}

function expectCode(v, T) {
  const out = [];
  const L = v.target !== undefined ? `${locatorCode(v.target)}.first()` : null;
  const t = `{ timeout: ${T} }`;
  if (v.visible !== undefined) out.push(`await expect(${v.visible === true ? L : `${locatorCode(v.visible)}.first()`}).toBeVisible(${t});`);
  if (v.hidden !== undefined) out.push(`await expect(${v.hidden === true ? L : `${locatorCode(v.hidden)}.first()`}).toBeHidden(${t});`);
  if (v.text !== undefined) out.push(`await expect(${L}).toHaveText(${textMatcher(v.text)}, { useInnerText: true, timeout: ${T} });`);
  if (v.contains !== undefined) out.push(`await expect(${L}).toContainText(${textMatcher(v.contains)}, { useInnerText: true, timeout: ${T} });`);
  if (v.notContains !== undefined) out.push(`await expect(${L}).not.toContainText(${textMatcher(v.notContains)}, { useInnerText: true, timeout: ${T} });`);
  if (v.value !== undefined) out.push(`await expect(${L}).toHaveValue(${textMatcher(v.value)}, ${t});`);
  if (v.count !== undefined) out.push(`await expect(${locatorCode(v.target)}).toHaveCount(${Number(v.count)}, ${t});`);
  if (v.enabled !== undefined) out.push(`await expect(${L}).toBeEnabled({ enabled: ${!!v.enabled}, timeout: ${T} });`);
  if (v.disabled !== undefined) out.push(`await expect(${L}).toBeEnabled({ enabled: ${!v.disabled}, timeout: ${T} });`);
  if (v.checked !== undefined) out.push(`await expect(${L}).toBeChecked({ checked: ${!!v.checked}, timeout: ${T} });`);
  if (v.url !== undefined) {
    // パスだけなら「含む」、正規表現か完全な URL ならそのまま（自前の実行と同じ）
    const m = isRegex(v.url) ? regexCode(v.url) : /^[a-z]+:/i.test(v.url) ? q(v.url) : `new RegExp(${q(escapeRegex(v.url))})`;
    out.push(`await expect(page).toHaveURL(${m}, ${t});`);
  }
  if (v.title !== undefined) out.push(`await expect(page).toHaveTitle(${textMatcher(v.title)}, ${t});`);
  return out;
}

function stepCode(step, T, fileDir, baseUrl) {
  const kind = stepKind(step);
  const v = step[kind];
  const timeout = step.timeout || T;
  const opt = `{ timeout: ${timeout} }`;
  switch (kind) {
    case 'goto': return [`await page.goto(${q(baseUrl ? resolveUrl(v, baseUrl) : v)});`];
    case 'reload': return ['await page.reload();'];
    case 'click': case 'dblclick': case 'hover': case 'check': case 'uncheck':
      return [`await ${locatorCode(v)}.${kind}(${opt});`];
    case 'fill': return [`await ${locatorCode(v.target)}.fill(${q(v.value)}, ${opt});`];
    case 'select': return [`await ${locatorCode(v.target)}.selectOption(${Array.isArray(v.value) ? JSON.stringify(v.value.map(String)) : q(v.value)}, ${opt});`];
    case 'press':
      if (typeof v === 'string') return [`await page.keyboard.press(${q(v)});`];
      if (v.target !== undefined) return [`await ${locatorCode(v.target)}.press(${q(v.key)}, ${opt});`];
      return [`await page.keyboard.press(${q(v.key)});`];
    case 'upload': return [`await ${locatorCode(v.target)}.setInputFiles(${JSON.stringify([].concat(v.files).map((f) => path.resolve(fileDir, f)))}, ${opt});`];
    case 'eval': return [`await page.evaluate(${q(v)});`];
    case 'wait':
      if (typeof v === 'number') return [`await page.waitForTimeout(${v});`];
      if (v.url !== undefined) return [`await page.waitForURL(new RegExp(${q(escapeRegex(v.url))}), ${opt});`];
      if (v.load !== undefined) return [`await page.waitForLoadState(${q(v.load)});`];
      if (v.hidden !== undefined) return [`await ${locatorCode(v.hidden)}.first().waitFor({ state: 'hidden', timeout: ${timeout} });`];
      if (v.visible !== undefined) return [`await ${locatorCode(v.visible)}.first().waitFor({ state: 'visible', timeout: ${timeout} });`];
      return [`await ${locatorCode(v)}.first().waitFor({ state: 'visible', timeout: ${timeout} });`];
    case 'expect': return expectCode(v, timeout);
    default: return [];
  }
}

function shotCode(o, variant) {
  const opts = [];
  if (o.fullPage) opts.push('fullPage: true');
  if (o.target !== undefined) opts.push(`target: ${locatorCode(o.target)}.first()`);
  if (o.mask && o.mask.length) opts.push(`mask: [${o.mask.map(locatorCode).join(', ')}]`);
  if (o.path) opts.push(`copyTo: ${q(variant ? o.path.replace(/(\.png)?$/i, `.${slug(variant)}.png`) : o.path)}`);
  return `await shot(page, testInfo, ${q(o.name)}${opts.length ? `, { ${opts.join(', ')} }` : ''});`;
}

function indent(lines, n) {
  const pad = ' '.repeat(n);
  return lines.map((l) => (l ? pad + l : l));
}

function specFor(suite, opts = {}) {
  const { env, baseUrl } = opts;
  const runs = planSuite(suite, { env, baseUrl });
  const fileDir = suite.file ? path.dirname(suite.file) : process.cwd();
  const lines = [
    `// web-test export で ${suite.file ? path.basename(suite.file) : 'テストケース'} から作成。元の YAML を直して書き出し直す（このファイルは手で直さない）`,
    "import { test, expect } from '@playwright/test';",
    "import { shot, prepare } from './web-test-runtime';",
    '',
    `test.describe(${q(suite.suite)}, () => {`,
  ];
  for (const run of runs) {
    const st = run.settings;
    const use = {
      baseURL: st.baseUrl || undefined,
      viewport: st.viewport,
      locale: st.locale,
      timezoneId: st.timezone,
      colorScheme: st.colorScheme,
      extraHTTPHeaders: Object.keys(st.headers).length ? st.headers : undefined,
      storageState: st.storageState || undefined,
      actionTimeout: st.timeout,
    };
    const body = [];
    const prep = { mocks: st.mocks, localStorage: st.localStorage, sessionStorage: st.sessionStorage, cookies: st.cookies, baseUrl: st.baseUrl };
    body.push(`await prepare(page, ${JSON.stringify(prep)});`);
    for (const step of run.steps) {
      const d = describeStep(step);
      const kind = stepKind(step);
      const inner = [];
      if (kind === 'screenshot') {
        const v = step.screenshot;
        inner.push(shotCode(typeof v === 'string' ? { name: v } : v, run.variant));
      } else {
        inner.push(...stepCode(step, st.timeout, fileDir, st.baseUrl));
        if ((opts.screenshot || suite.screenshot) === 'step' && ACTION_STEPS.includes(kind) && !step._setup) inner.push(`await shot(page, testInfo, ${q(kind)});`);
      }
      const title = `${step._setup ? '[準備] ' : ''}${d.text}${d.note ? ` — ${d.note}` : ''}`;
      body.push(`await test.step(${q(title)}, async () => {`, ...indent(inner, 2), '});');
    }
    const tags = [run.requirement, ...(run.tags || []).map(String)].filter(Boolean);
    const name = `${run.id} ${run.title}`;
    const details = tags.length ? `, { tag: ${JSON.stringify(tags.map((t) => '@' + String(t).replace(/\s+/g, '-')))} }` : '';
    const clean = Object.fromEntries(Object.entries(use).filter(([, v]) => v !== undefined));
    // ケースごとの設定（test.use）を閉じ込める枠。variant があるときだけ名前を付ける
    lines.push(run.variant ? `  test.describe(${q(`[${run.variant}]`)}, () => {` : '  test.describe(() => {');
    lines.push(`    test.use(${JSON.stringify(clean)});`);
    if (run.skip) {
      lines.push(`    test.skip(${q(name)}${details}, async () => {}); // ${run.skip === 'skip' ? '' : run.skip}`);
    } else {
      lines.push(`    test(${q(name)}${details}, async ({ page }, testInfo) => {`);
      lines.push(...indent(body, 6));
      lines.push('    });');
    }
    lines.push('  });');
  }
  lines.push('});', '');
  return lines.join('\n');
}

const RUNTIME = `// web-test export が書き出す実行時の補助（通信のモック・storage の事前設定・スクリーンショット）
import fs from 'fs';
import path from 'path';
import type { Page, TestInfo, Locator } from '@playwright/test';

let shotNo = new WeakMap<TestInfo, number>();

function slug(s: string) {
  return String(s).trim().replace(/[\\\\/:*?"<>|\\s]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'x';
}

export async function shot(page: Page, testInfo: TestInfo, name: string, o: { fullPage?: boolean; target?: Locator; mask?: Locator[]; copyTo?: string } = {}) {
  const n = (shotNo.get(testInfo) || 0) + 1;
  shotNo.set(testInfo, n);
  const file = testInfo.outputPath(\`\${String(n).padStart(2, '0')}-\${slug(name)}.png\`);
  if (o.target) await o.target.screenshot({ path: file, mask: o.mask });
  else await page.screenshot({ path: file, fullPage: !!o.fullPage, mask: o.mask });
  await testInfo.attach(name, { path: file, contentType: 'image/png' });
  if (o.copyTo) {
    const dest = path.resolve(process.env.WEB_TEST_CAPTURE_ROOT || process.cwd(), o.copyTo);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(file, dest);
  }
}

type Mock = { url: string; method?: string; status?: number; body?: unknown; json?: unknown; headers?: Record<string, string>; contentType?: string; delayMs?: number; abort?: boolean | string; times?: number };

export async function prepare(page: Page, p: { mocks: Mock[]; localStorage: Record<string, unknown>; sessionStorage: Record<string, unknown>; cookies: any[]; baseUrl: string }) {
  const context = page.context();
  for (const m of p.mocks) {
    let used = 0;
    await context.route(m.url, async (route) => {
      const req = route.request();
      if (m.method && req.method().toUpperCase() !== String(m.method).toUpperCase()) return route.fallback();
      if (m.times !== undefined && used >= m.times) return route.fallback();
      used += 1;
      if (m.delayMs) await new Promise((r) => setTimeout(r, m.delayMs));
      if (m.abort) return route.abort(typeof m.abort === 'string' ? m.abort : 'failed');
      const isJson = m.json !== undefined || (m.body !== undefined && typeof m.body === 'object');
      const body = m.json !== undefined ? JSON.stringify(m.json) : typeof m.body === 'object' ? JSON.stringify(m.body) : m.body !== undefined ? String(m.body) : '';
      return route.fulfill({ status: m.status || 200, headers: m.headers || {}, contentType: m.contentType || (isJson ? 'application/json' : 'text/plain'), body });
    });
  }
  const toStr = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]));
  const L = toStr(p.localStorage);
  const S = toStr(p.sessionStorage);
  let origin = '';
  try { origin = p.baseUrl ? new URL(p.baseUrl).origin : ''; } catch { origin = ''; }
  if (Object.keys(L).length || Object.keys(S).length) {
    await context.addInitScript(({ L, S, O }) => {
      try {
        if (O && location.origin !== O) return;
        const mark = '__web_test_seeded__';
        if (!sessionStorage.getItem(mark)) {
          for (const k in L) localStorage.setItem(k, L[k]);
          for (const k in S) sessionStorage.setItem(k, S[k]);
          sessionStorage.setItem(mark, '1');
        }
      } catch (e) { /* storage が使えないページ */ }
    }, { L, S, O: origin });
  }
  const cookies = (p.cookies || []).map((c) => (c.url || c.domain ? c : { ...c, url: p.baseUrl }));
  if (cookies.length) await context.addCookies(cookies);
}
`;

function configFor(browsers, specs) {
  const projects = browsers.map((b) => `    { name: ${q(b)}, use: { browserName: ${q(b)}${b === 'chromium' ? ', launchOptions: { executablePath: process.env.WEB_TEST_EXECUTABLE_PATH || undefined }' : ''} }, testMatch: ${JSON.stringify(specs.filter((s) => s.browser === b).map((s) => s.file))} },`);
  return `// web-test export で作成。npx playwright test --config この ファイル で動かす
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: __dirname,
  outputDir: 'test-results',
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
${projects.join('\n')}
  ],
});
`.replace('この ファイル', 'このファイル');
}

// suites を outDir に書き出す。戻り値は書いたファイルの一覧
function exportSuites(suites, outDir, opts = {}) {
  fs.mkdirSync(outDir, { recursive: true });
  const specs = [];
  const used = new Set();
  for (const suite of suites) {
    let base = slug(suite.file ? path.basename(suite.file).replace(/\.(ya?ml|json)$/i, '') : suite.suite);
    while (used.has(base)) base += '-2';
    used.add(base);
    const file = `${base}.spec.ts`;
    fs.writeFileSync(path.join(outDir, file), specFor(suite, opts));
    specs.push({ file, browser: suite.browser });
  }
  fs.writeFileSync(path.join(outDir, 'web-test-runtime.ts'), RUNTIME);
  const browsers = [...new Set(specs.map((s) => s.browser))];
  fs.writeFileSync(path.join(outDir, 'playwright.config.ts'), configFor(browsers, specs));
  return ['playwright.config.ts', 'web-test-runtime.ts', ...specs.map((s) => s.file)].map((f) => path.join(outDir, f));
}

module.exports = { exportSuites, specFor, locatorCode };
