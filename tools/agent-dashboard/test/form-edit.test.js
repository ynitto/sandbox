'use strict';

// フォーム編集（charter / policy / repos の構造化パース・シリアライズ）のテスト。
// 追加依存なしで `node test/form-edit.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
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
  assert.strictEqual(f._constraintsDefined, true);
  assert.strictEqual(f._assumptionsDefined, false);
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
  const f = {
    name: 'v2', master: false, goal: 'やること', acceptance: ['pytest -q'],
    constraints: ['v2 だけの制約'], assumptions: ['v2 だけの前提'],
  };
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /## acceptance\n- pytest -q/);
  assert.match(out, /## constraints\n- v2 だけの制約/);
  assert.match(out, /## assumptions\n- v2 だけの前提/);
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

test('fieldsToCharter は _constraintsDefined:false で見出しを省略する（マスター継承の維持）', () => {
  // 見出しの無いバージョン＝マスターへ動的に追従。フォームで開いて保存しても
  // 見出しを書き足さない（書くと「明示値」になり以後マスターの変更が伝搬しない）。
  const md = '# Charter: v1\n\n## goal\nx\n\n## acceptance\n- true\n';
  const f = authoring.charterToFields(md);
  assert.strictEqual(f._constraintsDefined, false);
  f.constraints = ['マスターの制約（表示用プレビュー）']; // 画面表示で値が入っていても
  const out = authoring.fieldsToCharter(f);
  assert.ok(!/^## constraints$/m.test(out), '見出しは書かれない（追従を維持）');
  assert.ok(!out.includes('マスターの制約'), 'プレビュー値は書き込まれない');
  // 再パースしても未定義のまま＝往復で継承状態を失わない
  assert.strictEqual(authoring.charterToFields(out)._constraintsDefined, false);
});

test('fieldsToCharter は _constraintsDefined:true なら空でも見出しを書く（空で上書きの明示）', () => {
  const f = {
    name: 'v1', goal: 'x', acceptance: ['true'],
    constraints: [], _constraintsDefined: true,        // 明示の空 = 継承値を空に上書き
    assumptions: ['前提あり'], _assumptionsDefined: true,
  };
  const out = authoring.fieldsToCharter(f);
  assert.match(out, /^## constraints$/m, '空でも見出しが残る（本体は「空に上書き」と解釈）');
  assert.match(out, /## assumptions\n- 前提あり/);
  // フラグ未指定（旧呼び出し）は従来どおり書く
  const legacy = authoring.fieldsToCharter({ name: 'v1', goal: 'x', constraints: ['c1'] });
  assert.match(legacy, /## constraints\n- c1/);
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

test('repos フォーム往復は path/target/readonly とフォーム外キー（local 等）を失わない', () => {
  // モノレポ: 同じ URL を path 別のエントリに分け、local / dir / docs などフォームに
  // 列が無いキーも持つ repos.json。フォームを開いて保存しても消えないこと。
  const json = JSON.stringify({
    'app-api': {
      url: 'git@h:t/mono.git', base: 'main', target: 'develop', path: 'apps/api',
      owns: ['apps/api/**'], local: '/home/me/mono', docs: ['docs/**'],
    },
    'app-web': { url: 'git@h:t/mono.git', base: 'main', path: 'apps/web', owns: ['apps/web/**'] },
    spec: { url: 'git@h:t/spec.git', readonly: true },
  });
  const rows = authoring.reposJsonToRows(json);
  const api = rows.find((r) => r.name === 'app-api');
  assert.strictEqual(api.path, 'apps/api');
  assert.strictEqual(api.target, 'develop');
  assert.deepStrictEqual(api._extra, { local: '/home/me/mono', docs: ['docs/**'] });
  assert.strictEqual(rows.find((r) => r.name === 'spec').readonly, true);
  const data = JSON.parse(authoring.exportReposJson(rows, false));
  assert.strictEqual(data['app-api'].path, 'apps/api');
  assert.strictEqual(data['app-api'].target, 'develop');
  assert.strictEqual(data['app-api'].local, '/home/me/mono', 'フォーム外キーが保存で戻る');
  assert.deepStrictEqual(data['app-api'].docs, ['docs/**']);
  assert.strictEqual(data['app-web'].path, 'apps/web');
  assert.strictEqual(data.spec.readonly, true, '参照のみフラグが保存で戻る');
});

test('validateRepoRows は名前重複と (url, path, base) 重複を弾く', () => {
  // モノレポの正しい分割（path 違い）は通る
  authoring.validateRepoRows([
    { name: 'api', url: 'git@h:t/mono.git', base: 'main', path: 'apps/api' },
    { name: 'web', url: 'git@h:t/mono.git', base: 'main', path: 'apps/web' },
  ]);
  // 名前が同じ → エントリを黙って上書きするので弾く
  assert.throws(
    () => authoring.validateRepoRows([
      { name: 'app', url: 'git@h:t/a.git' },
      { name: 'app', url: 'git@h:t/b.git' },
    ]),
    /リポジトリ名 'app' が重複/
  );
  // 同じ URL で path も base/target も同じ → identity が潰れるので弾く
  assert.throws(
    () => authoring.validateRepoRows([
      { name: 'a', url: 'git@h:t/mono.git', base: 'main' },
      { name: 'b', url: 'git@h:t/mono.git', base: 'main' },
    ]),
    /重複/
  );
});

test('reposFileName は本体と同じ yaml → yml → json の優先順で解決する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-form-'));
  try {
    assert.strictEqual(authoring.reposFileName(tmp), 'repos.json', '無ければ既定 repos.json');
    fs.writeFileSync(path.join(tmp, 'repos.json'), '{}', 'utf8');
    assert.strictEqual(authoring.reposFileName(tmp), 'repos.json');
    fs.writeFileSync(path.join(tmp, 'repos.yaml'), 'app:\n  url: git@h:t/a.git\n', 'utf8');
    assert.strictEqual(authoring.reposFileName(tmp), 'repos.yaml', 'yaml があれば yaml が正');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
