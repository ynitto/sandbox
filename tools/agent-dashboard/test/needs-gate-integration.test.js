'use strict';

// needs 側の codd-gate / 回帰失敗要約（needs-diagnosis）を、概要タブの「一貫性ゲート」節
// （consistencyGateHtml）と同じ語彙・視覚言語で統合表示することを確認する。同時に、既存の
// 要約可読性 — 見出し（検証失敗ラベル）・要約行（summary の <strong>）・詳細の折り畳み
// （<details>判断材料を見る</details>） — が落ちないことを、実画面相当の DOM で検証する。
//
// 実画面相当 = renderNeedFacts / renderNeedDetail の“本物”を、スタブでごまかさず合成した
// 出力（＝画面に出る HTML そのもの）をノード木へ起こして、要素の入れ子・クラス・テキストを
// 見る。DOM ライブラリ（jsdom 等）は依存に無いので、タグをスタックで積む最小パーサを同梱する。

const assert = require('assert');
const project = require('../src/main/project');
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

// ---- 実画面の描画関数を、依存も含めて“本物”で組み立てる ----
/* eslint-disable no-new-func */
const esc = new Function(`${grab('esc')}; return esc;`)();
const normalizeProse = new Function(`${grab('normalizeProse')}; return normalizeProse;`)();
const inlineMd = new Function('esc', `${grab('inlineMd')}; return inlineMd;`)(esc);
const prosePreview = new Function(
  'normalizeProse', 'inlineMd', `${grab('prosePreview')}; return prosePreview;`
)(normalizeProse, inlineMd);
const mdToHtml = new Function(
  'normalizeProse', 'esc', 'inlineMd', `${grab('mdToHtml')}; return mdToHtml;`
)(normalizeProse, esc, inlineMd);
const deliveryReviewState = new Function(`${grab('deliveryReviewState')}; return deliveryReviewState;`)();
const needFailureViewModel = new Function(`${grab('needFailureViewModel')}; return needFailureViewModel;`)();
const needGateSource = new Function(`${grab('needGateSource')}; return needGateSource;`)();
const consistencyGateHtml = new Function(
  'esc', `${grab('consistencyGateHtml')}; return consistencyGateHtml;`
)(esc);
const bindConsistencyGate = new Function(
  'api', 'guard', `${grab('bindConsistencyGate')}; return bindConsistencyGate;`
);
const renderNeedFacts = new Function(
  'needFailureViewModel', 'needGateSource', 'esc', 'inlineMd', 'prosePreview', 'deliveryReviewState',
  `${grab('renderNeedFacts')}; return renderNeedFacts;`
)(needFailureViewModel, needGateSource, esc, inlineMd, prosePreview, deliveryReviewState);

// renderNeedDetail は本物の renderNeedFacts / mdToHtml / esc を挿し、ヘッダ・操作・成果は
// スタブ。折り畳み（detailBlock）と「状況」節は renderNeedDetail のテンプレ本体なので、
// 本物の facts と同じカード上に出ることを確認できる。
const noop = () => '';
const renderNeedDetail = new Function(
  'isNeedSent', 'esc', 'needKindLabel', 'riskBadgeHtml', 'needDisplayTitle', 'NEED_ASK',
  'renderNeedFacts', 'needActionsHtml', 'specFilesHtml', 'mdToHtml', 'needVerifyRevisionHtml',
  'taskForNeed', 'taskCompletionHint', 'runsForTask', 'canDiagnoseNeed', 'needDecisionViewModel', 'state',
  'needFinalVerificationFailure', 'finalVerificationFailureHtml', 'needAssistActionsHtml',
  'needArtifactsButtonHtml', 'ownerBadgeHtml', 'commandFailureHtml', 'commandReceiptHtml',
  'reviewCommentsHtml', 'taskGuideHtml', 'needNextStepHtml',
  `${grab('renderNeedDetail')}; return renderNeedDetail;`
)(
  () => false, esc, () => '対応依頼', noop, (n) => String(n.title || n.id),
  { blocked: '対応方法を指示してください。' }, renderNeedFacts, () => '<div>回答欄</div>', noop,
  mdToHtml, noop, () => null, () => null, () => [], () => false,
  () => ({ actionTitle: '判断すること', nextStep: '対応方法を指示してください。' }), { flowRuns: [] },
  () => null, noop, () => '<button>AIに相談</button>', noop, noop, noop, noop, noop, noop, noop,
  (_n, ask) => `<section><h3>判断すること</h3>${ask}</section>`
);
/* eslint-enable no-new-func */

// ---- 依存ゼロの最小 DOM（タグをスタックで積むだけ）----
const VOID = new Set(['br', 'img', 'input', 'hr', 'meta', 'link', 'source', 'area', 'col',
  'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'svg']);
function parseDom(html) {
  const elements = [];
  const stack = [];
  let mismatch = 0; // 入れ子が閉じタグと一致しない（＝壊れた DOM）回数
  const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^"'>])*?)(\/?)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[5] !== undefined) {
      if (stack.length) stack[stack.length - 1].text += m[5];
      continue;
    }
    const tag = m[2].toLowerCase();
    if (m[1] === '/') {
      // 直近の開きタグと一致してこそ整形式。ずれていれば壊れた入れ子として数える。
      if (stack.length && stack[stack.length - 1].tag === tag) stack.pop();
      else mismatch += 1;
      continue;
    }
    const attrs = m[3] || '';
    const cls = (attrs.match(/class="([^"]*)"/) || ['', ''])[1].split(/\s+/).filter(Boolean);
    const node = {
      tag, attrs, classes: new Set(cls), text: '',
      ancestors: stack.slice(),
    };
    elements.push(node);
    if (!(m[4] === '/' || VOID.has(tag))) stack.push(node);
  }
  return { elements, openDepth: stack.length + mismatch };
}
const hasAncestor = (node, cls) => node.ancestors.some((a) => a.classes.has(cls));
function dom(html) {
  const { elements, openDepth } = parseDom(html);
  assert.strictEqual(openDepth, 0, '描画された HTML のタグが閉じていない（DOM が壊れている）');
  return {
    byClass: (cls) => elements.filter((e) => e.classes.has(cls)),
    byTag: (tag) => elements.filter((e) => e.tag === tag),
    one: (cls) => elements.find((e) => e.classes.has(cls)) || null,
  };
}

let passed = 0;
function test(name, fn) { fn(); passed += 1; console.log(`ok - ${name}`); }

test('概要は公式 consistencyGate 契約の3状態を読むだけで、開く操作も状態を変えない', () => {
  const states = [
    [{ regressionConfigured: true, intakeConfigured: true,
      regressionWired: true, intakeWired: true, wired: true,
      regressionCmd: 'codd-gate verify --base rev', intakeCmd: 'codd-gate tasks --debt' },
    '結線済み', 2],
    [{ regressionConfigured: true, intakeConfigured: false,
      regressionWired: true, intakeWired: false, wired: false,
      regressionCmd: 'codd-gate verify --base rev', intakeCmd: null },
    '一部結線', 1],
    [{ regressionConfigured: false, intakeConfigured: false,
      regressionWired: false, intakeWired: false, wired: false,
      regressionCmd: null, intakeCmd: null },
    '未結線', 0],
  ];
  for (const [gate, label, configured] of states) {
    gate.configFile = '/ws/.agents/agent-project.yaml';
    const payload = { consistencyGate: gate };
    Object.defineProperty(payload, 'regression_cmd', {
      get() { throw new Error('UI が公式 payload 外の設定を読んだ'); },
    });
    const before = JSON.stringify(gate);
    const html = consistencyGateHtml(payload);
    assert.ok(html.includes(`>${label}<`), `${label} が表示されない`);
    assert.strictEqual((html.match(/設定: あり/g) || []).length, configured);
    assert.strictEqual(JSON.stringify(gate), before, '描画が公式 payload を書き換えた');
  }

  let click;
  const opened = [];
  const gate = states[1][0];
  bindConsistencyGate(
    { openPath: (target) => opened.push(target) },
    (_label, action) => action()
  )({ querySelectorAll: (selector) => {
    assert.strictEqual(selector, 'button[data-gate-open]');
    return [{ dataset: { gateOpen: gate.configFile }, addEventListener: (_event, fn) => { click = fn; } }];
  } });
  click();
  assert.deepStrictEqual(opened, [gate.configFile], '有効化導線は設定ファイルを開くだけ');
  assert.strictEqual(gate.intakeConfigured, false, 'UI が未設定状態を楽観更新した');
});

// codd-gate 由来の回帰失敗（failureContext.command が codd-gate verify）。intake は未結線。
const gateNeed = {
  id: 'T-GATE', taskId: 'T-GATE', kind: 'blocked', decided: false,
  title: '一貫性ゲート失敗', date: '2026-07-21',
  why: '回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json` 失敗',
  failureSummary: '回帰検査に失敗しました（終了コード 2）。',
  failureResolution: 'ドキュメントとコードのズレを解消してください。',
  failureContext: {
    category: '検証工程', owner: '検査設定・実行環境',
    command: 'codd-gate verify --base abc123 --repos repos.json',
    workdir: '/work/app', exitCode: '2',
  },
  detail: '## 判断材料\n\n- 検証: `codd-gate verify` → FAIL（exit=2）',
};
const projectIntakeUnwired = {
  consistencyGate: {
    configFile: '/ws/.agents/agent-project.yaml',
    regressionWired: true, intakeWired: false, wired: false,
    regressionCmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json',
    intakeCmd: null,
  },
};

test('公式 needs failure 契約の codd-gate 回帰失敗を要約し、blocked のまま表示する', () => {
  const need = project.parseNeeds(`---
status: proposed
task-id: T-CONTRACT
kind: blocked
failure-phase: regression
verify-verdict: failed
failure-summary: codd-gate の回帰検査が失敗しました（終了コード 2）。
failure-resolution: ドキュメントとコードのズレを解消してください。
failure-category: 検証工程
failure-owner: 検査設定・実行環境
failure-command: codd-gate verify --base abc123 --repos repos.json
failure-exit: 2
---

# 要対応: T-CONTRACT — 一貫性ゲート失敗

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 \`codd-gate verify --base abc123 --repos repos.json\` 失敗
- 状態: blocked（agent-project の判断待ち）
`, 'T-CONTRACT');
  const d = dom(renderNeedFacts(projectIntakeUnwired, need));
  const summary = d.byTag('strong').find((e) => hasAncestor(e, 'need-diag'));
  assert.ok(summary && /codd-gate の回帰検査が失敗/.test(summary.text),
    'needs の failure-summary 契約を回帰要約として表示していない');
  assert.ok(d.one('need-gate'), '記録された codd-gate 回帰失敗を一貫性ゲートとして表示していない');
  assert.strictEqual(need.failurePhase, 'regression');
  assert.strictEqual(need.kind, 'blocked');
  assert.strictEqual(need.decided, false, '表示側が done 相当へ確定している');
});

// 由来は経路ごとに分かれる（regression = 完了前の回帰検査 / verify = タスク自身の検証）。
// `回帰検知` は codd-gate 以外の regression_cmd にも付くため、記録中の実コマンドと併せて
// 「回帰検査が止めた」と判断する。現在の結線状態は失敗時点の実行経路を上書きしない。
const gateBoth = { regressionWired: true, intakeWired: true, wired: true };
test('needGateSource は経路を分けて codd-gate 由来だけを拾う（断定側は触らない）', () => {
  assert.strictEqual(
    needGateSource(
      { summary: '完了前の回帰検査で一貫性ゲートが停止しました。',
        context: { command: 'codd-gate verify --base abc123' } },
      { failurePhase: 'regression', why: '回帰検査が失敗しました' },
      gateBoth),
    'regression', '構造化された failure-phase をタスク自身の verify より優先する');
  assert.strictEqual(
    needGateSource({ summary: '回帰検知: グローバル検査 `codd-gate verify` 失敗' }, {}, gateBoth),
    'regression');
  assert.strictEqual(
    needGateSource({ summary: '回帰検知: グローバル検査 `codd-gate verify` 失敗' }, {},
      { regressionWired: false, intakeWired: false, wired: false }),
    'regression', '失敗後に未結線となっても記録中の実コマンドを根拠に回帰経路を示す');
  assert.strictEqual(needGateSource({ context: { command: 'codd-gate verify' } }, {}, gateBoth), 'verify');
  assert.strictEqual(
    needGateSource({ summary: '回帰検知: codd-gate doctor が失敗' }, {}, gateBoth),
    null, 'verify 以外の codd-gate コマンドをゲート検査と断定しない');
  assert.strictEqual(
    needGateSource({ context: { command: 'echo codd-gate' } }, {}, gateBoth),
    null, '単なる codd-gate 言及をゲート検査と断定しない');
  assert.strictEqual(
    needGateSource({ summary: '回帰検知: グローバル検査 `make smoke` 失敗' }, {}, gateBoth),
    null, '現在は結線済みでも過去の別 regression_cmd 失敗を codd-gate と断定しない');
  assert.strictEqual(
    needGateSource({ summary: '回帰検知: グローバル検査 失敗' }, {},
      { regressionWired: false, intakeWired: false, wired: false }),
    null, 'regression_cmd 未結線なら回帰検知の文面だけで断定しない');
  assert.strictEqual(
    needGateSource({ summary: 'テストが 4 件失敗しました。', context: { command: 'pytest' } }, { why: '' }, gateBoth),
    null);
  assert.strictEqual(needGateSource(null, {}, gateBoth), null);
  assert.strictEqual(needGateSource({ context: { command: 'codd-gate verify' } }, {}, null), null);
});

test('統合表示: ゲート失敗の facts に「一貫性ゲート」節が概要と同じ語彙で出る', () => {
  const d = dom(renderNeedFacts(projectIntakeUnwired, gateNeed));
  const gate = d.one('need-gate');
  assert.ok(gate, 'ゲート由来の失敗に need-gate ブロックが出る');
  // 見出し語彙: 概要の consistencyGateHtml と同じ「一貫性ゲート」ラベルチップ
  const chip = d.byClass('label-chip').find((e) => hasAncestor(e, 'need-gate'));
  assert.ok(chip && chip.text.trim() === '一貫性ゲート', 'need-gate 内に「一貫性ゲート」ラベルチップ');
  // 概要ゲート節と同じ yaml キー語彙
  assert.match(gate.text + d.byClass('mono').map((e) => e.text).join(''),
    /regression_cmd/, 'regression_cmd を明示');
  // intake 未結線を、概要と同じ badge warn ＋ intake_cmd で示す
  const warn = d.byTag('span').find((e) => e.classes.has('badge') && e.classes.has('warn') && hasAncestor(e, 'need-gate'));
  assert.ok(warn, 'intake 未結線を badge warn で示す');
  const monoKeys = d.byClass('mono').filter((e) => hasAncestor(e, 'need-gate')).map((e) => e.text);
  assert.ok(monoKeys.includes('intake_cmd'), 'intake_cmd を明示');
  // 有効化操作は概要へ集約し、needs に同じ設定ファイル導線を重複表示しない。
  const open = d.byTag('button').find((e) => hasAncestor(e, 'need-gate') && /data-open="[^"]+agent-project\.yaml"/.test(e.attrs));
  assert.ok(!open, 'needs に概要と重複する設定ファイル導線を出さない');
});

test('可読性: 既存の検証失敗 見出し・要約行・context は落ちない', () => {
  const d = dom(renderNeedFacts(projectIntakeUnwired, gateNeed));
  const diag = d.one('need-diag');
  assert.ok(diag, 'need-diag（検証失敗ブロック）が残る');
  // 見出し: 「検証失敗」ラベルチップは従来どおり（ゲート統合で潰していない）
  const diagChip = d.byClass('label-chip').find((e) => hasAncestor(e, 'need-diag'));
  assert.ok(diagChip && diagChip.text.trim() === '検証失敗', '検証失敗ラベルは従来どおり');
  // 要約行: summary の <strong> が need-diag 内に残る
  const strong = d.byTag('strong').find((e) => hasAncestor(e, 'need-diag'));
  assert.ok(strong && /回帰検査に失敗しました/.test(strong.text), '要約行の strong が残る');
  // context の dl が残る
  assert.ok(d.one('need-failure-context'), 'need-failure-context の dl が残る');
});

test('実画面相当: 詳細カードで折り畳み（詳しい判断材料を見る）とゲート節が共存する', () => {
  const card = renderNeedDetail(projectIntakeUnwired, gateNeed);
  const d = dom(card);
  // 折り畳み: <details class="need-detail"><summary>詳しい判断材料を見る</summary>
  const details = d.byTag('details').find((e) => e.classes.has('need-detail'));
  assert.ok(details, '判断材料の <details> 折り畳みが残る');
  const summary = d.byTag('summary').find((e) => hasAncestor(e, 'need-detail'));
  assert.ok(summary && summary.text.trim() === '詳しい判断材料を見る', 'summary 文言が残る');
  // 見出し: 「状況」「判断すること」の h3 が残る
  const h3 = d.byTag('h3').map((e) => e.text.trim());
  assert.ok(h3.includes('状況') && h3.includes('判断すること'), 'セクション見出し h3 が残る');
  // 統合: 同じカード上に need-gate が出る（facts 節の中）
  assert.ok(d.one('need-gate') && hasAncestor(d.one('need-gate'), 'need-facts'), 'ゲート節は状況(need-facts)の中');
});

test('intake 結線済みなら起票される旨を出し、開くボタンは出さない', () => {
  const wired = { consistencyGate: { configFile: '/ws/.agents/agent-project.yaml', regressionWired: true, intakeWired: true, wired: true, regressionCmd: 'x', intakeCmd: 'y' } };
  const d = dom(renderNeedFacts(wired, gateNeed));
  const gate = d.one('need-gate');
  assert.ok(gate && /正常に実行された場合.*起票します/.test(gate.text + d.byClass('badge').map((e) => e.text).join('')),
    '結線済みでも正常実行時だけ起票すると示す');
  const open = d.byTag('button').find((e) => hasAncestor(e, 'need-gate'));
  assert.ok(!open, '両方結線済みなら開くボタンは出さない');
});

// ペイロード無し（旧 main と組み合わせた場合）は概要タブ側のゲート節も空になる。存在しない
// セクションへ誘導しないため、needs 側も何も足さない——既存の失敗要約はそのまま残る。
test('consistencyGate 未提供ならゲート節を出さない（要約は従来どおり）', () => {
  const d = dom(renderNeedFacts({}, gateNeed));
  assert.ok(!d.one('need-gate'), 'ペイロード無しで結線状態を騙らない');
  assert.ok(d.one('need-diag'), '検証失敗の要約は残る');
});

test('ゲート由来でない検証失敗にはゲート節を出さない（要約は従来どおり）', () => {
  const plain = {
    id: 'T2', kind: 'blocked',
    failureSummary: 'テストが 4 件失敗しました。',
    failureContext: { category: 'テスト失敗', command: 'pytest', exitCode: '1' },
  };
  const d = dom(renderNeedFacts(projectIntakeUnwired, plain));
  assert.ok(!d.one('need-gate'), '通常の検証失敗にゲート節を出さない');
  assert.ok(d.one('need-diag'), '検証失敗の要約は従来どおり出る');
  const chip = d.byClass('label-chip').find((e) => hasAncestor(e, 'need-diag'));
  assert.ok(chip && chip.text.trim() === '検証失敗');
});

test('失敗が無い要対応にはゲート節も検証失敗ブロックも出さない', () => {
  const d = dom(renderNeedFacts(projectIntakeUnwired, { id: 'T3', kind: 'blocked', why: '人の判断が必要' }) || '<span></span>');
  assert.ok(!d.one('need-gate') && !d.one('need-diag'), '失敗要約が無ければどちらも出さない');
});

console.log(`\n${passed} passed`);
