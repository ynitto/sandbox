'use strict';

// セッション開始コマンド（agent-session-commands 契約）のテスト（Electron 不使用）。
// - 契約の読み書き: 不在・破損・enabled=false の no-op、revision 単調増加、未知キーの保持
// - 計画の決定性: プレースホルダ展開（未定義は空文字・クォートを足さない）・when の AND 結合・
//   chat の no-session スキップ・timeout と max_total_timeout の二段有界化
// - cowork の起動スクリプト: 新規セッションのときだけ前準備を差し込み、on_error で分岐する
// - 画面: 設定カードが「共通設定」に載り、保存・プレビューの動線を持つ

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const sc = require('../src/features/orchestration/main/sessionCommands');
const loopProvider = require('../src/features/cowork/main/loopProvider');
const rendererSrc = require('./helpers/renderer-src');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function cfgWith(dir) {
  return { orchestration: { sessionDir: dir } };
}

// ---------------------------------------------------------------------------
// 契約の読み書き
// ---------------------------------------------------------------------------

test('load は無ければ既定（revision:0・enabled:true・コマンドなし）を返す', () => {
  const dir = tmpdir('sesscmd-empty-');
  const data = sc.loadSessionCommands(dir);
  assert.strictEqual(data.revision, 0);
  assert.strictEqual(data.enabled, true);
  assert.deepStrictEqual(data.commands, []);
  assert.strictEqual(data.max_total_timeout, sc.DEFAULT_MAX_TOTAL_TIMEOUT);
});

test('load は壊れた JSON でも既定へ落ちる（エンジンを止めない）', () => {
  const dir = tmpdir('sesscmd-broken-');
  fs.writeFileSync(path.join(dir, 'session.json'), '{ これは JSON ではない');
  const data = sc.loadSessionCommands(dir);
  assert.strictEqual(data.revision, 0);
  assert.deepStrictEqual(data.commands, []);
});

test('save は patch をマージし revision を単調増加させ未知キーを保持する', () => {
  const dir = tmpdir('sesscmd-save-');
  fs.writeFileSync(path.join(dir, 'session.json'), JSON.stringify({
    version: 1, revision: 4, enabled: true, commands: [], future_key: 'keep',
  }));
  const saved = sc.saveSessionCommands(cfgWith(dir), {
    commands: [{ id: 'a', mode: 'process', run: 'echo hi' }],
  });
  assert.strictEqual(saved.revision, 5);
  assert.strictEqual(saved.commands.length, 1);
  assert.strictEqual(saved.updated_by, 'dashboard');
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'session.json'), 'utf8'));
  assert.strictEqual(raw.future_key, 'keep', '未知キーは書換で失われない');
});

test('save は ID か実行内容が欠けた行と ID 重複を拒否する', () => {
  const dir = tmpdir('sesscmd-invalid-');
  assert.throws(
    () => sc.saveSessionCommands(cfgWith(dir), { commands: [{ id: '', run: 'echo hi' }] }),
    /ID と実行内容/
  );
  assert.throws(
    () => sc.saveSessionCommands(cfgWith(dir), {
      commands: [{ id: 'a', run: 'echo 1' }, { id: 'a', run: 'echo 2' }],
    }),
    /重複/
  );
});

test('resolveSessionDir は設定 → 環境変数 → 共通ホームの順で決まる', () => {
  assert.strictEqual(sc.resolveSessionDir({ orchestration: { sessionDir: '/tmp/x' } }), '/tmp/x');
  const prev = process.env.AGENT_SESSION_DIR;
  process.env.AGENT_SESSION_DIR = '/tmp/env-session';
  try {
    assert.strictEqual(sc.resolveSessionDir({}), '/tmp/env-session');
  } finally {
    if (prev === undefined) delete process.env.AGENT_SESSION_DIR;
    else process.env.AGENT_SESSION_DIR = prev;
  }
  assert.ok(sc.resolveSessionDir({}).endsWith(path.join('session')));
});

// ---------------------------------------------------------------------------
// 計画（プレースホルダ・when・有界化）
// ---------------------------------------------------------------------------

test('プレースホルダは決定的に展開し、未定義は空文字へ落とす', () => {
  const ctx = { cwd: '/w/repo', engine: 'kiro-loop' };
  assert.strictEqual(sc.expandPlaceholders('git -C {cwd} fetch', ctx), 'git -C /w/repo fetch');
  assert.strictEqual(sc.expandPlaceholders('run {node_id} now', ctx), 'run  now');
  assert.strictEqual(sc.expandPlaceholders('{unknown}', ctx), '{unknown}', '契約外の名前は触らない');
});

test('プレースホルダ展開はクォートを足さない（引用は利用者の責任）', () => {
  const out = sc.expandPlaceholders('cd {cwd} && ls', { cwd: '/w/my repo' });
  assert.strictEqual(out, 'cd /w/my repo && ls');
});

test('when は指定した軸をすべて満たすときだけ通る（AND 結合）', () => {
  const when = { engines: ['kiro-loop'], workloads: ['routine'] };
  assert.strictEqual(sc.matchesWhen(when, { engine: 'kiro-loop', workload: 'routine' }), true);
  assert.strictEqual(sc.matchesWhen(when, { engine: 'agent-flow', workload: 'routine' }), false);
  assert.strictEqual(sc.matchesWhen(null, { engine: 'agent-flow' }), true, '省略は全適用');
  assert.strictEqual(sc.matchesWhen(when, {}), true, '判定材料が無い軸では絞らない');
});

test('plan は enabled=false を完全な no-op にする', () => {
  const data = { enabled: false, commands: [{ id: 'a', run: 'echo hi' }] };
  assert.deepStrictEqual(sc.plan(data, { engine: 'kiro-loop' }), []);
});

test('plan は chat を単発系で no-session としてスキップする', () => {
  const data = { commands: [{ id: 'c', mode: 'chat', run: 'docs を読んで' }] };
  const onLoop = sc.plan(data, { engine: 'kiro-loop' });
  assert.strictEqual(onLoop[0].skip, null);
  const onFlow = sc.plan(data, { engine: 'agent-flow' });
  assert.strictEqual(onFlow[0].skip, 'no-session', '理由を残す（黙って落とさない）');
});

test('plan は when で外れた行も理由つきで残す（UI が灰色で見せられる）', () => {
  const data = { commands: [{ id: 'a', run: 'echo hi', when: { engines: ['agent-flow'] } }] };
  const entries = sc.plan(data, { engine: 'kiro-loop' });
  assert.strictEqual(entries.length, 1);
  assert.strictEqual(entries[0].skip, 'when');
});

test('plan は合計上限秒で timeout を切り詰め、超えた行を budget でスキップする', () => {
  const data = {
    max_total_timeout: 100,
    commands: [
      { id: 'a', run: 'x', timeout: 60 },
      { id: 'b', run: 'y', timeout: 60 },
      { id: 'c', run: 'z', timeout: 30 },
    ],
  };
  const entries = sc.plan(data, { engine: 'kiro-loop' });
  assert.strictEqual(entries[0].timeout, 60);
  assert.strictEqual(entries[1].timeout, 40, '残り予算へ切り詰める');
  assert.strictEqual(entries[2].skip, 'budget');
});

test('plan は cwd 省略時にセッションの cwd を使い、既定 timeout を埋める', () => {
  const entries = sc.plan({ commands: [{ id: 'a', run: 'echo hi' }] }, { engine: 'kiro-loop', cwd: '/w' });
  assert.strictEqual(entries[0].cwd, '/w');
  assert.strictEqual(entries[0].timeout, sc.DEFAULT_TIMEOUT);
  assert.strictEqual(entries[0].on_error, 'warn', '既定は続行（フェイルセーフ）');
});

// ---------------------------------------------------------------------------
// cowork の起動スクリプト
// ---------------------------------------------------------------------------

test('起動スクリプトは新規セッションのときだけ前準備を差し込む', () => {
  const entries = sc.plan({ commands: [{ id: 'sync', run: 'git fetch' }] }, { engine: 'dashboard', cwd: '/w' });
  const script = loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat', cwd: '/w', session: 'kiro-dash-x',
    prompt: '定期処理を実行して', sessionCommands: entries,
  });
  const head = script.slice(0, script.indexOf('tmux new-session'));
  assert.ok(head.includes('git fetch'), '前準備は new-session より前に置く');
  assert.ok(head.includes('has-session'), 'has-session が偽の分岐の中にある');
  assert.ok(script.includes('__new=1'), '新規作成を示すフラグを立てる');
});

test('起動スクリプトは on_error で分岐する（fail は起動しない）', () => {
  const warn = loopProvider.sessionProcessLines([
    { id: 'a', mode: 'process', run: 'echo hi', timeout: 30, on_error: 'warn' },
  ]);
  assert.ok(warn.includes('timeout 30'));
  assert.ok(warn.includes('続行します'));
  assert.ok(!warn.includes('exit 1'));
  const fail = loopProvider.sessionProcessLines([
    { id: 'a', mode: 'process', run: 'echo hi', timeout: 30, on_error: 'fail' },
  ]);
  assert.ok(fail.includes('exit 1'), 'fail はスクリプトを抜けて tmux を作らせない');
});

test('起動スクリプトはスキップ済みの行を差し込まない', () => {
  const lines = loopProvider.sessionProcessLines([
    { id: 'a', mode: 'process', run: 'echo skipped', skip: 'when' },
  ]);
  assert.strictEqual(lines, '');
});

test('chat は paste-buffer で送り、業務プロンプトより前に置く', () => {
  const entries = sc.plan(
    { commands: [{ id: 'prime', mode: 'chat', run: 'docs を読んで' }] },
    { engine: 'dashboard' }
  );
  const script = loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat', cwd: '/w', session: 's',
    prompt: '本題のプロンプト', sessionCommands: entries,
  });
  const chatAt = script.indexOf('docs を読んで');
  const promptAt = script.indexOf('本題のプロンプト');
  assert.ok(chatAt > 0 && promptAt > 0);
  assert.ok(chatAt < promptAt, 'chat コマンドは業務プロンプトより先に送る');
  assert.ok(script.includes('paste-buffer'), 'send-keys ではなく paste-buffer で送る');
});

// CLIチャット起動ボタン（プロンプトを送らない経路）との統合。両機能が同じ
// chatWindowScript を通るため、片方だけを見たテストでは落ちない穴が空きやすい。
test('プロンプトを送らない起動（CLIチャット）は毎回「エージェントに送る」を実行する', () => {
  const entries = sc.plan(
    { commands: [
      { id: 'sync', mode: 'process', run: 'git fetch' },
      { id: 'prime', mode: 'chat', run: 'docs を読んで' },
    ] },
    { engine: 'dashboard', cwd: '/w' }
  );
  const script = loopProvider.chatWindowScript({
    chatCommand: ['claude', '--model', 'sonnet'], cwd: '/w', session: 's',
    prompt: null, sessionCommands: entries,
  });
  assert.ok(script.includes('git fetch'), 'process はプロンプト無しでも走る');
  assert.ok(script.includes('docs を読んで'), 'chat もプロンプト無しの起動で送る');
  assert.ok(script.includes('grep -qE'), 'chat を送るなら入力プロンプトを待つ');
  // 業務プロンプト無しの手動オープンでは、既存セッションへ再接続しても送れるよう
  // 新規セッション（__new）に限定しない。__new 限定だと初回以降・設定変更後に一度も効かない。
  const chatAt = script.indexOf('docs を読んで');
  const guardAt = script.lastIndexOf('__new -eq 1', chatAt);
  assert.ok(
    guardAt < 0 || script.slice(guardAt, chatAt).includes('fi;'),
    'chat 送信は __new（新規セッション）に閉じ込めない'
  );
});

test('業務プロンプトを送る定常ループでは chat 前準備を新規セッション時だけ送る（二重送信を避ける）', () => {
  const entries = sc.plan(
    { commands: [{ id: 'prime', mode: 'chat', run: 'docs を読んで' }] },
    { engine: 'kiro-loop', cwd: '/w' }
  );
  const script = loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat', cwd: '/w', session: 's',
    prompt: '本題のプロンプト', sessionCommands: entries,
  });
  const chatAt = script.indexOf('docs を読んで');
  const guardAt = script.lastIndexOf('if [ $__new -eq 1 ]; then', chatAt);
  assert.ok(guardAt >= 0, 'ループでは chat 前準備を __new で囲う');
});

test('送るものが何も無ければ入力プロンプトを待たずに接続する', () => {
  const script = loopProvider.chatWindowScript({
    chatCommand: ['claude'], cwd: '/w', session: 's', prompt: null, sessionCommands: [],
  });
  assert.ok(!script.includes('grep -qE'), '接続だけなら待たない');
  assert.ok(!script.includes('tmux set-buffer -b agentdash --'), '空プロンプトを送らない');
  assert.ok(script.includes('tmux attach -t "$__ses"'));
});

test('CLIチャット起動ボタンの経路が開始コマンドを渡す', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'features', 'agent-project', 'main', 'agent.js'), 'utf8'
  );
  assert.ok(/sessionCommands:\s*planSessionCommands\(cfg, projectDir\)/.test(src),
    'openInteractiveChat が計画を渡していない（新規セッションなのに前準備が走らなくなる）');
});

test('コマンドが無ければ起動スクリプトは従来と同じ形のままになる', () => {
  const withNone = loopProvider.chatWindowScript({
    chatCommand: 'kiro-cli chat', cwd: '/w', session: 's', prompt: 'p', sessionCommands: [],
  });
  assert.ok(!withNone.includes('セッション開始コマンド'));
  assert.ok(withNone.includes('tmux new-session'));
});

// ---------------------------------------------------------------------------
// 画面
// ---------------------------------------------------------------------------

test('設定カードは「共通設定」に共通指示と並べて置く', () => {
  const src = rendererSrc.read();
  assert.ok(src.includes('orchSessionCommandsPanelHtml'));
  assert.ok(/orchInstructionsPanelHtml\(overview\)\}\$\{orchSessionCommandsPanelHtml\(overview\)/.test(src),
    '共通指示カードの直下に並べる');
});

test('設定カードは保存・追加・プレビューの動線を持つ', () => {
  const src = rendererSrc.read();
  for (const id of ['btn-orch-sess-save', 'btn-orch-sess-add', 'btn-orch-sess-preview',
    'orch-sess-enabled', 'orch-sess-max-total', 'orch-sess-preview-engine']) {
    assert.ok(src.includes(id), `${id} が無い`);
  }
  assert.ok(src.includes('orchestrationSessionCommandsSave'));
  assert.ok(src.includes('orchestrationSessionCommandsPreview'));
  assert.ok(src.includes('使用するコマンド'), '利用者が設定内容を見出しだけで判断できる');
  assert.ok(src.includes('通常は設定しなくても使えます'), '設定が任意であることを明示する');
  assert.ok(/id="btn-orch-sess-save" class="primary-inline"[^>]*>保存</.test(src),
    '保存操作は共通の色と短い文言にする');
});

test('未保存の入力はポーリング再描画から守る', () => {
  const src = rendererSrc.read();
  assert.ok(src.includes('state.orchSessionDirty'));
  assert.ok(/!state\.orchInstructionsDirty && !state\.orchSessionDirty/.test(src));
});

test('画面は反映状況と注意書き（引用・中止・反映点）を出す', () => {
  const src = rendererSrc.read();
  assert.ok(src.includes('session_commands_revision_applied'));
  assert.ok(src.includes('で囲んでください'), 'シェル引用は利用者の責任だと明示する');
  assert.ok(src.includes('起動しなくなります'), 'fail の副作用を明示する');
  assert.ok(src.includes('次に始まるセッションから'), '反映点を明示する');
});

console.log(`\n${passed} session-commands tests passed`);
