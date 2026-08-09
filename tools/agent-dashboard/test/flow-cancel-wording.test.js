'use strict';

// run 画面の「打ち切ってやり直す」と「保留にする」を検証する。
//
// 背景: バックログのタスクに紐づく run の中止は、run を終端化したあとタスクを detach→ready で
// 積み直す（ipc.js flow:cancel ＋ loop.py の cancelled → retries+1・ready）。ボタンが「中止」と
// 名乗ると、押した人は止まったと思い、実行中へ戻るたびに何度も押すことになる。作業を止める
// 口（hold）は要対応カードとバックログ詳細にしかなく、実行中のタスクには要対応の票が無いため
// この画面からは到達できなかった。

const assert = require('assert');

const renderer = require('./helpers/renderer-src').read();

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- 文言: 積み直しが起きる run を「中止」と呼ばない ---------------------------------

const runActions = renderer.slice(
  renderer.indexOf('const cancelable = !archived'),
  renderer.indexOf('flow-primary-actions') + 200
);

(async () => {
  await test('積み直す run のボタンは「打ち切ってやり直す」', () => {
    assert.ok(/const requeues = /.test(runActions), 'run がタスクへ積み直されるかを判定する');
    assert.ok(/taskOfRun\(run\)/.test(runActions), '紐づくバックログタスクの有無で判定する');
    assert.ok(/打ち切ってやり直す/.test(runActions), '積み直す run はやり直しと名乗る');
    assert.ok(
      /requeues \? '↺ 打ち切ってやり直す' : '■ 中止'/.test(runActions),
      'タスクに紐づかない run（agent-flow 単体）は従来どおり「中止」'
    );
  });

  await test('run 画面に保留（作業そのものを止める口）がある', () => {
    assert.ok(/id="flow-hold"/.test(runActions), '保留ボタンを出す');
    assert.ok(/data-hold-task=/.test(runActions), '対象タスク id をボタンに載せる');
    assert.ok(
      runActions.indexOf('${holdBtn}') > 0,
      '保留ボタンを主要操作の並びへ入れる（定義しただけで描画しないのを防ぐ）'
    );
  });

  // --- 挙動: 保留は commands ドロップ（hold）を投函する ------------------------------

  await test('保留ボタンは hold を投函する（確認 → runAction → 再読込）', async () => {
    const calls = [];
    // renderer は古典スクリプトなので、関数 1 本を切り出して依存を注入するにはこれしかない
    // （他の renderer テストと同じ手口）。
    // eslint-disable-next-line no-new-func
    const holdFlowRunTask = new Function(
      'state', 'confirmDialog', 'guard', 'api', 'uiLog', 'toast', 'reloadProject',
      `async ${grab('holdFlowRunTask')}; return holdFlowRunTask;`
    )(
      { project: { dir: '/p' } },
      async () => true,
      async (_label, fn) => fn(),
      { runAction: async (payload) => { calls.push(payload); return { ok: true }; } },
      () => {},
      () => {},
      async () => { calls.push('reload'); }
    );
    await holdFlowRunTask('T-1');
    assert.deepStrictEqual(
      { ...calls[0], reason: undefined },
      { dir: '/p', action: 'hold', id: 'T-1', reason: undefined },
      'hold を対象タスクへ投函する'
    );
    assert.ok(String(calls[0].reason || '').trim(), '決定記録に残る理由を付ける');
    assert.strictEqual(calls[1], 'reload', '投函後に読み直す');
  });

  await test('確認をキャンセルしたら何も投函しない', async () => {
    const calls = [];
    // renderer は古典スクリプトなので、関数 1 本を切り出して依存を注入するにはこれしかない
    // （他の renderer テストと同じ手口）。
    // eslint-disable-next-line no-new-func
    const holdFlowRunTask = new Function(
      'state', 'confirmDialog', 'guard', 'api', 'uiLog', 'toast', 'reloadProject',
      `async ${grab('holdFlowRunTask')}; return holdFlowRunTask;`
    )(
      { project: { dir: '/p' } },
      async () => false,
      async (_label, fn) => fn(),
      { runAction: async (payload) => { calls.push(payload); return {}; } },
      () => {}, () => {}, async () => {}
    );
    await holdFlowRunTask('T-1');
    assert.strictEqual(calls.length, 0);
  });

  await test('打ち切りの確認文は積み直しを先に言う', () => {
    const src = grab('cancelFlowRun');
    assert.ok(/requeues/.test(src), '積み直す run かで文面を分ける');
    assert.ok(/積み直され/.test(src), '新しい実行が始まることを確認文で言う');
    assert.ok(/保留にする/.test(src), '止めたい人を保留へ案内する');
  });

  console.log(`\n${passed} tests passed (flow-cancel-wording)`);
})();
