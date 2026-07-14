'use strict';

// 検収サブ画面向け: needs の delivery / mr-url パースと「変更ファイル」差分の内訳。

const assert = require('assert');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('frontmatter の delivery / mr-url を構造化する', () => {
  const delivery = [
    {
      name: 'app',
      role: 'write',
      path: '/tmp/app',
      base: 'main',
      branch: 'ap/T1',
      files: ['src/a.py'],
      files_total: 1,
      mr_url: 'https://gitlab.example.com/g/app/-/merge_requests/3',
    },
    { name: 'spec', role: 'reference', url: 'https://gitlab.example.com/g/spec.git', files: [] },
  ];
  const md = `---
kind: review
task-id: T1
mr-url: https://gitlab.example.com/g/app/-/merge_requests/3
delivery: ${JSON.stringify(delivery)}
---

# 要対応: T1 — 直す

## Context and Problem Statement

- なぜ: 検収待ち
- 状態: review

## 判断材料
- 変更ファイル（1 件）:
    - src/a.py
`;
  const n = project.parseNeeds(md, 'T1');
  assert.strictEqual(n.mrUrl, 'https://gitlab.example.com/g/app/-/merge_requests/3');
  assert.strictEqual(n.delivery.length, 2);
  assert.strictEqual(n.delivery[0].role, 'write');
  assert.strictEqual(n.delivery[1].role, 'reference');
  assert.deepStrictEqual(n.delivery[0].files, ['src/a.py']);
});

test('現行形式「変更ファイル（N 件）」を差分として拾う', () => {
  const n = project.parseNeeds(
    `---
kind: review
---
# 要対応: T2 — x

- なぜ: 検収
- 状態: review

## 判断材料
- 成果物: ブランチ \`kp/T2\`（2 ファイル変更・base \`main\`）
- 差分を見る: \`git -C /tmp/app diff main...kp/T2\`
- 変更ファイル（2 件）:
    - lib/x.js
    - test/x.test.js
- 検証: \`true\` → PASS
`,
    'T2'
  );
  assert.strictEqual(n.diff.hasDiff, true);
  assert.deepStrictEqual(n.diff.artifacts, ['lib/x.js', 'test/x.test.js']);
  assert.ok(n.delivery.length >= 1);
  assert.ok(n.delivery[0].diff_cmd.includes('git -C'));
});

test('複数リポジトリ見出しから delivery を復元する', () => {
  const detail = [
    '### リポジトリ: payments（書込先）',
    '- 成果物: ブランチ `kp/T9`（1 ファイル変更・base `main`）',
    '- 所在: /work/payments',
    '- 差分を見る: `git -C /work/payments diff main...origin/kp/T9`',
    '- MR: https://gitlab.example.com/g/payments/-/merge_requests/9（承認時にクリーンなら自動マージ）',
    '- 変更ファイル（1 件）:',
    '    - src/pay.py',
    '### リポジトリ: shared-spec（参照（読取））',
    '- 参照: https://gitlab.example.com/g/shared-spec.git',
    '- ブランチ指定: `main`',
    '- 注: 参照リポジトリ。本タスクの成果差分は書込先を見る',
  ].join('\n');
  const entries = project._deliveryFromDetail(detail);
  assert.strictEqual(entries.length, 2);
  assert.strictEqual(entries[0].name, 'payments');
  assert.strictEqual(entries[0].role, 'write');
  assert.deepStrictEqual(entries[0].files, ['src/pay.py']);
  assert.match(entries[0].mr_url, /merge_requests\/9/);
  assert.strictEqual(entries[1].role, 'reference');
});

test('backlog の mr_url を合成票へ補う', () => {
  const needs = [
    {
      id: 'T3',
      taskId: 'T3',
      kind: 'review',
      mrUrl: '',
      mrUrls: [],
      delivery: [],
      detail: '',
    },
  ];
  const backlog = [
    {
      id: 'T3',
      extra: { mr_url: 'https://gitlab.example.com/g/app/-/merge_requests/11' },
    },
  ];
  project.attachDeliveryHintsFromBacklog(needs, backlog);
  assert.strictEqual(needs[0].mrUrl, 'https://gitlab.example.com/g/app/-/merge_requests/11');
  assert.ok(needs[0].delivery.length >= 1);
});

console.log(`\n${passed} passed`);
