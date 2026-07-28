'use strict';

// バックログの削除と、その取り消し（墓標の解除）のビュアー層テスト。
// 追加依存なしで `node test/backlog-delete-tombstone.test.js` で走る。
//
// 背景（削除の契約の変遷）:
//   かつて削除は本体の却下（reject）へ委ねていた——当時は charter 分解が自動で走ったため、
//   生の削除では墓標が残らず「次の再分解が同じタスクを作り直す」からだ。いまは分解が人の
//   明示操作でしか走らないので、削除は**物理削除**（needs も一緒に掃除）に戻した:
//   - 削除 → 明示的な分解で同種のタスクがまた提案されるのは期待どおり（試行錯誤の口）。
//   - 「作り直させない」意思表示は ✕ 却下（reject）——archive・墓標・決定記録に残り、
//     次の分解でプランナーに「意図の似た再提案も抑止」として渡る。
//   墓標の一覧と解除（revive）は却下の取り消しの口として引き続き画面に置く。

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
  await test('削除は物理削除（backlog と needs を掃除し、指示は投函しない）', async () => {
    const dir = mkProject();
    writeTask(dir, 'T1');
    fs.writeFileSync(path.join(dir, 'needs', 'T1.md'), '# 要対応\n', 'utf8');
    const res = await actions.requestDeleteTask(cfg, { dir, id: 'T1' });
    assert.strictEqual(res.via, 'delete');
    assert.ok(!fs.existsSync(path.join(dir, 'backlog', 'T1.md')), 'タスクファイルは消える');
    assert.ok(!fs.existsSync(path.join(dir, 'needs', 'T1.md')),
      '要対応カードも一緒に消える（孤児票は [x] でも消せない袋小路になるため）');
    assert.ok(!fs.existsSync(path.join(dir, 'commands')), '本体への指示は投函しない');
  });

  await test('trash が渡されればゴミ箱移動を優先する', async () => {
    const dir = mkProject();
    writeTask(dir, 'T1b');
    const trashed = [];
    const res = await actions.requestDeleteTask(cfg, { dir, id: 'T1b' }, async (target) => {
      trashed.push(target);
      fs.rmSync(target, { force: true });
      return 'trash';
    });
    assert.strictEqual(res.via, 'trash');
    assert.deepStrictEqual(trashed, [path.join(dir, 'backlog', 'T1b.md')]);
  });

  await test('実行中（doing）のタスクは削除できない', async () => {
    const dir = mkProject();
    writeTask(dir, 'T2', { status: 'doing' });
    await assert.rejects(async () => actions.requestDeleteTask(cfg, { dir, id: 'T2' }), /実行中/);
    assert.ok(fs.existsSync(path.join(dir, 'backlog', 'T2.md')), 'ファイルは残る');
  });

  await test('委譲実行中（offloaded）のタスクは削除できない', async () => {
    const dir = mkProject();
    writeTask(dir, 'T3', { status: 'offloaded' });
    await assert.rejects(async () => actions.requestDeleteTask(cfg, { dir, id: 'T3' }), /委譲先で実行中/);
    assert.ok(fs.existsSync(path.join(dir, 'backlog', 'T3.md')), 'ファイルは残る');
  });

  await test('存在しないタスク ID は拒否する', async () => {
    const dir = mkProject();
    await assert.rejects(async () => actions.requestDeleteTask(cfg, { dir, id: 'NOPE' }),
      /タスクファイルがありません/);
    await assert.rejects(async () => actions.requestDeleteTask(cfg, { dir, id: '../evil' }),
      /不正なタスク ID/);
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
