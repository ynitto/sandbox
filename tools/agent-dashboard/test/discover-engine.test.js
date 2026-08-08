'use strict';

// プロジェクト発見は実行エンジンの状況ファイル（engine/status.json の children）が唯一の入口
// （実装計画 W2-4）。フォルダ列挙設定・親フォルダのスキャン・稼働レコードからの自動追加は廃止。
// 追加依存なしで `node test/discover-engine.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const project = require('../src/main/project');
const engine = require('../src/features/agent-project/main/engine');

// 定常業務の走査ルートには**常にユーザーホーム**が入る（`~/.kiro/kiro-loop.yml` を拾うため）。
// 一覧を数える検査では実機のホームを混ぜない——結果が実行環境の持ち物で変わってしまう。
const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'home-stub-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-engine-'));
}

function mkdirp(...parts) {
  const dir = path.join(...parts);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// <home>/.agents/engine/status.json を書き、それを指す設定を返す
function writeStatus(home, status) {
  const agents = mkdirp(home, '.agents');
  mkdirp(agents, 'engine');
  fs.writeFileSync(
    path.join(agents, 'engine', 'status.json'),
    JSON.stringify({
      node: 'pc-a',
      contract_version: engine.EXPECTED_CONTRACT_VERSION,
      heartbeat: new Date().toISOString(),
      ...status,
    }),
    'utf8'
  );
  return { engine: { home: agents, distro: '' }, projects: {} };
}

(async () => {
  await test('discover は engine/status.json の children を 1 プロジェクトずつ返す', async () => {
    const root = mkRoot();
    const alpha = mkdirp(root, 'alpha');
    fs.writeFileSync(path.join(alpha, 'charter.md'), '# Charter: 見やすい名前\n', 'utf8');
    const beta = mkdirp(root, 'beta');
    mkdirp(beta, 'backlog');
    const cfg = writeStatus(mkRoot(), {
      children: [
        { name: 'alpha', alive: true, quarantined: false, deaths: 0, root: alpha },
        { name: 'beta', alive: true, quarantined: false, deaths: 0, root: beta },
      ],
    });
    // ユーザーホームは登録簿に関係なく常に並ぶ（定常業務の置き場）。ここで見たいのは
    // engine/status.json の children なので、その由来だけを取り出して比べる。
    const { projects } = project.discover(cfg);
    const fromEngine = projects.filter((p) => p.source === 'engine');
    assert.deepStrictEqual(fromEngine.map((p) => p.dir).sort(), [alpha, beta].sort());
    assert.ok(fromEngine.every((p) => p.source === 'engine'));
    assert.strictEqual(projects.find((p) => p.dir === alpha).charterName, '見やすい名前');
    assert.strictEqual(projects.find((p) => p.dir === beta).charterName, '');
  });

  await test('discover は開発フォルダと配下の .agent-project を 1 件へ統合する', async () => {
    const root = mkRoot();
    const workspace = mkdirp(root, 'alpha');
    const stateRoot = mkdirp(workspace, '.agent-project');
    mkdirp(stateRoot, 'backlog');
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'alpha', alive: true, root: workspace }],
    });
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects.filter((p) => p.source === 'engine').length, 1);
    assert.strictEqual(projects[0].name, 'alpha');    // .agent-project を表示名にしない
    assert.strictEqual(projects[0].dir, workspace);
    assert.strictEqual(projects[0].root, stateRoot);
    assert.strictEqual(projects[0].isProject, true);
  });

  await test('discover は root の無い children を落とす（発見できないものを幽霊で出さない）', async () => {
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'no-root', alive: true }],
    });
    assert.deepStrictEqual(project.discover(cfg).projects.filter((p) => p.source === 'engine'), []);
  });

  await test('discover は切り離されたプロジェクトに quarantined を立てる', async () => {
    const root = mkRoot();
    const held = mkdirp(root, 'held');
    mkdirp(held, 'backlog');
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'held', alive: false, quarantined: true, deaths: 6, root: held }],
    });
    const projects = project.discover(cfg).projects.filter((p) => p.source === 'engine');
    assert.strictEqual(projects[0].quarantined, true);
  });

  await test('計画停止（稼働時間外）は切り離しと別の状態として出す', async () => {
    // 切り離し＝人が原因を直すまで戻らない / 計画停止＝時間が来れば自動で戻る。
    // 同じ「停止中」に見せると、直す必要が無いものを人が直しに行く。
    const root = mkRoot();
    const night = mkdirp(root, 'night');
    mkdirp(night, 'backlog');
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'night', alive: false, quarantined: false, paused: true, root: night }],
    });
    const projects = project.discover(cfg).projects.filter((p) => p.source === 'engine');
    assert.strictEqual(projects[0].offHours, true);
    assert.strictEqual(projects[0].quarantined, false);
    const s = engine.summarize(engine.readStatus(cfg));
    assert.strictEqual(s.level, 'ok');            // 異常ではない（色を付けない）
    assert.match(s.summary, /稼働時間外/);
  });

  await test('状況ファイルが無ければプロジェクトは 0 件（案内表示へ倒す）', async () => {
    const cfg = { engine: { home: path.join(mkRoot(), '.agents') }, projects: {} };
    const res = project.discover(cfg);
    assert.deepStrictEqual(res.projects.filter((p) => p.source === 'engine'), []);
    assert.strictEqual(res.engine.exists, false);
    assert.strictEqual(res.engine.running, null);
  });

  await test('心拍が古ければ running:false（停止中か不明として案内する）', async () => {
    const cfg = writeStatus(mkRoot(), {
      heartbeat: new Date(Date.now() - (engine.FRESH_SEC + 60) * 1000).toISOString(),
      children: [],
    });
    const status = engine.readStatus(cfg);
    assert.strictEqual(status.exists, true);
    assert.strictEqual(status.running, false);
    assert.strictEqual(engine.summarize(status).level, 'error');
  });

  await test('discover は実行側の POSIX パスを win32 で UNC へ寄せる（幽霊 C:\\home\\... にしない）', async () => {
    // 実行側（WSL）が書く root は /home/... の POSIX パス。これを path.resolve すると
    // C:\home\... の幽霊になり exists:false でサイドバーから消える。
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'wsl-proj', alive: true, root: '/home/dev/kiro-proj' }],
    });
    cfg.engine.distro = 'Ubuntu';
    const origPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
    Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
    try {
      const dirs = project.discover(cfg).projects.filter((p) => p.source === 'engine').map((p) => p.dir);
      assert.deepStrictEqual(dirs, ['\\\\wsl.localhost\\Ubuntu\\home\\dev\\kiro-proj']);
    } finally {
      if (origPlatform) Object.defineProperty(process, 'platform', origPlatform);
    }
  });

  await test('summarize は共有の遅れと切り離しを人の言葉で返す', async () => {
    const cfg = writeStatus(mkRoot(), {
      children: [{ name: 'held', alive: false, quarantined: true, root: '/x' }],
      sync_health: [{ name: 'state', ahead: 0, behind: 3, last_error: null }],
    });
    const behind = engine.summarize(engine.readStatus(cfg));
    assert.strictEqual(behind.level, 'warn');
    assert.match(behind.summary, /未取得 3 件/);

    const cfg2 = writeStatus(mkRoot(), {
      children: [],
      sync_health: [{ name: 'state', ahead: 0, behind: 0, last_error: 'connection refused' }],
    });
    const failing = engine.summarize(engine.readStatus(cfg2));
    assert.strictEqual(failing.level, 'error');
    // 内部名（node / sync / resident）を利用者向けの文へ出さない（R10）
    assert.ok(!/node|sync|resident/i.test(failing.summary), failing.summary);
  });

  await test('契約バージョンが合わない実行エンジンは「更新が必要」と表示する', async () => {
    // 黙って一部の情報を欠いたまま「正常」に見せると、更新し忘れた PC が気づかれない。
    const cfg = writeStatus(mkRoot(), { contract_version: 0, children: [] });
    const s = engine.summarize(engine.readStatus(cfg));
    assert.strictEqual(s.level, 'warn');
    assert.match(s.summary, /更新が必要/);
  });

  await test('「今すぐ同期」は commands/heal を投函するだけ（git は叩かない）', async () => {
    // 自動回復が既定で、これはその前倒し。実際の取り込み・送信は常駐体が行う（W2-5）。
    const actions = require('../src/main/actions');
    const dir = mkRoot();
    const { file, rec } = actions.dropCommand(dir, { action: 'heal', reason: '画面から' });
    assert.ok(file.startsWith(path.join(dir, 'commands')), file);
    assert.strictEqual(rec.command, 'heal');
    assert.strictEqual(rec.id, undefined);          // プロジェクト単位（id を載せない）
    assert.strictEqual(rec.actor, 'agent-dashboard');
    assert.ok(fs.existsSync(file));
    assert.ok(!fs.existsSync(`${file}.tmp`), '書きかけを残さない（tmp → rename）');
  });

  console.log(`\n${passed} tests passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
