'use strict';

// Amigos feature のテスト（Electron 不使用）。
// - ノード予算（node-budget 契約）の集計・超過判定・保存
// - ミッション読み取りビュー（ローカルバス / GitBus workdir の両形式・phase 近似）
// - agent-amigos（Python 実装・stub）が実際に生成したバスを読めるかのクロス検証
//   （python3 が無い環境ではクロス検証だけスキップ）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const budget = require('../src/features/amigos/main/budget');
const deliveries = require('../src/features/amigos/main/deliveries');
const missions = require('../src/features/amigos/main/missions');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function utcDay() {
  const d = new Date();
  return (
    String(d.getUTCFullYear()) +
    String(d.getUTCMonth() + 1).padStart(2, '0') +
    String(d.getUTCDate()).padStart(2, '0')
  );
}

function writeLedger(dir, day, records) {
  fs.mkdirSync(path.join(dir, 'ledger'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'ledger', `${day}.jsonl`),
    records.map((r) => JSON.stringify(r)).join('\n') + '\n'
  );
}

function cfgFor(dir, extra) {
  return { amigos: { budgetDir: dir, busDirs: [], ...(extra || {}) } };
}

// --- ノード予算 -------------------------------------------------------------

test('ノード予算: 設定なし = 0 = 無制限（hasData も false）', () => {
  const dir = tmpdir('amigos-budget-');
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.exceeded, false);
  assert.strictEqual(u.limitSeconds, 0);
  assert.strictEqual(u.hasData, false);
});

test('ノード予算: 台帳をワークロード別に集計し合計上限で超過判定する', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'routine', seconds: 60 },
    { ts: 'x', workload: 'amigos', seconds: 30 },
    { ts: 'x', workload: 'amigos', seconds: 30 },
    'broken-line-not-json' && { ts: 'x', workload: 'project', seconds: 0 },
  ]);
  fs.appendFileSync(path.join(dir, 'ledger', `${utcDay()}.jsonl`), 'broken\n');
  budget.save(cfgFor(dir), { executionMinutes: 2, period: 'day' }); // 上限 2 分 = 120 秒
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.totals.routine, 60);
  assert.strictEqual(u.totals.amigos, 60);
  assert.strictEqual(u.totalSeconds, 120);
  assert.strictEqual(u.exceeded, true); // 120 >= 120（定常業務 + amigos の合計で超過）
});

test('ノード予算: 内訳上限は合計が無制限でも効く', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, utcDay(), [{ ts: 'x', workload: 'amigos', seconds: 61 }]);
  budget.save(cfgFor(dir), { executionMinutes: 0, workloads: { amigos: 1 } });
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.limitSeconds, 0);
  assert.deepStrictEqual(u.exceededWorkloads, ['amigos']);
  assert.strictEqual(u.exceeded, true);
});

test('ノード予算: period=day は今日の台帳だけを数える', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, '19990101', [{ ts: 'x', workload: 'amigos', seconds: 999 }]);
  budget.save(cfgFor(dir), { executionMinutes: 1, period: 'day' });
  assert.strictEqual(budget.usage(cfgFor(dir)).exceeded, false);
  budget.save(cfgFor(dir), { period: 'total' });
  assert.strictEqual(budget.usage(cfgFor(dir)).exceeded, true);
});

test('ノード予算: save は部分更新で config.json（契約形式）を書く', () => {
  const dir = tmpdir('amigos-budget-');
  budget.save(cfgFor(dir), { executionMinutes: 240 });
  budget.save(cfgFor(dir), { workloads: { amigos: 60 } });
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  assert.strictEqual(raw.execution_minutes, 240); // 前回の値が保持される
  assert.strictEqual(raw.workloads.amigos, 60);
  assert.strictEqual(raw.updated_by, 'dashboard');
  assert.throws(() => budget.save(cfgFor(dir), { executionMinutes: -1 }));
  assert.throws(() => budget.save(cfgFor(dir), { period: 'week' }));
});

// --- ミッション読み取りビュー -----------------------------------------------

function makeMission(dir, mid, { phaseSetup } = {}) {
  const m = path.join(dir, 'missions', mid);
  const w = (rel, data) => {
    const p = path.join(m, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, typeof data === 'string' ? data : JSON.stringify(data));
  };
  w('mission.json', {
    id: mid, title: 'テスト', goal: 'g', owner_node: 'node-a',
    posted_at: '2026-07-17T00:00:00Z',
    budget: { execution_minutes: 1, soft_ratio: 0.9, on_exhausted: 'wrap-up' },
  });
  w('roles/architect.json', { id: 'architect', title: '設計担当', mission: '全体の方針を決める', required: true });
  w('roles/impl.json', { id: 'impl', title: '実装担当', mission: '画面を実装する', required: true });
  w('roles/integrator.json', { id: 'integrator', required: true, builtin: 'integrator' });
  if (phaseSetup !== 'open') {
    w('roster.json', {
      architect: { node: 'node-a' }, impl: { node: 'node-b' }, integrator: { node: 'node-a' },
    });
    w('status/node-a--architect.json', { node: 'node-a', role: 'architect', state: 'working', turn: 2, done_round: 0 });
    w('status/node-b--impl.json', { node: 'node-b', role: 'impl', state: 'paused', turn: 1, note: '[node-budget] 超過' });
    w('events/node-a--architect.jsonl', '{"cli_seconds": 30}\n{"cli_seconds": 30}\n');
    // 未回答質問 1 件（owner 宛は数えない）
    w('inbox/architect/01-impl.json', { id: '01', from: 'impl', to: 'architect', type: 'question' });
    w('inbox/owner/02-impl.json', { id: '02', from: 'impl', to: 'owner', type: 'question' });
  }
  if (phaseSetup === 'reviewing') {
    w('deliverable/MANIFEST.json', { round: 0, partial: true, reason: 'budget' });
  }
  return m;
}

test('ミッション: ローカルバス形式を読み phase/予算/未回答/一時停止を導出する', () => {
  const bus = tmpdir('amigos-bus-');
  makeMission(bus, 'am-1');
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [bus] }));
  assert.strictEqual(ov.missions.length, 1);
  const m = ov.missions[0];
  assert.strictEqual(m.phase, 'working');
  assert.strictEqual(m.round, 0);
  assert.strictEqual(m.budget.spentSeconds, 60);
  assert.strictEqual(m.budget.limitSeconds, 60);
  assert.strictEqual(m.budget.hard, true); // ミッション予算枯渇
  assert.strictEqual(m.unanswered, 1); // owner 宛は数えない
  assert.deepStrictEqual(m.pausedRoles, ['impl']);
  const impl = m.roles.find((r) => r.id === 'impl');
  assert.strictEqual(impl.node, 'node-b');
  assert.strictEqual(impl.state, 'paused');
  assert.strictEqual(impl.responsibility, '画面を実装する');
});

test('ミッション詳細: 会話を重複なく時系列化し、表示名・返信・要確認を導出する', () => {
  const bus = tmpdir('amigos-detail-');
  const dir = makeMission(bus, 'am-detail', { phaseSetup: 'open' });
  const writeMessage = (rel, data) => {
    const target = path.join(dir, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(data));
  };
  const question = {
    id: 'msg-1', from: 'impl', to: 'architect', type: 'question',
    subject: '表示方針の確認', body: 'カードには目的を載せますか？', created_at: '2026-07-18T01:00:00Z',
  };
  writeMessage('inbox/architect/msg-1-impl.json', question);
  writeMessage('channels/all/impl/msg-1.json', question); // 同じメッセージは1件にまとめる
  writeMessage('inbox/impl/msg-2-architect.json', {
    id: 'msg-2', from: 'architect', to: 'impl', type: 'answer', reply_to: 'msg-1',
    subject: '', body: '目的を一行で表示してください。', created_at: '2026-07-18T01:01:00Z',
  });
  writeMessage('inbox/owner/msg-3-architect.json', {
    id: 'msg-3', from: 'architect', to: 'owner', type: 'decision-request',
    subject: '確認をお願いします', body: '公開範囲を決めてください。', created_at: '2026-07-18T01:02:00Z',
  });
  writeMessage('channels/all/impl/msg-4.json', {
    id: 'msg-4', from: 'impl', to: 'all', type: 'status',
    subject: '', body: 'カード部分まで完了しました。\n次は詳細画面です。', created_at: '2026-07-18T01:03:00Z',
  });

  const detail = missions.readMissionSummary('am-detail', dir);
  assert.strictEqual(detail.messages.length, 4);
  assert.deepStrictEqual(detail.messages.map((m) => m.id), ['msg-1', 'msg-2', 'msg-3', 'msg-4']);
  assert.strictEqual(detail.messages[0].fromLabel, '実装担当');
  assert.strictEqual(detail.messages[0].toLabel, '設計担当');
  assert.strictEqual(detail.messages[0].answered, true);
  assert.strictEqual(detail.messages[1].replyTo, 'msg-1');
  assert.strictEqual(detail.messages[2].requiresAttention, true);
  assert.strictEqual(detail.messages[3].summary, 'カード部分まで完了しました。 次は詳細画面です。');
  assert.strictEqual(detail.attentionCount, 1);
});

test('ミッション: 募集中（roster 未充足）は open、MANIFEST 現行ラウンドは reviewing', () => {
  const bus = tmpdir('amigos-bus-');
  makeMission(bus, 'am-open', { phaseSetup: 'open' });
  makeMission(bus, 'am-rev', { phaseSetup: 'reviewing' });
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [bus] }));
  const byId = Object.fromEntries(ov.missions.map((m) => [m.id, m]));
  assert.strictEqual(byId['am-open'].phase, 'open');
  assert.strictEqual(byId['am-rev'].phase, 'reviewing');
  assert.strictEqual(byId['am-rev'].manifest.partial, true);
  assert.ok(!byId['am-rev'].roles.some((role) => role.builtin === 'integrator'),
    '自動統合を担当メンバーとして表示しない');
  assert.deepStrictEqual(byId['am-rev'].integration, {
    status: 'done', label: '成果物の取りまとめ完了',
  });
});

test('ミッション: GitBus workdir 形式（mission__<mid>/）も読める', () => {
  const wd = tmpdir('amigos-wd-');
  // GitBus のクローンはリポジトリ直下が内容ルート
  const inner = tmpdir('amigos-src-');
  makeMission(inner, 'am-git');
  fs.cpSync(path.join(inner, 'missions', 'am-git'), path.join(wd, 'mission__am-git'), { recursive: true });
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [wd] }));
  assert.strictEqual(ov.missions.length, 1);
  assert.strictEqual(ov.missions[0].id, 'am-git');
});

// --- クロス検証: agent-amigos（Python 実装）が作った本物のバスを読む ----------

test('クロス検証: agent-amigos stub の実バスを dashboard リーダーで読める', () => {
  const py = spawnSync('python3', ['--version'], { encoding: 'utf8' });
  if (py.status !== 0) {
    console.log('   (python3 なし — クロス検証はスキップ)');
    return;
  }
  const work = tmpdir('amigos-x-');
  const bus = path.join(work, 'bus');
  fs.writeFileSync(path.join(work, 'design.md'), '# design\n');
  fs.writeFileSync(
    path.join(work, 'roles.json'),
    JSON.stringify({
      mission: { title: 'クロス', goal: 'g', staffing_timeout: 0,
                 convergence: { done_when: 'all-required-done' } },
      roles: [
        { id: 'architect', mission: 'a', deliverables: ['arch.md'] },
        { id: 'impl', mission: 'b', deliverables: ['main.py'], collaborates_with: ['architect'] },
      ],
    })
  );
  const entry = path.join(__dirname, '..', '..', 'agent-amigos', 'agent-amigos.py');
  const env = { ...process.env, AGENT_AMIGOS_NODE: 'owner-node', AGENT_AMIGOS_STUB_COST: '0.01',
                AGENT_BUDGET_DIR: path.join(work, 'nb') };
  let r = spawnSync('python3', [entry, 'post', '--bus', bus, '--design',
    path.join(work, 'design.md'), '--roles', path.join(work, 'roles.json'),
    '--mission-id', 'am-x', '--serve', '--agent-cli', 'stub', '--cycles', '10',
    '--interval', '0'], { encoding: 'utf8', env, cwd: work });
  assert.strictEqual(r.status, 0, r.stderr);
  const ov = missions.overview(cfgFor(path.join(work, 'nb'), { busDirs: [bus] }));
  assert.strictEqual(ov.missions.length, 1);
  const m = ov.missions[0];
  assert.strictEqual(m.id, 'am-x');
  assert.strictEqual(m.phase, 'reviewing'); // stub 完走 → 受入待ち
  assert.ok(m.roles.every((r2) => r2.node), '全ロールに担当が付く');
  assert.ok(!m.roles.some((r2) => r2.builtin === 'integrator'),
    'stubの自動統合を未完了メンバーとして返さない');
  assert.deepStrictEqual(m.integration, {
    status: 'done', label: '成果物の取りまとめ完了',
  }, 'stubが生成したMANIFESTを自動統合の完了根拠にする');
  assert.ok(m.budget.spentSeconds > 0, 'events の cli_seconds が読める');
  // ノード予算の台帳（workload=amigos）も agent-amigos が記帳している
  const u = budget.usage(cfgFor(path.join(work, 'nb')));
  assert.ok((u.totals.amigos || 0) > 0, '共有台帳に amigos の記帳がある');
});

// --- ホーム（常駐デーモン）発見と commands 投函 ------------------------------

const homes = require('../src/features/amigos/main/homes');

function makeHome(root, name, configText, ext) {
  const home = path.join(root, name);
  fs.mkdirSync(path.join(home, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(home, '.agents', `agent-amigos.${ext || 'yaml'}`), configText);
  return home;
}

test('ホーム発見: projects.roots 配下の .agent/agent-amigos.* をマーカーに拾う', () => {
  const root = tmpdir('amigos-homes-');
  const h1 = makeHome(root, 'node-a', 'node_id: pc-a\nbus: .\n');
  const h2 = makeHome(root, 'sub/node-b',
    JSON.stringify({ node_id: 'pc-b', bus: 'shared', manual_claim: true }), 'json');
  fs.mkdirSync(path.join(root, 'not-a-home'), { recursive: true });
  const found = homes.discoverHomes({ projects: { roots: [root], scanDepth: 3 }, amigos: {} });
  const byNode = Object.fromEntries(found.map((h) => [h.nodeId, h]));
  assert.ok(byNode['pc-a'] && byNode['pc-b']);
  assert.strictEqual(path.resolve(byNode['pc-a'].busDir), path.resolve(h1));
  assert.strictEqual(path.resolve(byNode['pc-b'].busDir), path.resolve(h2, 'shared'));
  assert.strictEqual(byNode['pc-b'].manualClaim, true);
  assert.strictEqual(byNode['pc-a'].commandsDir,
    path.join(h1, '.agents', 'agent-amigos', 'commands'));
});

test('ホーム発見: ルート直下の agent-amigos.* と manual_claim の yes/on/boolean', () => {
  const root = tmpdir('amigos-homes-');
  const hRoot = path.join(root, 'root-cfg');
  fs.mkdirSync(hRoot, { recursive: true });
  fs.writeFileSync(path.join(hRoot, 'agent-amigos.yaml'), 'node_id: root-n\nmanual_claim: yes\n');
  const hYes = makeHome(root, 'yes-n', 'node_id: yes-n\nmanual_claim: on\n');
  const hBool = makeHome(root, 'bool-n',
    JSON.stringify({ node_id: 'bool-n', manual_claim: true }), 'json');
  const found = homes.discoverHomes({ projects: { roots: [root], scanDepth: 2 }, amigos: {} });
  const byNode = Object.fromEntries(found.map((h) => [h.nodeId, h]));
  assert.ok(byNode['root-n'] && byNode['yes-n'] && byNode['bool-n']);
  assert.strictEqual(path.resolve(byNode['root-n'].dir), path.resolve(hRoot));
  assert.strictEqual(byNode['root-n'].manualClaim, true);
  assert.strictEqual(byNode['yes-n'].manualClaim, true);
  assert.strictEqual(byNode['bool-n'].manualClaim, true);
  assert.strictEqual(byNode['yes-n'].dir, hYes);
});

test('投函: 発見済みホームの commands/ にだけ書ける（外は拒否）', () => {
  const root = tmpdir('amigos-homes-');
  const h1 = makeHome(root, 'node-a', 'node_id: pc-a\n');
  const cfg = { projects: { roots: [root] }, amigos: {} };
  const res = homes.writeCommand(cfg, h1, { command: 'claim', mission: 'am-1', role: 'impl' });
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.deepStrictEqual(rec, { command: 'claim', mission: 'am-1', role: 'impl' });
  assert.throws(() => homes.writeCommand(cfg, path.join(root, 'not-a-home'),
    { command: 'claim', mission: 'x', role: 'y' }), /ホーム/);
  assert.throws(() => homes.writeCommand(cfg, h1, { command: 'rm -rf' }), /不正なコマンド/);
});

test('投函コマンド一覧は schemas/amigos-command.schema.json（契約の正典）と一致する', () => {
  const schema = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', '..', '..', 'schemas', 'amigos-command.schema.json'), 'utf8')
  );
  assert.deepStrictEqual(
    [...homes.ALLOWED_COMMANDS].sort(),
    [...schema.properties.command.enum].sort(),
    '投函側（dashboard）と契約スキーマのコマンド一覧が一致する'
  );
});

test('ホーム発見: git+/hub+ バスはローカルミラー（bus_workdir または既定 digest）へ解決する', () => {
  const root = tmpdir('amigos-homes-');
  // bus_workdir 明示（相対パスはホーム基準）
  const h1 = makeHome(root, 'git-explicit',
    'node_id: git-a\nbus: git+ssh://git@host/team/amigos-bus.git\nbus_workdir: mirror\n');
  // bus_workdir 無し → agent-amigos（gitbus.py / hubbus.py）と同じ共通ホーム配下
  //   ~/.agents/amigos/{bus|hub}/<sha1[:8]>（旧 ~/.agent/ しか無い環境ではそちら）
  makeHome(root, 'git-default', 'node_id: git-b\nbus: git+https://host/team/bus.git\n');
  makeHome(root, 'hub-default', 'node_id: hub-a\nbus: hub+http://hub:8787\n');
  const found = homes.discoverHomes({ projects: { roots: [root], scanDepth: 2 }, amigos: {} });
  const byNode = Object.fromEntries(found.map((h) => [h.nodeId, h]));
  assert.strictEqual(path.resolve(byNode['git-a'].busDir), path.resolve(h1, 'mirror'));
  const digestOf = (url) =>
    require('crypto').createHash('sha1').update(url, 'utf8').digest('hex').slice(0, 8);
  const { agentHomeSubdir } = require('../src/base/main/agent-home');
  assert.strictEqual(byNode['git-b'].busDir,
    agentHomeSubdir('amigos', 'bus', digestOf('https://host/team/bus.git')));
  assert.strictEqual(byNode['hub-a'].busDir,
    agentHomeSubdir('amigos', 'hub', digestOf('http://hub:8787')));
});

test('overview はホームのバスを含め、ミッションへ home を対応付ける', () => {
  const root = tmpdir('amigos-homes-');
  const h1 = makeHome(root, 'node-a', 'node_id: pc-a\nbus: .\n');
  makeMission(h1, 'am-home');   // ホーム = バス（missions/<mid>/）
  const cfg = { projects: { roots: [root] },
                amigos: { budgetDir: tmpdir('amigos-b-'), busDirs: [] } };
  const homeList = homes.discoverHomes(cfg);
  const ov = missions.overview(cfg, homeList.map((h) => h.busDir));
  assert.strictEqual(ov.missions.length, 1);
  assert.strictEqual(ov.missions[0].id, 'am-home');
  const byBus = new Map(homeList.map((h) => [path.resolve(h.busDir), h.dir]));
  assert.strictEqual(byBus.get(path.resolve(ov.missions[0].busDir)), h1);
});

test('クロス検証: dashboard の投函 → Python 常駐デーモンが取り込み公示・引き受け', () => {
  const py = spawnSync('python3', ['--version'], { encoding: 'utf8' });
  if (py.status !== 0) {
    console.log('   (python3 なし — クロス検証はスキップ)');
    return;
  }
  const root = tmpdir('amigos-x-');
  const home = makeHome(root, 'node-a', JSON.stringify({ node_id: 'pc-a' }), 'json');
  const cfg = { projects: { roots: [root] }, amigos: {} };
  // dashboard からタスク依頼（post）を投函
  homes.writeCommand(cfg, home, {
    command: 'post', title: 'ダッシュボード依頼', goal: 'g', mission_id: 'am-dash',
    design: '# design\n', mission: { staffing_timeout: 0 },
    roles: [{ id: 'impl', mission: '実装', deliverables: ['main.py'] }],
  });
  // 常駐デーモン（serve --cycles）が取り込む
  const entry = path.join(__dirname, '..', '..', 'agent-amigos', 'agent-amigos.py');
  const env = { ...process.env, AGENT_AMIGOS_STUB_COST: '0.01',
                AGENT_BUDGET_DIR: path.join(root, 'nb') };
  const r = spawnSync('python3',
    [entry, 'serve', '--agent-cli', 'stub', '--cycles', '10', '--interval', '0'],
    { encoding: 'utf8', env, cwd: home });
  assert.strictEqual(r.status, 0, r.stderr);
  // dashboard 側のビューでミッションが見え、home が対応付く
  const homeList = homes.discoverHomes(cfg);
  const ov = missions.overview({ ...cfg, amigos: { budgetDir: path.join(root, 'nb'),
                                                   busDirs: [] } },
                               homeList.map((h) => h.busDir));
  const m = ov.missions.find((x) => x.id === 'am-dash');
  assert.ok(m, 'デーモンが post を取り込み公示している');
  assert.strictEqual(m.phase, 'reviewing');
  assert.ok(m.roles.every((r2) => r2.node), '自己補充で全ロールに担当が付く');
});

// --- 納品棚と受入プレビュー -------------------------------------------------

test('受入プレビュー: 文書・画像・バイナリを種別ごとに読み分ける', () => {
  const dir = path.join(tmpdir('amigos-preview-'), 'mission');
  const deliv = path.join(dir, 'deliverable', 'researcher');
  fs.mkdirSync(deliv, { recursive: true });
  fs.writeFileSync(path.join(dir, 'deliverable', 'MANIFEST.json'), '{}');
  fs.writeFileSync(path.join(deliv, 'report.md'), '# 調査結果\n\n本文');
  fs.writeFileSync(path.join(deliv, 'data.csv'), 'a,b\n1,2\n');
  // 1x1 PNG（data URI で返ることの確認用）
  fs.writeFileSync(path.join(deliv, 'chart.png'), Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
    'base64'
  ));
  fs.writeFileSync(path.join(deliv, 'model.bin'), Buffer.from([0, 1, 2, 3]));

  const preview = missions.readDeliverablePreview(dir);
  const byPath = Object.fromEntries(preview.files.map((f) => [f.path, f]));
  assert.ok(!('MANIFEST.json' in byPath), 'MANIFEST は納品書に置き換わるので出さない');
  assert.strictEqual(byPath['researcher/report.md'].kind, 'markdown');
  assert.ok(byPath['researcher/report.md'].text.includes('調査結果'));
  assert.strictEqual(byPath['researcher/report.md'].role, 'researcher');
  assert.strictEqual(byPath['researcher/data.csv'].kind, 'text');
  assert.strictEqual(byPath['researcher/chart.png'].kind, 'image');
  assert.ok(byPath['researcher/chart.png'].dataUri.startsWith('data:image/png;base64,'));
  assert.strictEqual(byPath['researcher/model.bin'].kind, 'binary');
  assert.strictEqual(byPath['researcher/model.bin'].text, undefined);
});

test('受入プレビューは受入待ちのミッションだけで読む', () => {
  const bus = tmpdir('amigos-preview-bus-');
  const dir = path.join(bus, 'missions', 'am-p');
  fs.mkdirSync(path.join(dir, 'roles'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'mission.json'),
    JSON.stringify({ id: 'am-p', title: 't', owner_node: 'n' }));
  fs.writeFileSync(path.join(dir, 'roles', 'impl.json'),
    JSON.stringify({ id: 'impl', mission: '実装', required: true }));
  fs.mkdirSync(path.join(dir, 'deliverable', 'impl'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'deliverable', 'impl', 'out.md'), '# 成果');
  // 担当が付かない = open。プレビューは読まない
  assert.strictEqual(missions.readMissionSummary('am-p', dir).deliverable, null);
  // 担当 + 現行ラウンドの MANIFEST = reviewing。プレビューを読む
  fs.writeFileSync(path.join(dir, 'roster.json'), JSON.stringify({ impl: { node: 'n' } }));
  fs.writeFileSync(path.join(dir, 'deliverable', 'MANIFEST.json'),
    JSON.stringify({ round: 0, partial: false }));
  const summary = missions.readMissionSummary('am-p', dir);
  assert.strictEqual(summary.phase, 'reviewing');
  assert.strictEqual(summary.deliverable.files.length, 1);
});

test('納品棚: 納品書を新しい順に読み、参照のみのファイルも保持する', () => {
  const home = tmpdir('amigos-shelf-');
  const write = (mid, rec) => {
    fs.mkdirSync(path.join(home, 'deliveries', mid), { recursive: true });
    fs.writeFileSync(path.join(home, 'deliveries', mid, 'delivery.json'),
      JSON.stringify(rec));
  };
  write('am-1', {
    mission: 'am-1', title: '古い納品', accepted_at: '2026-07-01T00:00:00Z',
    accepted_by: 'pc-a', acceptance: 'manual', execution_seconds: 120,
    files: [{ path: 'impl/main.py', role: 'impl', bytes: 40, exported: true }],
  });
  write('am-2', {
    mission: 'am-2', title: '新しい納品', accepted_at: '2026-07-10T00:00:00Z',
    partial: true, partial_reason: 'budget', execution_seconds: 60,
    files: [
      { path: 'r/report.md', role: 'r', bytes: 10, exported: true },
      { path: 'r/video.mp4', role: 'r', bytes: 99, exported: false, skip_reason: 'size' },
    ],
    code: { repo: 'ssh://git@x/y.git', branch: 'amigos/am-2/integration' },
  });
  fs.mkdirSync(path.join(home, 'deliveries', 'am-broken'), { recursive: true });

  const rows = deliveries.list([{ dir: home }]);
  assert.deepStrictEqual(rows.map((r) => r.mission), ['am-2', 'am-1']);
  assert.strictEqual(rows[0].partial, true);
  assert.strictEqual(rows[0].files.filter((f) => f.exported).length, 1);
  assert.strictEqual(rows[0].files.find((f) => !f.exported).skipReason, 'size');
  assert.strictEqual(rows[0].code.branch, 'amigos/am-2/integration');
  assert.strictEqual(rows[0].dir, path.join(home, 'deliveries', 'am-2'));
  assert.strictEqual(rows[1].executionSeconds, 120);
});

test('納品棚: 選択フォルダへ実体ファイルだけを相対構造を保ってコピーする', () => {
  const home = tmpdir('amigos-export-home-');
  const shelf = path.join(home, 'deliveries', 'am-export');
  fs.mkdirSync(path.join(shelf, 'research'), { recursive: true });
  fs.writeFileSync(path.join(shelf, 'research', 'report.md'), '# 結果');
  fs.writeFileSync(path.join(shelf, 'delivery.json'), JSON.stringify({
    mission: 'am-export',
    title: '調査 / レポート',
    files: [
      { path: 'research/report.md', exported: true },
      { path: 'research/movie.mp4', exported: false, skip_reason: 'size' },
    ],
  }));
  const destination = tmpdir('amigos-export-dest-');
  const first = deliveries.copyToFolder(home, 'am-export', destination);
  assert.strictEqual(first.copied, 1);
  assert.strictEqual(first.skipped, 1);
  assert.strictEqual(first.missing, 0);
  assert.ok(fs.existsSync(path.join(first.target, 'research', 'report.md')));
  assert.ok(!fs.existsSync(path.join(first.target, 'delivery.json')), '内部納品書はコピーしない');
  assert.ok(fs.existsSync(path.join(shelf, 'research', 'report.md')), '納品棚の原本を残す');
  const second = deliveries.copyToFolder(home, 'am-export', destination);
  assert.notStrictEqual(second.target, first.target, '既存の保存先を上書きしない');
  assert.ok(second.target.endsWith('-2'));
});

test('納品棚: 不正パスと納品棚配下への再帰コピーを拒否する', () => {
  const home = tmpdir('amigos-export-safe-');
  const shelf = path.join(home, 'deliveries', 'am-safe');
  fs.mkdirSync(shelf, { recursive: true });
  fs.writeFileSync(path.join(shelf, 'delivery.json'), JSON.stringify({
    mission: 'am-safe', title: '安全確認', files: [{ path: '../outside.txt', exported: true }],
  }));
  assert.throws(() => deliveries.copyToFolder(home, 'am-safe', tmpdir('amigos-export-out-')),
    /不正な成果物パス/);
  assert.throws(() => deliveries.copyToFolder(home, 'am-safe', shelf), /配下にはコピーできません/);
  const outside = tmpdir('amigos-export-link-');
  fs.writeFileSync(path.join(outside, 'secret.txt'), 'outside');
  fs.symlinkSync(outside, path.join(shelf, 'linked'), 'dir');
  fs.writeFileSync(path.join(shelf, 'delivery.json'), JSON.stringify({
    mission: 'am-safe', title: '安全確認', files: [{ path: 'linked/secret.txt', exported: true }],
  }));
  assert.throws(() => deliveries.copyToFolder(home, 'am-safe', tmpdir('amigos-export-link-dest-')),
    /納品棚の外を指す成果物/, '中間シンボリックリンクで外部ファイルを読まない');
});

test('クロス検証: accept 投函 → デーモンが納品棚へ搬出し dashboard が読む', () => {
  const py = spawnSync('python3', ['--version'], { encoding: 'utf8' });
  if (py.status !== 0) {
    console.log('   (python3 なし — クロス検証はスキップ)');
    return;
  }
  const root = tmpdir('amigos-deliv-');
  const home = makeHome(root, 'node-d', JSON.stringify({ node_id: 'pc-d' }), 'json');
  const cfg = { projects: { roots: [root] }, amigos: {} };
  const entry = path.join(__dirname, '..', '..', 'agent-amigos', 'agent-amigos.py');
  const env = { ...process.env, AGENT_AMIGOS_STUB_COST: '0.01',
                AGENT_BUDGET_DIR: path.join(root, 'nb') };
  const serve = (cycles) => {
    const r = spawnSync('python3',
      [entry, 'serve', '--agent-cli', 'stub', '--cycles', String(cycles), '--interval', '0'],
      { encoding: 'utf8', env, cwd: home });
    assert.strictEqual(r.status, 0, r.stderr);
  };
  homes.writeCommand(cfg, home, {
    command: 'post', title: '納品テスト', goal: '成果物を納品する', mission_id: 'am-deliv',
    design: '# design\n', mission: { staffing_timeout: 0 },
    roles: [{ id: 'impl', mission: '実装', deliverables: ['main.py'] }],
  });
  serve(10);

  // dashboard の受入プレビューが成果物を読める
  const homeList = homes.discoverHomes(cfg);
  const ov = missions.overview(cfg, homeList.map((h) => h.busDir));
  const m = ov.missions.find((x) => x.id === 'am-deliv');
  assert.strictEqual(m.phase, 'reviewing');
  assert.ok(m.deliverable.files.length > 0, '受入待ちでは成果物のプレビューが付く');

  // 受入は commands 投函（dashboard はバスへ書かない）→ デーモンが納品棚へ搬出
  homes.writeCommand(cfg, home, { command: 'accept', mission: 'am-deliv' });
  serve(2);
  const rows = deliveries.list(homeList);
  const rec = rows.find((r) => r.mission === 'am-deliv');
  assert.ok(rec, 'accept で納品棚に納品書ができる');
  assert.strictEqual(rec.title, '納品テスト');
  assert.ok(rec.files.some((f) => f.path.startsWith('impl/') && f.exported));
  assert.ok(fs.existsSync(path.join(rec.dir, rec.files[0].path)), '成果物の本体が納品棚にある');
  assert.ok(fs.existsSync(path.join(home, 'DELIVERY.md')), '受領一覧に追記される');
});

test('未取り込みの指示は pendingCommands として数える（常駐停止に気づける）', () => {
  const root = tmpdir('amigos-pending-');
  const home = makeHome(root, 'node-p', JSON.stringify({ node_id: 'pc-p' }), 'json');
  const cfg = { projects: { roots: [root] }, amigos: {} };
  assert.strictEqual(homes.discoverHomes(cfg)[0].pendingCommands, 0);
  homes.writeCommand(cfg, home, { command: 'accept', mission: 'am-x' });
  assert.strictEqual(homes.discoverHomes(cfg)[0].pendingCommands, 1);
});

// renderer の純関数を切り出して評価する（Electron 不使用）。
function rendererFns(names) {
  const src = require('./helpers/renderer-src').read();
  const vm = require('vm');
  const ctx = vm.createContext({});
  const code = names.map((n) => {
    const i = src.indexOf(`function ${n}(`);
    assert.ok(i >= 0, `renderer に ${n} がありません`);
    return src.slice(i, src.indexOf('\n}\n', i) + 3);
  }).join('\n');
  vm.runInContext(code, ctx);
  return ctx;
}

test('納品はミッションへ結び付き、bus から消えたものだけ別枠になる', () => {
  const root = tmpdir('amigos-attach-');
  const home = makeHome(root, 'node-a', JSON.stringify({ node_id: 'pc-a' }), 'json');
  const cfg = { projects: { roots: [root] }, amigos: {} };
  const bus = path.join(home, 'missions');
  // 生きているミッション（バス上にある）
  const dir = path.join(bus, 'am-live');
  fs.mkdirSync(path.join(dir, 'roles'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'mission.json'),
    JSON.stringify({ id: 'am-live', title: '生存', owner_node: 'pc-a' }));
  fs.writeFileSync(path.join(dir, 'roles', 'impl.json'), JSON.stringify({ id: 'impl' }));
  const shelf = (mid) => {
    const d = path.join(home, 'deliveries', mid);
    fs.mkdirSync(d, { recursive: true });
    fs.writeFileSync(path.join(d, 'delivery.json'), JSON.stringify({
      mission: mid, title: mid, accepted_at: '2026-07-19T00:00:00Z',
      files: [{ path: 'impl/out.md', role: 'impl', bytes: 5, exported: true }],
    }));
    fs.mkdirSync(path.join(d, 'impl'), { recursive: true });
    fs.writeFileSync(path.join(d, 'impl', 'out.md'), '# 成果\n本文');
  };
  shelf('am-live');
  shelf('am-gone');   // バスには無い（gc 済み）

  const handlers = {};
  require('../src/features/amigos/index.js').registerIpc({
    handle: (ch, fn) => { handlers[ch] = fn; }, loadConfig: () => cfg, saveConfig: () => cfg,
  });
  const ov = handlers['amigos:overview']();
  const live = ov.missions.find((m) => m.id === 'am-live');
  assert.ok(live.delivery, '生きているミッションには納品が結び付く');
  assert.strictEqual(ov.orphanDeliveries.length, 1);
  assert.strictEqual(ov.orphanDeliveries[0].mission, 'am-gone');

  // 中身は詳細を開いたときだけ読む（一覧には載せない）
  assert.strictEqual(live.delivery.files[0].text, undefined, '一覧では中身を運ばない');
  const got = handlers['amigos:deliveryContents']({ home, mission: 'am-live' });
  const md = got.files.find((f) => f.path === 'impl/out.md');
  assert.strictEqual(md.kind, 'markdown');
  assert.ok(md.text.includes('成果'), '受け取り済み成果物の中身が読める');
  assert.throws(() => handlers['amigos:deliveryContents']({ home: '/tmp/other', mission: 'am-live' }),
    /ホームではありません/, '発見済みホーム以外は読まない');
});

test('画面スコープ: 選択中プロジェクトのホームの納品だけを出す', () => {
  const ctx = rendererFns(['coworkPathKey', 'amigosForProject']);
  const home = '/Users/x/team';
  const ov = {
    homes: [{ dir: home, configFile: `${home}/.agent/agent-amigos.json` }],
    missions: [{ id: 'am-x', home }],
    deliveries: [{ mission: 'am-x', home }],
    orphanDeliveries: [{ mission: 'am-old', home, title: '過去' }],
  };
  const scoped = ctx.amigosForProject(ov, home);
  assert.strictEqual(scoped.orphanMissions.length, 1, '整理済みもミッションの器で開ける');
  assert.strictEqual(scoped.orphanMissions[0].archived, true);
  assert.strictEqual(scoped.orphanMissions[0].delivery.mission, 'am-old');
  assert.strictEqual(ctx.amigosForProject(ov, '/Users/x/other').orphanMissions.length, 0);
  const inScope = ctx.amigosForProject(ov, home);
  assert.strictEqual(inScope.deliveries.length, 1, 'ホーム選択時は納品が見える');
  assert.strictEqual(ctx.amigosForProject(ov, `${home}/`).deliveries.length, 1,
    '末尾スラッシュでも一致する');
  assert.strictEqual(ctx.amigosForProject(ov, '/Users/x/other').deliveries.length, 0,
    '別プロジェクト選択時は出さない');
  const none = ctx.amigosForProject(ov, '');
  assert.strictEqual(none.deliveries.length, 0,
    '未選択時に納品だけ素通りさせない（missions と同じ扱い）');
  assert.strictEqual(none.missions.length, 0);
});

test('修正依頼は window.prompt を使わない（Electron では動かない）', () => {
  const src = require('./helpers/renderer-src').read();
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.ok(!/\bwindow\.prompt\s*\(/.test(src), 'window.prompt は Electron で未対応');
  assert.ok(!/[^.\w]prompt\s*\(/.test(src.replace(/sendPrompt\(/g, '')),
    '素の prompt() も使わない');
  assert.ok(html.includes('id="dlg-amigos-reject"'), '修正依頼はダイアログで受ける');
  assert.ok(html.includes('id="amigos-reject-feedback"'));
  assert.ok(src.includes('function openAmigosRejectDialog('));
});

test('受け取った成果物はミッションの中で見せる（別枠の一覧にしない）', () => {
  const src = require('./helpers/renderer-src').read();
  assert.ok(src.includes('function amigosReceivedSectionHtml('),
    'ミッション詳細に「受け取った成果物」節を出す');
  const detail = src.slice(src.indexOf('function amigosMissionDetailHtml('),
                           src.indexOf('function setupAmigosOpenButtons('));
  assert.ok(detail.includes('amigosReceivedSectionHtml(m)'),
    '詳細ダイアログから成果物を開ける');
  assert.ok(!src.includes('function amigosDeliveryCardHtml('),
    'ミッションから切り離した納品一覧カードは残さない');
  assert.ok(src.includes('function loadAmigosReceived('),
    '中身は詳細を開いたときに読む');
  assert.match(src, /<details(?:\s+open)?\s+class="[^"]*amigos-received-section/,
    '受け取った成果物は折りたためる');
  assert.doesNotMatch(src, /<details\s+open\s+class="[^"]*amigos-received-section/,
    '受け取った成果物は既定で閉じる');
  assert.ok(src.includes("addEventListener('toggle'"),
    '初回展開時に内容を遅延取得する');
  const openDetail = src.slice(src.indexOf('function openAmigosDetail('),
    src.indexOf('const AMIGOS_ROLES_SAMPLE'));
  assert.ok(!openDetail.includes('loadAmigosReceived(mission)'),
    '詳細を開いただけでは受領成果物の本文を取得しない');
  const shell = src.slice(src.indexOf('const archivedHtml ='), src.indexOf('const refreshBtn'));
  assert.ok(shell.includes('過去の成果物'), '経過が整理済みのものだけ別節にする');
  assert.ok(shell.includes('保存先'), 'ミッション一覧から保存先も案内する');
});

test('ミッションUIは密度を整え、検収と同じ二ペイン成果物ビューを使う', () => {
  const src = require('./helpers/renderer-src').read();
  const css = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');
  assert.ok(src.includes('<button id="btn-amigos-request">ミッションを依頼</button>'));
  assert.ok(!src.includes('ミッションを依頼…'));
  assert.ok(src.includes('class="amigos-card-footer"'), 'カード下段にメタ情報と操作をまとめる');
  assert.ok(src.includes('function amigosArtifactWorkspaceHtml('));
  assert.ok(src.includes('class="amigos-artifact-files"'));
  assert.ok(src.includes('class="amigos-artifact-preview"'));
  assert.ok(src.includes('aria-current='), 'ファイル選択状態を支援技術へ伝える');
  assert.ok(src.includes('data-amigos-export='), '別フォルダへの保存操作を表示する');
  assert.ok(src.includes('function amigosIntegrationHtml('),
    '自動統合は担当者ではなくシステム工程として表示する');
  assert.match(css, /\.amigos-artifact-workspace\s*\{[\s\S]*grid-template-columns/);
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*\.amigos-artifact-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
});

console.log(`\n${passed} amigos tests passed`);
