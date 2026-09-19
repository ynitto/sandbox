'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const skills = require('../src/main/skills');
const removal = require('../src/main/skillRemoval');

function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-removal-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const roots = [
    { path: path.join(base, 'repo/.agents/skills'), kind: 'skill-dir', place: 'repo', repo: path.join(base, 'repo') },
    { path: path.join(base, 'home/.agents/skills'), kind: 'skill-dir', place: 'home', repo: '' },
    { path: path.join(base, 'home/.claude/commands'), kind: 'command-dir', place: 'home', repo: '' },
  ];
  for (const root of roots) fs.mkdirSync(root.path, { recursive: true });
  const add = (root, name) => {
    const dir = path.join(root.path, root.kind === 'command-dir' ? `${name}.md` : name);
    if (root.kind !== 'command-dir') fs.mkdirSync(dir, { recursive: true });
    const file = root.kind === 'command-dir' ? dir : path.join(dir, 'SKILL.md');
    fs.writeFileSync(file, `# ${name}`);
    return dir;
  };
  const items = () => skills.catalogFromRoots(roots).map((item) => ({ ...item, ...removal.describe(item, roots) }));
  const trashed = [];
  const trash = path.join(base, 'trash');
  fs.mkdirSync(trash);
  const run = (keys, overrides = {}) => removal.remove({
    keys, roots: () => roots, confirm: async () => true,
    trashItem: async (target) => {
      trashed.push(target);
      fs.renameSync(target, path.join(trash, `${trashed.length}-${path.basename(target)}`));
    }, ...overrides,
  });
  return { base, roots, add, items, trashed, trash, run };
}

test('選んだスキルのフォルダ全体と共通コマンドをゴミ箱へ渡す。同名の別コピーは残す', async (t) => {
  const f = fixture(t);
  const repo = f.add(f.roots[0], 'review');
  const other = f.add(f.roots[1], 'review');
  const common = f.add(f.roots[1], 'design');
  const command = f.add(f.roots[2], 'deploy');
  fs.writeFileSync(path.join(repo, 'helper.py'), 'print(1)');
  const selected = f.items();
  assert.equal(selected.length, 3);
  let confirmation;
  const result = await f.run(selected.map((item) => item.removalKey), {
    confirm: async (items) => { confirmation = items; return true; },
  });
  assert.equal(result.removed.length, 3);
  assert.equal(result.failed.length, 0);
  assert.equal(result.cancelled, false);
  assert.deepEqual(new Set(f.trashed), new Set([repo, common, command].map((p) => fs.realpathSync(path.dirname(p)) + path.sep + path.basename(p))));
  assert.ok(confirmation.every((item) => item.name && path.isAbsolute(item.deletePath)));
  assert.ok(fs.existsSync(other));
  assert.equal(fs.readFileSync(path.join(f.trash, '3-review/helper.py'), 'utf8'), 'print(1)');
  assert.notEqual(f.items()[0].removalKey, selected.find((item) => item.name === 'review').removalKey);
});

test('確認のキャンセルはファイルを変更しない', async (t) => {
  const f = fixture(t);
  const target = f.add(f.roots[0], 'review');
  const result = await f.run([f.items()[0].removalKey], { confirm: async () => false });
  assert.deepEqual(result, { cancelled: true, removed: [], failed: [] });
  assert.deepEqual(f.trashed, []);
  assert.ok(fs.existsSync(target));
});

test('空・任意のパス・未知のキー・別のAIやリポジトリのキーを拒否する', async (t) => {
  const f = fixture(t);
  f.add(f.roots[0], 'review');
  const key = f.items()[0].removalKey;
  const confirm = async () => { assert.fail('不正な選択では確認まで進まない'); };
  for (const keys of [[], null, ['../../SKILL.md'], ['0'.repeat(64)], [key, '0'.repeat(64)]]) {
    await assert.rejects(f.run(keys, { confirm }));
  }
  await assert.rejects(f.run([key], { roots: () => f.roots.slice(1), confirm }));
  assert.deepEqual(f.trashed, []);
});

test('確認中にスキルが差し替わった場合は新しい実体を消さない', async (t) => {
  const f = fixture(t);
  const dir = f.add(f.roots[0], 'review');
  const key = f.items()[0].removalKey;
  const result = await f.run([key], { confirm: async () => {
    fs.renameSync(dir, path.join(f.base, 'old-review'));
    f.add(f.roots[0], 'review');
    return true;
  } });
  assert.equal(result.removed.length, 0);
  assert.match(result.failed[0].error, /変わりました/);
  assert.deepEqual(f.trashed, []);
  assert.ok(fs.existsSync(dir));
});

test('削除したあとに同じ名前の別コピーが現れても、古いキーで消さない', async (t) => {
  const f = fixture(t);
  f.add(f.roots[0], 'review');
  const other = f.add(f.roots[1], 'review');
  const key = f.items()[0].removalKey;
  await f.run([key, key]);
  assert.equal(f.trashed.length, 1);
  await assert.rejects(f.run([key]), /選び直して/);
  assert.ok(fs.existsSync(other));
});

test('リンクになった置き場・スキルは削除せず、内側のリンクの参照先も消さない', async (t) => {
  const f = fixture(t);
  const dir = f.add(f.roots[0], 'review');
  const original = f.items()[0];
  const external = path.join(f.base, 'external');
  fs.mkdirSync(external);
  fs.writeFileSync(path.join(external, 'SKILL.md'), '# external');
  fs.renameSync(dir, path.join(f.base, 'old-review'));
  fs.symlinkSync(external, dir, 'dir');
  assert.equal(removal.describe(original, f.roots).removalKey, '');
  await assert.rejects(f.run([original.removalKey]));
  fs.unlinkSync(dir);
  f.add(f.roots[0], 'review');
  fs.symlinkSync(external, path.join(dir, 'references'), 'dir');
  await f.run([f.items()[0].removalKey]);
  assert.ok(fs.existsSync(path.join(external, 'SKILL.md')));

  f.add(f.roots[0], 'another');
  const before = f.items()[0];
  fs.renameSync(f.roots[0].path, path.join(f.base, 'linked-skills'));
  fs.symlinkSync(path.join(f.base, 'linked-skills'), f.roots[0].path, 'dir');
  const after = f.items()[0];
  assert.equal(after.removalKey, '');
  assert.match(after.removalError, /リンク経由/);
  await assert.rejects(f.run([before.removalKey]));
});

test('ゴミ箱の失敗は理由を返し、完全削除せず、他の選択項目の結果も返す', async (t) => {
  const f = fixture(t);
  const one = f.add(f.roots[0], 'one');
  f.add(f.roots[0], 'two');
  const attempted = [];
  const result = await f.run(f.items().map((item) => item.removalKey), {
    trashItem: async (target) => {
      attempted.push(target);
      if (path.basename(target) === 'one') throw new Error('ゴミ箱を使えません');
      fs.renameSync(target, path.join(f.trash, 'two'));
    },
  });
  assert.equal(attempted.length, 2);
  assert.equal(result.removed[0].name, 'two');
  assert.equal(result.failed[0].name, 'one');
  assert.equal(result.failed[0].error, 'ゴミ箱を使えません');
  assert.ok(fs.existsSync(one));
});

test('確認中に登録リポジトリが解除されたら処理を止める', async (t) => {
  const f = fixture(t);
  f.add(f.roots[0], 'review');
  let registered = true;
  const result = await f.run([f.items()[0].removalKey], {
    roots: () => { if (!registered) throw new Error('登録していないフォルダです'); return f.roots; },
    confirm: async () => { registered = false; return true; },
  });
  assert.match(result.failed[0].error, /登録していない/);
  assert.deepEqual(f.trashed, []);
});
