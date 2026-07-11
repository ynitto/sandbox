'use strict';

// フォーム編集（charter / policy / repos の構造化パース・シリアライズ）のテスト。
// 追加依存なしで `node test/form-edit.test.js` で走る。

const assert = require('assert');
const authoring = require('../src/main/authoring');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- charter: フィールド往復 ---

test('charterToFields は各セクションを配列/文字列に分解する', () => {
  const md =
    '# Charter: v1\n\n## goal\nCSV を要約する\n\n' +
    '## constraints\n- 標準ライブラリのみ\n- 後方互換を壊さない\n\n' +
    '## acceptance\n- `pytest -q`\n- accept: 使用例が載っている\n\n' +
    '## repos\n- app = git@h:t/app.git\n  - base: main\n';
  const f = authoring.charterToFields(md);
  assert.strictEqual(f.name, 'v1');
  assert.strictEqual(f.master, false);
  assert.strictEqual(f.goal, 'CSV を要約する');
  assert.deepStrictEqual(f.constraints, ['標準ライブラリのみ', '後方互換を壊さない']);
  assert.deepStrictEqual(f.acceptance, ['pytest -q', 'accept: 使用例が載っている']);
  assert.match(f._reposRaw, /app = git@h:t\/app\.git/); // repos は保持（フォームでは触らない）
});

test('fieldsToCharter は往復で内容を保つ（repos は保持される）', () => {
  const md =
    '# Charter: v1\n\n## goal\nx\n\n## acceptance\n- `true`\n\n' +
    '## repos\n- app = git@h:t/app.git\n  - base: main\n';
  const f = authoring.charterToFields(md);
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /# Charter: v1/);
  assert.match(out, /## goal\nx/);
  assert.match(out, /## acceptance\n- true/); // バッククォートは剥がして素のコマンドで保存
  assert.match(out, /## repos\n- app = git@h:t\/app\.git/); // 保持セクションが戻る
  // 再パースして安定していること
  const f2 = authoring.charterToFields(out);
  assert.deepStrictEqual(f2.acceptance, ['true']);
});

test('マスター憲章のフォームは完了条件を書き出さない', () => {
  const f = {
    name: 'demo',
    master: true,
    goal: '全体目標',
    constraints: ['標準ライブラリのみ'],
    acceptance: ['これは書かれないはず'],
    _masterRaw: '',
  };
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /^## master$/m, 'master セクションは出る');
  assert.ok(!/^## acceptance$/m.test(out), 'マスターは acceptance を書かない');
  assert.ok(!out.includes('これは書かれないはず'));
  assert.match(out, /## constraints\n- 標準ライブラリのみ/);
});

test('バージョン（master=false）のフォームは完了条件を書き出す', () => {
  const f = { name: 'v2', master: false, goal: 'やること', acceptance: ['pytest -q'] };
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /## acceptance\n- pytest -q/);
  assert.ok(!/^## master$/m.test(out));
});

test('リセットのマスター化: 非マスター charter を master 化し完了条件を落とす（他は保つ）', () => {
  // リセット時に ipc が行う変換: charterToFields → master=true → fieldsToCharter。
  // これで charter.md は分解されないマスターになり、リセット後に初版のマイルストーンが出ない。
  const md =
    '# Charter: demo\n## goal\nやる\n## constraints\n- 標準ライブラリのみ\n' +
    '## acceptance\n- `pytest`\n## repos\n- app = git@h:t/app.git\n';
  const f = authoring.charterToFields(md);
  assert.strictEqual(f.master, false);
  f.master = true; // リセットがやること
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /^## master$/m, 'マスター宣言が付く');
  assert.ok(!/^## acceptance$/m.test(out), 'マスター化で完了条件は落ちる');
  assert.match(out, /## goal\nやる/, '目標は保たれる');
  assert.match(out, /## constraints\n- 標準ライブラリのみ/, '制約は保たれる');
  assert.match(out, /## repos\n- app = git@h:t\/app\.git/, 'リポジトリは保たれる');
  // 冪等: 既にマスターならフラグは true のまま
  assert.strictEqual(authoring.charterToFields(out).master, true);
});

// --- policy: ルール往復 ---

test('policyToRules / rulesToPolicy は往復する', () => {
  const text = 'deny: prod\npin: T3  # コメント\nroute: heavy=gpu\n';
  const rules = authoring.policyToRules(text);
  assert.deepStrictEqual(rules, [
    { kind: 'deny', value: 'prod' },
    { kind: 'pin', value: 'T3' },
    { kind: 'route', value: 'heavy=gpu' },
  ]);
  const out = authoring.rulesToPolicy(rules);
  assert.strictEqual(out, 'deny: prod\npin: T3\nroute: heavy=gpu\n');
});

test('rulesToPolicy は未知の種類・空の値を落とす', () => {
  const out = authoring.rulesToPolicy([
    { kind: 'deny', value: 'x' },
    { kind: 'bogus', value: 'y' },
    { kind: 'pin', value: '  ' },
  ]);
  assert.strictEqual(out, 'deny: x\n');
});

// --- repos: 行往復 ---

test('reposJsonToRows / exportReposJson（_meta 無し）は往復する', () => {
  const json = JSON.stringify({
    _meta: { generated_from: 'charter.md ## repos' },
    app: { url: 'git@h:t/app.git', base: 'main', owns: ['apps/**', 'services/**'], desc: '本体' },
    lib: { url: 'git@h:t/lib.git' },
  });
  const rows = authoring.reposJsonToRows(json);
  assert.strictEqual(rows.length, 2, '_meta は行にしない');
  const app = rows.find((r) => r.name === 'app');
  assert.strictEqual(app.owns, 'apps/**, services/**');
  const out = authoring.exportReposJson(rows, false); // フォーム保存は _meta 無し（手管理）
  const data = JSON.parse(out);
  assert.ok(!data._meta, 'フォーム保存では _meta を付けない（repos.json が正になる）');
  assert.deepStrictEqual(data.app.owns, ['apps/**', 'services/**']);
  assert.strictEqual(data.lib.url, 'git@h:t/lib.git');
});

console.log(`\n${passed} passed`);
