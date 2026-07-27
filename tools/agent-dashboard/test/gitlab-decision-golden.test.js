'use strict';

// gitlab executor の決着推定の正典は Python（`tools/agent-flow/executors/gitlab.py` の
// `_mr_decision` / `_decision_from_comments` / `_closed_issue_decision`）。この画面が持つ
// `flow.js` の同名ロジックはその**写し**で、「乖離させない」という約束が両方の docstring と
// S4/S5 詳細設計 §1.4 に明文で書かれている——が、約束を守らせる機構は無かった。
//
// CONTRACT_VERSION / DIFF_CRITERION と同型の「2 実装が黙ってずれる」候補（棚卸し
// 2026-07-27 §5-13 → §2c-4）。ここでは**正典を実際に読んで**手掛かり語とラベルを突き合わせ、
// 片方だけ足した / 消した変更を落とす。実装の統合まではしない（判定の入力源が
// 別プロセス・別言語なので、写しを許して検査で縛るのがコンセプト正典 C7 の明文どおり）。
//
// 追加依存なしで `node test/gitlab-decision-golden.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const flow = require('../src/main/flow');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// このファイルは tools/agent-dashboard/test/ にある。リポジトリルートまで 2 段上がる。
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const CANON = path.join(REPO_ROOT, 'tools', 'agent-flow', 'executors', 'gitlab.py');
const py = fs.readFileSync(CANON, 'utf8');

// `_NAME = ("a", "b", …)` / `_NAME = ["a", …]` の文字列要素を読む（複数行可）。
function pyStrings(name) {
  const re = new RegExp(`^${name}\\s*=\\s*[([]([\\s\\S]*?)[)\\]]\\s*$`, 'm');
  const m = re.exec(py);
  assert.ok(m, `正典の定義を読めません（定数が動いた？）: ${name} in ${CANON}`);
  return [...m[1].matchAll(/"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'/g)]
    .map((x) => (x[1] !== undefined ? x[1] : x[2]).replace(/\\(.)/g, '$1'));
}

test('却下の手掛かり語が正典と一致する', () => {
  assert.deepStrictEqual(
    flow.GITLAB_REJECT_HINTS,
    pyStrings('_REJECT_HINTS'),
    '手掛かり語を足す/消すときは executors/gitlab.py と flow.js を同一コミットで揃えます'
  );
});

test('承認の手掛かり語が正典と一致する', () => {
  assert.deepStrictEqual(
    flow.GITLAB_APPROVE_HINTS,
    pyStrings('_APPROVE_HINTS'),
    '手掛かり語を足す/消すときは executors/gitlab.py と flow.js を同一コミットで揃えます'
  );
});

test('承認とみなすラベルが正典（executor の既定設定）と一致する', () => {
  // Python 側は設定キー approved_label / done_label の既定値が正典（_DEFAULTS）。
  const approved = /"approved_label"\s*:\s*"([^"]+)"/.exec(py);
  const done = /"done_label"\s*:\s*"([^"]+)"/.exec(py);
  assert.ok(approved && done, `正典のラベル既定を読めません: ${CANON}`);
  assert.deepStrictEqual(flow.GITLAB_APPROVED_LABELS, [approved[1], done[1]]);
});

test('却下語は承認語より優先する（両方を含むコメントはやり直し指示）', () => {
  // 正典の _decision_from_comments と同じ優先順。写しだけ順序が入れ替わると、
  // 「承認と書いたうえで却下」のコメントが承認として先読みされる。
  assert.strictEqual(
    flow.gitlabClosedIssueDecision({ labels: [], comments: [{ body: '承認できません。却下します' }] }),
    'rejected'
  );
});

test('system note と agent-flow の自動コメントは手掛かりにしない', () => {
  const issue = {
    labels: [],
    comments: [
      { body: '承認', system: true },
      { body: 'agent-flow: 承認しました（自動）' },
      { body: '<!-- gitlab-idd:creator-node-id:x --> 承認' },
    ],
  };
  assert.strictEqual(flow.gitlabClosedIssueDecision(issue), '');
});

test('MR 状態だけの決着が正典の 3 分岐と一致する', () => {
  assert.strictEqual(flow.gitlabMrDecision(['merged', 'merged']), 'approved');
  assert.strictEqual(flow.gitlabMrDecision(['merged', 'closed']), 'rejected');
  assert.strictEqual(flow.gitlabMrDecision(['opened', 'closed']), ''); // open があれば未決着
  assert.strictEqual(flow.gitlabMrDecision([]), ''); // MR 未作成も未決着
});

console.log(`\n${passed} tests passed (gitlab-decision-golden)`);
