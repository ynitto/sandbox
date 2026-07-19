'use strict';

// 要対応の待ち時間・SLA バッジと「停滞の長い順」ソートの中核ロジックを検証する。
//   - humanizeAge / needAgeInfo（純関数）: mtime → 待ち時間ラベル・SLA レベル
//   - needsViewModel: 未対応（open）バケットを待ち時間の長い順に並べる

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
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
const humanizeAge = new Function(`${grab('humanizeAge')}; return humanizeAge;`)();
// needAgeInfo は humanizeAge を内部で呼ぶので一緒に評価する。
// eslint-disable-next-line no-new-func
const needAgeInfo = new Function(
  `${grab('humanizeAge')}; ${grab('needAgeInfo')}; return needAgeInfo;`
)();
// needsViewModel は needBucket に依存する。テスト用に「open のみ」を返す簡易版を注入する。
// eslint-disable-next-line no-new-func
const needsViewModel = new Function(
  'needBucket',
  `${grab('needsViewModel')}; return needsViewModel;`
)(() => 'open');

const HOUR = 3600000;
const now = 1_000_000_000_000; // 固定の「現在」（テスト決定性）

// 1) humanizeAge: 分・時間・日の境界
{
  assert.strictEqual(humanizeAge(0), 'たった今');
  assert.strictEqual(humanizeAge(30 * 1000), 'たった今');
  assert.strictEqual(humanizeAge(5 * 60000), '5分待ち');
  assert.strictEqual(humanizeAge(3 * HOUR), '3時間待ち');
  assert.strictEqual(humanizeAge(50 * HOUR), '2日待ち');
  console.log('ok - humanizeAge は分/時間/日の境界を正しくラベル化する');
}

// 2) needAgeInfo: SLA レベル（既定 24h → warn=8h, danger=24h）
{
  const mk = (hoursAgo) => ({ mtime: now - hoursAgo * HOUR });
  assert.strictEqual(needAgeInfo(mk(1), now, 24).level, '', '1h は色なし');
  assert.strictEqual(needAgeInfo(mk(8), now, 24).level, 'warn', '8h（sla/3）は warn');
  assert.strictEqual(needAgeInfo(mk(25), now, 24).level, 'danger', '25h（>sla）は danger');
  assert.strictEqual(needAgeInfo(mk(3), now, 24).label, '3時間待ち', 'ラベルも返す');
  console.log('ok - needAgeInfo は SLA しきい値で warn / danger を切り替える');
}

// 3) needAgeInfo: mtime が無ければ date（日付文字列）にフォールバック、両方無ければ空
{
  const byDate = needAgeInfo({ date: new Date(now - 2 * HOUR).toISOString() }, now, 24);
  assert.ok(byDate.label, 'date からも待ち時間を出す');
  const none = needAgeInfo({}, now, 24);
  assert.deepStrictEqual({ ms: none.ms, label: none.label, level: none.level }, { ms: 0, label: '', level: '' });
  console.log('ok - mtime 欠落時は date にフォールバック・両方無しは空');
}

// 4) needsViewModel: 未対応は待ち時間の長い順（mtime 昇順）＝最も停滞したものが先頭
{
  const needs = [
    { id: 'newer', mtime: now - 1 * HOUR, date: '2026-07-15' },
    { id: 'oldest', mtime: now - 30 * HOUR, date: '2026-07-14' },
    { id: 'mid', mtime: now - 10 * HOUR, date: '2026-07-15' },
  ];
  const vm = needsViewModel(needs, 'open', null, () => 'open');
  assert.deepStrictEqual(vm.items.map((n) => n.id), ['oldest', 'mid', 'newer'], '停滞の長い順');
  assert.strictEqual(vm.selected.id, 'oldest', '既定選択は最も停滞したカード');
  console.log('ok - 未対応は待ち時間の長い順に並び、既定選択が最優先になる');
}

console.log('needs-sla: all passed');
