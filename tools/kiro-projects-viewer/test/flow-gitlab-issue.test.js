'use strict';

// flow.js の関連 GitLab イシュー検出（readRun の issueUrl / gitlabIssues）を検証する。
// 追加依存なしで `node test/flow-gitlab-issue.test.js` で走る。
//
// 背景: 以前は node の output 全文を正規表現でスキャンしてイシュー URL を拾っていたため、
// gitlab executor を使っていない run でも、worker が gitlab.py のテストを流した pytest ログに
// 紛れたサンプル URL を「関連イシュー」として拾い、概要ペインに Issue ボタンが出て、押すと
// 実在しないリポジトリへ飛んでいた。executor の確実な証跡（data / wait 記録）がある run に
// 限って output のフォールバックを許す。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// run を組み立てる小道具。nodes は {id: {state, output, data}} で渡す。
function buildRun(tmp, nodes, { waits } = {}) {
  const runDir = path.join(tmp, 'runs', 'req-deadbeef-TASK-1-r0');
  fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
  fs.mkdirSync(path.join(runDir, 'waits'), { recursive: true });
  fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({ status: 'done', request: 'x' }));
  const graphNodes = {};
  for (const [id, n] of Object.entries(nodes)) {
    graphNodes[id] = { goal: id, deps: [] };
    const result = { id, who: 'worker-1', status: n.state === 'failed' ? 'failed' : 'done' };
    if (n.output !== undefined) result.output = n.output;
    if (n.data !== undefined) result.data = n.data;
    fs.writeFileSync(path.join(runDir, 'results', `${id}.json`), JSON.stringify(result));
  }
  fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: graphNodes }));
  for (const [id, rec] of Object.entries(waits || {})) {
    fs.writeFileSync(path.join(runDir, 'waits', `${id}.json`), JSON.stringify(rec));
  }
  return runDir;
}

// gitlab executor のログを模した pytest 出力（実際に誤検出を起こしたのはこの形）
const PYTEST_LOG_WITH_SAMPLE_URL = [
  'tools/kiro-flow/executors/gitlab.py:960: DeferDecision',
  '----------------------------- Captured stdout call -----------------------------',
  '[2026-07-12T08:24:43Z] [gitlab] イシュー #8 を起票し関連 MR の決着待ち: https://gitlab.com/group/repo/-/issues/1',
].join('\n');

test('gitlab executor を使っていない run では、出力に含まれるイシュー URL を拾わない', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gl-'));
  try {
    // data も wait 記録も無い＝executor の証跡ゼロ。出力はテストログにすぎない。
    const runDir = buildRun(tmp, { t1: { state: 'done', output: PYTEST_LOG_WITH_SAMPLE_URL } });
    const run = flow.readRun(runDir);
    assert.strictEqual(run.nodes.t1.issueUrl, null);
    assert.deepStrictEqual(run.gitlabIssues, []);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('証跡が無ければ、失敗ノードの出力に URL があっても拾わない', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gl-'));
  try {
    // 却下ノードの output フォールバックは executor の証跡がある run だけに効く。
    // テストが落ちた（failed）だけの run で誤検出しないこと。
    const runDir = buildRun(tmp, { t1: { state: 'failed', output: PYTEST_LOG_WITH_SAMPLE_URL } });
    const run = flow.readRun(runDir);
    assert.strictEqual(run.nodes.t1.issueUrl, null);
    assert.deepStrictEqual(run.gitlabIssues, []);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('data に web_url があれば関連イシューとして拾う（gitlab executor の成果）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gl-'));
  try {
    const runDir = buildRun(tmp, {
      t1: {
        state: 'done',
        data: { issue_iid: 42, web_url: 'https://gitlab.example.com/g/p/-/issues/42', decision: 'approved' },
      },
    });
    const run = flow.readRun(runDir);
    assert.strictEqual(run.nodes.t1.issueUrl, 'https://gitlab.example.com/g/p/-/issues/42');
    assert.strictEqual(run.gitlabIssues.length, 1);
    assert.strictEqual(run.gitlabIssues[0].issueIid, 42);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('executor の証跡がある run では、却下ノードの出力からもイシュー URL を拾う', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gl-'));
  try {
    // t1 が data を持つ＝この run は gitlab executor を使っている。
    // t2 は却下（failed）で data が無いため、output の URL から拾えなければならない。
    const runDir = buildRun(tmp, {
      t1: { state: 'done', data: { issue_iid: 7, web_url: 'https://gitlab.example.com/g/p/-/issues/7' } },
      t2: {
        state: 'failed',
        output: '[gitlab-reject] 却下されました: https://gitlab.example.com/g/p/-/issues/8',
      },
    });
    const run = flow.readRun(runDir);
    assert.strictEqual(run.nodes.t2.issueUrl, 'https://gitlab.example.com/g/p/-/issues/8');
    const urls = run.gitlabIssues.map((i) => i.url).sort();
    assert.deepStrictEqual(urls, [
      'https://gitlab.example.com/g/p/-/issues/7',
      'https://gitlab.example.com/g/p/-/issues/8',
    ]);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('park 中の wait 記録にイシュー座標があれば拾う', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gl-'));
  try {
    const runDir = path.join(tmp, 'runs', 'req-deadbeef-TASK-1-r0');
    fs.mkdirSync(path.join(runDir, 'waits'), { recursive: true });
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({ status: 'running', request: 'x' }));
    fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: { t1: { goal: 'g', deps: [] } } }));
    fs.writeFileSync(
      path.join(runDir, 'waits', 't1.json'),
      JSON.stringify({
        wait_lease_until: Date.now() / 1000 + 600,
        who: 'worker-1',
        issue: { host: 'gitlab.example.com', project: 'g/p', iid: 3, url: 'https://gitlab.example.com/g/p/-/issues/3' },
      })
    );
    const run = flow.readRun(runDir);
    assert.strictEqual(run.nodes.t1.state, 'parked');
    assert.strictEqual(run.nodes.t1.issueUrl, 'https://gitlab.example.com/g/p/-/issues/3');
    assert.strictEqual(run.gitlabIssues.length, 1);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('gitlabish: meta.executor が正（agent なら証跡があっても GitLab UI を出させない）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-flow-'));
  try {
    const runDir = buildRun(tmp, {
      t1: { state: 'done', data: { web_url: 'https://gitlab.example.com/g/p/-/issues/9' } },
    });
    // executor 記録なし（旧 run）→ 証跡（data の issue 座標）から推定 = gitlabish
    assert.strictEqual(flow.readRun(runDir).gitlabish, true);
    // executor=agent と明記 → 証跡らしきものがあっても gitlab UI は不要
    fs.writeFileSync(path.join(runDir, 'meta.json'),
      JSON.stringify({ status: 'done', request: 'x', executor: 'agent' }));
    const r2 = flow.readRun(runDir);
    assert.strictEqual(r2.gitlabish, false);
    assert.strictEqual(r2.executor, 'agent');
    // executor=gitlab と明記 → gitlabish
    fs.writeFileSync(path.join(runDir, 'meta.json'),
      JSON.stringify({ status: 'done', request: 'x', executor: 'gitlab' }));
    assert.strictEqual(flow.readRun(runDir).gitlabish, true);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
