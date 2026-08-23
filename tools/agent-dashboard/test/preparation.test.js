'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const preparation = require('../src/features/preparation/main/preparation');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const designSession = require('../src/features/adhoc-flow/main/design-session');
const preparationIpc = require('../src/features/adhoc-flow/main/ipc');

// 設計書の書式は手法カタログ（design-document-format）が正典。アプリでは ipc が引いて
// 渡すので、テストも同じ同梱書式を使う。
const designFormat = adhoc.designDocumentFormat({});

// 設計 run の成果は必須4節に加えて「変更対象の強制レイヤー」まで要る
// （文言でしか守られていない契約を実装 run へ渡さないため）。
const completeDesignDocument = [
  '## 目的\nCSVの文字コード判定を改善する',
  '## 変更対象\nsrc/csv.js\n- 強制レイヤー: 読み込みは csv.js の decode() で強制する',
  '## 受入基準\nUTF-8とShift_JISを読める',
  '## 検証方法\nnpm test',
].join('\n\n');

function designFlowSnapshot(overrides = {}) {
  return {
    version: 1,
    id: 'custom-design-flow',
    name: 'カスタム設計フロー',
    origin: { scope: 'user', repository: '' },
    digest: 'sha256:custom-design-flow',
    definition: {
      version: 2,
      purpose: 'design',
      libraryVisibility: 'library',
      entry: ['draft'],
      exit: ['finish'],
      nodes: [
        { id: 'draft', goal: '要件を整理する', kind: 'work', tier: 'auto', deps: [] },
        { id: 'finish', goal: '設計書を仕上げる', kind: 'synthesize', tier: 'auto', deps: ['draft'] },
      ],
    },
    ...overrides,
  };
}

test('情報が不足した要望にはエージェント設計を推奨する', () => {
  const recommendation = preparation.recommendRoute({ goal: 'CSV対応を改善したい', format: designFormat });
  assert.strictEqual(recommendation.route, 'agent-design');
});

test('実装に必要な節が揃った要望は直接実装を推奨する', () => {
  const recommendation = preparation.recommendRoute({ goal: [
    '## 目的\nCSVの文字コード判定を改善する',
    '## 変更対象\nsrc/csv.js',
    '## 受入基準\nUTF-8とShift_JISを読める',
    '## 検証方法\nnpm test',
  ].join('\n\n'), format: designFormat });
  assert.strictEqual(recommendation.route, 'direct');
});

test('完成済み設計書が材料にあれば外部設計の利用を推奨する', () => {
  const recommendation = preparation.recommendRoute({
    goal: 'CSV対応を改善したい',
    format: designFormat,
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

test('設計フローのノード割り当ては形を整えて保存し、パッケージの子項目へ引き継ぐ', () => {
  const item = preparation.createItem({
    title: '割り当て付き',
    goal: 'CSV対応を改善したい',
    route: 'agent-design',
    designAssignments: {
      draft: { tier: 'large', agent_cli: 'codex', model: 'gpt-5' },
      broken: { tier: '', agent_cli: 'codex' },      // tier 無しは捨てる
      noagent: { tier: 'large', agent_cli: '' },     // agent 無しは捨てる
    },
  });
  assert.deepStrictEqual(item.designAssignments,
    { draft: { tier: 'large', agent_cli: 'codex', model: 'gpt-5' } });
  assert.strictEqual(preparation.createItem({
    title: '割り当てなし', goal: 'x', route: 'direct',
  }).designAssignments, null);

  const package_ = preparation.createPackage({
    projectDir: '/tmp/project',
    goal: '大きな要望',
    designAssignments: { draft: { tier: 'large', agent_cli: 'codex', model: 'gpt-5' } },
    candidates: [
      { title: '子A', goal: 'a', route: 'agent-design' },
      { title: '子B', goal: 'b', route: 'direct',
        designAssignments: { draft: { tier: 'medium', agent_cli: 'claude', model: 'sonnet' } } },
    ],
  });
  assert.deepStrictEqual(package_.items[0].designAssignments,
    { draft: { tier: 'large', agent_cli: 'codex', model: 'gpt-5' } },
    '親の割り当てを継承する');
  assert.deepStrictEqual(package_.items[1].designAssignments,
    { draft: { tier: 'medium', agent_cli: 'claude', model: 'sonnet' } },
    '子項目固有の割り当てが親より優先する');
});

test('作業準備項目は選択した設計フローを保持する', () => {
  const item = preparation.createItem({
    title: '全自動で設計', goal: 'CSV対応を改善したい', route: 'agent-design', designMode: 'auto',
  });
  assert.strictEqual(item.designMode, 'auto');
});

test('準備パッケージは選択した設計フローを子項目へ引き継ぐ', () => {
  const package_ = preparation.createPackage({
    projectDir: '/tmp/project', goal: 'CSV対応を分解する', designMode: 'auto',
    candidates: [{ title: '読込', goal: 'CSV読込を改善する', route: 'agent-design' }],
  });
  assert.strictEqual(package_.items[0].designMode, 'auto');
});

test('設計flowは正規形のsnapshotとして保存し、永続化後も定義・出所・digestを保持する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-flow-'));
  try {
    const config = { preparationDir: dir };
    const flow = designFlowSnapshot();
    const item = preparation.createItem({
      id: 'prep-design-flow', title: '設計フロー付き', goal: 'CSV対応を改善する',
      route: 'agent-design', design: { flow },
    });
    const saved = preparation.saveItem(config, item);
    const loaded = preparation.getItem(config, saved.id);
    assert.deepStrictEqual(saved.design.flow, flow);
    assert.deepStrictEqual(loaded.design.flow, flow);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('親の設計flowはagent-design子へ継承し、external/direct子には保持しない', () => {
  const flow = designFlowSnapshot();
  const package_ = preparation.createPackage({
    projectDir: '/projects/csv', goal: 'CSV改善を分解する',
    design: { flow },
    candidates: [
      { title: '設計する子', goal: '要件を設計する', route: 'agent-design' },
      { title: '外部設計の子', goal: '設計書を使う', route: 'external-design' },
      { title: '直接実装の子', goal: 'そのまま実装する', route: 'direct' },
    ],
  });
  assert.deepStrictEqual(package_.items[0].design.flow, flow);
  assert.ok(!package_.items[1].design.flow, 'external-design子へ設計flowを持ち込まない');
  assert.ok(!package_.items[2].design.flow, 'direct子へ設計flowを持ち込まない');
});

test('既存のmode=auto保存物は設計開始時にdesign-autoのsnapshotを遅延補完する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-legacy-mode-'));
  try {
    const config = { preparationDir: dir };
    const item = preparation.createItem({
      id: 'prep-legacy-auto', title: '旧形式の自動設計', goal: 'CSV対応を改善する',
      route: 'agent-design', designMode: 'auto',
    });
    const legacy = { ...item, design: { sessionId: '', document: '', runIds: [] } };
    const itemFile = path.join(dir, 'items', `${item.id}.json`);
    fs.mkdirSync(path.dirname(itemFile), { recursive: true });
    fs.writeFileSync(itemFile, `${JSON.stringify(legacy)}\n`, 'utf8');

    const started = preparation.startDesign(preparation.getItem(config, item.id), {
      sessionId: 'ds-auto', runId: 'design-run-auto',
    });
    const saved = preparation.saveItem(config, started);
    assert.strictEqual(saved.design.flow.id, 'design-auto');
    assert.strictEqual(saved.design.flow.origin.scope, 'builtin');
    assert.strictEqual(preparation.getItem(config, item.id).design.flow.id, 'design-auto');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('設計開始は準備項目で選択した設計フローを実行する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-mode-'));
  const config = { preparationDir: dir };
  const item = preparation.saveItem(config, preparation.createItem({
    title: '全自動で設計', goal: 'CSV対応を改善したい', route: 'agent-design', designMode: 'auto',
  }));
  const handlers = {};
  const originalStartRound = designSession.startRound;
  let received;
  designSession.startRound = (_config, payload) => {
    received = payload;
    return { id: 'ds-auto', runId: 'design-run-auto' };
  };
  try {
    preparationIpc.registerIpc({
      handle: (name, handler) => { handlers[name] = handler; },
      loadConfig: () => config,
      saveConfig: () => {},
    });
    handlers['preparation:startDesign']({ id: item.id });
    assert.strictEqual(received.mode, 'auto');
  } finally {
    designSession.startRound = originalStartRound;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('保存済み設計snapshotは元フロー変更後もagent-design子へ引き継ぐ', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-snapshot-'));
  const workflowDir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-workflows-'));
  const builtinWorkflowDir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-builtin-'));
  const busDir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-bus-'));
  const config = {
    preparationDir: dir,
    adhocFlow: { workflowDir, builtinWorkflowDir, busDir },
  };
  const originalStartRound = designSession.startRound;
  const workflow = adhoc.saveWorkflow(config, {
    id: 'snapshot-child-flow', name: 'snapshot対象', purpose: 'design', version: 2,
    entry: ['draft'], exit: ['finish'],
    nodes: [
      { id: 'draft', goal: '旧ドラフト', tier: 'large', deps: [] },
      { id: 'finish', goal: '旧成果', kind: 'synthesize', tier: 'large', deps: ['draft'] },
    ],
  });
  let received;
  designSession.startRound = (_config, payload) => {
    received = payload;
    return { id: 'ds-snapshot', runId: 'run-snapshot' };
  };
  const handlers = {};
  try {
    preparationIpc.registerIpc({
      handle: (name, handler) => { handlers[name] = handler; },
      loadConfig: () => config,
      saveConfig: () => {},
    });
    const created = handlers['preparation:create']({
      title: 'snapshotを使う子', goal: '設計する', route: 'agent-design',
      designFlow: { id: workflow.id, scope: 'user', repository: '' },
    }).item;
    adhoc.saveWorkflow(config, {
      ...workflow,
      nodes: workflow.nodes.map((node) => ({ ...node, goal: `新しい${node.goal}` })),
    });

    const started = handlers['preparation:startDesign']({ id: created.id });
    assert.strictEqual(
      received.resolvedFlowSnapshot.definition.nodes[0].goal,
      '旧ドラフト',
      '設計run起動時に元フローを再読込してはならない'
    );
    assert.strictEqual(started.item.design.flow.definition.nodes[1].goal, '旧成果');
  } finally {
    designSession.startRound = originalStartRound;
    for (const target of [dir, workflowDir, builtinWorkflowDir, busDir]) {
      fs.rmSync(target, { recursive: true, force: true });
    }
  }
});

test('不完全な再設計成果は旧成果を保持するがhandoffへ進めない', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-design-retain-'));
  const config = { preparationDir: dir };
  const originalGetSession = designSession.getSession;
  const valid = preparation.completeDesign(preparation.createItem({
    id: 'prep-retain', title: '旧成果を保持', goal: '設計する', route: 'agent-design',
  }), { sessionId: 'ds-old', document: completeDesignDocument, runIds: ['run-old'] }, designFormat);
  const designing = preparation.startDesign(valid, { sessionId: 'ds-new', runId: 'run-new' });
  preparation.saveItem(config, designing);
  designSession.getSession = () => ({
    id: 'ds-new', runStatus: 'done', error: '設計成果に必須節が不足しています', document: '',
    rounds: [{ runId: 'run-new' }],
  });
  const handlers = {};
  try {
    preparationIpc.registerIpc({
      handle: (name, handler) => { handlers[name] = handler; },
      loadConfig: () => config,
      saveConfig: () => {},
    });
    const synced = handlers['preparation:syncDesign']({ id: designing.id }).item;
    assert.strictEqual(synced.design.document, completeDesignDocument);
    assert.strictEqual(synced.phase, 'designing');
    assert.strictEqual(preparation.canHandoff(synced, designFormat), false);
    assert.throws(() => handlers['preparation:completeDesign']({ id: designing.id }), /完了できません/);
  } finally {
    designSession.getSession = originalGetSession;
    fs.rmSync(dir, { recursive: true, force: true });
  }
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
    projectDir: '/projects/csv', goal: 'CSV改善を分解する', format: designFormat,
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
    sessionId: 'ds-1', document: completeDesignDocument, runIds: ['design-run-1'],
  }, designFormat);
  assert.deepStrictEqual({
    phase: completed.phase,
    canHandoff: preparation.canHandoff(completed, designFormat),
    design: completed.design,
    material: completed.materials.at(-1),
  }, {
    phase: 'implementation-ready',
    canHandoff: true,
    design: { sessionId: 'ds-1', document: completeDesignDocument, runIds: ['design-run-1'] },
    material: {
      id: `design-result:${item.id}`, kind: 'design-result', name: '設計結果.md', content: completeDesignDocument,
      sourcePath: '', sourceHash: '', selectedFor: ['design', 'implementation'],
    },
  });
});

test('設計確認の差し戻しは理由を必須にして次の設計runへ戻す', () => {
  const started = preparation.startDesign(preparation.createItem({
    title: '差し戻す設計', goal: 'CSV対応を改善する', route: 'agent-design',
  }), { sessionId: 'ds-review', runId: 'run-first' });
  const review = {
    ...started,
    phase: 'design-review',
    design: { ...started.design, document: completeDesignDocument },
  };

  assert.throws(() => preparation.reviseDesign(review, {
    sessionId: 'ds-review', runId: 'run-second', feedback: '  ',
  }), /差し戻し理由/);
  assert.throws(() => preparation.reviseDesign({ ...review, phase: 'designing' }, {
    sessionId: 'ds-review', runId: 'run-second', feedback: '受入基準を直す',
  }), /設計確認/);

  const revised = preparation.reviseDesign(review, {
    sessionId: 'ds-review', runId: 'run-second', feedback: '受入基準を直す',
  });
  assert.deepStrictEqual({ phase: revised.phase, sessionId: revised.design.sessionId, runIds: revised.design.runIds }, {
    phase: 'designing', sessionId: 'ds-review', runIds: ['run-first', 'run-second'],
  });
  assert.strictEqual(revised.design.document, completeDesignDocument);
});

test('設計差し戻しAPIは理由を同じセッションの次ラウンドへ渡して状態を保存する', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'preparation-revise-'));
  const config = { preparationDir: dir };
  const originalStartRound = designSession.startRound;
  let received = null;
  try {
    const started = preparation.startDesign(preparation.createItem({
      id: 'prep-revise', title: '差し戻しAPI', goal: '設計する', route: 'agent-design',
    }), { sessionId: 'ds-revise', runId: 'run-first' });
    preparation.saveItem(config, { ...started, phase: 'design-review' });
    designSession.startRound = (_config, input) => {
      received = input;
      return { id: 'ds-revise', runId: 'run-second', runStatus: 'running' };
    };
    const handlers = {};
    preparationIpc.registerIpc({
      handle: (name, handler) => { handlers[name] = handler; },
      loadConfig: () => config,
      saveConfig: () => {},
    });

    const result = handlers['preparation:reviseDesign']({
      id: 'prep-revise', feedback: '差し戻し内容を反映する',
    });
    assert.deepStrictEqual(received, { id: 'ds-revise', feedback: '差し戻し内容を反映する' });
    assert.strictEqual(result.item.phase, 'designing');
    assert.deepStrictEqual(result.item.design.runIds, ['run-first', 'run-second']);
  } finally {
    designSession.startRound = originalStartRound;
    fs.rmSync(dir, { recursive: true, force: true });
  }
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
    sessionId: 'ds-1', document: completeDesignDocument, runIds: ['design-run-1'],
  }, designFormat);
  const implementing = preparation.recordHandoff(ready, { runId: 'implementation-run-1' }, designFormat);
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
  assert.strictEqual(preparation.canHandoff(null, designFormat), false);
  assert.throws(() => preparation.completeDesign(null, {}), /エージェント設計/);
  assert.throws(() => preparation.completeDesign(preparation.createItem({
    title: '直接', goal: 'x', route: 'direct',
  }), {}), /エージェント設計/);
  const designItem = preparation.createItem({ title: '設計', goal: 'x', route: 'agent-design' });
  assert.throws(() => preparation.completeDesign(designItem, {}), /設計結果/);
  assert.throws(() => preparation.startDesign(null, {}), /設計run/);
  assert.throws(() => preparation.startDesign(designItem, {}), /セッションとrun ID/);
  assert.throws(() => preparation.recordHandoff(designItem, {}), /実装準備/);
  const ready = preparation.completeDesign(designItem, { document: completeDesignDocument }, designFormat);
  assert.throws(() => preparation.recordHandoff(ready, {}, designFormat), /実装先のID/);
  const queued = preparation.recordHandoff(ready, { taskId: 'task-1' }, designFormat);
  assert.deepStrictEqual({ phase: queued.phase, taskId: queued.handoff.taskId },
    { phase: 'queued', taskId: 'task-1' });
  assert.throws(() => preparation.saveItem({ preparationDir: '/tmp' }, null), /項目が不正/);
  assert.throws(() => preparation.getItem({ preparationDir: '/tmp' }, '../bad'), /IDが不正/);
  assert.throws(() => preparation.savePackage({ preparationDir: '/tmp' }, null), /パッケージが不正/);
});

test('不完全な設計結果はcompleteDesignで拒否し、実装待ちへ移せない', () => {
  const item = preparation.createItem({ title: '不完全な設計', goal: 'x', route: 'agent-design' });
  const before = JSON.parse(JSON.stringify(item));
  assert.throws(() => preparation.completeDesign(item, {
    sessionId: 'ds-incomplete', document: '## 目的\n目的だけです', runIds: ['run-incomplete'],
  }, designFormat), /必須|検証方法/);
  assert.deepStrictEqual(item, before);
  assert.strictEqual(item.phase, 'design-ready');
  assert.strictEqual(preparation.canHandoff(item, designFormat), false);
  const forgedReady = {
    ...item,
    phase: 'implementation-ready',
    design: { ...item.design, document: '## 目的\n目的だけです' },
  };
  assert.strictEqual(preparation.canHandoff(forgedReady, designFormat), false);
  assert.throws(() => preparation.recordHandoff(forgedReady, { runId: 'run-forged' }, designFormat), /実装準備/);
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
