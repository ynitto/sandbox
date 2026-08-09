'use strict';

// メッセージキュー投函（M3）のテスト。
//   ・queueMessage は agent-loop msg の CLI 依頼を組む（生の send-keys もファイル直書きもしない）
//   ・listQueue は受信ボックス（~/.kiro/agents/<name>/inbox）の待機中と処理済みを読む
// listQueue は実スクリプトを一時フィクスチャへ向けて bash で実行し、
// スクリプトと JSON 解析の両方を通す（shInWsl は非 win32 では bash -lc に落ちる）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const send = require('../src/features/routines/main/send');
const exec = require('../src/features/routines/main/exec');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('queueMessage は repo を cwd にして agent-loop msg を組み立てる', () => {
  const orig = exec.shInWsl;
  let script = '';
  exec.shInWsl = (s) => {
    script = s;
    return { ok: true, stdout: '', stderr: '[agent-loop msg] メッセージを投函しました', error: '', status: 0 };
  };
  try {
    const res = send.queueMessage({
      repo: '\\\\wsl.localhost\\Ubuntu\\home\\me\\app',
      agent: 'worker1',
      subject: '依頼',
      body: 'feature X を実装して',
    });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.queued, true);
    assert.ok(script.includes("cd '/home/me/app'"), 'repo を WSL パスへ寄せて cd する');
    assert.ok(script.includes('command -v agent-loop'), 'CLI の存在を先に確かめる');
    assert.ok(script.includes("agent-loop msg --to 'worker1' --from agent-dashboard --subject '依頼' 'feature X を実装して'"),
      '本文は argv 1 要素で渡す');
  } finally {
    exec.shInWsl = orig;
  }
});

test('queueMessage は件名なしなら --subject を付けず、宛先・本文の欠落は CLI を呼ばず拒否する', () => {
  const orig = exec.shInWsl;
  let called = 0;
  let script = '';
  exec.shInWsl = (s) => {
    called += 1;
    script = s;
    return { ok: true, stdout: '', stderr: '', error: '', status: 0 };
  };
  try {
    assert.strictEqual(send.queueMessage({ agent: '', body: 'x' }).ok, false);
    assert.strictEqual(send.queueMessage({ agent: 'worker1', body: '  ' }).ok, false);
    assert.strictEqual(called, 0, '入力不備では CLI を呼ばない');
    assert.strictEqual(send.queueMessage({ agent: 'worker1', body: 'x' }).ok, true);
    assert.ok(!script.includes('--subject'), '件名なしなら --subject を付けない');
    assert.ok(!script.includes('cd '), 'repo なしなら cd しない');
  } finally {
    exec.shInWsl = orig;
  }
});

test('queueMessage は CLI の失敗をエラーとして返す', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = () => ({ ok: false, stdout: '', stderr: 'ERROR: 書き込みに失敗', error: '', status: 1 });
  try {
    const res = send.queueMessage({ agent: 'worker1', body: 'x' });
    assert.strictEqual(res.ok, false);
    assert.strictEqual(res.queued, false);
    assert.match(res.error, /書き込みに失敗/);
  } finally {
    exec.shInWsl = orig;
  }
});

// フィクスチャ: <base>/worker1/inbox に待機 2 件 + .processed 1 件、<base>/orchestrator は空
function makeInboxFixture() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-inbox-'));
  const inbox = path.join(base, 'worker1', 'inbox');
  fs.mkdirSync(path.join(inbox, '.processed'), { recursive: true });
  fs.writeFileSync(path.join(inbox, '1754700100_bbb.json'), JSON.stringify({
    id: 'bbb', from: 'orchestrator', to: 'worker1', created_at: 1754700100.0,
    subject: '2番目', body: 'あとで', correlation_id: 'c2',
  }, null, 2), 'utf8');
  fs.writeFileSync(path.join(inbox, '1754700000_aaa.json'), JSON.stringify({
    id: 'aaa', from: 'agent-dashboard', to: 'worker1', created_at: 1754700000.0,
    subject: '1番目', body: 'さきに', correlation_id: 'c1',
  }, null, 2), 'utf8');
  fs.writeFileSync(path.join(inbox, '.processed', '1754600000_old.json'), JSON.stringify({
    id: 'old', from: 'x', to: 'worker1', created_at: 1754600000.0, subject: '済み', body: 'done',
  }), 'utf8');
  fs.mkdirSync(path.join(base, 'orchestrator', 'inbox'), { recursive: true });
  return base;
}

test('listQueue は受信ボックスの待機中（投函順）と処理済み件数を返す', () => {
  const base = makeInboxFixture();
  const orig = exec.shInWsl;
  // 実スクリプトの $HOME/.kiro/agents だけをフィクスチャへ差し替え、実 bash で走らせる
  exec.shInWsl = (script, timeoutMs, distro) =>
    orig(script.replace('"$HOME"/.kiro/agents', exec.shellQuote(base)), timeoutMs, distro);
  try {
    const res = send.listQueue({});
    assert.strictEqual(res.ok, true);
    assert.deepStrictEqual(res.agents.map((a) => a.name), ['orchestrator', 'worker1']);
    const worker = res.agents.find((a) => a.name === 'worker1');
    assert.strictEqual(worker.processed, 1);
    assert.deepStrictEqual(worker.pending.map((m) => m.id), ['aaa', 'bbb'], '投函順（created_at 昇順）');
    assert.deepStrictEqual(worker.pending.map((m) => m.subject), ['1番目', '2番目']);
    assert.strictEqual(worker.pending[0].from, 'agent-dashboard');
    const empty = res.agents.find((a) => a.name === 'orchestrator');
    assert.deepStrictEqual(empty.pending, []);
    assert.strictEqual(empty.processed, 0);
    // agent 指定で絞れる
    const only = send.listQueue({ agent: 'worker1' });
    assert.deepStrictEqual(only.agents.map((a) => a.name), ['worker1']);
  } finally {
    exec.shInWsl = orig;
  }
});

test('parseInboxOutput は壊れたメッセージ JSON をスキップする', () => {
  const sep = '\u001e';
  const agents = send.parseInboxOutput(
    `${sep}A worker1\n${sep}M {broken json${sep}M ${JSON.stringify({ id: 'ok1', from: 'a', created_at: 1 })}${sep}P 3\n`
  );
  assert.strictEqual(agents.length, 1);
  assert.deepStrictEqual(agents[0].pending.map((m) => m.id), ['ok1']);
  assert.strictEqual(agents[0].processed, 3);
});

console.log(`\n${passed} routine-agent-queue tests passed`);
