'use strict';

// 初版 charter.md のバージョン化（charters/<name>.md への昇格）のテスト。
// 追加依存なしで `node test/charter-promote.test.js` で走る。
//   - charter.md → charters/<name>.md へ移動（本文は無変更）
//   - 未タグ backlog タスクへ `- charter: <name>` を付与（タグ済みは触らない）
//   - project.json のトップレベル収束状態（accepted 等）を charters[<name>] へ移す
//   - milestone カード needs/<pid>.md を needs/<pid>-<name>.md へ改名

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

const CHARTER = '# Charter: demo\n\n## goal\nCSV を要約する\n\n## acceptance\n- `true`\n';

function mkProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-promote-'));
  const dir = path.join(root, 'demo');
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'needs'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charter.md'), CHARTER, 'utf8');
  return { root, dir };
}

test('promoteCharterVersion は charter.md を charters/<name>.md へ移し状態を引き継ぐ', () => {
  const { root, dir } = mkProject();
  try {
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T1.md'),
      '## T1: 未タグのタスク\n- status: ready\n- verify: `true`\n',
      'utf8'
    );
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T2.md'),
      '## T2: タグ済みのタスク\n- status: ready\n- charter: v2\n',
      'utf8'
    );
    const pid = path.basename(dir); // agent-project の _project_id と同じ（ルートのディレクトリ名）
    fs.writeFileSync(
      path.join(dir, 'project.json'),
      JSON.stringify({
        id: pid,
        name: 'demo',
        history: [2],
        best: 2,
        status: 'accepted',
        accepted_charter_sig: 'sig',
        charters: { v2: { id: `${pid}-v2`, status: 'converged' } },
      }),
      'utf8'
    );
    fs.writeFileSync(path.join(dir, 'needs', `${pid}.md`), '# マイルストーン: demo\n', 'utf8');

    const res = authoring.promoteCharterVersion(dir, 'v1');
    assert.strictEqual(res.name, 'v1');
    assert.strictEqual(res.tagged, 1, '未タグの 1 件だけタグ付けされる');

    // 1. 本文の移動（無変更）
    assert.ok(!fs.existsSync(path.join(dir, 'charter.md')), 'charter.md は消える');
    assert.strictEqual(fs.readFileSync(path.join(dir, 'charters', 'v1.md'), 'utf8'), CHARTER);

    // 2. タスクの帰属タグ
    assert.match(fs.readFileSync(path.join(dir, 'backlog', 'T1.md'), 'utf8'), /- charter: v1\n$/);
    const t2 = fs.readFileSync(path.join(dir, 'backlog', 'T2.md'), 'utf8');
    assert.ok(!t2.includes('charter: v1'), 'タグ済みタスクは触らない');

    // 3. project.json の状態移行
    const state = JSON.parse(fs.readFileSync(path.join(dir, 'project.json'), 'utf8'));
    assert.strictEqual(state.id, undefined, 'トップレベルの初版状態は消える');
    assert.strictEqual(state.charters.v1.id, `${pid}-v1`, 'id は <project>-<name>');
    assert.strictEqual(state.charters.v1.status, 'accepted', '承認済みを引き継ぐ');
    assert.strictEqual(state.charters.v1.accepted_charter_sig, 'sig');
    assert.strictEqual(state.charters.v2.status, 'converged', '既存バージョンの状態はそのまま');

    // 4. milestone カードの改名
    assert.ok(!fs.existsSync(path.join(dir, 'needs', `${pid}.md`)));
    assert.ok(fs.existsSync(path.join(dir, 'needs', `${pid}-v1.md`)));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('promoteCharterVersion は project.json が無くても本文の移動だけ行う', () => {
  const { root, dir } = mkProject();
  try {
    const res = authoring.promoteCharterVersion(dir, 'base');
    assert.strictEqual(res.tagged, 0);
    assert.ok(fs.existsSync(path.join(dir, 'charters', 'base.md')));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('promoteCharterVersion は不正な名前・対象なし・上書きを拒否する', () => {
  const { root, dir } = mkProject();
  try {
    assert.throws(() => authoring.promoteCharterVersion(dir, ''), /バージョン名が不正/);
    assert.throws(() => authoring.promoteCharterVersion(dir, 'a b'), /バージョン名が不正/);
    assert.throws(() => authoring.promoteCharterVersion(dir, '..'), /バージョン名が不正/);

    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v9.md'), 'x', 'utf8');
    assert.throws(() => authoring.promoteCharterVersion(dir, 'v9'), /すでに存在/);

    fs.rmSync(path.join(dir, 'charter.md'));
    assert.throws(() => authoring.promoteCharterVersion(dir, 'v1'), /charter\.md がありません/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
