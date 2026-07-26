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
const nodeCommands = require('../src/features/delegation/main/node-commands');
const { engineConfigWithBoard } = require('./helpers/engine-status');

// 板の所在（host.yaml の board）。実行エンジンが status.json に宣言する値で、
// 指示ドロップの `board:` に載るのもこれ（作業フォルダではない）。
const BOARD_LOCATION = 'git+ssh://example/board.git';

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

// --- 指示の置き場（P0-2） ----------------------------------------------------
//
// 投函先は「実行エンジンが読む場所」でなければ意味がない。正典構成（Windows の画面 +
// WSL の実行エンジン）で os.homedir() 基準に落ちると、投函先と取り込み先が別ファイル
// システムになり、押しても何も起きない（.err すら出ない）。

test('指示の置き場は実行エンジンのホーム配下（画面の PC のホームではない）', () => {
  const cfg = engineConfigWithBoard(tmpdir('deleg-board-'));
  assert.strictEqual(
    nodeCommands.resolveCommandsDir(cfg),
    path.join(cfg.engine.home, 'commands')
  );
  assert.ok(!nodeCommands.resolveCommandsDir(cfg).startsWith(path.join(os.homedir(), '.agents')),
    '画面の PC のホームへ落ちている');
});

test('指示の置き場: 明示指定が最優先・~ は画面の PC のホームへ展開する', () => {
  const dir = tmpdir('node-commands-');
  const cfg = engineConfigWithBoard(tmpdir('deleg-board-'), {
    extra: { delegation: { nodeCommandsDir: dir } },
  });
  assert.strictEqual(nodeCommands.resolveCommandsDir(cfg), dir);
  cfg.delegation.nodeCommandsDir = '~/cmds';
  assert.strictEqual(nodeCommands.resolveCommandsDir(cfg), path.join(os.homedir(), 'cmds'));
  cfg.delegation.nodeCommandsDir = '~notauser/cmds';   // `~name` は展開しない
  assert.strictEqual(nodeCommands.resolveCommandsDir(cfg), '~notauser/cmds');
});

test('指示の置き場: 旧ホーム（~/.agent）へは落とさない', () => {
  // 実行エンジン側に旧ホームのフォールバックが無い。落とすと「書けるのに誰も読まない
  // 場所」が増えるだけになる。旧ホームしか無い環境は nodeCommandsDir で明示する。
  const home = tmpdir('legacy-home-');
  fs.mkdirSync(path.join(home, '.agent', 'commands'), { recursive: true });
  const cfg = { engine: { home: path.join(home, '.agents'), distro: '' } };
  assert.strictEqual(nodeCommands.resolveCommandsDir(cfg),
    path.join(home, '.agents', 'commands'));
});

test('往復: 投函 → 受理レシートで「送信済み → 受理済み」になる', () => {
  const dir = tmpdir('node-commands-');
  const cfg = { delegation: { nodeCommandsDir: dir } };
  const { file } = nodeCommands.dropNodeCommand(cfg, { action: 'board-bid', id: 'dg-r' });
  assert.strictEqual(nodeCommands.nodeCommandStatus(cfg)['dg-r'].state, 'pending');
  // 常駐体の取り込み: 元ファイルを消し processed/<同じ名前>.json を残す
  fs.unlinkSync(file);
  writeJson(path.join(dir, 'processed', path.basename(file)),
    { ok: true, action: 'board-bid', id: 'dg-r', source: path.basename(file),
      processed_at: '2026-07-26T10:00:00Z' });
  assert.strictEqual(nodeCommands.nodeCommandStatus(cfg)['dg-r'].state, 'done');
});

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
  const h = ipcHandlers(engineConfigWithBoard(repo, {
    extra: { delegation: { boardRepos: [repo], nodeCommandsDir: commands } },
  }));

  const aw = h['delegation:award']({ target: 'board', boardRepo: repo, id: 'dg-9', node: 'pc-a' });
  const awRec = JSON.parse(fs.readFileSync(aw.file, 'utf8'));
  assert.strictEqual(awRec.command, 'board-award');
  assert.strictEqual(awRec.id, 'dg-9');
  assert.strictEqual(awRec.node, 'pc-a');
  // **作業フォルダではなく板の所在**を載せる。常駐体は host.yaml の board と完全一致で
  // 照合するので、フォルダを載せると正典構成では必ず .err へ落ちる（P0-2）。
  assert.strictEqual(awRec.board, BOARD_LOCATION);
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
  const h = ipcHandlers(engineConfigWithBoard(repo, {
    extra: { delegation: { boardRepos: [repo], nodeCommandsDir: commands } },
  }));
  const res = h['delegation:nodeCommand']({
    action: 'board-bid', id: 'dg-7', boardRepo: repo, reason: '手動で引き受け',
  });
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.command, 'board-bid');
  assert.strictEqual(rec.id, 'dg-7');
  assert.strictEqual(rec.board, BOARD_LOCATION);
  assert.ok(!fs.existsSync(path.join(repo, 'delegations', 'dg-7', 'bids')), '板へ bid を書かない');
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-bid', id: '../etc' }),
    /不正な id/);
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-explode', id: 'dg-7' }),
    /未知のノード指示/);
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-award', id: 'dg-7' }),
    /ノードが必要/);
});

// P0-2: 置き場を直すだけでは足りない。`board:` に作業フォルダを載せると、常駐体の
// 「宣言と違う板は取り込まない」検査（resident_cli の完全一致）に必ず引っかかり、
// 正典構成（Windows の画面 + WSL の実行エンジン）では全指示が .err へ落ちていた。
test('IPC: 実行エンジンが参加していない板への指示は投函せず、その場で断る', () => {
  const mine = tmpdir('deleg-board-mine-');
  const other = tmpdir('deleg-board-other-');
  const commands = tmpdir('node-commands-');
  const h = ipcHandlers(engineConfigWithBoard(mine, {
    extra: { delegation: { boardRepos: [mine, other], nodeCommandsDir: commands } },
  }));
  assert.throws(
    () => h['delegation:nodeCommand']({ action: 'board-bid', id: 'dg-1', boardRepo: other }),
    /参加していません/
  );
  // 押しても届かない指示をファイルとして残さない（.err で後から失敗を知るより短い）
  assert.deepStrictEqual(fs.readdirSync(commands).filter((n) => n.endsWith('.json')), []);
});

test('IPC: 板の作業フォルダを宣言していない古い実行エンジンには board を省略して渡す', () => {
  // 契約上「省略時は host.yaml の board」＝この端末の板。宣言できない相手に
  // 必ず弾かれる値を送るより、省略して常駐体の宣言に委ねる。
  const repo = tmpdir('deleg-board-');
  const commands = tmpdir('node-commands-');
  const cfg = engineConfigWithBoard(repo, {
    extra: { delegation: { boardRepos: [repo], nodeCommandsDir: commands } },
  });
  const statusFile = path.join(cfg.engine.home, 'engine', 'status.json');
  const data = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
  delete data.board.workdir;
  fs.writeFileSync(statusFile, JSON.stringify(data), 'utf8');
  const h = ipcHandlers(cfg);
  const res = h['delegation:nodeCommand']({ action: 'board-bid', id: 'dg-8', boardRepo: repo });
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.ok(!('board' in rec), `board を載せないはず: ${JSON.stringify(rec)}`);
});

test('IPC: 板に参加していない端末では、押した時点で理由を返す', () => {
  const commands = tmpdir('node-commands-');
  const h = ipcHandlers({ delegation: { boardRepos: [], nodeCommandsDir: commands } });
  assert.throws(() => h['delegation:nodeCommand']({ action: 'board-bid', id: 'dg-1' }),
    /参加していません/);
});

test('IPC: 投函した指示の状況（送信済み → 受理済み / 失敗）を一覧に載せる', () => {
  const repo = tmpdir('deleg-board-');
  const commands = tmpdir('node-commands-');
  const cfg = { ...engineConfigWithBoard(repo), amigos: { homeDirs: [], busDirs: [] },
                projects: { roots: [] },
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
