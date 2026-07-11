'use strict';

// ヒューマンインザループ強化のビュアー層テスト。追加依存なしで
// `node test/hitl-review.test.js` で走る。
//   - actions.runAction('reject'): commands/ ドロップ（id あり）
//   - kiro.dependentsOf: after 逆辺の推移閉包（却下・修正の影響一覧）
//   - authoring: charters/<name>.md の編集許可・charter 名つき作成（複数バージョン運用）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const kiro = require('../src/main/kiro');
const authoring = require('../src/main/authoring');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-hitl-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  return dir;
}

(async () => {
  await test("runAction('reject') は commands/ に却下指示をドロップする", async () => {
    const dir = mkProject();
    const res = await actions.runAction(
      { kiro: { actionMode: 'file' } },
      { dir, action: 'reject', id: 'T1', reason: '方針転換で不要' }
    );
    assert.strictEqual(res.via, 'file');
    const files = fs.readdirSync(path.join(dir, 'commands')).filter((f) => f.endsWith('.json'));
    assert.strictEqual(files.length, 1);
    const rec = JSON.parse(fs.readFileSync(path.join(dir, 'commands', files[0]), 'utf8'));
    assert.strictEqual(rec.command, 'reject');
    assert.strictEqual(rec.id, 'T1');
    assert.strictEqual(rec.reason, '方針転換で不要');
  });

  await test('dependentsOf は after 逆辺の推移閉包を返す', async () => {
    const tasks = [
      { id: 'A', status: 'ready', extra: {} },
      { id: 'B', status: 'ready', extra: { after: 'A' } },
      { id: 'C', status: 'proposed', extra: { after: 'B, X' } },
      { id: 'D', status: 'ready', extra: {} },
    ];
    const downs = kiro.dependentsOf(tasks, 'A').map((t) => t.id);
    assert.deepStrictEqual(downs.sort(), ['B', 'C']); // 推移（A→B→C）。D は無関係
    assert.deepStrictEqual(kiro.dependentsOf(tasks, 'D'), []);
  });

  await test('readProject は specs/<task-id>/ の spec 成果物を一覧する', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'specs', 'T1'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'spec.md'), '# 要求仕様\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'design.md'), '# 設計\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'tasks.md'), '[{"title":"x"}]\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'stray.md'), 'ディレクトリ以外は無視\n', 'utf8');
    const p = kiro.readProject(dir, { kiro: {} });
    assert.strictEqual(p.specs.length, 1);
    assert.strictEqual(p.specs[0].id, 'T1');
    assert.deepStrictEqual(
      p.specs[0].files.map((f) => f.name),
      ['spec.md', 'design.md', 'tasks.md']
    );
    // specs/ の無いプロジェクトでは空配列（後方互換）
    const p2 = kiro.readProject(mkProject(), { kiro: {} });
    assert.deepStrictEqual(p2.specs, []);
  });

  await test('readProject は charters/*.md を一覧する（複数バージョン運用）', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v1.md'),
      '# Charter: v1\n## goal\n保守\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'charters', 'v2.md'),
      '# Charter: v2\n## goal\n新機能\n', 'utf8');
    const p = kiro.readProject(dir, { kiro: {} });
    assert.deepStrictEqual(p.charters.map((c) => c.name), ['v1', 'v2']);
    assert.strictEqual(p.charters[1].goal, '新機能');
  });

  await test('charters の name はファイル名を優先する（# Charter タイトルが違っても化けない）', async () => {
    // 前バージョンをコピーしてタイトルを直し忘れると `# Charter:` が同じ名前になりがち。
    // それでもバージョンの identity（表示名・編集先・charter タグ）はファイル名でなければならない。
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v2.md'),
      '# Charter: sandbox\n## goal\nA\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'charters', 'v3.md'),
      '# Charter: sandbox\n## goal\nB\n', 'utf8');
    const p = kiro.readProject(dir, { kiro: {} });
    assert.deepStrictEqual(p.charters.map((c) => c.name), ['v2', 'v3']);
    assert.deepStrictEqual(p.charters.map((c) => c.title), ['sandbox', 'sandbox']);
    assert.deepStrictEqual(p.charters.map((c) => c.goal), ['A', 'B']); // 中身は各ファイルのまま
  });

  await test('authoring は charters/<name>.md の編集を許可し、パス外は拒否する', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v1.md'), '# Charter: v1\n', 'utf8');
    const info = authoring.readProjectFile(dir, 'charters/v1.md');
    assert.ok(info.exists);
    assert.ok(info.label.includes('charters/v1.md'));
    authoring.writeProjectFile(dir, 'charters/v1.md', '# Charter: v1\n## goal\ny\n');
    assert.ok(fs.readFileSync(path.join(dir, 'charters', 'v1.md'), 'utf8').includes('goal'));
    assert.throws(() => authoring.readProjectFile(dir, 'charters/../secrets.md'));
    assert.throws(() => authoring.readProjectFile(dir, 'backlog/T1.md'));
  });

  await test('createProject は charterName 指定で charters/<name>.md に作る', async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-hitl-root-'));
    const res = authoring.createProject({
      root, name: 'proj', charterName: 'v1', goal: 'g', acceptance: 'true',
    });
    assert.strictEqual(res.charterFile, path.join(root, 'proj', 'charters', 'v1.md'));
    const body = fs.readFileSync(res.charterFile, 'utf8');
    assert.ok(body.startsWith('# Charter: v1'));
    // charterName 無しは従来の charter.md
    const res2 = authoring.createProject({ root, name: 'proj2', goal: 'g' });
    assert.strictEqual(res2.charterFile, path.join(root, 'proj2', 'charter.md'));
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
