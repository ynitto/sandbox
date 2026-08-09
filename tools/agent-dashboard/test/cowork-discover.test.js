'use strict';

// Cowork 自動発見（定常業務ループの設定・.statemachine のジョブ抽出）と、保存時の実体
// ファイル外科的書き戻しのテスト。追加依存なしで `node test/cowork-discover.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const discover = require('../src/features/cowork/main/discover');
const { engineConfig } = require('./helpers/engine-status');
const wb = require('../src/features/cowork/main/writeback');
const cowork = require('../src/features/cowork/main/cowork');

// 定常業務の走査ルートには**常にユーザーホーム**が入る（`~/.agents/agent-loop.yml` を拾うため）。
// テストでは実機のホームを覗かせない——結果が実行環境の持ち物で変わってしまう。
const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'home-stub-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

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

function writeAgentLoop(dir, text, ext) {
  fs.mkdirSync(path.join(dir, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(dir, '.agents', `agent-loop.${ext || 'yml'}`), text);
}

function writeSm(dir, name, text) {
  const d = path.join(dir, '.statemachine', name);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'workflow.yaml'), text);
}

// --- パーサ ---
test('parseAgentLoopPrompts はブロックスカラ本文を field として拾わない', () => {
  const e = discover.parseAgentLoopPrompts(SAMPLE_YAML);
  assert.strictEqual(e.length, 3);
  assert.deepStrictEqual(e[0], { name: 'MR コメント返答', interval_minutes: 60, enabled: true });
  assert.deepStrictEqual(e[1], { name: 'issue-worker', cron: '0 9 * * 1-5', enabled: false });
  assert.deepStrictEqual(e[2], { name: 'no-sched' });   // schedule/enabled 無し
});

test('parseAgentLoopPrompts はインラインコメントを除去し引用名を保つ', () => {
  const e = discover.parseAgentLoopPrompts('prompts:\n  - name: "a # b"\n    interval_minutes: 5  # every 5\n');
  assert.strictEqual(e[0].name, 'a # b');            // 引用内の # は残す
  assert.strictEqual(e[0].interval_minutes, 5);      // 末尾コメントは除去
});

test('parseAgentLoopPrompts は引用値の後ろのコメントを剥がす', () => {
  const e = discover.parseAgentLoopPrompts('prompts:\n  - name: n\n    cron: "0 9 * * 1-5"      # 平日9:00\n');
  assert.strictEqual(e[0].cron, '0 9 * * 1-5');      // 閉じ引用符の後ろのコメントは無視
});

// サンプルは agent-loop 側のものを読む。agent-loop の同名サンプルを読んでいると、
// tools/agent-loop/ を消した瞬間（計画 S4）にこのテストが落ちる。
test('parseAgentLoopPrompts は実サンプル agent-loop.yaml.example を壊さず読む', () => {
  const sample = fs.readFileSync(path.join(__dirname, '..', '..', 'agent-loop', 'agent-loop.yaml.example'), 'utf8');
  const e = discover.parseAgentLoopPrompts(sample);
  assert.ok(e.length >= 5);                          // 複数の prompts エントリ
  assert.ok(e.every((x) => x.name && !/#/.test(x.cron || '')));  // コメント混入なし
});

test('agentLoopPromptTexts はインライン/ブロックスカラの prompt 本文を返す', () => {
  const t = discover.agentLoopPromptTexts(SAMPLE_YAML);
  assert.strictEqual(t.length, 3);
  assert.ok(t[0].includes('python scripts/gl.py list-mrs'));
  assert.ok(t[0].includes('resolve: これは key: value に見えるが本文'));
  assert.strictEqual(t[1], '作業する');
  assert.strictEqual(t[2], 'hi');
});

// --- 発見 ---
test('ユーザーホームでは旧 ~/.agent の loop 設定を読まない', () => {
  fs.mkdirSync(path.join(HOME_STUB, '.agent'), { recursive: true });
  fs.writeFileSync(path.join(HOME_STUB, '.agent', 'agent-loop.yml'), SAMPLE_YAML);
  assert.strictEqual(discover.scanForCoworkConfigs(HOME_STUB, 0, false).length, 0);
  assert.deepStrictEqual(discover.loopConfigFile(HOME_STUB),
    { file: path.join(HOME_STUB, '.agents', 'agent-loop.yml'), format: 'yaml' });
});

test('discoverCoworkItems は prompts を per-job の loop 項目にする', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projA');
  writeAgentLoop(proj, SAMPLE_YAML);
  const items = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} });
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
  const items = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} });
  const sms = items.filter((i) => i.type === 'state-machine');
  assert.strictEqual(sms.length, 2);
  const rel = sms.find((s) => s.workflow === 'release');
  assert.strictEqual(rel.name, 'リリース');
  assert.strictEqual(rel.description, 'デプロイ');
  const tri = sms.find((s) => s.workflow === 'triage');
  assert.strictEqual(tri.name, 'triage');            // 先頭 name: 無し → ディレクトリ名
});

const PAIRED_YAML = `prompts:
  - name: release-runner
    prompt: |
      release ステートマシンを実行して
    interval_minutes: 30
    enabled: false

  - name: other
    prompt: hi
    interval_minutes: 5
`;

test('「xxx ステートマシンを実行して」の対エントリはステートマシン項目へ統合される', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projP');
  writeAgentLoop(proj, PAIRED_YAML);
  writeSm(proj, 'release', 'name: "リリース"\ndescription: "デプロイ"\nstates:\n  s: {}\n');
  const items = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} });
  // 対エントリは loop 項目として出さない（other のみ）
  const loops = items.filter((i) => i.type === 'loop');
  assert.deepStrictEqual(loops.map((l) => l.name), ['other']);
  // ステートマシン項目が schedule/enabled と対エントリ情報を引き継ぐ
  const sm = items.find((i) => i.type === 'state-machine');
  assert.strictEqual(sm.name, 'リリース');
  assert.strictEqual(sm.schedule, '30m');
  assert.strictEqual(sm.enabled, false);
  assert.strictEqual(sm._src.loop.promptName, 'release-runner');
  assert.strictEqual(sm._src.loop.scheduleKey, 'interval_minutes');
  assert.strictEqual(sm._src.loop.file, path.join(proj, '.agents', 'agent-loop.yml'));
});

test('対エントリは workflow.yaml の表示名（name:）でも照合できる', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projQ');
  writeAgentLoop(proj, 'prompts:\n  - name: run-release\n    prompt: リリース ステートマシンを実行して\n    cron: "0 9 * * *"\n');
  writeSm(proj, 'release', 'name: "リリース"\nstates:\n  s: {}\n');
  const items = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} });
  assert.strictEqual(items.filter((i) => i.type === 'loop').length, 0);
  const sm = items.find((i) => i.type === 'state-machine');
  assert.strictEqual(sm.schedule, '0 9 * * *');
  assert.strictEqual(sm._src.loop.promptName, 'run-release');
});

test('ステートマシンに言及しない prompt は統合されない', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projR');
  writeAgentLoop(proj, 'prompts:\n  - name: mention\n    prompt: release を確認して\n    interval_minutes: 5\n');
  writeSm(proj, 'release', 'states:\n  s: {}\n');
  const items = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} });
  assert.strictEqual(items.filter((i) => i.type === 'loop').length, 1);
  const sm = items.find((i) => i.type === 'state-machine');
  assert.ok(!sm._src.loop);
  assert.strictEqual(sm.schedule, '');
});

test('統合ステートマシンの実行は対プロンプト名を agent-loop send で送る', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projS');
  writeAgentLoop(proj, PAIRED_YAML);
  writeSm(proj, 'release', 'name: "リリース"\nstates:\n  s: {}\n');
  const config = { ...engineConfig([root]), cowork: { loopCommand: 'echo', runWindow: false, items: [] } };
  const sm = cowork.overview(config).items.find((i) => i.type === 'state-machine');
  const r = cowork.runStateMachine(config, sm.id, '');
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send release-runner');
});

test('discoverCoworkItems は .json 形式も同じ項目にする', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projJ');
  writeAgentLoop(proj, JSON.stringify({ prompts: [
    { name: 'j1', interval_minutes: 30, enabled: true },
    { name: 'j2', cron: '* * * * *', enabled: false },
  ] }), 'json');
  const loops = discover.discoverCoworkItems({ ...engineConfig([root]), cowork: {} }).filter((i) => i.type === 'loop');
  assert.strictEqual(loops.length, 2);
  assert.strictEqual(loops[0].schedule, '30m');
  assert.strictEqual(loops[1].schedule, '* * * * *');
  assert.strictEqual(loops[1].enabled, false);
});

test('scanForCoworkConfigs は深さ上限を守り workspace 配下へ潜らない・node_modules を飛ばす', () => {
  const root = mkRoot();
  const deep = path.join(root, 'a', 'b', 'proj');    // 深さ 3
  writeAgentLoop(deep, SAMPLE_YAML);
  writeAgentLoop(path.join(root, 'nm-holder', 'node_modules', 'x'), SAMPLE_YAML);
  assert.strictEqual(discover.scanForCoworkConfigs(root, 2).length, 0);      // 既定 2 では届かない
  assert.strictEqual(discover.scanForCoworkConfigs(root, 3).length, 1);      // 3 で発見
  // workspace 内部に別マーカーがあっても 1 件のまま
  writeAgentLoop(path.join(deep, 'inner'), SAMPLE_YAML);
  assert.strictEqual(discover.scanForCoworkConfigs(root, 5).length, 1);
});

test('discover は id が安定し、手動登録と重複する発見項目は config 勝ちで排除される', () => {
  const root = mkRoot();
  const proj = path.join(root, 'projC');
  writeAgentLoop(proj, SAMPLE_YAML);
  const cfg = { ...engineConfig([root]), cowork: {} };
  const a = discover.discoverCoworkItems(cfg).map((x) => x.id);
  const b = discover.discoverCoworkItems(cfg).map((x) => x.id);
  assert.deepStrictEqual(a, b);                       // id 安定
  // 手動 config が同じ (type, repo, name) を持つと発見側を抑止
  const ov = cowork.overview({ ...engineConfig([root]), cowork: { items: [
    { id: 'manual', type: 'loop', name: 'issue-worker', repo: proj },
  ] } });
  const issueItems = ov.items.filter((i) => i.name === 'issue-worker' && i.type === 'loop');
  assert.strictEqual(issueItems.length, 1);
  assert.strictEqual(issueItems[0].source, 'config');
  assert.strictEqual(ov.items.filter((i) => i.type === 'loop').length, 3);   // 重複は増えない
});

test('dashboard が作成した定型業務は workflow 識別名で発見項目と重複排除する', () => {
  const repo = mkRoot();
  writeSm(repo, 'release-check', 'name: "リリース確認"\nstates:\n  done:\n    terminal: true\n');
  const config = {
    ...engineConfig([repo]),
    cowork: { items: [{
      id: 'managed-release', type: 'state-machine', name: 'リリース確認',
      repo, workflow: 'release-check', managed: true,
    }] },
  };
  const items = cowork.overview(config).items.filter((item) => item.type === 'state-machine');
  assert.strictEqual(items.length, 1);
  assert.strictEqual(items[0].source, 'config');
});

test('overview は config + discovered をマージし source と state を付ける', () => {
  const root = mkRoot();
  writeAgentLoop(path.join(root, 'projD'), SAMPLE_YAML);
  const ov = cowork.overview({ ...engineConfig([root]), cowork: { items: [] } });
  assert.strictEqual(ov.items.length, 3);
  assert.ok(ov.items.every((i) => i.source === 'discovered' && i.state));
  // 所属は**登録したフォルダ**（走査ルート）。設定ファイルがその下のサブフォルダにあっても、
  // 画面で選ぶフォルダはこちらなので、鍵もこちらで持つ（root ＝ 実行時の cwd）。
  assert.deepStrictEqual(ov.discoveredRepos, [root]);
  assert.deepStrictEqual([...new Set(ov.items.map((i) => i.root))], [root]);
  assert.deepStrictEqual([...new Set(ov.items.map((i) => i.repo))], [path.join(root, 'projD')],
    '設定ファイルの在り処（repo）は従来どおりサブフォルダを指す');
});

// --- 書き戻し（外科的） ---
test('applyAgentLoopEdits: enabled トグルは当該 1 行のみ変更しコメント/ブロックを保つ', () => {
  const r = wb.applyAgentLoopEdits(SAMPLE_YAML, [
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

test('applyAgentLoopEdits: entry#2 の編集は他エントリに波及せず再パースで新値', () => {
  const r = wb.applyAgentLoopEdits(SAMPLE_YAML, [
    { promptIndex: 1, promptName: 'issue-worker', name: 'renamed', scheduleKey: 'cron', schedule: '30 8 * * *' },
  ]);
  const e = discover.parseAgentLoopPrompts(r.text);
  assert.strictEqual(e.length, 3);
  assert.strictEqual(e[0].name, 'MR コメント返答');   // #1 不変
  assert.strictEqual(e[1].name, 'renamed');
  assert.strictEqual(e[1].cron, '30 8 * * *');
  assert.strictEqual(e[2].name, 'no-sched');           // #3 不変
});

test('applyAgentLoopEdits: 欠落フィールドは dash 直後へ挿入される', () => {
  const r = wb.applyAgentLoopEdits(SAMPLE_YAML, [
    { promptIndex: 2, promptName: 'no-sched', enabled: false, scheduleKey: '' },
  ]);
  const e = discover.parseAgentLoopPrompts(r.text);
  assert.strictEqual(e[2].enabled, false);             // 追加された
  assert.strictEqual(e[0].name, 'MR コメント返答');
});

test('applyAgentLoopEdits: CRLF を保つ / promptName 不一致は name 照合にフォールバック', () => {
  const crlf = SAMPLE_YAML.replace(/\n/g, '\r\n');
  const r = wb.applyAgentLoopEdits(crlf, [{ promptIndex: 0, promptName: 'MR コメント返答', enabled: false, scheduleKey: 'interval_minutes' }]);
  assert.ok(r.text.includes('\r\n'));
  // index がずれていても name で当てる
  const r2 = wb.applyAgentLoopEdits(SAMPLE_YAML, [{ promptIndex: 9, promptName: 'issue-worker', enabled: true, scheduleKey: 'cron' }]);
  assert.deepStrictEqual(r2.errors, []);
  assert.strictEqual(discover.parseAgentLoopPrompts(r2.text)[1].enabled, true);
});

test('applyAgentLoopEdits は prompt 本文だけを書き換え、古いブロック本文を残さない', () => {
  const r = wb.applyAgentLoopEdits(SAMPLE_YAML, [{
    promptIndex: 1, promptName: 'issue-worker', prompt: '新しい手順\n次の行',
  }]);
  const texts = discover.agentLoopPromptTexts(r.text);
  assert.strictEqual(texts[1], '新しい手順\n次の行');
  assert.ok(!r.text.includes('      作業する'));
  assert.strictEqual(texts[2], 'hi');
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
  writeAgentLoop(repo, SAMPLE_YAML);
  spawnSync('git', ['add', '-A'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });

  const config = { ...engineConfig([repo]), cowork: { items: [] } };
  const ov = cowork.overview(config);
  const item = ov.items.find((i) => i._src && i._src.promptName === 'issue-worker');
  item.enabled = true;                                 // false → true に編集

  let savedCfg = null;
  const res = cowork.saveWork(config, (c) => { savedCfg = c; return c; }, { items: ov.items, push: false });

  // 設定には発見項目を混ぜない
  assert.deepStrictEqual(savedCfg.cowork.items, []);
  // 実体ファイルが書き換わっている
  const onDisk = discover.parseAgentLoopPrompts(fs.readFileSync(path.join(repo, '.agents', 'agent-loop.yml'), 'utf8'));
  assert.strictEqual(onDisk[1].enabled, true);
  // commit された（相対 POSIX パスで staged）
  const g = res.git.find((x) => x.repo === repo);
  assert.strictEqual(g.result.commit.ok, true);
  const log = spawnSync('git', ['show', '--stat', '--name-only', 'HEAD'], { cwd: repo, encoding: 'utf8' }).stdout;
  assert.ok(log.includes('.agents/agent-loop.yml'));
});

test('saveWork は統合ステートマシンの schedule/enabled を agent-loop 側へ、name を workflow.yaml へ書き戻す', () => {
  const repo = tmpGitRepo();
  writeAgentLoop(repo, 'prompts:\n  - name: release-runner\n    prompt: release ステートマシンを実行して\n    interval_minutes: 30\n    enabled: false\n');
  writeSm(repo, 'release', 'name: "リリース"\nstates:\n  s: {}\n');
  spawnSync('git', ['add', '-A'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });

  const config = { ...engineConfig([repo]), cowork: { items: [] } };
  const ov = cowork.overview(config);
  const sm = ov.items.find((i) => i.type === 'state-machine');
  sm.name = 'リリース v2';
  sm.schedule = '60m';
  sm.enabled = true;
  const res = cowork.saveWork(config, (c) => c, { items: ov.items, push: false });
  assert.deepStrictEqual(res.writeback.errors, []);

  const kiro = discover.parseAgentLoopPrompts(fs.readFileSync(path.join(repo, '.agents', 'agent-loop.yml'), 'utf8'));
  assert.strictEqual(kiro[0].name, 'release-runner');   // 対プロンプト名は変更しない
  assert.strictEqual(kiro[0].interval_minutes, 60);
  assert.strictEqual(kiro[0].enabled, true);
  const wf = fs.readFileSync(path.join(repo, '.statemachine', 'release', 'workflow.yaml'), 'utf8');
  assert.match(wf, /^name: "リリース v2"$/m);
});

test('saveWork は編集が無ければ実体を触らず commit を skip する', () => {
  const repo = tmpGitRepo();
  writeAgentLoop(repo, SAMPLE_YAML);
  spawnSync('git', ['add', '-A'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });
  const config = { ...engineConfig([repo]), cowork: { items: [] } };
  const ov = cowork.overview(config);
  const before = fs.readFileSync(path.join(repo, '.agents', 'agent-loop.yml'), 'utf8');
  const res = cowork.saveWork(config, (c) => c, { items: ov.items, push: false });
  // 編集ゼロ・手動項目ゼロ → どの repo にも触らない（余計な commit/push をしない）
  assert.deepStrictEqual(res.writeback.errors, []);
  assert.strictEqual(res.git.length, 0);
  assert.strictEqual(fs.readFileSync(path.join(repo, '.agents', 'agent-loop.yml'), 'utf8'), before);
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

// 新規作成先は **agent-loop が読む場所**。`.agents/agent-loop.yml` へ書くと、画面では
// 設定できたように見えて agent-loop はそのファイルを一度も読まない。
test('dashboard 管理項目はプロンプトと予約済み定型業務を .agents/agent-loop.yml に書く', () => {
  const repo = mkRoot();
  const result = cowork.applyManagedItems([
    { id: 'daily', managed: true, type: 'loop', name: '日次確認', repo, prompt: '課題を確認して' },
    { id: 'release', managed: true, type: 'state-machine', name: 'リリース', repo, workflow: 'release', schedule: '0 9 * * 1' },
    { id: 'manual', managed: true, type: 'state-machine', name: '手動だけ', repo, workflow: 'manual', schedule: '' },
  ]);
  assert.deepStrictEqual(result.errors, []);
  const file = path.join(repo, '.agents', 'agent-loop.yml');
  const raw = fs.readFileSync(file, 'utf8');
  const entries = discover.parseAgentLoopPrompts(raw);
  const texts = discover.agentLoopPromptTexts(raw);
  assert.deepStrictEqual(entries.map((entry) => entry.name).sort(), ['リリース', '日次確認'].sort());
  assert.ok(texts.includes('課題を確認して'));
  assert.ok(texts.some((text) => text.includes('statemachine-use スキルでreleaseステートマシンを実行して')));
});

// --- 設定ファイルの探索先（agent-loop が読む場所が正） ---
test('発見は agent-loop の .agents/agent-loop.yml を読む', () => {
  const repo = mkRoot();
  writeAgentLoop(repo, SAMPLE_YAML);
  const mk = discover.detectMarkers(repo);
  assert.strictEqual(mk.kiroFile, path.join(repo, '.agents', 'agent-loop.yml'));
  assert.strictEqual(mk.kiroFormat, 'yaml');
});

test('発見は旧 .agent/ ホームの agent-loop.yml も読む', () => {
  const repo = mkRoot();
  fs.mkdirSync(path.join(repo, '.agent'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agent', 'agent-loop.yml'), SAMPLE_YAML);
  assert.strictEqual(discover.detectMarkers(repo).kiroFile,
    path.join(repo, '.agent', 'agent-loop.yml'));
});

test('loopConfigFile は既存設定を使い、無ければ agent-loop の場所を新規に選ぶ', () => {
  const fresh = mkRoot();
  assert.deepStrictEqual(discover.loopConfigFile(fresh),
    { file: path.join(fresh, '.agents', 'agent-loop.yml'), format: 'yaml' });
});

test('管理項目の保存は既存の agent-loop.yml へ追記し、新しいファイルを作らない', () => {
  const repo = mkRoot();
  writeAgentLoop(repo, SAMPLE_YAML);
  const result = cowork.applyManagedItems([
    { id: 'daily', managed: true, type: 'loop', name: '日次確認', repo, prompt: '課題を確認して' },
  ]);
  assert.deepStrictEqual(result.errors, []);
  assert.ok(!fs.existsSync(path.join(repo, '.kiro')), '旧パスへは書かない');
  const raw = fs.readFileSync(path.join(repo, '.agents', 'agent-loop.yml'), 'utf8');
  assert.ok(discover.agentLoopPromptTexts(raw).includes('課題を確認して'));
  assert.ok(raw.includes('MR コメント返答'), '既存エントリを消さない');
});

// JSON 設定へ YAML の外科的書換を流し込むと壊れる。避けて .yml を作ると agent-loop の
// 探索順（yaml → yml → json）で元の .json が無視され、プロンプトが消えたように見える。
test('管理項目の保存は JSON 設定のフォルダを黙って壊さず断る', () => {
  const repo = mkRoot();
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  const file = path.join(repo, '.agents', 'agent-loop.json');
  fs.writeFileSync(file, JSON.stringify({ prompts: [{ name: '既存', prompt: 'x' }] }, null, 2));
  const before = fs.readFileSync(file, 'utf8');
  const result = cowork.applyManagedItems([
    { id: 'daily', managed: true, type: 'loop', name: '日次確認', repo, prompt: '課題を確認して' },
  ]);
  assert.strictEqual(result.errors.length, 1);
  assert.ok(result.errors[0].includes('agent-loop.json'));
  assert.strictEqual(fs.readFileSync(file, 'utf8'), before, '実体を触らない');
  assert.ok(!fs.existsSync(path.join(repo, '.agents', 'agent-loop.yml')), '別ファイルも作らない');
});

// --- YAML のコメント（値パーサとアンカーパーサの整合） ---
const COMMENTED_LOOP = [
  'prompts:  # 定期実行',
  '  # ---- 一つ目 ----',
  '  - name: one   # 名前',
  '    prompt: |',
  '      手順:',
  '        1. A する',
  '    interval_minutes: 60   # 1 時間ごと',
  '    enabled: true  # 有効',
  '# ==== 列 0 の区切りコメント ====',
  '  - name: "two # ハッシュ入り"',
  '    prompt: B',
  '    cron: "0 * * * *"  # 毎時',
  '',
  'kiro_options: --trust-all-tools',
  '',
].join('\n');

test('列 0 のコメント行で prompts リストを打ち切らない', () => {
  // 打ち切ると 2 件目以降が発見から消える（.agents/agent-loop.yml があるのに画面に出ない）。
  const values = discover.parseAgentLoopPrompts(COMMENTED_LOOP);
  assert.strictEqual(values.length, 2);
  assert.deepStrictEqual(values[0], { name: 'one', interval_minutes: 60, enabled: true });
  assert.strictEqual(values[1].name, 'two # ハッシュ入り', '引用値の中の # は値の一部');
  assert.strictEqual(values[1].cron, '0 * * * *');
});

test('値パーサと書き戻しアンカーは prompts の並びが一致する', () => {
  // ずれると別のエントリへ書き戻す。writeback は promptIndex で両者を突き合わせる。
  const values = discover.parseAgentLoopPrompts(COMMENTED_LOOP);
  const anchors = discover.parseAgentLoopPromptsWithLines(COMMENTED_LOOP);
  assert.strictEqual(anchors.length, values.length);
  anchors.forEach((a, i) => {
    assert.strictEqual(discover.scalarValue(a.fields.name.rawVal), values[i].name);
  });
});

test('ブロックスカラの本文は相対インデントを保つ', () => {
  const texts = discover.agentLoopPromptTexts(COMMENTED_LOOP);
  assert.strictEqual(texts[0], '手順:\n  1. A する');
  assert.strictEqual(texts[1], 'B');
});

test('コメントだらけのファイルでも 2 件目を正しく書き戻す', () => {
  const values = discover.parseAgentLoopPrompts(COMMENTED_LOOP);
  const idx = values.findIndex((v) => v.name === 'two # ハッシュ入り');
  const { text, errors } = wb.applyAgentLoopEdits(COMMENTED_LOOP, [{
    promptIndex: idx, promptName: values[idx].name, enabled: false,
  }]);
  assert.deepStrictEqual(errors, []);
  assert.ok(text.includes('# ---- 一つ目 ----'), 'コメントを消さない');
  assert.ok(text.includes('# ==== 列 0 の区切りコメント ===='), '列 0 のコメントを消さない');
  assert.ok(text.includes('interval_minutes: 60   # 1 時間ごと'), '別エントリを触らない');
  const after = discover.parseAgentLoopPrompts(text);
  assert.strictEqual(after[0].enabled, true, '1 件目は変わらない');
  assert.strictEqual(after[idx].enabled, false);
});

test('prompts の後の別トップレベルキーではリストを閉じる', () => {
  const anchors = discover.parseAgentLoopPromptsWithLines(COMMENTED_LOOP);
  assert.strictEqual(anchors.length, 2, 'kiro_options 以降を巻き込まない');
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
