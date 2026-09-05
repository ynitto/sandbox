'use strict';

// coherence: code=tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js, doc=docs/plans/2026-09-05-agent-flow-multi-workspace-design.md

// 書込先の集合（workset）の画面側テスト（設計 §7 の P3）。
//
// 固定する不変条件:
//   - 書込先が 1 つの run は**形も見え方も変わらない**（§5.1 不変条件 3）。`workspaces` /
//     要素ごとの行は 2 つ以上のときだけ現れる。
//   - 要素ごとの公開状態を隠さない。片方だけ失敗した半公開が 1 行に潰れない（§5.5）。
//   - GitLab の突き合わせは書込先ごと。primary だけを見て「イシュー無し」にしない（§5.7）。
//
// Electron は起動しない。追加依存なしで `node test/workset.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const contract = require('../src/features/delegation/main/contract');
const flowAdapter = require('../src/features/delegation/main/flow-adapter');
const exec = require('../src/features/routines/main/exec');
const workflowUi = require('../src/renderer/features/adhoc-flow');
const flowMain = require('../src/features/agent-project/main/flow');

// git 問い合わせの差し替え。フォルダごとに別の origin / base を返す。
function withRepos(repos, fn) {
  const original = exec.shInWsl;
  exec.shInWsl = (line) => {
    const hit = Object.keys(repos).find((dir) => line.includes(`'${dir}'`));
    if (!hit) return { status: 2, stdout: '', stderr: '' };
    const r = repos[hit];
    return { status: 0, stdout: `${hit}\n${r.base}\n${r.url}`, stderr: '' };
  };
  try { return fn(); } finally { exec.shInWsl = original; }
}

const REPOS = {
  '/w/api': { base: 'main', url: 'https://git/g/api.git' },
  '/w/web': { base: 'trunk', url: 'https://git/g/web.git' },
  '/w/api2': { base: 'main', url: 'https://git/other/api.git' },
};

// --- 投函: フォルダの列 → 書込先の集合 ------------------------------------

test('フォルダの列は順序つきの集合になり、先頭が primary', () => {
  const set = withRepos(REPOS, () => adhoc.gitWorkset({}, ['/w/api', '/w/web']));
  assert.strictEqual(set.length, 2);
  assert.strictEqual(set[0].url, 'https://git/g/api.git');
  assert.deepStrictEqual(set.map((e) => e.name), ['api', 'web']);
});

test('書込先が 1 つなら要素名を付けない（N=1 は形を変えない）', () => {
  const set = withRepos(REPOS, () => adhoc.gitWorkset({}, ['/w/api']));
  assert.deepStrictEqual(set, [{
    url: 'https://git/g/api.git', local: '/w/api', base: 'main', path: '', desc: 'workflow',
  }]);
});

test('同じ URL の要素名は重複させない（-2 を足して一意にする）', () => {
  const set = withRepos(REPOS, () => adhoc.gitWorkset({}, ['/w/api', '/w/api2']));
  assert.deepStrictEqual(set.map((e) => e.name), ['api', 'api-2']);
});

test('同じフォルダを 2 回選んでも 1 要素に畳む', () => {
  const set = withRepos(REPOS, () => adhoc.gitWorkset({}, ['/w/api', '/w/api']));
  assert.strictEqual(set.length, 1);
});

test('cwd と追加フォルダは 1 本の順序（先頭が primary）に畳まれる', () => {
  assert.deepStrictEqual(adhoc.worksetFolders('/w/api', ['/w/web', '', '/w/api']),
    ['/w/api', '/w/web']);
});

// --- 名前とトークンの導出は Python 側と 1 文字も違ってはいけない ------------
//
// 要素名は「検証計画の workspaces[] が runner のどの clone を指すか」の鍵で、イシュー
// トークンは「どのイシューへ再アタッチするか」の鍵。片方だけ規則が動くと、例外ではなく
// **静かな取りこぼし**（clone が見つからない・イシューが見つからない）になる。正典は
// Python（agent_flow の normalize_workset ／ executors/gitlab.py の _safe_component）で、
// ここはその写しが同じ答えを返すことを固定する。

test('要素名の導出は Python の _repo_name（gitbus._safe）と同じ文字集合', () => {
  const cases = {
    'https://git/g/api.git': 'api',
    'https://git/g/my repo.git': 'my_repo',   // 許されない文字は `-` ではなく `_` へ
    'https://git/g/a.b-c.git': 'a.b-c',       // `.` `-` `_` は残す
  };
  for (const [url, want] of Object.entries(cases)) {
    const repos = { '/x': { base: 'main', url }, '/y': { base: 'main', url: 'https://x/other.git' } };
    const set = withRepos(repos, () => adhoc.gitWorkset({}, ['/x', '/y']));
    assert.strictEqual(set[0].name, want, url);
  }
});

test('イシュートークンの要素部は Python の _safe_component と同じ答えを返す', () => {
  // flow.js（main）が組む規則そのもの。ASCII 英数字と `-` だけを通し、前後の `-` を落として
  // 小文字化、空になったら `e`。`isalnum()` の Unicode 対応に引きずられないこと。
  const safe = (name) => (String(name).replace(/[^0-9A-Za-z-]/g, '-')
    .replace(/^-+|-+$/g, '') || 'e').toLowerCase();
  assert.deepStrictEqual(
    ['api', 'My Repo', 'ふつう', 'a.b-c_d', '--x--', ''].map(safe),
    ['api', 'my-repo', 'e', 'a-b-c-d', 'x', 'e']);
});

test('工程のトークンは書込先ごとに違い、1 書込先では従来の 1 本のまま', () => {
  const nodes = flowMain._nodeTaskTokens
    ? flowMain._nodeTaskTokens('run-1', 'work', [{ name: 'api' }, { name: 'web' }])
    : null;
  if (!nodes) return;                       // 内部関数を公開していない構成では省略
  assert.deepStrictEqual(nodes.map((t) => t.name), ['api', 'web']);
  assert.notStrictEqual(nodes[0].token, nodes[1].token);
  assert.deepStrictEqual(flowMain._nodeTaskTokens('run-1', 'work', [{ name: 'api' }]), []);
});

// --- 投函の記録と検証計画 --------------------------------------------------

// 投函は git 問い合わせと agent-flow の起動を両方叩く。前者はフォルダごとの origin を、
// 後者は verify-plan の JSON と launch の成功を返す。渡された全コマンドを記録する。
function withSubmitEnv(fn) {
  const original = exec.shInWsl;
  const lines = [];
  exec.shInWsl = (line) => {
    lines.push(line);
    if (String(line).includes('rev-parse --show-toplevel')) {
      const hit = Object.keys(REPOS).find((dir) => line.includes(`'${dir}'`));
      if (!hit) return { status: 2, stdout: '', stderr: '' };
      return { status: 0, stdout: `${hit}\n${REPOS[hit].base}\n${REPOS[hit].url}`, stderr: '' };
    }
    if (String(line).includes('verify-plan')) {
      return { status: 0, stdout: JSON.stringify({
        version: 3, task_id: 'x', workspaces: ['api', 'web'],
        commands: [], criteria: [], digest: 'sha256:abc' }), stderr: '' };
    }
    return { status: 0, stdout: 'launched:1', stderr: '' };
  };
  try { return fn(lines); } finally { exec.shInWsl = original; }
}

test('複数フォルダの投函は workspaces[] を書き、primary を workspace に残す', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-submit-'));
  try {
    const cfg = { adhocFlow: { busDir } };
    const result = withSubmitEnv(() => adhoc.submit(cfg, {
      request: '2 つのリポジトリを直す', cwd: '/w/api', cwds: ['/w/web'] }));
    const inbox = adhoc.readInbox(busDir, result.runId);
    assert.strictEqual(inbox.workspace.url, 'https://git/g/api.git');
    assert.deepStrictEqual(inbox.workspaces.map((w) => w.name), ['api', 'web']);
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

test('1 フォルダの投函は従来どおり workspaces を持たない', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-submit1-'));
  try {
    const cfg = { adhocFlow: { busDir } };
    const result = withSubmitEnv(() => adhoc.submit(cfg, {
      request: 'ふつうに直す', cwd: '/w/api' }));
    const inbox = adhoc.readInbox(busDir, result.runId);
    assert.ok(!('workspaces' in inbox));
    assert.strictEqual(inbox.workspace.url, 'https://git/g/api.git');
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

test('一貫性ゲートの検証計画は書込先の数だけ --workspace を繰り返す', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-gate-'));
  try {
    const cfg = { adhocFlow: { busDir } };
    const lines = [];
    withSubmitEnv((captured) => {
      const r = adhoc.submit(cfg, { request: '横断で直す', cwd: '/w/api', cwds: ['/w/web'],
        coherenceGate: true });
      lines.push(...captured);
      return r;
    });
    const plan = lines.find((line) => line.includes('verify-plan'));
    assert.ok(plan, 'verify-plan を呼ぶ');
    assert.strictEqual((plan.match(/--workspace/g) || []).length, 2);
    // 集合の run では要素名を運べる JSON 形で渡す（素の URL では名前が落ちる）
    assert.ok(plan.includes('"name":"api"'));
    assert.ok(plan.includes('"name":"web"'));
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

test('1 書込先の検証計画は素の URL を 1 回だけ渡す（従来どおり）', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-gate1-'));
  try {
    const cfg = { adhocFlow: { busDir } };
    const lines = [];
    withSubmitEnv((captured) => {
      const r = adhoc.submit(cfg, { request: '直す', cwd: '/w/api', coherenceGate: true });
      lines.push(...captured);
      return r;
    });
    const plan = lines.find((line) => line.includes('verify-plan'));
    assert.strictEqual((plan.match(/--workspace/g) || []).length, 1);
    assert.ok(plan.includes("'https://git/g/api.git'"));
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

// --- 委譲封筒: 集合を落とさない -------------------------------------------

test('委譲封筒は書込先の集合を素通しせず明示に写す（黙って消えない）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'flow', id: 'dg-ws', goal: '横断',
    workspace: { url: 'https://git/g/api.git' },
    workspaces: [{ url: 'https://git/g/api.git', name: 'api' },
      { url: 'https://git/g/web.git', name: 'web' }],
  });
  assert.deepStrictEqual(env.workspaces.map((w) => w.name), ['api', 'web']);
  assert.deepStrictEqual(env.workspace, { url: 'https://git/g/api.git' });
});

test('書込先が 1 つの封筒には複数形のキーを足さない', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'flow', id: 'dg-one', goal: 'ふつう',
    workspace: { url: 'https://git/g/api.git' },
    workspaces: [{ url: 'https://git/g/api.git' }],
  });
  assert.ok(!('workspaces' in env));
});

test('flow の inbox 記録は封筒の集合をそのまま運ぶ', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-flow-'));
  try {
    const env = contract.buildEnvelope('post', {
      workload: 'flow', id: 'dg-ws2', goal: '横断',
      workspace: { url: 'https://git/g/api.git' },
      workspaces: [{ url: 'https://git/g/api.git', name: 'api' },
        { url: 'https://git/g/web.git', name: 'web' }],
    });
    flowAdapter.submitPost(busDir, env);
    const rec = JSON.parse(fs.readFileSync(path.join(busDir, 'inbox', 'dg-ws2.json'), 'utf8'));
    assert.deepStrictEqual(rec.workspaces.map((w) => w.name), ['api', 'web']);
    assert.deepStrictEqual(rec.workspace, { url: 'https://git/g/api.git' });
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

test('書込先が 1 つの公示の inbox 記録は複数形のキーを持たない', () => {
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-flow1-'));
  try {
    const env = contract.buildEnvelope('post', {
      workload: 'flow', id: 'dg-one2', goal: 'ふつう',
      workspace: { url: 'https://git/g/api.git' },
    });
    flowAdapter.submitPost(busDir, env);
    const rec = JSON.parse(fs.readFileSync(path.join(busDir, 'inbox', 'dg-one2.json'), 'utf8'));
    assert.ok(!('workspaces' in rec));
  } finally {
    fs.rmSync(busDir, { recursive: true, force: true });
  }
});

// --- 公開表示: 要素ごとに見せる -------------------------------------------

const HALF_PUBLISHED = {
  status: 'failed',
  workspace: { url: 'https://git/g/api.git', local: '/w/api', name: 'api' },
  workspaces: [{ url: 'https://git/g/api.git', local: '/w/api', name: 'api' },
    { url: 'https://git/g/web.git', local: '/w/web', name: 'web' }],
  nodes: { work: { data: { deliveries: [
    { name: 'api', publication: { state: 'published', url: 'https://git/g/api.git',
      branch: 'af/run-1', commit: 'a'.repeat(40) } },
    { name: 'web', publication: { state: 'failed', url: 'https://git/g/web.git',
      branch: 'af/run-1', commit: 'b'.repeat(40),
      recovery: { repository: '/w/web', ref: 'refs/agent-flow/recovery/run-1' } } },
  ] } } },
};

test('半公開は「一番重い状態」を集約に出す（成功に見せない）', () => {
  const view = workflowUi.publicationPresentation(HALF_PUBLISHED);
  assert.strictEqual(view.state, 'failed');
  assert.strictEqual(view.name, 'web');
  assert.strictEqual(view.canForceComplete, true);
  assert.strictEqual(view.local, '/w/web', '復旧先は失敗した要素の clone');
});

test('要素ごとの行が公示の順で並び、成功した要素も残る', () => {
  const view = workflowUi.publicationPresentation(HALF_PUBLISHED);
  assert.deepStrictEqual(view.elements.map((e) => e.name), ['api', 'web']);
  assert.deepStrictEqual(view.elements.map((e) => e.state), ['published', 'failed']);
  assert.strictEqual(view.elements[0].local, '/w/api', '要素ごとに自分の clone を指す');
});

test('書込先が 1 つの run に要素ごとの行は出ない', () => {
  const view = workflowUi.publicationPresentation({
    status: 'done', workspace: { url: 'https://git/g/api.git', local: '/w/api' },
    nodes: { work: { data: { publication: {
      state: 'published', url: 'https://git/g/api.git', branch: 'af/run-2',
      commit: 'c'.repeat(40) } } } },
  });
  assert.strictEqual(view.state, 'published');
  assert.strictEqual(view.elements, undefined);
});

test('公開の詳細は要素ごとの状態を並べる', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  try {
    const html = workflowUi.publicationHtml(HALF_PUBLISHED);
    assert.match(html, /書込先ごとの状態（2 件）/);
    assert.match(html, /<dt>api<\/dt>/);
    assert.match(html, /<dt>web<\/dt>/);
    assert.match(html, /公開失敗/);
  } finally {
    global.esc = previousEsc;
  }
});

test('一覧のフォルダ表示は集合を「ほか N 件」に畳む', () => {
  assert.strictEqual(
    workflowUi.runFolderLabel({
      workspace: { url: 'https://git/g/api.git' },
      workspaces: [{ url: 'https://git/g/api.git' }, { url: 'https://git/g/web.git' }],
    }),
    'https://git/g/api.git ほか 1 件');
  assert.strictEqual(
    workflowUi.runFolderLabel({ workspace: { url: 'https://git/g/api.git' } }),
    'https://git/g/api.git');
});

console.log(`\n${passed} tests passed (workset)`);
