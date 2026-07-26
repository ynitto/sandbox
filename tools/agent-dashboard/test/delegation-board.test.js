'use strict';

// 委譲公示板（agent-board）ターゲットのテスト（Electron 不使用）。
// - contract: post 封筒が additive な requires / speculation を保持する
// - board アダプタ: post/award/cancel のファイル投函、板ファイル → 正規化ビュー（入札の勝者判定・フェーズ）
// - IPC 配線: target='board' で post/award/cancel が板リポジトリへ届き、list が board を含める

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const contract = require('../src/features/delegation/main/contract');
const boardAdapter = require('../src/features/delegation/main/board-adapter');
const delegationIpc = require('../src/features/delegation/main/ipc');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function ipcHandlers(cfg) {
  const handlers = {};
  delegationIpc.registerIpc({
    handle: (ch, fn) => { handlers[ch] = fn; },
    loadConfig: () => cfg,
    saveConfig: () => cfg,
  });
  return handlers;
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj), 'utf8');
}

// --- contract: additive ブロックの保持 --------------------------------------

test('封筒: post が requires / speculation を保持する（board が解釈する additive）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'flow', goal: '実装',
    requires: { tags: ['python'], repos: ['app'] },
    speculation: { max_runners: 2, resolve: 'first-valid' },
  });
  assert.deepStrictEqual(env.requires, { tags: ['python'], repos: ['app'] });
  assert.deepStrictEqual(env.speculation, { max_runners: 2, resolve: 'first-valid' });
});

// --- board アダプタ ---------------------------------------------------------

test('board: submitPost が delegations/<id>/post.json を書く（冪等）', () => {
  const repo = tmpdir('deleg-board-');
  const env = contract.buildEnvelope('post', { id: 'dg-1', workload: 'flow', goal: 'g' });
  const res = boardAdapter.submitPost(repo, env);
  assert.strictEqual(res.file, path.join(repo, 'delegations', 'dg-1', 'post.json'));
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.id, 'dg-1');
  // 再投函は同一公示（二重公示防止）
  const res2 = boardAdapter.submitPost(repo, env);
  assert.ok(res2.duplicate);
});

test('board: toView が open → working → done のフェーズと勝者を導く', () => {
  const repo = tmpdir('deleg-board-');
  const dir = path.join(repo, 'delegations', 'dg-2');
  writeJson(path.join(dir, 'post.json'),
    { op: 'post', version: 1, id: 'dg-2', workload: 'flow', goal: 'g', title: 'T' });
  // open: 入札なし
  let v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'open');
  assert.strictEqual(v.units[0].bids.length, 0);

  // 入札 2 件（ts 昇順で pc-b が勝者）
  const soon = 9999999999;
  writeJson(path.join(dir, 'bids', 'pc-b.json'), { who: 'pc-b', ts: 100, lease_until: soon });
  writeJson(path.join(dir, 'bids', 'pc-a.json'), { who: 'pc-a', ts: 200, lease_until: soon });
  writeJson(path.join(dir, 'status', 'pc-b.json'), { who: 'pc-b', state: 'working' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'working');
  assert.strictEqual(v.units[0].assignee, 'pc-b');
  const winner = v.units[0].bids.find((b) => b.state === 'winner');
  assert.strictEqual(winner.who, 'pc-b');

  // done: result.json
  writeJson(path.join(dir, 'result.json'), { winner: 'pc-b', status: 'done', resolved_at: 'x' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'done');
  assert.strictEqual(v.result.by, 'pc-b');
});

test('board: 失効した入札は expired・cancelled マーカーで cancelled', () => {
  const repo = tmpdir('deleg-board-');
  const dir = path.join(repo, 'delegations', 'dg-3');
  writeJson(path.join(dir, 'post.json'), { id: 'dg-3', workload: 'flow', goal: 'g' });
  writeJson(path.join(dir, 'bids', 'pc-a.json'), { who: 'pc-a', ts: 1, lease_until: 5 });
  let v = boardAdapter.toView(dir, 1000);  // now=1000 > lease 5 → 失効
  assert.strictEqual(v.units[0].bids[0].state, 'expired');
  assert.strictEqual(v.phase, 'open');
  writeJson(path.join(dir, 'cancelled.json'), { cancelled_at: 'x' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'cancelled');
});

// --- IPC 配線 ---------------------------------------------------------------

test('IPC: target=board の post は板リポジトリへ投函する', () => {
  const repo = tmpdir('deleg-board-');
  const cfg = { delegation: { boardRepos: [repo] } };
  const h = ipcHandlers(cfg);
  const res = h['delegation:post']({
    target: 'board', boardRepo: repo, workload: 'flow', goal: '実装',
    requires: { repos: ['app'] },
  });
  assert.strictEqual(path.dirname(res.file), path.join(repo, 'delegations', res.id));
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.deepStrictEqual(rec.requires, { repos: ['app'] });
});

// S8-2/8-3: 板への書き込み（cancel / award / 手動入札）は**この PC の常駐体へ指示を投函**する。
// dashboard が板の作業クローンへ直接書いても push する主体が居らず、`git+` 板では
// 黙って効かないボタンだった（board-adapter の直書きはこの経路へ移した）。
test('IPC: target=board の award / cancel は板へ直接書かず常駐体へ指示を投函する', () => {
  const repo = tmpdir('deleg-board-');
  const commands = tmpdir('node-commands-');
  const h = ipcHandlers({ delegation: { boardRepos: [repo], nodeCommandsDir: commands } });

  const aw = h['delegation:award']({ target: 'board', boardRepo: repo, id: 'dg-9', node: 'pc-a' });
  const awRec = JSON.parse(fs.readFileSync(aw.file, 'utf8'));
  assert.strictEqual(awRec.command, 'board-award');
  assert.strictEqual(awRec.id, 'dg-9');
  assert.strictEqual(awRec.node, 'pc-a');
  assert.strictEqual(awRec.board, repo);
  assert.strictEqual(awRec.issued_by, 'agent-dashboard');

  const cn = h['delegation:cancel']({
    target: 'board', boardRepo: repo, id: 'dg-9', workload: 'flow', reason: 'stop',
  });
  const cnRec = JSON.parse(fs.readFileSync(cn.file, 'utf8'));
  assert.strictEqual(cnRec.command, 'board-cancel');
  assert.strictEqual(cnRec.reason, 'stop');

  // 板そのものは 1 バイトも変わらない（書くのは常駐体だけ）。
  assert.ok(!fs.existsSync(path.join(repo, 'delegations', 'dg-9', 'award.json')));
  assert.ok(!fs.existsSync(path.join(repo, 'delegations', 'dg-9', 'cancelled.json')));
  // 常駐体は名前順に処理するので、投函順（入札 → 中止）がファイル名の順序で保たれる。
  const names = fs.readdirSync(commands).filter((n) => n.endsWith('.json')).sort();
  assert.deepStrictEqual(names, [path.basename(aw.file), path.basename(cn.file)].sort());
});

test('IPC: 手動入札はノード宛て指示として投函される（claim 規則を UI に複製しない）', () => {
  const repo = tmpdir('deleg-board-');
  const commands = tmpdir('node-commands-');
  const h = ipcHandlers({ delegation: { boardRepos: [repo], nodeCommandsDir: commands } });
  const res = h['delegation:nodeCommand']({
    action: 'board-bid', id: 'dg-7', boardRepo: repo, reason: '手動で引き受け',
  });
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.command, 'board-bid');
  assert.strictEqual(rec.id, 'dg-7');
  assert.ok(!fs.existsSync(path.join(repo, 'delegations', 'dg-7', 'bids')), '板へ bid を書かない');
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-bid', id: '../etc' }),
    /不正な id/);
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-explode', id: 'dg-7' }),
    /未知のノード指示/);
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-award', id: 'dg-7' }),
    /ノードが必要/);
});

test('IPC: 投函した指示の状況（送信済み → 受理済み / 失敗）を一覧に載せる', () => {
  const repo = tmpdir('deleg-board-');
  const commands = tmpdir('node-commands-');
  const cfg = { amigos: { homeDirs: [], busDirs: [] }, projects: { roots: [] },
                delegation: { flowBusDirs: [], boardRepos: [repo], nodeCommandsDir: commands } };
  const h = ipcHandlers(cfg);
  h['delegation:nodeCommand']({ action: 'board-bid', id: 'dg-p', boardRepo: repo });
  assert.strictEqual(h['delegation:list']({ only: 'board' }).commands['dg-p'].state, 'pending');

  // 常駐体が取り込むと、元ファイルが消えてレシートが残る。
  for (const n of fs.readdirSync(commands)) fs.unlinkSync(path.join(commands, n));
  writeJson(path.join(commands, 'processed', 'x.json'),
    { ok: true, action: 'board-bid', id: 'dg-p', source: 'x.json', processed_at: '2026-07-26T10:00:00Z' });
  assert.strictEqual(h['delegation:list']({ only: 'board' }).commands['dg-p'].state, 'done');

  // 取り込めなかった指示は理由つきで残る（押しても何も起きない、を作らない）。
  fs.writeFileSync(path.join(commands, 'y.json.err'), JSON.stringify(
    { error: '終端済みの公示です', failed_at: '2026-07-26T11:00:00Z',
      command: { command: 'board-bid', id: 'dg-e' } }), 'utf8');
  const e = h['delegation:list']({ only: 'board' }).commands['dg-e'];
  assert.strictEqual(e.state, 'error');
  assert.match(e.error, /終端/);
});

test('IPC: only=board は amigos ホームと flow バスを走査しない', () => {
  // タスク画面の「委任先」1 行のために毎回 amigos の探索まで引き連れない（S8-1）。
  const repo = tmpdir('deleg-board-');
  writeJson(path.join(repo, 'delegations', 'dg-o', 'post.json'),
    { id: 'dg-o', workload: 'flow', goal: 'g' });
  const cfg = { amigos: { homeDirs: ['/nope/amigos'], busDirs: [] }, projects: { roots: [] },
                delegation: { flowBusDirs: ['/nope/bus'], boardRepos: [repo] } };
  const h = ipcHandlers(cfg);
  const { items, errors } = h['delegation:list']({ only: 'board' });
  assert.deepStrictEqual(errors, [], '走査していないバスのエラーは出ない');
  assert.deepStrictEqual(items.map((i) => i.id), ['dg-o']);
});

test('IPC: 板の参加ノード一覧を返す（local は出さない）', () => {
  const repo = tmpdir('deleg-board-');
  const fresh = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  writeJson(path.join(repo, 'nodes', 'pc-a.json'), {
    node: 'pc-a', workloads: ['flow'], tags: ['python'], agent_cli: ['codex'],
    repos: [{ url: 'https://git.example.com/team/app.git', local: '/home/me/mirrors/app' }],
    max_concurrent: 2, contract_version: 1, heartbeat: fresh, fresh_after_sec: 1200,
  });
  writeJson(path.join(repo, 'nodes', 'pc-b.json'), {
    node: 'pc-b', heartbeat: '2020-01-01T00:00:00Z', fresh_after_sec: 60,
  });
  const h = ipcHandlers({ delegation: { boardRepos: [repo] } });
  const { nodes } = h['delegation:nodes']();
  assert.deepStrictEqual(nodes.map((n) => n.name), ['pc-a', 'pc-b']);
  const a = nodes[0];
  assert.deepStrictEqual(a.repos, ['app'], '手元にあるリポジトリ名までを出す');
  assert.strictEqual(a.stale, false);
  assert.ok(!JSON.stringify(a).includes('/home/me/mirrors/app'),
    '他 PC の絶対パスは読み手に意味が無いので出さない');
  assert.strictEqual(nodes[1].stale, true, '心拍が途絶えたノードは stale');
});

test('IPC: list は board リポジトリの委譲も揃えて返す', () => {
  const repo = tmpdir('deleg-board-');
  writeJson(path.join(repo, 'delegations', 'dg-l', 'post.json'),
    { id: 'dg-l', workload: 'amigos', goal: 'g', title: 'L' });
  const cfg = {
    amigos: { homeDirs: [], busDirs: [] }, projects: { roots: [] },
    delegation: { flowBusDirs: [], boardRepos: [repo] },
  };
  const h = ipcHandlers(cfg);
  const { items, errors } = h['delegation:list']();
  assert.deepStrictEqual(errors, []);
  const item = items.find((i) => i.id === 'dg-l');
  assert.ok(item, 'board の委譲がビューに含まれる');
  assert.strictEqual(item.target, 'board');
  assert.strictEqual(item.boardRepo, repo);
});

console.log(`\n${passed} tests passed`);
