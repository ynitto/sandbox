'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { classify, parseTarget, validTarget, locatorExpr, safeUrl, checkProbe, actionRecord, initState, loadState, browseMain } = require('../src/probe');
const { buildPrompt, openExploreSession } = require('../src/generate');
const { aggregate, toMarkdown } = require('../scripts/compare-explore');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');

const FAKE_AGENT = `node ${JSON.stringify(path.join(__dirname, 'fixtures', 'fake-agent.js'))}`;
const FAKE_PWCLI = [process.execPath, path.join(__dirname, 'fixtures', 'fake-pwcli.js')];

function capture() {
  let out = '';
  let err = '';
  return { io: { out: { write: (s) => { out += s; } }, err: { write: (s) => { err += s; } } }, get out() { return out; }, get err() { return err; } };
}

function readEvidence(dir) {
  const f = path.join(dir, 'explore-evidence.jsonl');
  return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
}

// ---- ブラウザを使わない検査 ----

test('probe: 確かめが要る操作・読むだけ・画面遷移・それ以外を分ける', () => {
  for (const c of ['click', 'dblclick', 'fill', 'select', 'check', 'uncheck']) assert.strictEqual(classify(c, ['e1']), 'action', c);
  assert.strictEqual(classify('press', ['Enter']), 'action');
  assert.strictEqual(classify('press', ['Space', '--probe', 'p']), 'action');
  assert.strictEqual(classify('press', ['--probe', 'p', 'Enter']), 'action');
  assert.strictEqual(classify('press', ['Tab']), 'other');
  for (const c of ['snapshot', 'find', 'screenshot', 'console']) assert.strictEqual(classify(c), 'read', c);
  for (const c of ['goto', 'go-back', 'reload']) assert.strictEqual(classify(c), 'navigation', c);
  for (const c of ['open', 'close', 'kill-all']) assert.strictEqual(classify(c), 'lifecycle', c);
  assert.strictEqual(classify('eval'), 'other');
  assert.strictEqual(classify('probe'), 'probe');
});

test('probe: 対象の引数をテストケースの書式と同じ形にし、Playwright の指し方に写す', () => {
  const { target, rest } = parseTarget(['--role', 'button', '--name=保存', '--exact', 'x']);
  assert.deepStrictEqual(target, { role: 'button', name: '保存', exact: true });
  assert.deepStrictEqual(rest, ['x']);
  assert.deepStrictEqual(parseTarget(['--test-id', 'save', '--nth', '1']).target, { testId: 'save', nth: 1 });
  assert.strictEqual(locatorExpr({ role: 'button', name: '保存', exact: true }), 'page.getByRole("button", {"exact":true,"name":"保存"})');
  assert.strictEqual(locatorExpr({ label: 'タイトル' }), 'page.getByLabel("タイトル", {})');
  assert.strictEqual(locatorExpr({ css: '.save', nth: 1 }), 'page.locator(".save").nth(1)');
  assert.match(validTarget(null), /対象を/);
  assert.match(validTarget({ name: '保存' }), /--role と一緒/);
  assert.match(validTarget({ role: 'button', text: 'x' }), /1 種類/);
  assert.strictEqual(validTarget({ role: 'button', name: '保存' }), null);
});

test('probe: 記録に残す URL はクエリとハッシュを落とす', () => {
  assert.strictEqual(safeUrl('https://h.test/a/b?token=secret#access_token=x'), 'https://h.test/a/b');
  assert.strictEqual(safeUrl('not a url'), null);
});

test('probe: 古い・無い・操作できない probe と指紋の食い違いを断る', () => {
  const state = { generation: 2, probes: {
    fresh: { generation: 2, ready: true, fingerprint: 'sha256:a' },
    old: { generation: 1, ready: true, fingerprint: 'sha256:a' },
    many: { generation: 2, ready: false, matches: 2, why: '2 個' },
    none: { generation: 2, ready: false, matches: 0, why: '無い' },
    hidden: { generation: 2, ready: false, matches: 1, why: '隠れている' },
  } };
  assert.strictEqual(checkProbe(state, undefined).reason, 'missingProbe');
  assert.strictEqual(checkProbe(state, 'nope').reason, 'unknownProbe');
  assert.strictEqual(checkProbe(state, 'old').reason, 'stale');
  assert.strictEqual(checkProbe(state, 'many').reason, 'ambiguous');
  assert.strictEqual(checkProbe(state, 'none').reason, 'missing');
  assert.strictEqual(checkProbe(state, 'hidden').reason, 'notReady');
  assert.strictEqual(checkProbe(state, 'fresh', { fingerprint: 'sha256:b' }).reason, 'fingerprintMismatch');
  assert.strictEqual(checkProbe(state, 'fresh', { fingerprint: 'sha256:a' }), null);
  assert.strictEqual(checkProbe(state, 'fresh'), null);
});

test('probe: 入力した値は記録に残さない（選択肢・キーは残す）', () => {
  const fill = actionRecord({ action: 'fill', id: 'p', value: 'hunter2', status: 'done' });
  assert.strictEqual(fill.value, '[伏せた]');
  assert.ok(!JSON.stringify(fill).includes('hunter2'));
  assert.strictEqual(actionRecord({ action: 'select', value: 'jp', status: 'done' }).value, 'jp');
  assert.strictEqual(actionRecord({ action: 'press', value: 'Enter', status: 'done' }).key, 'Enter');
});

test('probe: --probe-before-act のときだけ依頼文に確かめ方が入る（--explore だけなら今までどおり）', () => {
  const plain = buildPrompt({ conditions: 'c', url: 'http://h/', explore: { command: 'pwcli -s=x' } });
  assert.match(plain, /## 画面を操作して確かめる/);
  assert.doesNotMatch(plain, /状態を変える操作の前に確かめる/);
  const probed = buildPrompt({ conditions: 'c', url: 'http://h/', explore: { command: 'guard', probe: true } });
  assert.match(probed, /## 状態を変える操作の前に確かめる/);
  assert.match(probed, /guard probe --role button --name "保存"/);
  assert.match(probed, /guard click --probe probe-0001/);
});

test('browse: 確かめたあとに位置が変わった対象は操作せず、ref での直の操作・ブラウザの開け閉めも断る', async (t) => {
  const dir = tmpDir(t);
  const state = path.join(dir, 'state.json');
  const ev = path.join(dir, 'ev');
  const log = path.join(dir, 'pwcli.log');
  initState(state);
  process.env.FAKE_PWCLI_LOG = log;
  t.after(() => { delete process.env.FAKE_PWCLI_LOG; delete process.env.FAKE_PWCLI_MOVED; });
  const run = async (...args) => { const c = capture(); const code = await browseMain([`--session=s`, `--state=${state}`, `--evidence=${ev}`, ...args], c.io, { bin: FAKE_PWCLI }); return { code, out: c.out, err: c.err }; };

  const p = await run('probe', '--role', 'button', '--name', '保存');
  assert.strictEqual(p.code, 0, p.err);
  const probe = JSON.parse(p.out);
  assert.strictEqual(probe.ready, true);
  assert.strictEqual(probe.url, 'http://app.test/edit', 'トークン入りのクエリを落とす');
  assert.match(probe.fingerprint, /^sha256:[0-9a-f]{64}$/);

  assert.strictEqual((await run('click', 'e3')).code, 1);
  assert.strictEqual((await run('close')).code, 1);
  assert.strictEqual((await run('click', '--probe', probe.probeId, '--fingerprint', 'sha256:00')).code, 1);

  process.env.FAKE_PWCLI_MOVED = '1';
  const moved = await run('click', '--probe', probe.probeId);
  assert.strictEqual(moved.code, 1);
  assert.strictEqual(JSON.parse(moved.out).reason, 'changed');
  delete process.env.FAKE_PWCLI_MOVED;

  const ok = await run('click', '--probe', probe.probeId, '--fingerprint', probe.fingerprint);
  assert.strictEqual(ok.code, 0, ok.out + ok.err);
  // 操作したので、同じ probe はもう使えない
  assert.strictEqual(JSON.parse((await run('click', '--probe', probe.probeId)).out).reason, 'stale');

  const st = loadState(state).stats;
  assert.deepStrictEqual({ ...st.rejected }, { missingProbe: 1, unknownProbe: 0, stale: 1, ambiguous: 0, missing: 0, notReady: 0, changed: 1, fingerprintMismatch: 1, lifecycle: 1 });
  assert.strictEqual(st.actions, 1);
  // 断った操作は playwright-cli に届いていない（run-code は probe 1・確かめ直し 2・操作 1 の 4 回だけ）
  assert.deepStrictEqual(fs.readFileSync(log, 'utf8').trim().split('\n'), ['run-code', 'run-code', 'run-code', 'run-code']);
  const recs = readEvidence(ev);
  assert.deepStrictEqual(recs.map((r) => `${r.type}:${r.status || ''}`), ['probe:', 'action:rejected', 'action:rejected', 'action:rejected', 'action:done', 'action:rejected']);
});

test('compare: 使用量は不明のまま 0 にせず、baseline に無い数字は「対象外」と書く', () => {
  const rows = [
    { mode: 'baseline', validationPass: true, firstRunPass: false, retries: 0, ambiguousTargets: 1, missingOrNotReadyTargets: 0, probe: null, generateMs: 10 },
    { mode: 'candidate', validationPass: true, firstRunPass: true, retries: 0, ambiguousTargets: 0, missingOrNotReadyTargets: 0, generateMs: 20,
      probe: { probes: 3, probesPerCase: 3, ambiguousProbes: 1, missingProbes: 0, hiddenOrDisabledProbes: 0, staleRejections: 0, missingProbeRejections: 0 } },
  ];
  const s = aggregate(rows);
  assert.strictEqual(s.baseline.usage, null);
  assert.strictEqual(s.candidate.ambiguousTargetsAtProbe, 1);
  const md = toMarkdown({ url: 'u', agent: 'a', runs: 1, summary: s });
  assert.match(md, /使用量（トークン） \| 不明 \| 不明 \|/);
  assert.match(md, /古い確認で断った操作 \| 対象外 \| 0 \|/);
});

// ---- 手元のブラウザで確かめる（外のサイトには行かない） ----

async function openEditor(t, query = '') {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const session = await openExploreSession(`${app.baseUrl}/editor.html${query}`, dir, { executablePath: executablePath(), cwd: dir });
  t.after(() => session.close());
  const state = path.join(dir, 'state.json');
  const ev = path.join(dir, 'evidence-out');
  initState(state);
  const prev = process.cwd();
  const run = async (...args) => {
    const c = capture();
    process.chdir(dir); // playwright-cli は作業ディレクトリに .playwright-cli/ を作る
    try {
      const code = await browseMain([`--session=${session.session}`, `--state=${state}`, `--evidence=${ev}`, ...args], c.io);
      return { code, json: (() => { try { return JSON.parse(c.out); } catch (_) { return null; } })(), out: c.out, err: c.err };
    } finally { process.chdir(prev); }
  };
  return { run, state, ev, dir, baseUrl: app.baseUrl };
}

test('browse（ブラウザ）: 同じ名前が 2 つ・片方が隠れている・押せない→押せる を見分ける', async (t) => {
  const { run, ev } = await openEditor(t);
  const byText = await run('probe', '--text', '保存', '--exact');
  assert.strictEqual(byText.code, 1);
  assert.deepStrictEqual([byText.json.matches, byText.json.visibleMatches, byText.json.ready], [2, 1, false]);
  const hidden = await run('probe', '--css', '#mobile-menu .save');
  assert.deepStrictEqual([hidden.json.matches, hidden.json.visible, hidden.json.ready], [1, false, false]);
  const byRole = await run('probe', '--role', 'button', '--name', '保存');
  assert.strictEqual(byRole.code, 0, byRole.err);
  assert.strictEqual(byRole.json.screenshot, `evidence/${byRole.json.probeId}.png`);
  assert.ok(fs.existsSync(path.join(ev, byRole.json.screenshot)));
  assert.ok(byRole.json.bbox.width > 0);

  const publish = await run('probe', '--role', 'button', '--name', '公開');
  assert.deepStrictEqual([publish.json.visible, publish.json.enabled, publish.json.ready], [true, false, false]);
  assert.strictEqual((await run('click', '--probe', publish.json.probeId)).json.reason, 'notReady');
  const agree = await run('probe', '--label', '内容を確認しました');
  assert.strictEqual((await run('check', '--probe', agree.json.probeId)).code, 0);
  const again = await run('probe', '--role', 'button', '--name', '公開');
  assert.strictEqual(again.json.ready, true);
  assert.strictEqual((await run('click', '--probe', again.json.probeId)).code, 0);
  assert.strictEqual((await run('observe', '公開すると「公開しました」と出る', '--role', 'status')).code, 0);

  // 記録には DOM や画面の文字をまるごと持たない（確かめた対象・操作・観察だけ）
  const recs = readEvidence(ev);
  assert.deepStrictEqual([...new Set(recs.map((r) => r.type))].sort(), ['action', 'observation', 'probe']);
  const raw = fs.readFileSync(path.join(ev, 'explore-evidence.jsonl'), 'utf8');
  assert.doesNotMatch(raw, /<button|<html|innerHTML|メモ帳/);
});

test('browse（ブラウザ）: 言語と画面幅を変えても、そのときの画面にある対象だけを ready にする', async (t) => {
  const { run } = await openEditor(t);
  // 英語（アプリの言語設定は localStorage。variants と同じ切り替え方）
  assert.strictEqual((await run('localstorage-set', 'lang', 'en')).code, 0);
  assert.strictEqual((await run('reload')).code, 0);
  assert.strictEqual((await run('probe', '--role', 'button', '--name', 'Save')).json.ready, true);
  assert.strictEqual((await run('probe', '--role', 'button', '--name', '保存')).json.matches, 0);
  assert.strictEqual((await run('localstorage-set', 'lang', 'ja')).code, 0);
  assert.strictEqual((await run('reload')).code, 0);
  // 狭い画面: ツールバーの保存は隠れ、メニューを開くまで保存は 1 つも見えない
  assert.strictEqual((await run('resize', '360', '640')).code, 0);
  const none = await run('probe', '--role', 'button', '--name', '保存');
  assert.deepStrictEqual([none.json.matches, none.json.ready], [0, false]);
  const menu = await run('probe', '--role', 'button', '--name', 'メニュー');
  assert.strictEqual((await run('click', '--probe', menu.json.probeId)).code, 0);
  const save = await run('probe', '--role', 'button', '--name', '保存');
  assert.strictEqual(save.json.ready, true);
  assert.strictEqual((await run('click', '--probe', save.json.probeId)).code, 0);
});

test('browse（ブラウザ）: 操作のあとの probe は古いものとして断り、要素が作り直されたときも断る。入力した値は残さない', async (t) => {
  const { run, ev, state } = await openEditor(t, '?rerender=2500');
  const title = await run('probe', '--label', 'タイトル');
  const save = await run('probe', '--role', 'button', '--name', '保存');
  assert.strictEqual((await run('click', '--probe', save.json.probeId)).code, 0);
  const clicked = Date.now();
  // 1) 操作をはさんだので、前の probe は使えない
  assert.strictEqual((await run('fill', '--probe', title.json.probeId, 'hunter2')).json.reason, 'stale');
  // 2) 操作のあとに取り直した probe でも、アプリが要素を作り直したら使えない
  const fresh = await run('probe', '--label', 'タイトル');
  assert.strictEqual(fresh.json.ready, true);
  const wait = 2500 + 400 - (Date.now() - clicked);
  assert.ok(wait > 0, '作り直しの前に probe を取れていない（マシンが遅すぎる）');
  await new Promise((r) => setTimeout(r, wait));
  const replaced = await run('fill', '--probe', fresh.json.probeId, 'hunter2');
  assert.strictEqual(replaced.json.reason, 'stale');
  assert.match(replaced.json.message, /作り直された/);
  // 3) 取り直せば入力できる。値は記録に残らない
  const last = await run('probe', '--label', 'タイトル');
  assert.strictEqual((await run('fill', '--probe', last.json.probeId, 'hunter2')).code, 0);
  const all = fs.readdirSync(ev, { recursive: true }).filter((f) => f.endsWith('.jsonl')).map((f) => fs.readFileSync(path.join(ev, f), 'utf8')).join('');
  assert.doesNotMatch(all, /hunter2/);
  assert.strictEqual(loadState(state).stats.rejected.stale, 2);
});

test('generate --explore --probe-before-act: 見張り役を渡し、断った操作と確かめた記録を残し、作ったケースが通る', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const log = path.join(dir, 'log.txt');
  process.env.FAKE_AGENT_MODE = 'probe';
  process.env.FAKE_AGENT_LOG = log;
  t.after(() => { delete process.env.FAKE_AGENT_MODE; delete process.env.FAKE_AGENT_LOG; });
  const ep = executablePath() ? ['--executable-path', executablePath()] : [];
  const out = path.join(dir, 'g.yaml');
  const ev = path.join(dir, 'evidence');
  const r = await cli(['generate', '保存すると保存しましたと出る', '-o', out, '--agent-cmd', FAKE_AGENT, '--url', app.baseUrl + '/editor.html', '--explore', '--probe-before-act', '--evidence-dir', ev, '--no-snapshot', ...ep], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  assert.match(r.out, /確かめた記録: .*explore-evidence\.jsonl（確認 \d+ 回・操作 \d+ 回・断った操作 [1-9]\d* 回）/);
  const text = fs.readFileSync(log, 'utf8');
  assert.match(text, /## 状態を変える操作の前に確かめる/);
  assert.match(text, /\$ click e3\n[\s\S]*missingProbe/);
  const steps = require('yaml').parse(fs.readFileSync(out, 'utf8')).cases[0].steps;
  assert.deepStrictEqual(steps.find((s) => s.click).click, { role: 'button', name: '保存' }, 'ready になった指定でケースを書く');
  const recs = readEvidence(ev);
  assert.ok(recs.some((r) => r.type === 'probe' && r.ready === false && r.matches === 2), '文字「保存」の 2 つ一致を記録する');
  assert.ok(recs.some((r) => r.type === 'action' && r.action === 'click' && r.status === 'done'));
  assert.ok(recs.some((r) => r.type === 'observation'));
  assert.doesNotMatch(fs.readFileSync(path.join(ev, 'explore-evidence.jsonl'), 'utf8'), /hunter2-secret/);
  assert.ok(!fs.existsSync(path.join(dir, '.web-test')), '依頼の作業ディレクトリ（probe の状態を含む）を片付ける');
  const run = await cli(['run', out, '--out', path.join(dir, 'res'), ...ep], { cwd: dir });
  assert.strictEqual(run.code, 0, run.err);
});

test('generate: --probe-before-act は --explore なしでは使えない', async (t) => {
  const dir = tmpDir(t);
  const r = await cli(['generate', '条件', '-o', path.join(dir, 'g.yaml'), '--agent-cmd', FAKE_AGENT, '--probe-before-act'], { cwd: dir });
  assert.strictEqual(r.code, 2);
  assert.match(r.err, /--explore と一緒に/);
});

test('compare: 同じ条件で baseline と candidate を作って動かし、数字を残す', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  process.env.FAKE_AGENT_MODE = 'naive';
  t.after(() => { delete process.env.FAKE_AGENT_MODE; });
  const { main } = require('../scripts/compare-explore');
  const result = await main(['--url', app.baseUrl + '/editor.html', '--conditions', '保存すると保存しましたと出る', '--agent-cmd', FAKE_AGENT, '--out', dir, '--runs', '1',
    ...(executablePath() ? ['--executable-path', executablePath()] : [])]);
  const { baseline, candidate } = result.summary;
  // 文字「保存」は隠れたメニューの中にもあるので、確かめずに書くと実行で 1 つに決まらない
  assert.deepStrictEqual([baseline.validationPass, baseline.firstRunPass, baseline.ambiguousTargetsAtRun], ['1/1', '0/1', 1]);
  assert.deepStrictEqual([candidate.validationPass, candidate.firstRunPass, candidate.ambiguousTargetsAtProbe, candidate.ambiguousTargetsAtRun], ['1/1', '1/1', 1, 0]);
  assert.ok(candidate.probesPerCase > 0);
  assert.strictEqual(candidate.usage, null);
  assert.ok(fs.existsSync(path.join(dir, 'comparison.md')));
});
