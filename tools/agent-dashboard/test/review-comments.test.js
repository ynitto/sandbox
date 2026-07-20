'use strict';

// 成果物レビューのコメント（チーム運用）のテスト。
//   - actions.addReviewComment / editReviewComment / deleteReviewComment
//     … reviews/<task-id>/*.json（1 コメント = 1 ファイル）への読み書き。
//     タスク状態ファイルには触れないこと。
//   - project.readReviewComments … 正規化・時系列ソート・空本文スキップ・不正 ID 拒否。
//   - project.readProject … needs（合成票含む）への comments 付与。
//   - renderer … コメント欄の描画ゲートと配線。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');
const actions = require('../src/main/actions');

let passed = 0;
function test(name, fn) {
  const r = fn();
  if (r && typeof r.then === 'function') {
    return r.then(() => {
      passed += 1;
      console.log(`ok - ${name}`);
    });
  }
  passed += 1;
  console.log(`ok - ${name}`);
  return undefined;
}

function tmpProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-rc-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  return dir;
}

async function main() {
  // --- actions.addReviewComment ---

  test('addReviewComment は reviews/<task>/ に 1 コメント = 1 ファイルを書く', () => {
    const dir = tmpProject();
    const res = actions.addReviewComment(dir, 'T1', { author: 'alice', text: 'ここは良い' });
    assert.ok(fs.existsSync(res.file));
    const saved = JSON.parse(fs.readFileSync(res.file, 'utf8'));
    assert.strictEqual(saved.author, 'alice');
    assert.strictEqual(saved.text, 'ここは良い');
    assert.ok(saved.ts, 'ts が付く');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('addReviewComment は複数メンバーの同時投稿を別ファイルとして共存させる', () => {
    const dir = tmpProject();
    actions.addReviewComment(dir, 'T1', { author: 'alice', text: 'A' });
    actions.addReviewComment(dir, 'T1', { author: 'bob', text: 'B' });
    const files = fs.readdirSync(path.join(dir, 'reviews', 'T1'));
    assert.strictEqual(files.length, 2, '2 コメントが別ファイルで共存する');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('addReviewComment は空本文を拒否し、空 author は匿名にする', () => {
    const dir = tmpProject();
    assert.throws(() => actions.addReviewComment(dir, 'T1', { author: 'a', text: '  ' }), /本文が空/);
    const res = actions.addReviewComment(dir, 'T1', { text: 'メモ' });
    assert.strictEqual(res.comment.author, '匿名');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('addReviewComment は不正なタスク ID を拒否する', () => {
    const dir = tmpProject();
    assert.throws(() => actions.addReviewComment(dir, '../evil', { text: 'x' }), /不正なタスク ID/);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('コメント操作はタスク状態ファイルに触れない', () => {
    const dir = tmpProject();
    const taskFile = path.join(dir, 'backlog', 'T1.md');
    fs.writeFileSync(taskFile, '## T1: タスク\n- status: review\n', 'utf8');
    const before = fs.readFileSync(taskFile, 'utf8');
    actions.addReviewComment(dir, 'T1', { author: 'a', text: 'x' });
    assert.strictEqual(fs.readFileSync(taskFile, 'utf8'), before);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  // --- edit / delete ---

  test('editReviewComment は本文を書き換え author/ts を保持し editedTs を足す', () => {
    const dir = tmpProject();
    const { id } = actions.addReviewComment(dir, 'T1', { author: 'alice', text: '旧' });
    const res = actions.editReviewComment(dir, 'T1', id, '新');
    assert.strictEqual(res.comment.text, '新');
    assert.strictEqual(res.comment.author, 'alice');
    assert.ok(res.comment.editedTs, 'editedTs が付く');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('editReviewComment は不正なコメント ID を拒否する', () => {
    const dir = tmpProject();
    actions.addReviewComment(dir, 'T1', { author: 'a', text: 'x' });
    assert.throws(() => actions.editReviewComment(dir, 'T1', '../evil', 'y'), /不正なコメント ID/);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  await test('deleteReviewComment はコメントファイルを消す', async () => {
    const dir = tmpProject();
    const { id, file } = actions.addReviewComment(dir, 'T1', { author: 'a', text: 'x' });
    const res = await actions.deleteReviewComment(dir, 'T1', id);
    assert.ok(!fs.existsSync(file));
    assert.strictEqual(res.via, 'delete');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  // --- project.readReviewComments ---

  test('readReviewComments は時系列に並べ、空本文・不正 JSON を飛ばす', () => {
    const dir = tmpProject();
    const cdir = path.join(dir, 'reviews', 'T1');
    fs.mkdirSync(cdir, { recursive: true });
    fs.writeFileSync(path.join(cdir, '2026-01-02.json'), JSON.stringify({ author: 'b', text: '後', ts: '2026-01-02T00:00' }));
    fs.writeFileSync(path.join(cdir, '2026-01-01.json'), JSON.stringify({ author: 'a', text: '先', ts: '2026-01-01T00:00' }));
    fs.writeFileSync(path.join(cdir, 'empty.json'), JSON.stringify({ author: 'c', text: '  ' }));
    fs.writeFileSync(path.join(cdir, 'broken.json'), '{oops');
    const list = project.readReviewComments(dir, 'T1');
    assert.deepStrictEqual(list.map((c) => c.text), ['先', '後']);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('readReviewComments は不正なタスク ID で空を返す', () => {
    const dir = tmpProject();
    assert.deepStrictEqual(project.readReviewComments(dir, '../evil'), []);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  // --- readProject への付与（合成 needs にコメントが載る） ---

  test('readProject は要対応（review 票）へ comments を付与する', () => {
    const dir = tmpProject();
    fs.writeFileSync(
      path.join(dir, 'backlog', 'T1.md'),
      '## T1: 検収待ちのタスク\n- status: review\n- verify: `true`\n',
      'utf8'
    );
    actions.addReviewComment(dir, 'T1', { author: 'alice', text: 'LGTM' });
    const snap = project.readProject(dir, {});
    const need = snap.needs.find((n) => String(n.taskId || n.id) === 'T1');
    assert.ok(need, 'review の合成票が見つかりません');
    assert.strictEqual((need.comments || []).length, 1);
    assert.strictEqual(need.comments[0].text, 'LGTM');
    fs.rmSync(dir, { recursive: true, force: true });
  });

  // --- renderer（描画ゲートと配線の存在） ---

  const renderer = require('./helpers/renderer-src').read();
  test('renderer はレビューコメントを review/blocked でだけ出し、CRUD を配線する', () => {
    assert.ok(renderer.includes('function reviewCommentsHtml('), 'コメント描画関数がありません');
    assert.ok(
      /\['review',\s*'blocked'\]\.includes\(n\.kind/.test(renderer),
      '成果物レビュー（review/blocked）でだけ出すゲートがありません'
    );
    assert.ok(renderer.includes('reviewCommentsHtml(n)'), 'カードへの差し込みがありません');
    assert.ok(renderer.includes('bindReviewComments(root)'), 'コメント欄の配線呼び出しがありません');
    assert.ok(renderer.includes('api.addReviewComment'), '追加 API の呼び出しがありません');
    assert.ok(renderer.includes('api.editReviewComment'), '編集 API の呼び出しがありません');
    assert.ok(renderer.includes('api.deleteReviewComment'), '削除 API の呼び出しがありません');
  });

  console.log(`\n${passed} tests passed`);
}

main();
