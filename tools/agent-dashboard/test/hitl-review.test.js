'use strict';

// ヒューマンインザループ強化のビュアー層テスト。追加依存なしで
// `node test/hitl-review.test.js` で走る。
//   - actions.runAction('reject'): commands/ ドロップ（id あり）
//   - project.dependentsOf: after 逆辺の推移閉包（却下・修正の影響一覧）
//   - authoring: charters/<name>.md の編集許可・charter 名つき作成（複数バージョン運用）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const project = require('../src/main/project');
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
      { projects: { actionMode: 'file' } },
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

  await test("runAction は稼働状態に依らず file 経路一本（案2後半）", async () => {
    // actionMode 指定は無視され、常に commands ドロップ。CLI 経路・fallback は撤去。
    const dir = mkProject();
    const orig = project.isProjectRunning;
    project.isProjectRunning = () => false; // 停止中でも CLI へ行かずドロップ
    try {
      const res = await actions.runAction(
        { projects: { command: 'no-such-binary-xyz' } },
        { dir, action: 'hold', id: 'T2', reason: '保留' }
      );
      assert.strictEqual(res.via, 'file');
      assert.ok(!('cliError' in res));
    } finally {
      project.isProjectRunning = orig;
    }
  });

  await test("runAction('approve', complete) は complete フラグをドロップに載せる", async () => {
    // 以前の file-drop 経路は complete を落としており、稼働中に「承認して完了にする」を押すと
    // 完了指定が失われた（承認して完了にできない不具合の一因）。ドロップに complete を通す。
    const dir = mkProject();
    await actions.runAction({ projects: {} },
      { dir, action: 'approve', id: 'T3', reason: '成果を確認', complete: true });
    const files = fs.readdirSync(path.join(dir, 'commands')).filter((f) => f.endsWith('.json'));
    const rec = JSON.parse(fs.readFileSync(path.join(dir, 'commands', files[0]), 'utf8'));
    assert.strictEqual(rec.command, 'approve');
    assert.strictEqual(rec.complete, true);
  });

  await test('dependentsOf は after 逆辺の推移閉包を返す', async () => {
    const tasks = [
      { id: 'A', status: 'ready', extra: {} },
      { id: 'B', status: 'ready', extra: { after: 'A' } },
      { id: 'C', status: 'proposed', extra: { after: 'B, X' } },
      { id: 'D', status: 'ready', extra: {} },
    ];
    const downs = project.dependentsOf(tasks, 'A').map((t) => t.id);
    assert.deepStrictEqual(downs.sort(), ['B', 'C']); // 推移（A→B→C）。D は無関係
    assert.deepStrictEqual(project.dependentsOf(tasks, 'D'), []);
  });

  await test('readProject は rules.md（プロジェクトルール）を返す', async () => {
    const dir = mkProject();
    fs.writeFileSync(path.join(dir, 'rules.md'), '- テストは pytest -q で回す\n', 'utf8');
    const p = project.readProject(dir, { projects: {} });
    assert.match(p.rules, /pytest -q/);
    // 無いプロジェクトでは null（後方互換）
    assert.strictEqual(project.readProject(mkProject(), { projects: {} }).rules, null);
  });

  await test('readProject は specs/<task-id>/ の spec 成果物を一覧する', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'specs', 'T1'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'spec.md'), '# 要求仕様\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'design.md'), '# 設計\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'T1', 'tasks.md'), '[{"title":"x"}]\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'specs', 'stray.md'), 'ディレクトリ以外は無視\n', 'utf8');
    const p = project.readProject(dir, { projects: {} });
    assert.strictEqual(p.specs.length, 1);
    assert.strictEqual(p.specs[0].id, 'T1');
    assert.deepStrictEqual(
      p.specs[0].files.map((f) => f.name),
      ['spec.md', 'design.md', 'tasks.md']
    );
    // specs/ の無いプロジェクトでは空配列（後方互換）
    const p2 = project.readProject(mkProject(), { projects: {} });
    assert.deepStrictEqual(p2.specs, []);
  });

  await test('readProject は charters/*.md を一覧する（複数バージョン運用）', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v1.md'),
      '# Charter: v1\n## goal\n保守\n', 'utf8');
    fs.writeFileSync(path.join(dir, 'charters', 'v2.md'),
      '# Charter: v2\n## goal\n新機能\n', 'utf8');
    const p = project.readProject(dir, { projects: {} });
    assert.deepStrictEqual(p.charters.map((c) => c.name), ['v1', 'v2']);
    assert.strictEqual(p.charters[1].goal, '新機能');
  });

  await test('review の backlog に needs が無くても合成して要対応に出す（検収導線）', async () => {
    const dir = mkProject();
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T-review.md'),
      '## T-review: 成果を検収する\n- status: review\n- source: human\n- verify: `true`\n',
      'utf8'
    );
    const p = project.readProject(dir, { projects: {} });
    const n = p.needs.find((x) => x.id === 'T-review');
    assert.ok(n, 'needs ファイルが無くても票が出る');
    assert.strictEqual(n.kind, 'review');
    assert.strictEqual(n.synthesized, true);
    assert.match(n.why, /検収/);
    assert.ok(!fs.existsSync(path.join(dir, 'needs', 'T-review.md')), '読み取り時にファイルは作らない');
  });

  await test('既に needs がある review は二重合成しない', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'needs'), { recursive: true });
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T1.md'),
      '## T1: x\n- status: review\n- source: human\n',
      'utf8'
    );
    fs.writeFileSync(
      path.join(dir, 'needs', 'T1.md'),
      '---\nkind: review\ntask-id: T1\n---\n# 要対応: T1 — x\n\n- なぜ: 実ファイル\n',
      'utf8'
    );
    const p = project.readProject(dir, { projects: {} });
    assert.strictEqual(p.needs.filter((x) => x.id === 'T1').length, 1);
    assert.ok(!p.needs.find((x) => x.id === 'T1').synthesized);
    assert.match(p.needs.find((x) => x.id === 'T1').why, /実ファイル/);
  });

  await test('合成票への差し戻しは needs スタブを起こしてから feedback する', async () => {
    const dir = mkProject();
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T-fb.md'),
      '## T-fb: 差し戻し対象\n- status: review\n- source: human\n',
      'utf8'
    );
    const p = project.readProject(dir, { projects: {} });
    const n = p.needs.find((x) => x.id === 'T-fb');
    assert.ok(n && n.synthesized);
    assert.ok(!fs.existsSync(n.file));
    const res = actions.submitFeedback(n.file, 'ここを直して', {
      id: n.id, kind: n.kind, title: n.title, why: n.why,
    });
    assert.strictEqual(res.feedback, 'ここを直して');
    assert.ok(fs.existsSync(n.file), 'スタブファイルが作られる');
    const body = fs.readFileSync(n.file, 'utf8');
    assert.match(body, /kind: review/);
    assert.match(body, /ここを直して/);
    assert.match(body, /- \[x\]/);
  });

  await test('backlog と種別が違う stale needs は status の投影へ自己修復する', async () => {
    const dir = mkProject();
    fs.mkdirSync(path.join(dir, 'needs'), { recursive: true });
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T-stale.md'),
      '## T-stale: 検証失敗\n- status: blocked\n- needs_reason: 回帰テスト失敗\n',
      'utf8'
    );
    fs.writeFileSync(
      path.join(dir, 'needs', 'T-stale.md'),
      '---\nkind: plan-review\ntask-id: T-stale\n---\n# 古い計画レビュー\n',
      'utf8'
    );
    const p = project.readProject(dir, { projects: {} });
    const matches = p.needs.filter((n) => n.id === 'T-stale' || n.taskId === 'T-stale');
    assert.strictEqual(matches.length, 1, '同じタスクの票を二重表示しない');
    assert.strictEqual(matches[0].kind, 'blocked', 'backlog status を正として種別を補正する');
    assert.strictEqual(matches[0].status, 'blocked');
    assert.ok(matches[0].synthesized, '不整合を表示時に明示的に自己修復する');
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
    const p = project.readProject(dir, { projects: {} });
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
