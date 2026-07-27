'use strict';

// バックログの削除と、その取り消し（墓標の解除）のビュアー層テスト。
// 追加依存なしで `node test/backlog-delete-tombstone.test.js` で走る。
//
// 背景（この画面の「消しても消えない」）:
//   1. 削除が backlog/<id>.md の生 unlink だったため、要対応カード（needs/<id>.md）が残った。
//      対応タスクが無い票は本体の ingest_feedback が読み飛ばすので、[x] を付けても消せない。
//   2. 墓標（tombstones.md）が残らないので、charter 運用では次の再分解が同じタスクを作り直した。
//   3. 状態 git の裁定では backlog は実行側が正なので、viewer 側の生の削除は取り消されうる。
// 削除を本体の却下（reject）へ委ねると 3 つとも構造的に起きない。代わりに墓標が残るので、
// 一覧と解除（revive）を画面に用意して「消したら二度と入れ直せない」を作らない。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const project = require('../src/main/project');

const cfg = { projects: {} };

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-del-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'needs'), { recursive: true });
  return dir;
}

function writeTask(dir, id, { status = 'blocked', title = 'x' } = {}) {
  fs.writeFileSync(
    path.join(dir, 'backlog', `${id}.md`),
    `## ${id}: ${title}\n- status: ${status}\n- source: human\n- priority: 0\n- verify: \`true\`\n- retries: 0\n`,
    'utf8'
  );
}

function dropped(dir) {
  const cdir = path.join(dir, 'commands');
  const files = fs.readdirSync(cdir).filter((f) => f.endsWith('.json'));
  assert.strictEqual(files.length, 1, 'commands に 1 件だけドロップされる');
  return JSON.parse(fs.readFileSync(path.join(cdir, files[0]), 'utf8'));
}

(async () => {
  await test('削除は却下（reject）の指示として届く', async () => {
    const dir = mkProject();
    writeTask(dir, 'T1');
    const res = await actions.requestDeleteTask(cfg, { dir, id: 'T1' });
    assert.strictEqual(res.via, 'file');
    const rec = dropped(dir);
    assert.strictEqual(rec.command, 'reject');
    assert.strictEqual(rec.id, 'T1');
    // 本体が処理するまでタスクは消さない（生の削除だと状態 git の裁定で取り消されうる）
    assert.ok(fs.existsSync(path.join(dir, 'backlog', 'T1.md')), 'ファイルは本体が消す');
  });

  await test('実行中（doing）のタスクは削除できない', () => {
    const dir = mkProject();
    writeTask(dir, 'T2', { status: 'doing' });
    assert.throws(() => actions.requestDeleteTask(cfg, { dir, id: 'T2' }), /実行中/);
    assert.ok(!fs.existsSync(path.join(dir, 'commands')), '指示も投函しない');
  });

  await test('存在しないタスク ID は拒否する', () => {
    const dir = mkProject();
    assert.throws(() => actions.requestDeleteTask(cfg, { dir, id: 'NOPE' }), /タスクファイルがありません/);
    assert.throws(() => actions.requestDeleteTask(cfg, { dir, id: '../evil' }), /不正なタスク ID/);
  });

  await test('墓標の解除はタイトル指定のプロジェクト単位コマンドで届く', () => {
    const dir = mkProject();
    const res = actions.requestRevive(cfg, { dir, title: 'README に概要を足す' });
    assert.strictEqual(res.via, 'file');
    const rec = dropped(dir);
    assert.strictEqual(rec.command, 'revive');
    assert.strictEqual(rec.title, 'README に概要を足す');
    assert.strictEqual(rec.id, undefined, 'プロジェクト単位＝id を載せない');
  });

  await test('revive はタイトル未指定を拒否する', () => {
    const dir = mkProject();
    assert.throws(() => actions.requestRevive(cfg, { dir, title: '  ' }));
  });

  await test('墓標（tombstones.md）を読み、理由・日付・バージョンを取り出す', () => {
    const graves = project.parseTombstones(
      '# 墓標（このタスクは作り直さない）\n' +
        '<!-- 1 行 1 墓標: `- <タイトル> :: <理由> :: <日付> :: charter=<名前>`\n' +
        '     解除は `agent-project revive <タイトル>`。 -->\n\n' +
        '- README に概要を足す :: 要らなかった :: 2026-07-27 :: charter=v2\n' +
        '- 古い CI を消す :: 別タスクへ統合 :: 2026-07-26\n'
    );
    assert.deepStrictEqual(graves, [
      { title: 'README に概要を足す', reason: '要らなかった', date: '2026-07-27', charter: 'v2' },
      { title: '古い CI を消す', reason: '別タスクへ統合', date: '2026-07-26', charter: '' },
    ]);
  });

  await test('タスクを失った要対応カード（孤児）は一覧から落ちる', () => {
    // 従来は「タスクを持たない票 = milestone カード」と見なして残していた。だがタスク級の票は
    // タスクが消えた時点で操作不能（承認も却下も対象が無い）＝出し続けても人は何もできない。
    const needs = [
      { id: 'T1', taskId: 'T1', kind: 'blocked', title: 'T1 — 消したタスク' },
      { id: 'T2', taskId: 'T2', kind: 'review', title: 'T2 — 生きているタスク' },
      { id: 'proj', taskId: 'proj', kind: 'milestone', title: 'マイルストーン' },
    ];
    const backlog = [{ id: 'T2', status: 'review', title: '生きているタスク', extra: {} }];
    const out = project.synthesizeNeedsFromBacklog(needs, backlog, 'needs', []);
    assert.deepStrictEqual(out.map((n) => n.id), ['T2', 'proj'],
      '孤児のタスク票だけ落ち、milestone は残る');
  });

  console.log(`\n${passed} 件成功`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
