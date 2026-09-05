'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const model = require('../src/main/model');
const aiDiff = require('../src/main/ai-diff');

function sample() {
  return model.normalizeProcedure({
    name: '申請確認', machine: 'review', purpose: '申請を確認する',
    steps: [
      { id: 'open', kind: 'browser', title: '申請を開く', target: 'https://example.test', detail: '一覧を開く' },
      { id: 'judge', kind: 'agent', title: '判定', detail: '内容を確認する' },
    ],
  });
}

test('候補との差を意味単位の選択可能な提案にする', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  candidate.purpose = '申請を安全に確認する';
  candidate.steps[0].detail = '一覧を開き、対象を選ぶ';
  candidate.steps[1].outcomes = [{ when: 'label', label: 'OK', to: 'done' }, { when: 'label', label: 'NG', to: 'abort' }];
  const changes = aiDiff.diff(base, model.normalizeProcedure(candidate));

  assert.deepStrictEqual(changes.map((item) => item.id), ['metadata', 'step:open:content', 'step:judge:flow']);
  assert.ok(changes.every((item) => item.title && item.before !== undefined && item.after !== undefined));
});

test('選んだ提案だけを複製へ反映し、保存はしない', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  candidate.purpose = '申請を安全に確認する';
  candidate.steps[0].detail = '一覧を開き、対象を選ぶ';
  const result = aiDiff.apply({ base, candidate: model.normalizeProcedure(candidate), ids: ['step:open:content'] });

  assert.strictEqual(result.spec.purpose, base.purpose);
  assert.strictEqual(result.spec.steps[0].detail, '一覧を開き、対象を選ぶ');
  assert.strictEqual(base.steps[0].detail, '一覧を開く');
});

test('工程追加・削除・並べ替えは一つの構造変更として扱う', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  candidate.steps.push({ id: 'notify', kind: 'agent', title: '通知', detail: '結果を知らせる' });
  const changes = aiDiff.diff(base, model.normalizeProcedure(candidate));
  assert.deepStrictEqual(changes.map((item) => item.id), ['structure']);
});
