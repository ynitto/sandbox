'use strict';

// フロー run のアーカイブ（プロジェクト配下の保管庫）のテスト。追加依存なしで
// `node test/flow-archive.test.js` で走る。
//   - archiveRunSnapshot: run のスナップショット保存（同一内容の再保存はスキップ）
//   - listArchivedRuns / readArchivedRun: bus から掃除された run をアーカイブから読める
//   - 置き場は <projectDir>/flow-archive/（プロジェクトのデータ＝リセットで一緒に消える）
//   - 保持上限（prune）はここでは対象外（ARCHIVE_KEEP 件の頭打ちのみの単純規則）

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

// プロジェクト（root）と、その配下の bus に 1 run（2 ノード・両方 done・meta.status=done）を作る
function mkBus(runId) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-arch-'));
  const busDir = path.join(root, 'bus');
  const runDir = path.join(busDir, 'runs', runId);
  fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
  fs.mkdirSync(path.join(runDir, 'events'), { recursive: true });
  fs.writeFileSync(
    path.join(runDir, 'meta.json'),
    JSON.stringify({
      status: 'done',
      request: 'CSV を要約する',
      created_at: '2026-07-11T00:00:00Z',
      updated_at: '2026-07-11T00:10:00Z',
    }),
    'utf8'
  );
  fs.writeFileSync(
    path.join(runDir, 'graph.json'),
    JSON.stringify({ nodes: { a: { goal: 'A', deps: [] }, b: { goal: 'B', deps: ['a'] } } }),
    'utf8'
  );
  for (const id of ['a', 'b']) {
    fs.writeFileSync(
      path.join(runDir, 'results', `${id}.json`),
      JSON.stringify({ status: 'done', who: 'w1', finished_at: '2026-07-11T00:05:00Z' }),
      'utf8'
    );
  }
  fs.writeFileSync(
    path.join(runDir, 'events', 'w1.jsonl'),
    `${JSON.stringify({ ts: '2026-07-11T00:01:00Z', who: 'w1', kind: 'claimed', node: 'a' })}\n`,
    'utf8'
  );
  return { root, busDir, runDir };
}

test('archiveRunSnapshot → bus 掃除後も listArchivedRuns / readArchivedRun で読める', () => {
  const runId = 'req-abc123-T1-r0';
  const { root, busDir, runDir } = mkBus(runId);
  try {
    const [run] = flow.listRuns(busDir);
    assert.strictEqual(run.runId, runId);
    assert.strictEqual(flow.archiveRunSnapshot(root, busDir, run), true, '初回は保存する');
    assert.strictEqual(flow.archiveRunSnapshot(root, busDir, run), false, '同一内容はスキップ');

    // agent-flow の掃除を模して bus から run を消す
    fs.rmSync(runDir, { recursive: true, force: true });
    assert.strictEqual(flow.listRuns(busDir).length, 0);

    const archived = flow.listArchivedRuns(root);
    assert.strictEqual(archived.length, 1);
    assert.strictEqual(archived[0].runId, runId);
    assert.strictEqual(archived[0].archived, true);
    assert.strictEqual(archived[0].status, 'done');
    assert.strictEqual(archived[0].alive, null, '孤児と誤表示しない');
    assert.strictEqual(archived[0].taskId, 'T1', 'req- 形式の系統情報も保たれる');

    const snap = flow.readArchivedRun(root, runId);
    assert.ok(snap && snap.run && snap.run.runId === runId);
    assert.strictEqual(snap.run.counts.done, 2, 'ノード状態のスナップショットを保持');
    assert.ok(Array.isArray(snap.events) && snap.events.length === 1, 'イベントも保存される');
    assert.ok(snap.nodeEvents && snap.nodeEvents.a, 'ノード別タイムラインも保存される');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('リトライ墓標（inherited/）→ readRun 互換サマリ → アーカイブとして読める', () => {
  // agent-flow の inherit_from は世代交代で旧 run を bus から削除し、墓標
  // runs/<新>/inherited/<旧>.json を残す。viewer はこれを「アーカイブ済み run」相当で
  // 表示できる＝リトライで旧世代の成果記録が消えない。
  const newId = 'req-abc123-T1-r1';
  const oldId = 'req-abc123-T1-r0';
  const { root, busDir, runDir } = mkBus(newId);
  try {
    fs.mkdirSync(path.join(runDir, 'inherited'), { recursive: true });
    fs.writeFileSync(
      path.join(runDir, 'inherited', `${oldId}.json`),
      JSON.stringify({
        run_id: oldId,
        saved_at: '2026-07-11T00:20:00Z',
        meta: {
          status: 'done', request: 'CSV を要約する',
          created_at: '2026-07-10T00:00:00Z', updated_at: '2026-07-10T00:10:00Z',
        },
        graph: { nodes: { a: { goal: 'A', deps: [] }, b: { goal: 'B', deps: ['a'] } } },
        final: { finished_at: '2026-07-10T00:10:00Z', summary: '完走（verify NG で世代交代）' },
        results: {
          a: { status: 'done', who: 'w1', output: 'out-a', finished_at: '2026-07-10T00:05:00Z' },
          b: { status: 'failed', who: 'w1', output: 'boom', finished_at: '2026-07-10T00:06:00Z' },
        },
        artifacts: ['a/report.md'],
      }),
      'utf8'
    );
    const tombs = flow.readInheritedTombstones(busDir, newId);
    assert.strictEqual(tombs.length, 1);
    const t = tombs[0];
    assert.strictEqual(t.runId, oldId);
    assert.strictEqual(t.status, 'done');
    assert.strictEqual(t.taskId, 'T1', '系統情報（taskId/lineage）が解ける');
    assert.strictEqual(t.lineageId, 'req-abc123-T1');
    assert.strictEqual(t.tombstone, true);
    assert.strictEqual(t.counts.done, 1);
    assert.strictEqual(t.counts.failed, 1);
    assert.strictEqual(t.nodes.a.output, 'out-a', '工程出力（抜粋）が残る');
    assert.strictEqual(t.final.summary, '完走（verify NG で世代交代）');
    assert.deepStrictEqual(t.tombstoneArtifacts, ['a/report.md']);
    // アーカイブへ写せば bus から新 run が消えても読める（ipc flow:runs の補完と同じ経路）
    assert.strictEqual(flow.archiveRunSnapshot(root, busDir, t), true);
    fs.rmSync(runDir, { recursive: true, force: true });
    const snap = flow.readArchivedRun(root, oldId);
    assert.ok(snap && snap.run && snap.run.runId === oldId);
    assert.strictEqual(snap.run.tombstone, true);
    assert.strictEqual(snap.run.counts.done, 1);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('スナップショットの長い工程出力は冒頭＋末尾の抜粋で保存される', () => {
  const { root, busDir, runDir } = mkBus('run-long-output');
  const long = `先頭-${'x'.repeat(9000)}-末尾`;
  fs.writeFileSync(
    path.join(runDir, 'results', 'a.json'),
    JSON.stringify({ status: 'done', who: 'w1', finished_at: '2026-07-11T00:05:00Z', output: long }),
    'utf8'
  );
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.a.output, long, 'live 表示は全文のまま');
  flow.archiveRunSnapshot(root, busDir, run);
  const archived = flow.readArchivedRun(root, run.runId);
  const saved = archived.run.nodes.a.output;
  assert.ok(saved.length < long.length, 'アーカイブは抜粋');
  assert.ok(saved.includes('先頭-') && saved.includes('-末尾'), '冒頭と末尾は残す');
  assert.ok(saved.includes('中略'), '省略を明示する');
  assert.strictEqual(run.nodes.a.output, long, '渡した run オブジェクトは変更しない');
});

test('アーカイブはプロジェクトフォルダ配下（<dir>/flow-archive/）に置かれる', () => {
  // 置き場がプロジェクトの中にあることが、リセットで一緒に消える根拠（reset は非ドットの
  // 直下エントリを対象にする）。バスのパスでハッシュ分けした外部ディレクトリには置かない。
  const runId = 'run-xyz';
  const { root, busDir } = mkBus(runId);
  try {
    const [run] = flow.listRuns(busDir);
    flow.archiveRunSnapshot(root, busDir, run);
    const dir = path.join(root, 'flow-archive');
    assert.strictEqual(flow.flowArchiveDir(root), dir);
    assert.ok(fs.existsSync(path.join(dir, `${runId}.json`)), 'run ごとの JSON が置かれる');
    assert.ok(fs.readdirSync(root).includes('flow-archive'), 'プロジェクト直下の非ドット名');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('readArchivedRun は不正な runId / 未知の runId に null を返す', () => {
  const { root } = mkBus('run-1');
  try {
    assert.strictEqual(flow.readArchivedRun(root, '../etc'), null);
    assert.strictEqual(flow.readArchivedRun(root, 'unknown'), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});


// run を削除したらアーカイブのスナップショットも消す。残すと一覧が「live に無いアーカイブ」
// として拾い直し、削除したのに表示から消えない（人から見れば削除が効いていない）。
test('run の削除でアーカイブのスナップショットも消える', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-del-'));
  const runId = 'run-20260101-000000-1111';
  const archDir = flow.flowArchiveDir(dir);
  fs.mkdirSync(archDir, { recursive: true });
  const snap = path.join(archDir, `${runId}.json`);
  fs.writeFileSync(snap, JSON.stringify({ savedAt: 'x', run: { runId, status: 'done' } }));

  assert.strictEqual(flow.listArchivedRuns(dir).length, 1, '前提: アーカイブに 1 件ある');
  const removed = flow.removeArchivedRun(dir, runId);
  assert.ok(removed, 'スナップショットを消す');
  assert.strictEqual(flow.listArchivedRuns(dir).length, 0, '一覧から消える');
  assert.strictEqual(flow.removeArchivedRun(dir, runId), null, '無い run は null（冪等）');
});

// 中身の無いスナップショットを残さない。run が bus から消えた後に呼ばれると readRun は
// status='unknown' / total=0 の空を返す。それを保存すると、実体も記録も持たない「不明」な run が
// 一覧に永久に居座る（実際 11 件溜まり、viewer に「不明」が大量に並んだ）。
test('中身の無い run（status=unknown / total=0）はアーカイブしない', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-empty-'));
  const busDir = path.join(dir, 'bus');
  const empty = { runId: 'run-gone', status: 'unknown', total: 0, counts: {}, nodes: {} };
  assert.strictEqual(flow.archiveRunSnapshot(dir, busDir, empty), false, '保存しない');
  assert.strictEqual(flow.listArchivedRuns(dir).length, 0, '一覧に出ない');

  // 中身のある run は従来どおり保存する
  const real = { runId: 'run-real', status: 'done', total: 3, counts: { done: 3 }, nodes: {} };
  assert.strictEqual(flow.archiveRunSnapshot(dir, busDir, real), true);
  assert.strictEqual(flow.listArchivedRuns(dir).length, 1);
});


test('listRuns(limit<=0) は件数制限なし（live 集合用）', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-runs-'));
  const busDir = path.join(root, 'bus');
  for (let i = 0; i < 5; i += 1) {
    const runId = `req-abc-T${i}-r0`;
    const runDir = path.join(busDir, 'runs', runId);
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(
      path.join(runDir, 'meta.json'),
      JSON.stringify({
        status: 'running',
        created_at: `2026-07-11T00:0${i}:00Z`,
        updated_at: `2026-07-11T00:0${i}:00Z`,
      }),
      'utf8'
    );
    fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: {} }), 'utf8');
  }
  assert.strictEqual(flow.listRuns(busDir, 2).length, 2);
  assert.strictEqual(flow.listRuns(busDir, 0).length, 5);
  assert.strictEqual(flow.listRuns(busDir, -1).length, 5);
});

console.log(`\n${passed} passed`);