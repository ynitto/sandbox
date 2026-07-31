'use strict';

// ノード契約バージョンの正典は Python（`agentcore/board.py` の CONTRACT_VERSION）。
// この画面が持つ `EXPECTED_CONTRACT_VERSION` はその**写し**なので、写しが古いままだと
// 「実行エンジンの更新が必要です」を出す条件が実装とずれる（新しい常駐体を古い画面が
// 更新漏れと呼ぶ／その逆）。
//
// `agent-cli-golden.test.js` は期待値を両側に書き写す流儀だが、こちらは**正典を実際に
// 読んで**突き合わせる——CLI 定義の golden は「同じ入力から同じ出力が出る」ことの検査で
// 写しに意味があるのに対し、バージョン番号は写しそのものが問題（P2-1）。
//
// 追加依存なしで `node test/contract-version-golden.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const engine = require('../src/features/agent-project/main/engine');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// このファイルは tools/agent-dashboard/test/ にある。リポジトリルートまで 2 段上がる。
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const CANON = path.join(REPO_ROOT, 'tools', 'agent-tools', 'agentcore', 'agentcore', 'board.py');

test('画面の期待値が Python 側の正典（agentcore/board.py）と一致する', () => {
  const py = fs.readFileSync(CANON, 'utf8');
  const m = /^CONTRACT_VERSION\s*=\s*(\d+)\s*$/m.exec(py);
  assert.ok(m, `正典の定義を読めません（定数が動いた？）: ${CANON}`);
  assert.strictEqual(
    engine.EXPECTED_CONTRACT_VERSION,
    Number(m[1]),
    'ノード契約バージョンを上げるときは Python と dashboard を同一コミットで揃えます'
  );
});

console.log(`\n${passed} tests passed (contract-version-golden)`);
