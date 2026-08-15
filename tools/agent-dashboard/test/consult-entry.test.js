'use strict';

// 共通 AI 操作バー（「AIに相談」）と Dashboard AI 設定の境界テスト。
// 設計: docs/plans/2026-08-14-agent-dashboard-consult-entry-design.md
//
// 見ているのは 3 つ:
//   1. config.agent が Dashboard 自身の AI だけを実行方針より上で上書きすること。
//   2. 全画面の共通操作バーが詳細画面の対象も解決すること。
//   3. 各エンジンの resolveAgent の解決順を変えないこと。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agent = require('../src/features/agent-project/main/agent');
const control = require('../src/features/orchestration/main/control');
const rendererSrc = require('./helpers/renderer-src').read();

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function withControlDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'consult-control-'));
  try {
    return fn({ orchestration: { controlDir: dir } }, dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// --- 1. 実行方針への接続 ---

test('相談は workload=dashboard / purpose=chat として解決する', () => {
  assert.strictEqual(agent.CONSULT_WORKLOAD, 'dashboard');
  assert.strictEqual(agent.CONSULT_PURPOSE, 'chat');
});

test('アプリ設定は相談の実行方針より優先する', () => {
  withControlDir((cfg) => {
    control.saveControl(cfg, {
      workloads: { dashboard: { agents: { chat: { agent_cli: 'claude', model: 'sonnet' } } } },
    });
    const info = agent.resolveDashboardAgent({ ...cfg, agent: { cli: 'copilot' } }, null, { purpose: 'chat' });
    assert.strictEqual(info.cli, 'copilot');
    assert.strictEqual(info.model, 'sonnet', 'モデル未指定なら実行方針の値を引き継ぐ');
    assert.strictEqual(info.source, 'settings');
  });
});

test('アプリ設定が空なら相談は実行方針へ従う', () => {
  withControlDir((cfg) => {
    control.saveControl(cfg, {
      workloads: { dashboard: { agents: { chat: { agent_cli: '', model: '' } } } },
    });
    const info = agent.resolveDashboardAgent({ ...cfg, agent: { cli: '', model: '' } }, null, { purpose: 'chat' });
    assert.strictEqual(info.cli, 'kiro');
  });
});

test('workload 指定なしの解決は control を見ない（回帰の的を固定する）', () => {
  withControlDir((cfg) => {
    control.saveControl(cfg, {
      workloads: { dashboard: { agents: { chat: { agent_cli: 'claude' } } } },
    });
    const bare = agent.resolveAgent({ ...cfg, agent: { cli: 'copilot' } }, null);
    assert.strictEqual(bare.cli, 'copilot',
      'workload を渡さない解決は従来どおり。相談側が渡していることが効いている');
  });
});

test('通常の resolveAgent は実行方針優先のままで各エンジンへ影響しない', () => {
  withControlDir((cfg) => {
    control.saveControl(cfg, { workloads: { routine: { agent_cli: 'claude' } } });
    const resolved = agent.resolveAgent({ ...cfg, agent: { cli: 'copilot' } }, null, { workload: 'routine' });
    assert.strictEqual(resolved.cli, 'claude');
    assert.strictEqual(resolved.source, 'control');
  });
});

test('相談の起動は実行制御の停止・上限を尊重する', () => {
  withControlDir((cfg) => {
    control.saveControl(cfg, { workloads: { dashboard: { lifecycle: 'pause' } } });
    assert.throws(
      () => agent.assertDashboardAllowed(cfg, { cli: 'kiro', model: '' }, agent.CONSULT_PURPOSE),
      /agent-error:control/,
      '止まっている間は新しい対話を開かない'
    );
  });
});

test('openInteractiveChat が実行方針の判断を通る', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'features', 'agent-project', 'main', 'agent.js'), 'utf8');
  const body = source.slice(source.indexOf('function openInteractiveChat('),
    source.indexOf('function isExistingDir('));
  assert.ok(/resolveDashboardAgent\(cfg, projectDir, \{ purpose: CONSULT_PURPOSE \}\)/.test(body),
    '相談は Dashboard 専用の解決器へ purpose を渡す');
  assert.ok(body.includes('assertDashboardAllowed(cfg, resolved, CONSULT_PURPOSE)'),
    '起動前に実行制御へ問い合わせる');
  assert.ok(body.includes('interactiveLaunchSpec(cfg, projectDir, { resolved })'),
    '解決済みのエージェントをそのまま起動仕様へ渡す（二度解決しない）');
});

// --- 2. アプリ設定の保存境界 ---

test('Dashboard AI は config.agent へ保存し control.json を書かない', () => {
  assert.ok(/cfg\.agent\.cli\s*=/.test(rendererSrc));
  assert.ok(/cfg\.agent\.model\s*=/.test(rendererSrc));
  assert.ok(!rendererSrc.includes('saveConsultAgentControl'));
  assert.ok(!/orchestrationControlSave\(\{\s*\n?\s*workloads: \{ dashboard: \{ agents: \{ chat:/.test(rendererSrc));
});

// --- 3. 領域ごとの対象フォルダ ---

test('4 領域それぞれが対象フォルダの出しかたを登録する', () => {
  const workflowFeature = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'features', 'adhoc-flow.js'), 'utf8');
  for (const area of ['projects', 'routines', 'missions']) {
    assert.ok(rendererSrc.includes(`registerConsultSource('${area}'`), `${area} を登録します`);
  }
  assert.ok(workflowFeature.includes("registerConsultSource('workflows'"), 'workflows を登録します');
});

test('定常業務はプロジェクト前提の候補計算を通さない', () => {
  const at = rendererSrc.indexOf("registerConsultSource('routines'");
  const body = rendererSrc.slice(at, at + 400);
  assert.ok(!body.includes('cliChatCwdChoices'),
    'repos レジストリの候補を作業フォルダへ流用しない（実体と表示名が食い違う）');
  assert.ok(body.includes('choices: []'));
});

test('対象フォルダが未確定なら理由を文字で出し、起動しない', () => {
  assert.ok(rendererSrc.includes('function consultNoteText('));
  assert.ok(/if \(!target\.dir\) return toast\(target\.reason/.test(rendererSrc),
    '対象が無いまま起動しない');
  assert.ok(/if \(button\) button\.disabled = state\.consultBusy/.test(rendererSrc),
    '対象が無い場合は押下時に理由を伝え、起動中だけ無効にする');
});

test('相談コントロールは領域につき 1 組だけ出る', () => {
  assert.ok(rendererSrc.includes('group.hidden = group.dataset.consultGroup !== state.area'),
    '前の領域の 1 組が残ると、画面で見ているものとは別のフォルダで CLI が開く');
});

// --- 4. renderer のロジックを実際に動かす ---
//
// 文字列アサーションだけでは「関数が揃っている」ことしか言えない。領域の出し分けと
// 無効理由の組み立ては実際に動かして確かめる（agent-audit.test.js と同じ new Function 方式）。

function loadConsultModule({ area, projectFolder, choices = [] }) {
  const core = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
  const at = core.indexOf('const CONSULT_SOURCES = {};');
  const end = core.indexOf('// 起動先（cwd）の候補。');
  assert.ok(at > 0 && end > at, '相談ブロックを renderer.js から切り出せる');

  const el = (tag) => ({
    tag, dataset: {}, hidden: false, disabled: false, value: '', textContent: '',
    title: '', innerHTML: '', children: [],
    appendChild(child) { this.children.push(child); },
    querySelector(sel) {
      const key = sel.replace(/[[\]]/g, '');
      return this.children.find((c) => c.marker === key) || null;
    },
  });
  const group = (areaId) => {
    const g = el('div');
    g.dataset.consultGroup = areaId;
    const button = el('button'); button.marker = 'data-consult-open';
    const note = el('p'); note.marker = 'data-consult-note';
    const path = el('span'); path.marker = 'data-consult-path';
    const cwd = el('select'); cwd.marker = 'data-consult-cwd';
    g.children.push(button, note, path, cwd);
    return g;
  };
  const groups = ['projects', 'workflows', 'missions', 'routines'].map(group);
  const document = {
    querySelectorAll: () => groups,
    createElement: (tag) => el(tag),
  };
  const state = { area, consultBusy: false, cliChatCwdChoices: choices };
  const sandbox = {
    document, state,
    esc: (s) => String(s),
    uiLog: () => {},
    $: () => null,
    selectedProjectFolder: () => projectFolder,
  };
  // eslint-disable-next-line no-new-func
  const factory = new Function('sandbox', `
    const { document, state, esc, uiLog, $, selectedProjectFolder } = sandbox;
    ${core.slice(at, end)}
    return { consultTarget, renderConsultControls, consultControlHtml, consultNoteText };
  `);
  return { api: factory(sandbox), groups, state };
}

test('領域を切り替えると、その領域の 1 組だけが出る', () => {
  const { api: mod, groups } = loadConsultModule({ area: 'routines', projectFolder: '/work/repo' });
  mod.renderConsultControls();
  const visible = groups.filter((g) => !g.hidden).map((g) => g.dataset.consultGroup);
  assert.deepStrictEqual(visible, ['routines']);
});

test('定常業務: 作業フォルダを選んでいればボタンが押せる', () => {
  const { api: mod, groups } = loadConsultModule({
    area: 'routines', projectFolder: '/work/repo',
  });
  mod.renderConsultControls();
  const g = groups.find((x) => x.dataset.consultGroup === 'routines');
  assert.strictEqual(g.querySelector('data-consult-open').disabled, false);
  assert.strictEqual(g.querySelector('data-consult-note').textContent, '');
});

test('対象フォルダが無くても押せ、理由が文字で出る', () => {
  const { api: mod, groups } = loadConsultModule({ area: 'routines', projectFolder: '' });
  mod.renderConsultControls();
  const g = groups.find((x) => x.dataset.consultGroup === 'routines');
  assert.strictEqual(g.querySelector('data-consult-open').disabled, false);
  assert.strictEqual(g.querySelector('data-consult-note').textContent, '作業フォルダを選択してください');
});

test('プロジェクト: 候補が 1 件なら選択コントロールを出さない', () => {
  const one = loadConsultModule({
    area: 'projects', projectFolder: '/p',
    choices: [{ path: '/p', label: 'プロジェクト', enabled: true }],
  });
  one.api.renderConsultControls();
  const g1 = one.groups.find((x) => x.dataset.consultGroup === 'projects');
  assert.strictEqual(g1.querySelector('data-consult-cwd').hidden, true, '選べない select を置かない');

  const many = loadConsultModule({
    area: 'projects', projectFolder: '/p',
    choices: [
      { path: '/p', label: 'プロジェクト', enabled: true },
      { path: '', label: 'app', enabled: false, reason: 'クローンがありません' },
    ],
  });
  many.api.renderConsultControls();
  const g2 = many.groups.find((x) => x.dataset.consultGroup === 'projects');
  const sel = g2.querySelector('data-consult-cwd');
  assert.strictEqual(sel.hidden, false);
  assert.strictEqual(sel.children.length, 2);
  assert.strictEqual(sel.children[1].disabled, true, 'クローンが無いものは消さず非活性で見せる');
  assert.strictEqual(sel.children[1].title, 'クローンがありません', '選べない理由を残す');
});

test('領域が変わるすべての経路で相談を揃える', () => {
  // switchTab も data-area を見て state.area を書き換える。switchArea だけを直していた頃は、
  // 「現在のタブが新しい領域に無い」経路（switchArea → switchTab で早期 return）を通ると
  // 前の領域の 1 組と実効エージェントが次の定期更新まで残っていた。
  for (const fn of ['switchTab', 'async function switchArea']) {
    const at = rendererSrc.indexOf(fn === 'switchTab' ? 'function switchTab(' : fn);
    assert.ok(at > 0, `${fn} が見つかる`);
    const body = rendererSrc.slice(at, rendererSrc.indexOf('\n}', at));
    assert.ok(body.includes('refreshConsultInfo()'), `${fn} は相談を揃える`);
  }
});

test('起動モデルをラベル表示するための IPC を呼ばない', () => {
  assert.ok(!rendererSrc.includes('api.agentConsultInfo('));
  assert.ok(!rendererSrc.includes('consultInfo.model'));
});

test('登録の無い領域では相談を出さない（ホーム・設定など）', () => {
  const { api: mod, groups } = loadConsultModule({ area: 'home', projectFolder: '/p' });
  mod.renderConsultControls();
  assert.deepStrictEqual(groups.filter((g) => !g.hidden), []);
  assert.deepStrictEqual(mod.consultTarget(), { dir: '', choices: [], reason: '' });
});

console.log(`\n${passed} tests passed`);
