'use strict';
// テストケースを Playwright で実行し、ステップごとの結果とスクリーンショットを集める。
// ケースごとに新しいブラウザコンテキストを作るので、localStorage・Cookie・モックは混ざらない。

const fs = require('fs');
const path = require('path');
const { ACTION_STEPS, stepKind } = require('./casefile');
const { planSuite } = require('./plan');
const { compareImages } = require('./docimages');

function loadPlaywright() {
  try {
    return require('playwright');
  } catch (e) {
    throw new Error('playwright が見つかりません。tools/webui-test で `npm install` と `npx playwright install chromium` を実行してください');
  }
}

function slug(s) {
  return String(s).trim().replace(/[\\/:*?"<>|\s]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'x';
}

function resolveUrl(u, baseUrl) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(u)) return u;
  if (!baseUrl) throw new Error(`相対パス「${u}」には baseUrl が要ります（ファイルの baseUrl か --base-url）`);
  return new URL(u, baseUrl.endsWith('/') || u.startsWith('/') ? baseUrl : baseUrl + '/').toString();
}

function toLocator(page, t) {
  if (typeof t === 'string') return page.locator(t);
  const opt = t.exact !== undefined ? { exact: t.exact } : {};
  let loc;
  if (t.css) loc = page.locator(t.css);
  else if (t.role) loc = page.getByRole(t.role, { ...opt, ...(t.name !== undefined ? { name: String(t.name) } : {}) });
  else if (t.label) loc = page.getByLabel(String(t.label), opt);
  else if (t.text) loc = page.getByText(String(t.text), opt);
  else if (t.placeholder) loc = page.getByPlaceholder(String(t.placeholder), opt);
  else if (t.testId) loc = page.getByTestId(String(t.testId));
  else if (t.alt) loc = page.getByAltText(String(t.alt), opt);
  else if (t.title) loc = page.getByTitle(String(t.title), opt);
  if (t.nth !== undefined) loc = loc.nth(t.nth);
  return loc;
}

function describeTarget(t) {
  if (typeof t === 'string') return t;
  const { exact, nth, ...rest } = t;
  const s = Object.entries(rest).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ');
  return nth !== undefined ? `${s} (#${nth})` : s;
}

function describeStep(step) {
  const kind = stepKind(step);
  const v = step[kind];
  let detail = '';
  switch (kind) {
    case 'goto': detail = v; break;
    case 'reload': detail = ''; break;
    case 'fill': case 'select': detail = `${describeTarget(v.target)} ← ${JSON.stringify(v.value)}`; break;
    case 'press': detail = typeof v === 'string' ? v : `${v.target ? describeTarget(v.target) + ' ' : ''}${v.key}`; break;
    case 'upload': detail = `${describeTarget(v.target)} ← ${[].concat(v.files).join(', ')}`; break;
    case 'eval': detail = v.length > 60 ? v.slice(0, 57) + '...' : v; break;
    case 'wait': detail = typeof v === 'number' ? `${v}ms` : v && (v.url || v.load || v.visible || v.hidden) ? JSON.stringify(v) : describeTarget(v); break;
    case 'expect': detail = JSON.stringify(v); break;
    case 'screenshot': detail = typeof v === 'string' ? v : v.name; break;
    default: detail = describeTarget(v);
  }
  return { kind, text: `${kind}${detail ? ' ' + detail : ''}`, note: step.note || '' };
}

// 条件が満たされるまで繰り返し確かめる（Playwright Test の expect.poll 相当）
async function poll(check, timeout, message) {
  const end = Date.now() + timeout;
  let last;
  for (;;) {
    try {
      last = await check();
      if (last.ok) return;
    } catch (e) {
      last = { ok: false, actual: e.message.split('\n')[0] };
    }
    if (Date.now() > end) throw new Error(`${message}（実際: ${last && last.actual !== undefined ? JSON.stringify(last.actual) : '不明'}）`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

function matchText(actual, expected, contains) {
  const a = String(actual ?? '').replace(/\s+/g, ' ').trim();
  if (typeof expected === 'string' && /^\/.*\/[a-z]*$/.test(expected)) {
    const m = expected.match(/^\/(.*)\/([a-z]*)$/);
    return new RegExp(m[1], m[2]).test(a);
  }
  const e = String(expected).replace(/\s+/g, ' ').trim();
  return contains ? a.includes(e) : a === e;
}

async function runExpect(page, v, timeout) {
  // 1 回の読み取りは短く切り、poll で繰り返す（要素がまだ無いときに Playwright 既定の 30 秒待たない）
  const T = { timeout: Math.min(timeout, 1000) };
  const loc = v.target !== undefined ? toLocator(page, v.target) : null;
  if (v.visible !== undefined) {
    const l = v.visible === true ? loc : toLocator(page, v.visible);
    await l.first().waitFor({ state: 'visible', timeout }).catch(() => { throw new Error(`${describeTarget(v.visible === true ? v.target : v.visible)} が表示されません`); });
  }
  if (v.hidden !== undefined) {
    const l = v.hidden === true ? loc : toLocator(page, v.hidden);
    await poll(async () => ({ ok: (await l.count()) === 0 || !(await l.first().isVisible()) }), timeout, `${describeTarget(v.hidden === true ? v.target : v.hidden)} が消えません`);
  }
  if (v.text !== undefined) await poll(async () => { const a = await loc.first().innerText(T); return { ok: matchText(a, v.text, false), actual: a }; }, timeout, `${describeTarget(v.target)} の文字が ${JSON.stringify(v.text)} ではありません`);
  if (v.contains !== undefined) await poll(async () => { const a = await loc.first().innerText(T); return { ok: matchText(a, v.contains, true), actual: a }; }, timeout, `${describeTarget(v.target)} に ${JSON.stringify(v.contains)} が含まれません`);
  if (v.notContains !== undefined) await poll(async () => { const a = await loc.first().innerText(T); return { ok: !matchText(a, v.notContains, true), actual: a }; }, timeout, `${describeTarget(v.target)} に ${JSON.stringify(v.notContains)} が含まれています`);
  if (v.value !== undefined) await poll(async () => { const a = await loc.first().inputValue(T); return { ok: matchText(a, v.value, false), actual: a }; }, timeout, `${describeTarget(v.target)} の値が ${JSON.stringify(v.value)} ではありません`);
  if (v.count !== undefined) await poll(async () => { const a = await loc.count(); return { ok: a === v.count, actual: a }; }, timeout, `${describeTarget(v.target)} の数が ${v.count} ではありません`);
  if (v.enabled !== undefined) await poll(async () => { const a = await loc.first().isEnabled(T); return { ok: a === !!v.enabled, actual: a }; }, timeout, `${describeTarget(v.target)} が${v.enabled ? '押せません' : '押せてしまいます'}`);
  if (v.disabled !== undefined) await poll(async () => { const a = await loc.first().isDisabled(T); return { ok: a === !!v.disabled, actual: a }; }, timeout, `${describeTarget(v.target)} が${v.disabled ? '無効ではありません' : '無効です'}`);
  if (v.checked !== undefined) await poll(async () => { const a = await loc.first().isChecked(T); return { ok: a === !!v.checked, actual: a }; }, timeout, `${describeTarget(v.target)} のチェックが ${v.checked} ではありません`);
  if (v.url !== undefined) {
    const expected = /^\/.*\/[a-z]*$/.test(v.url) || /^[a-z]+:/i.test(v.url) ? v.url : null;
    await poll(async () => {
      const a = page.url();
      if (expected) return { ok: matchText(a, expected, false), actual: a };
      // パスだけなら「含む」で比べる（クエリ・ハッシュの有無で落ちないように）
      return { ok: a.includes(v.url), actual: a };
    }, timeout, `URL が ${JSON.stringify(v.url)} になりません`);
  }
  if (v.title !== undefined) await poll(async () => { const a = await page.title(); return { ok: matchText(a, v.title, false), actual: a }; }, timeout, `タイトルが ${JSON.stringify(v.title)} ではありません`);
}

async function runStep(page, step, ctx) {
  const kind = stepKind(step);
  const v = step[kind];
  const timeout = step.timeout || ctx.timeout;
  switch (kind) {
    case 'goto': await page.goto(resolveUrl(v, ctx.baseUrl), { timeout: Math.max(timeout, 30000) }); break;
    case 'reload': await page.reload({ timeout: Math.max(timeout, 30000) }); break;
    case 'click': await toLocator(page, v).click({ timeout }); break;
    case 'dblclick': await toLocator(page, v).dblclick({ timeout }); break;
    case 'hover': await toLocator(page, v).hover({ timeout }); break;
    case 'check': await toLocator(page, v).check({ timeout }); break;
    case 'uncheck': await toLocator(page, v).uncheck({ timeout }); break;
    case 'fill': await toLocator(page, v.target).fill(String(v.value), { timeout }); break;
    case 'select': await toLocator(page, v.target).selectOption(Array.isArray(v.value) ? v.value.map(String) : String(v.value), { timeout }); break;
    case 'press':
      if (typeof v === 'string') await page.keyboard.press(v);
      else if (v.target !== undefined) await toLocator(page, v.target).press(v.key, { timeout });
      else await page.keyboard.press(v.key);
      break;
    case 'upload': {
      const files = [].concat(v.files).map((f) => path.resolve(ctx.fileDir, f));
      await toLocator(page, v.target).setInputFiles(files, { timeout });
      break;
    }
    case 'eval': await page.evaluate(v); break;
    case 'wait':
      if (typeof v === 'number') await page.waitForTimeout(v);
      else if (v && typeof v === 'object' && v.url !== undefined) await poll(async () => ({ ok: page.url().includes(v.url), actual: page.url() }), timeout, `URL が ${v.url} になりません`);
      else if (v && typeof v === 'object' && v.load !== undefined) await page.waitForLoadState(v.load, { timeout: Math.max(timeout, 30000) });
      else if (v && typeof v === 'object' && v.hidden !== undefined) await toLocator(page, v.hidden).first().waitFor({ state: 'hidden', timeout });
      else if (v && typeof v === 'object' && v.visible !== undefined) await toLocator(page, v.visible).first().waitFor({ state: 'visible', timeout });
      else await toLocator(page, v).first().waitFor({ state: 'visible', timeout });
      break;
    case 'expect': await runExpect(page, v, timeout); break;
    case 'screenshot': break; // 撮影は呼び出し側
    default: throw new Error(`知らないステップ: ${kind}`);
  }
}

async function takeShot(page, file, opts = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const mask = (opts.mask || []).map((m) => toLocator(page, m));
  // 動きと点滅するカーソルを止めて撮る（同じ画面なら同じ画像になるように）
  const common = { path: file, mask, animations: 'disabled', caret: 'hide' };
  if (opts.target !== undefined) await toLocator(page, opts.target).first().screenshot(common);
  else await page.screenshot({ ...common, fullPage: !!opts.fullPage });
}

async function installMocks(context, mocks) {
  for (const m of mocks) {
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
      return route.fulfill({
        status: m.status || 200,
        headers: m.headers || {},
        contentType: m.contentType || (isJson ? 'application/json' : 'text/plain'),
        body,
      });
    });
  }
}

function storageInitScript(local, session, origin) {
  // アプリのスクリプトより先に、そのページの origin の storage へ入れる。
  // 同じコンテキストで何度読み込んでも上書きし直すのは最初の 1 回だけ（アプリの書き換えを消さない）。
  const toStr = (o) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]));
  return {
    content: `(() => { const L = ${JSON.stringify(toStr(local))}; const S = ${JSON.stringify(toStr(session))}; const O = ${JSON.stringify(origin || '')};
      try { if (O && location.origin !== O) return; const mark = '__webui_test_seeded__';
        if (!sessionStorage.getItem(mark)) { for (const k in L) localStorage.setItem(k, L[k]); for (const k in S) sessionStorage.setItem(k, S[k]); sessionStorage.setItem(mark, '1'); }
      } catch (e) {} })();`,
  };
}

function originOf(u) {
  try { return u ? new URL(u).origin : ''; } catch (_) { return ''; }
}

async function runCase(browser, suite, run, opts) {
  const caseDir = path.join(opts.outDir, slug(suite.suite), slug(run.variant ? `${run.id}-${run.variant}` : run.id));
  const result = { id: run.id, variant: run.variant, title: run.title, requirement: run.requirement, tags: run.tags, status: 'passed', steps: [], screenshots: [], error: null, durationMs: 0 };
  const started = Date.now();
  if (run.skip) {
    result.status = 'skipped';
    result.error = run.skip === 'skip' ? null : run.skip;
    return result;
  }
  const st = run.settings;
  const mode = opts.screenshot || suite.screenshot;
  const context = await browser.newContext({
    viewport: st.viewport,
    ...(st.locale ? { locale: st.locale } : {}),
    ...(st.timezone ? { timezoneId: st.timezone } : {}),
    ...(st.colorScheme ? { colorScheme: st.colorScheme } : {}),
    ...(Object.keys(st.headers).length ? { extraHTTPHeaders: st.headers } : {}),
    ...(st.storageState ? { storageState: st.storageState } : {}),
  });
  const consoleErrors = [];
  let shotNo = 0;
  const shoot = async (page, name, o = {}) => {
    shotNo += 1;
    const file = path.join(caseDir, `${String(shotNo).padStart(2, '0')}-${slug(name)}.png`);
    await takeShot(page, file, o);
    const rel = path.relative(opts.outDir, file).split(path.sep).join('/');
    result.screenshots.push({ name, file: rel });
    // 仕様書用の保存先。variants があるときは名前に variant を添えて上書きし合わないようにする
    const stable = run.variant ? `${slug(name)}.${slug(run.variant)}` : slug(name);
    if (o.path && opts.captureRoot && opts.docImages !== 'off') {
      const dest = path.resolve(opts.captureRoot, run.variant ? o.path.replace(/(\.png)?$/i, `.${slug(run.variant)}.png`) : o.path);
      const entry = { name, path: dest, actual: rel };
      if (opts.docImages === 'compare') {
        // 仕様書の画像は書き換えず、今の画面と同じかだけを見る（webui-test check）
        if (!fs.existsSync(dest)) entry.status = 'missing';
        else {
          const c = compareImages(fs.readFileSync(file), fs.readFileSync(dest), { maxDiffRatio: opts.maxDiffRatio });
          entry.status = c.same ? 'same' : 'changed';
          if (!c.same) {
            entry.ratio = c.ratio;
            entry.message = c.message;
            if (c.diff) {
              const diffFile = file.replace(/\.png$/, '.diff.png');
              fs.writeFileSync(diffFile, c.diff);
              entry.diff = path.relative(opts.outDir, diffFile).split(path.sep).join('/');
            }
          }
        }
      } else {
        const prev = fs.existsSync(dest) ? fs.readFileSync(dest) : null;
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.copyFileSync(file, dest);
        entry.status = !prev ? 'created' : compareImages(fs.readFileSync(file), prev, { maxDiffRatio: opts.maxDiffRatio }).same ? 'same' : 'updated';
      }
      result.docImages = (result.docImages || []).concat(entry);
    }
    if (opts.captureDir && o.explicit) {
      const dest = path.join(opts.captureDir, `${stable}.png`);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(file, dest);
      result.captured = (result.captured || []).concat(dest);
    }
    return rel;
  };
  try {
    await installMocks(context, st.mocks);
    if (Object.keys(st.localStorage).length || Object.keys(st.sessionStorage).length) await context.addInitScript(storageInitScript(st.localStorage, st.sessionStorage, originOf(st.baseUrl)));
    const cookies = st.cookies.map((ck) => (ck.url || ck.domain ? ck : { ...ck, url: st.baseUrl }));
    if (cookies.length) await context.addCookies(cookies);
    const page = await context.newPage();
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', (e) => consoleErrors.push(String(e.message || e)));
    const ctx = { baseUrl: st.baseUrl, timeout: st.timeout, fileDir: suite.file ? path.dirname(suite.file) : process.cwd() };
    for (const step of run.steps) {
      const d = describeStep(step);
      const sr = { action: d.text, note: d.note, status: 'passed', screenshot: null, setup: !!step._setup };
      result.steps.push(sr);
      const t0 = Date.now();
      try {
        await runStep(page, step, ctx);
        if (d.kind === 'screenshot') {
          const v = step.screenshot;
          const o = typeof v === 'string' ? { name: v } : v;
          sr.screenshot = await shoot(page, o.name, { ...o, explicit: true });
        } else if (mode === 'step' && ACTION_STEPS.includes(d.kind) && !step._setup) {
          sr.screenshot = await shoot(page, d.kind);
        }
      } catch (e) {
        sr.status = 'failed';
        sr.error = String(e.message || e).split('\n')[0];
        result.status = 'failed';
        result.error = sr.error;
        if (mode !== 'off') {
          try { sr.screenshot = await shoot(page, 'failure', { fullPage: true }); } catch (_) { /* 画面が閉じていれば撮れない */ }
        }
        sr.durationMs = Date.now() - t0;
        break;
      }
      sr.durationMs = Date.now() - t0;
    }
  } catch (e) {
    result.status = 'failed';
    result.error = result.error || String(e.message || e).split('\n')[0];
  } finally {
    await context.close().catch(() => {});
  }
  result.consoleErrors = consoleErrors;
  result.durationMs = Date.now() - started;
  return result;
}

// suites を実行して結果を返す。
// opts: { outDir, baseUrl, env, headed, workers, screenshot, captureDir, captureRoot, docImages, maxDiffRatio, only, variants, executablePath, onCase }
// docImages: copy（既定。path: へ写す）/ compare（写さずに今の画面と比べる）/ off
async function runSuites(suites, opts) {
  const pw = loadPlaywright();
  fs.mkdirSync(opts.outDir, { recursive: true });
  const browsers = {};
  const getBrowser = async (name) => {
    if (!browsers[name]) {
      const launchOpts = { headless: !opts.headed };
      if (opts.executablePath && name === 'chromium') launchOpts.executablePath = opts.executablePath;
      browsers[name] = pw[name].launch(launchOpts);
    }
    return browsers[name];
  };
  const report = { startedAt: new Date().toISOString(), env: opts.env ? opts.env.name : 'local', suites: [] };
  try {
    for (const suite of suites) {
      const runs = planSuite(suite, { env: opts.env, baseUrl: opts.baseUrl })
        .filter((r) => !opts.only || opts.only.some((o) => r.id === o || r.id.startsWith(o)))
        .filter((r) => !opts.variants || !r.variant || opts.variants.includes(r.variant));
      const browser = await getBrowser(suite.browser);
      const results = new Array(runs.length);
      let next = 0;
      const worker = async () => {
        while (next < runs.length) {
          const i = next++;
          results[i] = await runCase(browser, suite, runs[i], opts);
          if (opts.onCase) opts.onCase(suite, results[i]);
        }
      };
      await Promise.all(Array.from({ length: Math.max(1, Math.min(opts.workers || 1, runs.length)) }, worker));
      report.suites.push({ suite: suite.suite, file: suite.file, baseUrl: runs[0] ? runs[0].settings.baseUrl : suite.baseUrl, browser: suite.browser, cases: results });
    }
  } finally {
    for (const b of Object.values(browsers)) await (await b).close().catch(() => {});
  }
  report.finishedAt = new Date().toISOString();
  const all = report.suites.flatMap((s) => s.cases);
  report.summary = {
    total: all.length,
    passed: all.filter((c) => c.status === 'passed').length,
    failed: all.filter((c) => c.status === 'failed').length,
    skipped: all.filter((c) => c.status === 'skipped').length,
  };
  return report;
}

module.exports = { runSuites, toLocator, resolveUrl, describeStep, slug, matchText };
