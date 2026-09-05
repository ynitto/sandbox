'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const ai = require('../src/main/ai');
const model = require('../src/main/model');

function sample() {
  return model.normalizeProcedure({
    name: '申請確認', machine: 'review', purpose: '申請を確認する',
    preserved: { context: { api_token: 'keep-me' }, config: {}, states: {}, transitions: [], files: {} },
    steps: [
      {
        id: 'open', kind: 'browser', title: '申請を開く', target: 'https://example.test',
        detail: 'token=do-not-send を使う',
        recorded: [
          { op: 'fill', target: 'textbox', value: '{{user}}', example: '山田太郎' },
          { op: 'fill', target: 'password', value: 'actual-password' },
        ],
      },
      { id: 'judge', kind: 'agent', title: '判定', detail: '内容を確認する' },
    ],
  });
}

test('AIへ渡す仕様は保持データ・記録時の値・秘密らしい値を除く', () => {
  const prompt = ai.reviewPrompt({ spec: sample(), scope: { type: 'workflow' }, focus: '' });
  const sharedSpec = prompt.split('現在の仕様（保持用の内部データ、記録時の入力例、秘密らしい値は除外済み）:\n')[1]
    .split('\n\nfindings は説明用です。')[0];
  assert.doesNotMatch(prompt, /keep-me|山田太郎|do-not-send|actual-password/);
  assert.doesNotMatch(sharedSpec, /"preserved"|"example"/);
  assert.match(prompt, /token=\*\*\*/);
  assert.match(prompt, /整合性|効率性|エラー処理|エッジケース/);
});

test('質問応答は構造化カードとして受け取る', () => {
  const result = ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1,
    status: 'questions',
    summary: '実行条件を確認します',
    questions: [{ id: 'q1', text: '失敗時は再試行しますか？', reason: '経路が未定です', example: '最大2回' }],
    candidate: null,
    assumptions: [],
    findings: [],
  }), { mode: 'review', baseSpec: sample(), scope: { type: 'workflow' } });

  assert.strictEqual(result.status, 'questions');
  assert.strictEqual(result.questions[0].id, 'q1');
});

test('候補は正規化し、元の保持データと記録時の値を復元する', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  delete candidate.preserved;
  candidate.steps[0].detail = '申請ページを開く';
  candidate.steps[0].recorded[0].example = '';
  const result = ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1, status: 'candidate', summary: '明確化しました', questions: [], candidate,
    assumptions: ['ログイン済み'], findings: [],
  }), { mode: 'review', baseSpec: base, scope: { type: 'workflow' } });

  assert.deepStrictEqual(result.candidate.preserved, base.preserved);
  assert.strictEqual(result.candidate.steps[0].recorded[0].example, '山田太郎');
  assert.strictEqual(result.candidate.steps[0].detail, '申請ページを開く');
});

test('工程だけの見直しで範囲外が変わった候補は拒否する', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  candidate.steps[1].detail = '勝手に変えた';
  assert.throws(() => ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1, status: 'candidate', summary: '', questions: [], candidate,
    assumptions: [], findings: [],
  }), { mode: 'review', baseSpec: base, scope: { type: 'step', stepId: 'open' } }), /見直し範囲外/);

  const reordered = JSON.parse(JSON.stringify(base));
  reordered.steps.reverse();
  assert.throws(() => ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1, status: 'candidate', summary: '', questions: [], candidate: reordered,
    assumptions: [], findings: [],
  }), { mode: 'review', baseSpec: base, scope: { type: 'step', stepId: 'open' } }), /見直し範囲外/);
});

test('既存ワークフローの保存名はAIに変更させない', () => {
  const base = sample();
  const candidate = JSON.parse(JSON.stringify(base));
  candidate.machine = 'another-place';
  assert.throws(() => ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1, status: 'candidate', summary: '', questions: [], candidate,
    assumptions: [], findings: [],
  }), { mode: 'review', baseSpec: base, scope: { type: 'workflow' } }), /保存名/);
});

test('JSON以外や空の質問一覧を拒否する', () => {
  assert.throws(() => ai.parseEnvelope('説明です\n{}', { mode: 'draft' }), /JSON/);
  assert.throws(() => ai.parseEnvelope(JSON.stringify({
    schemaVersion: 1, status: 'questions', questions: [], candidate: null,
  }), { mode: 'draft' }), /質問/);
});
