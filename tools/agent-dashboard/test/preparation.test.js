'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const preparation = require('../src/features/preparation/main/preparation');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const preparationIpc = require('../src/features/adhoc-flow/main/ipc');

test('情報が不足した要望にはエージェント設計を推奨する', () => {
  const recommendation = preparation.recommendRoute({ goal: 'CSV対応を改善したい' });
  assert.strictEqual(recommendation.route, 'agent-design');
});

test('実装に必要な節が揃った要望は直接実装を推奨する', () => {
  const recommendation = preparation.recommendRoute({ goal: [
    '## 目的\nCSVの文字コード判定を改善する',
    '## 変更対象\nsrc/csv.js',
    '## 受入基準\nUTF-8とShift_JISを読める',
    '## 検証方法\nnpm test',
  ].join('\n\n') });
  assert.strictEqual(recommendation.route, 'direct');
});

test('完成済み設計書が材料にあれば外部設計の利用を推奨する', () => {
  const recommendation = preparation.recommendRoute({
    goal: 'CSV対応を改善したい',
    materials: [{ kind: 'document', name: 'design.md', content: [
      '# 設計', '## 目的\nCSV対応', '## 対象\nsrc/csv.js',
      '## 完了条件\n2種類の文字コードを読める', '## テスト方法\nnpm test',
    ].join('\n\n') }],
  });
  assert.strictEqual(recommendation.route, 'external-design');
});

test('材料はIDで重複を除き設計と実装の両方へ引き継ぐ', () => {
  const materials = preparation.normalizeMaterials([
    { id: 'note:idea', kind: 'note', name: 'idea.md', content: '最初の内容' },
    { id: 'note:idea', kind: 'note', name: 'idea-copy.md', content: '重複' },
  ]);
  assert.deepStrictEqual(materials, [{
    id: 'note:idea', kind: 'note', name: 'idea.md', content: '最初の内容',
    sourcePath: '', sourceHash: '', selectedFor: ['design', 'implementation'],
  }]);
});

test('推奨が設計でも利用者は直接実装を選べる', () => {
  const item = preparation.createItem({
    target: 'workflow', title: 'CSV対応', goal: 'CSV対応を改善したい', route: 'direct',
  });
  assert.deepStrictEqual({
    target: item.target, route: item.route, recommended: item.routeRecommendation.route, phase: item.phase,
  }, {
    target: 'workflow', route: 'direct', recommended: 'agent-design', phase: 'implementation-ready',
  });
});

test('プロジェクト分解は候補ごとに準備経路を判定する', () => {
  const package_ = preparation.createPackage({
    projectDir: '/projects/csv', goal: 'CSV改善を分解する',
    materials: [{ id: 'master', kind: 'master', name: 'charter.md', content: '共通制約' }],
    candidates: [
      { title: '文字コード対応', goal: '文字コード対応を改善する' },
      { title: 'README更新', goal: [
        '## 目的\nREADME更新', '## 変更対象\nREADME.md',
        '## 受入基準\n利用例がある', '## 検証方法\n目視確認',
      ].join('\n\n') },
    ],
  });
  assert.deepStrictEqual(package_.items.map((item) => ({
    packageId: item.packageId, route: item.route, materials: item.materials.map((material) => material.id),
  })), [
    { packageId: package_.id, route: 'agent-design', materials: ['master'] },
    { packageId: package_.id, route: 'direct', materials: ['master'] },
  ]);
});

test('エージェント設計の成果を実装材料へ追加して実装可能にする', () => {
  const item = preparation.createItem({
    target: 'project', projectDir: '/projects/csv', title: '文字コード対応',
    goal: '文字コード対応を改善する', route: 'agent-design',
  });
  const completed = preparation.completeDesign(item, {
    sessionId: 'ds-1', document: '# 設計結果', runIds: ['design-run-1'],
  });
  assert.deepStrictEqual({
    phase: completed.phase,
    canHandoff: preparation.canHandoff(completed),
    design: completed.design,
    material: completed.materials.at(-1),
  }, {
    phase: 'implementation-ready',
    canHandoff: true,
    design: { sessionId: 'ds-1', document: '# 設計結果', runIds: ['design-run-1'] },
    material: {
      id: `design-result:${item.id}`, kind: 'design-result', name: '設計結果.md', content: '# 設計結果',
      sourcePath: '', sourceHash: '', selectedFor: ['design', 'implementation'],
    },
  });
});

test('作業準備項目を対象プロジェクトごとに保存して再開できる', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-store-'));
  try {
    const config = { preparationDir: dir };
    preparation.saveItem(config, preparation.createItem({
      target: 'project', projectDir: '/projects/a', title: 'Aの仕事', goal: '設計する',
    }));
    preparation.saveItem(config, preparation.createItem({
      target: 'project', projectDir: '/projects/b', title: 'Bの仕事', goal: '設計する',
    }));
    preparation.saveItem(config, preparation.createItem({
      target: 'workflow', title: '単発の仕事', goal: '設計する',
    }));
    assert.deepStrictEqual(
      preparation.listItems(config, { target: 'project', projectDir: '/projects/a' }).map((item) => item.title),
      ['Aの仕事']
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('準備パッケージは分解した全子項目と一緒に保存する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-package-'));
  try {
    const config = { preparationDir: dir };
    const package_ = preparation.createPackage({
      projectDir: '/projects/csv', goal: 'CSV改善',
      candidates: [{ title: '読込', goal: '読込改善' }, { title: '出力', goal: '出力改善' }],
    });
    const saved = preparation.savePackage(config, package_);
    assert.deepStrictEqual({
      packageId: saved.id,
      itemPackageIds: preparation.listItems(config, { projectDir: '/projects/csv' })
        .map((item) => item.packageId).sort(),
    }, { packageId: package_.id, itemPackageIds: [package_.id, package_.id] });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('実装依頼は仕事の内容と実装対象に選んだ材料だけを運ぶ', () => {
  const item = preparation.createItem({
    target: 'workflow', title: 'CSV対応', goal: 'CSV対応を改善する', route: 'direct',
    materials: [
      { id: 'note:design-only', kind: 'note', name: '検討メモ', content: '設計だけで使う', selectedFor: ['design'] },
      { id: 'doc:spec', kind: 'document', name: '仕様.md', content: '実装仕様', selectedFor: ['implementation'] },
    ],
  });
  assert.strictEqual(preparation.implementationRequest(item),
    'CSV対応を改善する\n\n## 実装材料\n### document: 仕様.md\n実装仕様');
});

test('別々の設計runと実装runを同じ仕事の履歴として保持する', () => {
  const draft = preparation.createItem({
    target: 'workflow', title: 'CSV対応', goal: 'CSV対応を改善する', route: 'agent-design',
  });
  const designing = preparation.startDesign(draft, { sessionId: 'ds-1', runId: 'design-run-1' });
  const ready = preparation.completeDesign(designing, {
    sessionId: 'ds-1', document: '# 設計結果', runIds: ['design-run-1'],
  });
  const implementing = preparation.recordHandoff(ready, { runId: 'implementation-run-1' });
  assert.deepStrictEqual({ phase: implementing.phase, design: implementing.design.runIds,
    implementation: implementing.handoff.implementationRunIds }, {
    phase: 'implementing', design: ['design-run-1'], implementation: ['implementation-run-1'],
  });
});

test('保存した準備項目は公開APIから取得して破棄できる', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-remove-'));
  try {
    const config = { preparationDir: dir };
    const saved = preparation.saveItem(config, preparation.createItem({
      target: 'workflow', title: '一時的な仕事', goal: 'あとで破棄する',
    }));
    const before = preparation.getItem(config, saved.id);
    const removed = preparation.removeItem(config, saved.id);
    const after = preparation.getItem(config, saved.id);
    assert.deepStrictEqual({ title: before.title, removed, after },
      { title: '一時的な仕事', removed: true, after: null });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('不正な準備・遷移・保存入力は境界で拒否する', () => {
  assert.throws(() => preparation.createItem({ goal: 'x' }), /仕事名/);
  assert.throws(() => preparation.createItem({ title: 'x' }), /やりたいこと/);
  assert.throws(() => preparation.createPackage({ goal: 'x', candidates: [{}] }), /プロジェクト/);
  assert.throws(() => preparation.createPackage({ projectDir: '/p', candidates: [{}] }), /やりたいこと/);
  assert.throws(() => preparation.createPackage({ projectDir: '/p', goal: 'x' }), /候補/);
  assert.strictEqual(preparation.canHandoff(null), false);
  assert.throws(() => preparation.completeDesign(null, {}), /エージェント設計/);
  assert.throws(() => preparation.completeDesign(preparation.createItem({
    title: '直接', goal: 'x', route: 'direct',
  }), {}), /エージェント設計/);
  const designItem = preparation.createItem({ title: '設計', goal: 'x', route: 'agent-design' });
  assert.throws(() => preparation.completeDesign(designItem, {}), /設計結果/);
  assert.throws(() => preparation.startDesign(null, {}), /設計run/);
  assert.throws(() => preparation.startDesign(designItem, {}), /セッションとrun ID/);
  assert.throws(() => preparation.recordHandoff(designItem, {}), /実装準備/);
  const ready = preparation.completeDesign(designItem, { document: '設計' });
  assert.throws(() => preparation.recordHandoff(ready, {}), /実装先のID/);
  const queued = preparation.recordHandoff(ready, { taskId: 'task-1' });
  assert.deepStrictEqual({ phase: queued.phase, taskId: queued.handoff.taskId },
    { phase: 'queued', taskId: 'task-1' });
  assert.throws(() => preparation.saveItem({ preparationDir: '/tmp' }, null), /項目が不正/);
  assert.throws(() => preparation.getItem({ preparationDir: '/tmp' }, '../bad'), /IDが不正/);
  assert.throws(() => preparation.savePackage({ preparationDir: '/tmp' }, null), /パッケージが不正/);
});

test('保存一覧は未作成・欠損・壊れたファイルを安全に扱う', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-edge-'));
  try {
    const config = { adhocFlow: { preparationDir: dir } };
    assert.strictEqual(preparation.resolveDir(config), dir);
    assert.deepStrictEqual(preparation.listItems(config), []);
    assert.strictEqual(preparation.removeItem(config, 'prep-missing'), false);
    assert.strictEqual(preparation.implementationRequest(null), '');
    const itemsDir = path.join(dir, 'items');
    fs.mkdirSync(itemsDir, { recursive: true });
    fs.writeFileSync(path.join(itemsDir, 'prep-corrupt.json'), '{');
    assert.deepStrictEqual(preparation.listItems(config), []);
    assert.throws(() => preparation.getItem(config, 'prep-corrupt'), SyntaxError);
    const materials = preparation.normalizeMaterials([
      null,
      { id: 'empty', content: '' },
      { id: 'kept', kind: '', name: '', content: '本文', selectedFor: ['design', 'unknown', 'design'] },
    ]);
    assert.deepStrictEqual(materials[0].selectedFor, ['design']);
    assert.strictEqual(materials[0].kind, 'document');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('プロジェクトへの引き渡しは完了条件を正規バックログ契約へ変換する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-ipc-'));
  const config = { preparationDir: dir };
  const item = preparation.saveItem(config, preparation.createItem({
    target: 'project', projectDir: '/projects/csv', title: 'CSV読込', goal: 'CSV読込を改善する', route: 'direct',
    taskSpec: { desc: '文字コードを判定する', acceptance: ['UTF-8を読める', 'Shift_JISを読める'] },
  }));
  const handlers = {};
  const originalPromote = adhoc.promote;
  let received;
  adhoc.promote = (_config, payload) => { received = payload; return { id: 'task-csv' }; };
  try {
    preparationIpc.registerIpc({
      handle: (name, handler) => { handlers[name] = handler; },
      loadConfig: () => config,
      saveConfig: () => {},
    });
    const result = handlers['preparation:handoff']({ id: item.id });
    assert.deepStrictEqual(received.spec.task_acceptance_criteria,
      ['UTF-8を読める', 'Shift_JISを読める']);
    assert.strictEqual(result.item.phase, 'queued');
    assert.strictEqual(result.item.handoff.taskId, 'task-csv');
  } finally {
    adhoc.promote = originalPromote;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
