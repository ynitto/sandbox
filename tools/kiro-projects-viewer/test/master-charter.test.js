'use strict';

// マスター憲章運用（charter.md = 分解されない共通前提、charters/<name>.md = やるべきこと）と、
// needs（要対応）の構造化パースのテスト。追加依存なしで `node test/master-charter.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const authoring = require('../src/main/authoring');
const kiro = require('../src/main/kiro');

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
  const ch = kiro.parseCharter('# Charter: demo\n\n## master\n# 説明\n\n## goal\nx\n');
  assert.strictEqual(ch.master, true);
  const plain = kiro.parseCharter('# Charter: demo\n\n## goal\nx\n');
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

<!-- 完了として受領するなら \`kiro-project approve demo\` -->
`;

test('parseNeeds は なぜ/状態/概況 と判断材料（detail）を構造化する', () => {
  const n = kiro.parseNeeds(NEEDS_MD, 'demo');
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

test('parseNeeds は要点の無い needs でも壊れない（後方互換）', () => {
  const n = kiro.parseNeeds('# 要対応: T1 — 何か\n\n本文だけ\n', 'T1');
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
  const n = kiro.parseNeeds(md, 'T1');
  assert.strictEqual(n.risk, 'high');
  assert.match(n.detail, /## リスク/, 'リスク節は判断材料（detail）に残る');
  // risk が無い needs（旧形式）は空のまま
  const old = kiro.parseNeeds(NEEDS_MD, 'demo');
  assert.strictEqual(old.risk, '');
});

console.log(`\n${passed} passed`);
