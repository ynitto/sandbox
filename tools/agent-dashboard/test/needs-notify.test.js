'use strict';

// 要対応（needs）の増分検知 → OS 通知の中核ロジックを検証する。
//   - computeNeedsDelta（renderer.js の純関数）: needsCount の増分だけを通知に変換する
//   - notify.targetUrl（base/main/notify）: 通知クリックの遷移先ディープリンク組み立て

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const notify = require('../src/main/notify');

// renderer.js から純関数のソースを切り出して評価する（他 UI テストと同じ grab パターン）。
const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let i = at + `function ${name}`.length;
  while (i < renderer.length && renderer[i] !== '(') i++;
  let depth = 0;
  for (; i < renderer.length; i++) {
    const ch = renderer[i];
    if (ch === '(') depth++;
    else if (ch === ')') {
      depth--;
      if (depth === 0) {
        i++;
        break;
      }
    }
  }
  while (i < renderer.length && renderer[i] !== '{') i++;
  depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

// eslint-disable-next-line no-new-func
const computeNeedsDelta = new Function(`${grab('computeNeedsDelta')}; return computeNeedsDelta;`)();

// 1) 初回（prev 空）は通知ゼロ・カウントを seed・総数を集計する
{
  const projects = [
    { dir: 'A', root: 'A/.state', name: 'alpha', needsCount: 2 },
    { dir: 'B', root: 'B/.state', name: 'beta', needsCount: 0 },
  ];
  const r = computeNeedsDelta({}, projects);
  assert.strictEqual(r.total, 2, '総数は needsCount の合計');
  assert.strictEqual(r.notifications.length, 0, '初回観測（before=undefined）では通知しない');
  assert.deepStrictEqual(r.counts, { A: 2, B: 0 }, 'カウントを次回比較用に seed する');
  console.log('ok - 初回はベースライン取得のみで通知しない');
}

// 2) 観測済みプロジェクトで数が増えたら、増分ぶんの通知を作る
{
  const prev = { A: 2, B: 0 };
  const projects = [
    { dir: 'A', root: 'A/.state', name: 'alpha', needsCount: 2 }, // 変化なし
    { dir: 'B', root: 'B/.state', name: 'beta', needsCount: 3 }, // +3
  ];
  const r = computeNeedsDelta(prev, projects);
  assert.strictEqual(r.notifications.length, 1, '増えたプロジェクトだけ通知');
  assert.deepStrictEqual(r.notifications[0], {
    name: 'beta',
    root: 'B/.state',
    added: 3,
    total: 3,
  });
  assert.strictEqual(r.total, 5, 'バッジ用の総数は全プロジェクト合計');
  console.log('ok - 観測済みで needsCount が増えたら増分ぶんを通知する');
}

// 3) 減少・新規発見（before=undefined）では通知しない
{
  const prev = { A: 5 };
  const projects = [
    { dir: 'A', root: 'A/.state', name: 'alpha', needsCount: 1 }, // 5→1 減少（対応済み）
    { dir: 'C', root: 'C/.state', name: 'gamma', needsCount: 4 }, // 新規発見
  ];
  const r = computeNeedsDelta(prev, projects);
  assert.strictEqual(r.notifications.length, 0, '減少・新規発見では通知しない');
  assert.strictEqual(r.total, 5, '総数は追随する');
  console.log('ok - 減少・新規発見では通知しない（増分のみ）');
}

// 4) exists:false（登録が実在しない）は総数・通知の対象外
{
  const prev = { A: 0 };
  const projects = [
    { dir: 'A', root: 'A/.state', name: 'alpha', needsCount: 3, exists: false },
  ];
  const r = computeNeedsDelta(prev, projects);
  assert.strictEqual(r.total, 0, 'exists:false は総数に数えない');
  assert.strictEqual(r.notifications.length, 0, 'exists:false は通知しない');
  console.log('ok - exists:false は総数・通知の対象外');
}

// 5) charterName 優先の表示名 / needsCount 未定義は 0 扱い
{
  const prev = { A: 0 };
  const projects = [
    { dir: 'A', root: 'A/.state', name: 'alpha', charterName: 'Payments', needsCount: 1 },
    { dir: 'D', root: 'D/.state', name: 'delta' }, // needsCount 未定義
  ];
  const r = computeNeedsDelta(prev, projects);
  assert.strictEqual(r.notifications[0].name, 'Payments', '表示名は charterName を優先');
  assert.strictEqual(r.counts.D, 0, 'needsCount 未定義は 0 扱い');
  console.log('ok - 表示名は charterName 優先・未定義 needsCount は 0');
}

// 6) notify.targetUrl: 通知クリックの遷移先は既存のディープリンクスキーム
{
  const url = notify.targetUrl({ root: '/home/me/clones/payments', name: 'payments' });
  assert.ok(url.startsWith('agent-dashboard://open?'), 'agent-dashboard:// スキーム');
  const u = new URL(url);
  assert.strictEqual(u.searchParams.get('root'), '/home/me/clones/payments');
  assert.strictEqual(u.searchParams.get('project'), 'payments');
  assert.strictEqual(notify.targetUrl({}), '', 'root なしは空（窓を前面化するだけ）');
  assert.strictEqual(notify.targetUrl(null), '', 'null は空');
  console.log('ok - targetUrl は既存の agent-dashboard:// ディープリンクを組み立てる');
}

console.log('needs-notify: all passed');
