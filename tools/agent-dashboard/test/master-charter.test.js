'use strict';

// マスター憲章運用（charter.md = 分解されない共通前提、charters/<name>.md = やるべきこと）と、
// needs（要対応）の構造化パースのテスト。追加依存なしで `node test/master-charter.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const authoring = require('../src/main/authoring');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- buildCharter / createProject（マスター運用） ---

test('buildCharter は master 指定で ## master セクションを付ける', () => {
  const text = authoring.buildCharter({ name: 'demo', goal: '全体目標', master: true });
  assert.match(text, /^## master$/m);
  assert.ok(text.indexOf('## master') < text.indexOf('## goal'), 'master 宣言は先頭側');
  const plain = authoring.buildCharter({ name: 'demo', goal: 'x' });
  assert.ok(!/^## master$/m.test(plain), '未指定なら従来どおり');
});

test('createProject は master 運用で charter.md（マスター）＋最初のバージョンを作る', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-master-'));
  try {
    const res = authoring.createProject({
      root,
      name: 'demo',
      master: true,
      charterName: 'v1',
      goal: 'CSV を要約する',
      acceptance: 'pytest -q tests/',
    });
    const charter = fs.readFileSync(path.join(res.dir, 'charter.md'), 'utf8');
    assert.match(charter, /^## master$/m, 'charter.md はマスター憲章');
    assert.match(charter, /# Charter: demo/);
    const version = fs.readFileSync(path.join(res.dir, 'charters', 'v1.md'), 'utf8');
    assert.match(version, /# Charter: v1/);
    assert.match(version, /CSV を要約する/, 'goal はバージョンに引き継ぐ');
    assert.ok(!/^## master$/m.test(version), 'バージョンはマスターではない');
    assert.strictEqual(res.versionFile, path.join(res.dir, 'charters', 'v1.md'));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('createProject は master 運用（バージョン名なし）で charter.md だけ作る', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-master-'));
  try {
    const res = authoring.createProject({ root, name: 'demo', master: true, goal: 'x' });
    assert.ok(fs.existsSync(path.join(res.dir, 'charter.md')));
    assert.ok(!fs.existsSync(path.join(res.dir, 'charters')), 'バージョンは後から追加');
    assert.strictEqual(res.versionFile, null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// --- parseCharter（master フラグ） ---

test('parseCharter は ## master セクションを master フラグにする', () => {
  const ch = project.parseCharter('# Charter: demo\n\n## master\n# 説明\n\n## goal\nx\n');
  assert.strictEqual(ch.master, true);
  const plain = project.parseCharter('# Charter: demo\n\n## goal\nx\n');
  assert.strictEqual(plain.master, false);
});

// --- parseNeeds（構造化: 要点抽出と記入用足場の除去） ---

const NEEDS_MD = `---
kind: milestone
date: 2026-07-11
status: open
---
# マイルストーン: demo

## Context and Problem Statement

- なぜ: 収束候補（acceptance 全 PASS・改善ゼロ）
- 状態: converged
- 概況: cycle 3: acceptance 2/2 PASS

## goal
CSV を要約する CLI を完成させる

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 完了として受領するなら \`agent-project approve demo\` -->
`;

test('parseNeeds は なぜ/状態/概況 と判断材料（detail）を構造化する', () => {
  const n = project.parseNeeds(NEEDS_MD, 'demo');
  assert.strictEqual(n.kind, 'milestone');
  assert.strictEqual(n.title, 'マイルストーン: demo');
  assert.match(n.why, /収束候補/);
  assert.strictEqual(n.stateNote, 'converged');
  assert.match(n.summary, /2\/2 PASS/);
  assert.match(n.detail, /## goal/, '判断材料（goal セクション）は detail に残る');
  assert.ok(!n.detail.includes('Decision Outcome'), '記入用の足場は detail に含めない');
  assert.ok(!n.detail.includes('<!--'), 'HTML コメントのヒントは含めない');
  assert.ok(!n.detail.includes('なぜ:'), '抽出済みの要点行は重複させない');
  assert.strictEqual(n.decided, false);
});

test('parseNeeds の decided は Decision Outcome 配下の [x] だけを見る', () => {
  const md = `# 要対応: T1

## Context and Problem Statement

- [x] 下調べ済み（本文のチェックリスト）

## Decision Outcome

- [ ] 確定
`;
  assert.strictEqual(project.parseNeeds(md, 'T1').decided, false);
  const done = md.replace('- [ ] 確定', '- [x] 確定');
  assert.strictEqual(project.parseNeeds(done, 'T1').decided, true);
});

test('parseNeeds の decided は旧 ## フィードバック欄の [x] も見る', () => {
  const md = `# 要対応: T1

- [x] 本文の手順

## フィードバック
- [x] 確定
方針
`;
  assert.strictEqual(project.parseNeeds(md, 'T1').decided, true);
});

test('parseNeeds は要点の無い needs でも壊れない（後方互換）', () => {
  const n = project.parseNeeds('# 要対応: T1 — 何か\n\n本文だけ\n', 'T1');
  assert.strictEqual(n.why, '');
  assert.match(n.detail, /本文だけ/);
});

test('parseNeeds は frontmatter risk（リスクダイジェスト総合値）を拾う', () => {
  const md = `---
kind: review
date: 2026-07-12
status: proposed
task-id: T1
risk: high
---
# 要対応: T1 — 危ない変更

## Context and Problem Statement

- なぜ: verify=PASS だが保護パス変更

## リスク
- 総合: 高（protect/avoid=高、リトライ・大差分・合成 verify=中）
- 保護パス接触: auth/x.py
`;
  const n = project.parseNeeds(md, 'T1');
  assert.strictEqual(n.risk, 'high');
  assert.match(n.detail, /## リスク/, 'リスク節は判断材料（detail）に残る');
  // risk が無い needs（旧形式）は空のまま
  const old = project.parseNeeds(NEEDS_MD, 'demo');
  assert.strictEqual(old.risk, '');
});

// --- evidenceThin（stub 実行・無変更の痩せた判断材料の検出と整理） ---

const THIN_NEEDS_MD = `---
kind: review
date: 2026-07-12
status: review
task-id: T1
---
# 要対応: T1 — 何かを直す

## Context and Problem Statement

- なぜ: verify=PASS だが 承認ゲート

## 判断材料（成果物の所在・差分・検証）
- 成果物: (参照なし)
- 所在: /home/user/projects/demo / ブランチ ap/T1
- 実行先: local
- 差分: baseline 以降の変更なし
- 検証: \`true\` → PASS

## Decision Outcome
- [ ] 確定
`;

test('parseNeeds は痩せた evidence（stub・無変更）を検出し内部パス行を落とす', () => {
  const n = project.parseNeeds(THIN_NEEDS_MD, 'T1');
  assert.strictEqual(n.evidenceThin, true, '成果物プレースホルダ＋差分なし＝thin');
  assert.match(n.why, /承認ゲート/, '理由は残る');
  assert.ok(!/成果物: \(参照なし\)/.test(n.detail), '成果物プレースホルダ行は落とす');
  assert.ok(!/- 所在:/.test(n.detail), '所在（内部パス）行は落とす');
  assert.ok(!/- 実行先:/.test(n.detail), '実行先行は落とす');
  assert.ok(!/変更なし/.test(n.detail), '差分なし行は落とす');
  assert.match(n.detail, /検証:.*PASS/, '検証（意味のある行）は残す');
});

const REAL_NEEDS_MD = `---
kind: review
date: 2026-07-12
status: review
task-id: T2
---
# 要対応: T2 — 機能追加

## Context and Problem Statement

- なぜ: verify=PASS だが 承認ゲート

## 判断材料（成果物の所在・差分・検証）
- 成果物: https://gitlab.example.com/team/app/-/merge_requests/42
- 所在: /home/user/projects/demo / ブランチ ap/T2
- 差分: 3 ファイル
    - src/a.py
- 検証: \`pytest\` → PASS

## Decision Outcome
- [ ] 確定
`;

test('parseNeeds は実 evidence（MR リンク・差分あり）を thin と誤判定しない', () => {
  const n = project.parseNeeds(REAL_NEEDS_MD, 'T2');
  assert.strictEqual(n.evidenceThin, false, 'MR リンク＋差分あり＝not thin');
  assert.match(n.detail, /merge_requests\/42/, '成果物リンクは残る');
  assert.match(n.detail, /- 所在:/, '実運用では所在も残す');
  assert.match(n.detail, /3 ファイル/, '差分は残る');
});

test('parseNeeds は成果物行の無い判断材料（タスク定義等）を thin にしない', () => {
  const md = '# 実行前レビュー: T3 — x\n\n## タスク定義（レビュー対象）\n- title  : x\n- verify : `true`\n';
  const n = project.parseNeeds(md, 'T3');
  assert.strictEqual(n.evidenceThin, false, '成果物行が無い＝対象外');
  assert.match(n.detail, /タスク定義/, 'タスク定義は残す');
});

console.log(`\n${passed} passed`);
