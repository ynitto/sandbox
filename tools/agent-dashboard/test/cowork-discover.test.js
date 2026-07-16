'use strict';

// Cowork 自動発見（.kiro/kiro-loop・.statemachine のジョブ抽出）と、保存時の実体ファイル
// 外科的書き戻しのテスト。追加依存なしで `node test/cowork-discover.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const discover = require('../src/features/cowork/main/discover');
const wb = require('../src/features/cowork/main/writeback');
const cowork = require('../src/features/cowork/main/cowork');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'cwd-'));
}

const SAMPLE_YAML = `# ヘッダコメント
max_concurrent: 3

prompts:
  # エントリ前コメント
  - name: "MR コメント返答"
    prompt: |
      手順:
      1. python scripts/gl.py list-mrs
      2. resolve: これは key: value に見えるが本文
    interval_minutes: 60   # 1 時間ごと
    enabled: true

  - name: issue-worker
    prompt: |
      作業する
    cron: "0 9 * * 1-5"
    enabled: false

  - name: no-sched
    prompt: hi
`;

function writeKiro(dir, text, ext) {
  fs.mkdirSync(path.join(dir, '.kiro'), { recursive: true });
  fs.writeFileSync(path.join(dir, '.kiro', `kiro-loop.${ext || 'yml'}`), text);
}

function writeSm(dir, name, text) {
  const d = path.join(dir, '.statemachine', name);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'workflow.yaml'), text);
}

// --- パーサ ---
test('parseKiroLoopPrompts はブロックスカラ本文を field として拾わない', () => {
  const e = discover.parseKiroLoopPrompts(SAMPLE_YAML);
  assert.strictEqual(e.length, 3);
  assert.deepStrictEqual(e[0], { name: 'MR コメント返答', interval_minutes: 60, enabled: true });
  assert.deepStrictEqual(e[1], { name: 'issue-worker', cron: '0 9 * * 1-5', enabled: false });
  assert.deepStrictEqual(e[2], { name: 'no-sched' });   // schedule/enabled 無し
});

test('parseKiroLoopPrompts はインラインコメントを除去し引用名を保つ', () => {
  const e = discover.parseKiroLoopPrompts('prompts:\n  - name: "a # b"\n    interval_minutes: 5  # every 5\n');
  assert.strictEqual(e[0].name, 'a # b');            // 引用内の # は残す
  assert.strictEqual(e[0].interval_minutes, 5);      // 末尾コメントは除去
});

test('parseKiroLoopPrompts は引用値の後ろのコメントを剥がす', () => {
  const e = discover.parseKiroLoopPrompts('prompts:\n  - name: n\n    cron: "0 9 * * 1-5"      # 平日9:00\n');
  assert.strictEqual(e[0].cron, '0 9 * * 1-5');      // 閉じ引用符の後ろのコメントは無視
});

test('parseKiroLoopPrompts は実サンプル kiro-loop.yaml.example を壊さず読む', () => {
  const sample = fs.readFileSync(path.join(__dirname, '..', '..', 'kiro-loop', 'kiro-loop.yaml.example'), 'utf8');
  const e = discover.parseKiroLoopPrompts(sample);
  assert.ok(e.length >= 5);                          // 複数の prompts エントリ
  assert.ok(e.every((x) => x.name && !/#/.test(x.cron || '')));  // コメント混入なし
});

// --- 発見 ---
test('discoverCoworkItems は prompts を per-job の loop 項目にする', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projA');
  writeKiro(proj, SAMPLE_YAML);
  const items = discover.discoverCoworkItems({ projects: { roots: [root] }, cowork: {} });
  const loops = items.filter((i) => i.type === 'loop');
  assert.strictEqual(loops.length, 3);
  assert.strictEqual(loops[0].schedule, '60m');
  assert.strictEqual(loops[0]._src.scheduleKey, 'interval_minutes');
  assert.strictEqual(loops[1].schedule, '0 9 * * 1-5');
  assert.strictEqual(loops[1]._src.scheduleKey, 'cron');
  assert.strictEqual(loops[1].enabled, false);
  assert.strictEqual(loops[2].schedule, '');
  assert.strictEqual(loops[2]._src.scheduleKey, '');
  assert.ok(loops.every((l) => l.source === 'discovered' && l._src.promptIndex >= 0));
});

test('discoverCoworkItems は .statemachine/<name>/workflow.yaml を per-folder で出す', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projB');
  writeSm(proj, 'release', 'name: "リリース"\ndescription: "デプロイ"\nstates:\n  s:\n    description: "内側"\n');
  writeSm(proj, 'triage', 'states:\n  s: {}\n');   // name 無し → フォルダ名
  const items = discover.discoverCoworkItems({ projects: { roots: [root] }, cowork: {} });
  const sms = items.filter((i) => i.type === 'state-machine');
  assert.strictEqual(sms.length, 2);
  const rel = sms.find((s) => s.workflow === 'release');
  assert.strictEqual(rel.name, 'リリース');
  assert.strictEqual(rel.description, 'デプロイ');
  const tri = sms.find((s) => s.workflow === 'triage');
  assert.strictEqual(tri.name, 'triage');            // 先頭 name: 無し → ディレクトリ名
});

test('discoverCoworkItems は .json 形式も同じ項目にする', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projJ');
  writeKiro(proj, JSON.stringify({ prompts: [
    { name: 'j1', interval_minutes: 30, enabled: true },
    { name: 'j2', cron: '* * * * *', enabled: false },
  ] }), 'json');
  const loops = discover.discoverCoworkItems({ projects: { roots: [root] }, cowork: {} }).filter((i) => i.type === 'loop');
  assert.strictEqual(loops.length, 2);
  assert.strictEqual(loops[0].schedule, '30m');
  assert.strictEqual(loops[1].schedule, '* * * * *');
  assert.strictEqual(loops[1].enabled, false);
});

test('scanForCoworkConfigs は深さ上限を守り workspace 配下へ潜らない・node_modules を飛ばす', () => {
  const root = mkRoot();
  const deep = path.join(root, 'a', 'b', 'proj');    // 深さ 3
  writeKiro(deep, SAMPLE_YAML);
  writeKiro(path.join(root, 'nm-holder', 'node_modules', 'x'), SAMPLE_YAML);
  assert.strictEqual(discover.scanForCoworkConfigs(root, 2).length, 0);      // 既定 2 では届かない
  assert.strictEqual(discover.scanForCoworkConfigs(root, 3).length, 1);      // 3 で発見
  // workspace 内部に別マーカーがあっても 1 件のまま
  writeKiro(path.join(deep, 'inner'), SAMPLE_YAML);
  assert.strictEqual(discover.scanForCoworkConfigs(root, 5).length, 1);
});

test('discover は id が安定し、手動登録と重複する発見項目は config 勝ちで排除される', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projC');
  writeKiro(proj, SAMPLE_YAML);
  const cfg = { projects: { roots: [root] }, cowork: {} };
  const a = discover.discoverCoworkItems(cfg).map((x) => x.id);
  const b = discover.discoverCoworkItems(cfg).map((x) => x.id);
  assert.deepStrictEqual(a, b);                       // id 安定
  // 手動 config が同じ (type, repo, name) を持つと発見側を抑止
  const ov = cowork.overview({ projects: { roots: [root] }, cowork: { items: [
    { id: 'manual', type: 'loop', name: 'issue-worker', repo: proj },
  ] } });
  const issueItems = ov.items.filter((i) => i.name === 'issue-worker' && i.type === 'loop');
  assert.strictEqual(issueItems.length, 1);
  assert.strictEqual(issueItems[0].source, 'config');
  assert.strictEqual(ov.items.filter((i) => i.type === 'loop').length, 3);   // 重複は増えない
});

test('overview は config + discovered をマージし source と state を付ける', () => {
  const root = mkRoot();
  writeKiro(path.join(root, 'projD'), SAMPLE_YAML);
  const ov = cowork.overview({ projects: { roots: [root] }, cowork: { items: [] } });
  assert.strictEqual(ov.items.length, 3);
  assert.ok(ov.items.every((i) => i.source === 'discovered' && i.state));
});

// --- 書き戻し（外科的） ---
test('applyKiroLoopEdits: enabled トグルは当該 1 行のみ変更しコメント/ブロックを保つ', () => {
  const r = wb.applyKiroLoopEdits(SAMPLE_YAML, [
    { promptIndex: 0, promptName: 'MR コメント返答', enabled: false, scheduleKey: 'interval_minutes' },
  ]);
  assert.deepStrictEqual(r.errors, []);
  const before = SAMPLE_YAML.split('\n');
  const after = r.text.split('\n');
  const changed = before.map((l, i) => [l, after[i]]).filter(([x, y]) => x !== y);
  assert.strictEqual(changed.length, 1);
  assert.match(changed[0][1], /^ {4}enabled: false$/);
  assert.ok(r.text.includes('これは key: value に見えるが本文'));   // ブロックスカラ保全
  assert.ok(r.text.includes('# 1 時間ごと'));                       // インラインコメント保全
});

test('applyKiroLoopEdits: entry#2 の編集は他エントリに波及せず再パースで新値', () => {
  const r = wb.applyKiroLoopEdits(SAMPLE_YAML, [
    { promptIndex: 1, promptName: 'issue-worker', name: 'renamed', scheduleKey: 'cron', schedule: '30 8 * * *' },
  ]);
  const e = discover.parseKiroLoopPrompts(r.text);
  assert.strictEqual(e.length, 3);
  assert.strictEqual(e[0].name, 'MR コメント返答');   // #1 不変
  assert.strictEqual(e[1].name, 'renamed');
  assert.strictEqual(e[1].cron, '30 8 * * *');
  assert.strictEqual(e[2].name, 'no-sched');           // #3 不変
});

test('applyKiroLoopEdits: 欠落フィールドは dash 直後へ挿入される', () => {
  const r = wb.applyKiroLoopEdits(SAMPLE_YAML, [
    { promptIndex: 2, promptName: 'no-sched', enabled: false, scheduleKey: '' },
  ]);
  const e = discover.parseKiroLoopPrompts(r.text);
  assert.strictEqual(e[2].enabled, false);             // 追加された
  assert.strictEqual(e[0].name, 'MR コメント返答');
});

test('applyKiroLoopEdits: CRLF を保つ / promptName 不一致は name 照合にフォールバック', () => {
  const crlf = SAMPLE_YAML.replace(/\n/g, '\r\n');
  const r = wb.applyKiroLoopEdits(crlf, [{ promptIndex: 0, promptName: 'MR コメント返答', enabled: false, scheduleKey: 'interval_minutes' }]);
  assert.ok(r.text.includes('\r\n'));
  // index がずれていても name で当てる
  const r2 = wb.applyKiroLoopEdits(SAMPLE_YAML, [{ promptIndex: 9, promptName: 'issue-worker', enabled: true, scheduleKey: 'cron' }]);
  assert.deepStrictEqual(r2.errors, []);
  assert.strictEqual(discover.parseKiroLoopPrompts(r2.text)[1].enabled, true);
});

test('applyStatemachineEdits は先頭 name/description のみ書換え、state 内の description は触らない', () => {
  const src = 'name: "旧"\ndescription: "旧説明"\nstates:\n  s:\n    description: "内側は不変"\n';
  const r = wb.applyStatemachineEdits(src, { name: '新', description: '新説明' });
  assert.match(r.text, /^name: "新"$/m);
  assert.match(r.text, /^description: "新説明"$/m);
  assert.ok(r.text.includes('description: "内側は不変"'));
});

// --- saveWork（分割・書き戻し・git） ---
function tmpGitRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cwd-repo-'));
  const opt = { cwd: repo, encoding: 'utf8' };
  spawnSync('git', ['init', '-b', 'main'], opt);
  spawnSync('git', ['config', 'user.email', 'c@e.test'], opt);
  spawnSync('git', ['config', 'user.name', 'C'], opt);
  return repo;
}

test('saveWork は発見項目の編集を実体へ書き戻し、当該ファイルだけ commit する', () => {
  const repo = tmpGitRepo();
  writeKiro(repo, SAMPLE_YAML);
  spawnSync('git', ['add', '-A'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });

  const config = { projects: { roots: [repo] }, cowork: { items: [] } };
  const ov = cowork.overview(config);
  const item = ov.items.find((i) => i._src && i._src.promptName === 'issue-worker');
  item.enabled = true;                                 // false → true に編集

  let savedCfg = null;
  const res = cowork.saveWork(config, (c) => { savedCfg = c; return c; }, { items: ov.items, push: false });

  // 設定には発見項目を混ぜない
  assert.deepStrictEqual(savedCfg.cowork.items, []);
  // 実体ファイルが書き換わっている
  const onDisk = discover.parseKiroLoopPrompts(fs.readFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), 'utf8'));
  assert.strictEqual(onDisk[1].enabled, true);
  // commit された（相対 POSIX パスで staged）
  const g = res.git.find((x) => x.repo === repo);
  assert.strictEqual(g.result.commit.ok, true);
  const log = spawnSync('git', ['show', '--stat', '--name-only', 'HEAD'], { cwd: repo, encoding: 'utf8' }).stdout;
  assert.ok(log.includes('.kiro/kiro-loop.yml'));
});

test('saveWork は編集が無ければ実体を触らず commit を skip する', () => {
  const repo = tmpGitRepo();
  writeKiro(repo, SAMPLE_YAML);
  spawnSync('git', ['add', '-A'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });
  const config = { projects: { roots: [repo] }, cowork: { items: [] } };
  const ov = cowork.overview(config);
  const before = fs.readFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), 'utf8');
  const res = cowork.saveWork(config, (c) => c, { items: ov.items, push: false });
  // 編集ゼロ・手動項目ゼロ → どの repo にも触らない（余計な commit/push をしない）
  assert.deepStrictEqual(res.writeback.errors, []);
  assert.strictEqual(res.git.length, 0);
  assert.strictEqual(fs.readFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), 'utf8'), before);
});

test('saveWork は手動項目のみ config へ保存（後方互換）', () => {
  let saved = null;
  const items = [{ id: 'm1', type: 'loop', name: 'x', repo: '/r', source: 'config' }];
  const res = cowork.saveWork({ cowork: {} }, (c) => { saved = c; return c; }, { items });
  assert.strictEqual(saved.cowork.items.length, 1);
  assert.strictEqual(saved.cowork.items[0].id, 'm1');
  assert.ok(!('source' in saved.cowork.items[0]));     // 実行時フィールドは落とす
  assert.deepStrictEqual(res.writeback.errors, []);
});

// --- WSL パス ---
test('resolveRoot は win32 で WSL の POSIX パスを UNC へ寄せる', () => {
  const origPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  const origDistro = process.env.WSL_DISTRO_NAME;
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  process.env.WSL_DISTRO_NAME = 'Ubuntu';
  try {
    assert.strictEqual(discover.resolveRoot('/home/dev/proj'), '\\\\wsl.localhost\\Ubuntu\\home\\dev\\proj');
  } finally {
    if (origPlatform) Object.defineProperty(process, 'platform', origPlatform);
    if (origDistro === undefined) delete process.env.WSL_DISTRO_NAME;
    else process.env.WSL_DISTRO_NAME = origDistro;
  }
});

console.log(`\n${passed} tests passed`);
