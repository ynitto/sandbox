'use strict';

// 工程列 ⇄ 定義の往復。護るもの:
//   1. コンパイルは作成モードの原則どおり（1 ステート 1 工程・出力契約・分岐は遷移・check ゲート・末尾の単一指示・スキル名指し）。
//   2. 読み戻しは maker.json があれば正確に、無くても YAML から工程列を起こし、表せない部分は原文で保持する。
//   3. 構造検査は engine.validate_workflow と同じ規則で落とす。
//   4. 生成物は OS に依らない（`/` 区切り・LF・シェル無し）。

const { test } = require('node:test');
const assert = require('node:assert');
const YAML = require('yaml');
const model = require('../src/main/model');

function sample() {
  return {
    name: '月次の勤怠集計', machine: 'kintai-monthly', purpose: '勤怠システムから月次集計を出し、差し戻し候補を一覧にする',
    steps: [
      { kind: 'windows', title: '集計を出力', target: '勤怠管理', detail: 'メニュー「集計」→「月次」を開き {{month}} を入れて「出力」を押す',
        check: 'winauto wait name:=完了 --app 勤怠管理', checkRetries: 2 },
      { kind: 'browser', target: 'https://intra.example/list', detail: '申請一覧を開き {{month}} の行を読み取る',
        recorded: [{ op: 'goto', target: 'https://intra.example/list', label: 'x' },
          { op: 'fill', target: "getByRole('textbox', { name: '対象月' })", role: 'textbox', label: '対象月', value: '{{month}}', example: '2026-09' }] },
      { kind: 'command', target: 'python scripts/export.py --month {{month}}' },
      { kind: 'skill', target: 'redmine-use', detail: '{{month}} のチケット一覧を取得する' },
      { kind: 'agent', title: '判断', detail: '差し戻しが要るか判断する。{{last_output}} を材料にする',
        outcomes: [{ label: 'APPROVED', to: 'next' }, { label: 'RETRY ONCE', to: 'step:2' }, { label: 'NG', to: 'abort' }] },
    ],
  };
}

test('正規化: ID の採番・ラベルの空白畳み・予約語を除いた入力パラメータ', () => {
  const spec = model.normalizeProcedure(sample());
  assert.deepStrictEqual(spec.steps.map((s) => s.id), ['step_1', 'step_2', 'step_3', 'step_4', 'step_5']);
  assert.deepStrictEqual(spec.parameters, ['month']);
  assert.strictEqual(spec.steps[4].outcomes[1].label, 'RETRY_ONCE');
  assert.strictEqual(spec.steps[0].checkRetries, 2);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [] }), /工程を 1 つ以上/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'command', target: 'ls | wc' }] }), /シェル記号/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'agent', detail: 'a', check: 'x' }, { kind: 'agent', detail: 'b', outcomes: [{ label: 'A', to: 'step:9' }] }] }), /存在しない工程 9/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'agent', detail: 'a', id: 'complete' }] }), /予約/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'agent', detail: 'a', id: 'a' }, { kind: 'agent', detail: 'b', id: 'a' }] }), /重複/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', machine: '../x', steps: [{ kind: 'agent', detail: 'a' }] }), /識別名/);
  assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'agent', detail: 'a', recorded: [{ op: 'click', target: 'e15' }] }] }), /持てません/);
});

test('コンパイル: 作成モードの原則どおりの YAML と actions を書く', () => {
  const spec = model.normalizeProcedure(sample());
  const { workflow, files } = model.compile(spec);
  assert.strictEqual(workflow.initial_state, 'step_1');
  assert.deepStrictEqual(Object.keys(workflow.states), ['step_1', 'step_2', 'step_3', 'step_4', 'step_5', 'complete', 'failed']);
  // 検査を宣言した工程は事実（check_ok）で進み、モデルの OK は材料に入らない
  assert.deepStrictEqual(workflow.transitions.filter((t) => t.from === 'step_1'), [{ from: 'step_1', to: 'step_2', condition_rule: 'equals:check_ok:true', priority: 1 }]);
  assert.strictEqual(workflow.states.step_1.check, 'winauto wait name:=完了 --app 勤怠管理');
  assert.strictEqual(workflow.states.step_1.check_retries, 2);
  // 既定の出力契約は OK / FAILED
  assert.strictEqual(workflow.states.step_2.output_validator, 'startswith:OK,FAILED');
  assert.deepStrictEqual(workflow.transitions.filter((t) => t.from === 'step_2').map((t) => [t.to, t.condition_rule]),
    [['step_3', 'startswith:last_output:OK'], ['failed', 'startswith:last_output:FAILED']]);
  // 判断のラベルは output_validator と遷移に写る（分岐は本文に書かない）
  assert.strictEqual(workflow.states.step_5.output_validator, 'startswith:APPROVED,RETRY_ONCE,NG');
  assert.deepStrictEqual(workflow.transitions.filter((t) => t.from === 'step_5').map((t) => t.to), ['complete', 'step_2', 'failed']);
  assert.ok(!files['actions/step_5.md'].includes('RETRY_ONCE →'), '本文に遷移先を書かない');
  // 本文: スキルの名指し・対象・記録した操作・末尾の単一指示
  const win = files['actions/step_1.md'];
  assert.ok(win.startsWith('## [step_1: 集計を出力]\n'));
  assert.ok(win.includes('`windows-app-automation` スキル'));
  assert.ok(win.includes('対象アプリ: 勤怠管理'));
  assert.ok(win.trim().endsWith(model.TRAILER));
  const br = files['actions/step_2.md'];
  assert.ok(br.includes('`playwright-cli` スキル') && br.includes('対象 URL: https://intra.example/list'));
  assert.ok(br.includes("- `fill getByRole('textbox', { name: '対象月' }) \"{{month}}\"`（記録時の値の例: 2026-09）"));
  assert.ok(files['actions/step_3.md'].includes('`python scripts/export.py --month {{month}}`'));
  assert.ok(files['actions/step_4.md'].includes('`redmine-use` スキルに任せます'));
  assert.ok(files['actions/step_5.md'].includes('**出力形式:** 第1行に APPROVED / RETRY_ONCE / NG のいずれか一語'));
  // OS に依らない: LF・`/` 区切り
  for (const body of Object.values(files)) assert.ok(!body.includes('\r'));
  assert.ok(Object.keys(files).every((k) => !k.includes('\\')));
  // YAML として読み戻せ、構造検査を通る
  const parsed = YAML.parse(files['workflow.yaml']);
  assert.strictEqual(parsed.config.max_steps, 30);
  assert.deepStrictEqual(model.validateWorkflow(parsed, files), []);
  const side = JSON.parse(files['maker.json']);
  assert.strictEqual(side.tool, 'statemachine-maker');
  assert.strictEqual(side.steps.length, 5);
});

test('遷移の説明: 画面が見せる「どこへ行くか」', () => {
  const spec = model.normalizeProcedure(sample());
  assert.deepStrictEqual(model.stepTransitions(spec, 4).map((t) => [t.label, t.target, model.describeTarget(spec, 4, t.to)]),
    [['APPROVED', 'complete', '完了'], ['RETRY_ONCE', 'step_2', '工程 2 へ戻る'], ['NG', 'failed', '失敗として終了']]);
  assert.deepStrictEqual(model.stepTransitions(spec, 0).map((t) => [t.when, t.gated, t.target]), [['always', true, 'step_2']]);
  assert.strictEqual(model.describeTarget(spec, 1, 'next'), '工程 3 へ');
  assert.strictEqual(model.describeTarget(spec, 4, 'next'), '完了');
});

test('往復: maker.json があれば工程列が正確に戻り、無くても YAML から起こせる', () => {
  const spec = model.normalizeProcedure(sample());
  const { files } = model.compile(spec);
  const exact = model.decompile({ workflowText: files['workflow.yaml'], files, makerJson: files['maker.json'] });
  assert.deepStrictEqual(exact.warnings, []);
  assert.deepStrictEqual(model.normalizeProcedure({ ...exact.raw, machine: 'kintai-monthly' }).steps, spec.steps);

  const fromYaml = model.decompile({ workflowText: files['workflow.yaml'], files });
  assert.deepStrictEqual(fromYaml.warnings, []);
  const back = model.normalizeProcedure({ ...fromYaml.raw, machine: 'kintai-monthly' });
  assert.deepStrictEqual(back.steps.map((s) => [s.id, s.kind, s.title, s.target, s.check]), [
    ['step_1', 'windows', '集計を出力', '勤怠管理', 'winauto wait name:=完了 --app 勤怠管理'],
    ['step_2', 'browser', '', 'https://intra.example/list', ''],
    ['step_3', 'command', '', 'python scripts/export.py --month {{month}}', ''],
    ['step_4', 'skill', '', 'redmine-use', ''],
    ['step_5', 'agent', '判断', '', ''],
  ]);
  assert.strictEqual(back.steps[0].detail, 'メニュー「集計」→「月次」を開き {{month}} を入れて「出力」を押す', '定型（案内・守ること・出力形式・末尾指示）は外れる');
  assert.deepStrictEqual(back.steps[4].outcomes, [
    { when: 'label', label: 'APPROVED', to: 'next' },
    { when: 'label', label: 'RETRY_ONCE', to: 'step:2' },
    { when: 'label', label: 'NG', to: 'abort' }]);
  assert.deepStrictEqual(back.steps[1].outcomes, [], '既定の OK/FAILED は判断にしない');
  assert.strictEqual(back.steps[0].checkRetries, 2);
  // 読み戻したものを再コンパイルしても同じ YAML になる（安定）
  const again = model.compile(back);
  assert.strictEqual(again.files['workflow.yaml'], files['workflow.yaml']);
});

test('読み戻し: 文章の条件も無条件もそのまま画面の行になり、書き戻しで元へ戻る', () => {
  const text = `
name: レビュー
initial_state: analyze
context:
  retry_count: 0
config:
  max_steps: 12
  verbose: true
states:
  analyze:
    description: 分析する
    action: |
      コードを分析し、第1行に PASS / MAJOR を返す。
    output_key: analysis_result
  fix:
    description: 直す
    action: 直してください
    write: src/x.py
  approve:
    description: 承認
    terminal: true
  rejected:
    description: 差し戻し
    terminal: true
  error:
    description: 致命的エラー
    terminal: true
transitions:
  - from: analyze
    to: approve
    condition_rule: "startswith:last_output:PASS"
    priority: 1
  - from: analyze
    to: fix
    condition: "分析結果が MAJOR を含む"
    priority: 2
  - from: fix
    to: analyze
    priority: 1
  - from: "*"
    to: error
    condition: "FATAL が含まれる"
    priority: 100
`;
  const { raw, warnings } = model.decompile({ workflowText: text, files: {} });
  assert.deepStrictEqual(raw.steps.map((s) => [s.id, s.rawTransitions]), [['analyze', false], ['fix', false]],
    '手で書いた条件でも画面の行にする（原文のまま抱えない）');
  assert.deepStrictEqual(raw.steps[0].outcomes, [
    { when: 'label', label: 'PASS', to: 'done' },
    { when: 'text', text: '分析結果が MAJOR を含む', to: 'next' }]);
  assert.deepStrictEqual(raw.steps[1].outcomes, [{ when: 'always', to: 'step:1' }], '無条件の遷移は「いつでも」');
  assert.deepStrictEqual(warnings, [], '手で書いた条件・無条件・終端のどれも知らせることが無い');
  // 完了・中止のほかの終わり方も行き先として選べる
  assert.deepStrictEqual(raw.ends, [{ id: 'error', description: '致命的エラー' }]);

  const spec = model.normalizeProcedure({ ...raw, machine: 'review' });
  const { workflow, files } = model.compile(spec);
  // 遷移は数も中身も元のまま（ワイルドカードは保持、条件の文章はそのまま）
  assert.deepStrictEqual(workflow.transitions, [
    { from: 'analyze', to: 'approve', condition_rule: 'startswith:last_output:PASS', priority: 1 },
    { from: 'analyze', to: 'fix', condition: '分析結果が MAJOR を含む', priority: 2 },
    { from: 'fix', to: 'analyze', priority: 1 },
    { from: '*', to: 'error', condition: 'FATAL が含まれる', priority: 100 },
  ]);
  // 行き先が名前だけで決まらない工程に、第 1 行の契約を足さない（元も持っていない）
  assert.strictEqual(workflow.states.analyze.output_validator, undefined);
  assert.strictEqual(workflow.states.analyze.output_key, 'analysis_result');
  assert.strictEqual(workflow.states.fix.write, 'src/x.py');
  assert.ok(workflow.states.error && workflow.states.error.terminal);
  assert.deepStrictEqual(workflow.context, { retry_count: 0 });
  assert.strictEqual(workflow.config.verbose, true);
  assert.deepStrictEqual(model.validateWorkflow(workflow, files), []);
});

test('読み戻し: 元の出力契約は、書き直さない工程では消さずに持ち回る', () => {
  const text = `
name: x
initial_state: analyze
states:
  analyze:
    action: x
    output_validator: "startswith:PASS,MAJOR"
    max_retries: 2
  fix:
    action: y
  approve: {description: 承認, terminal: true}
  rejected: {description: 差し戻し, terminal: true}
transitions:
  - {from: analyze, to: approve, condition_rule: "startswith:last_output:PASS", priority: 1}
  - {from: analyze, to: fix, condition: "MAJOR を含む", priority: 2}
  - {from: fix, to: analyze, priority: 1}
`;
  const { raw } = model.decompile({ workflowText: text, files: {} });
  const { workflow } = model.compile(model.normalizeProcedure({ ...raw, machine: 'x' }));
  assert.strictEqual(workflow.states.analyze.output_validator, 'startswith:PASS,MAJOR');
  assert.strictEqual(workflow.states.analyze.max_retries, 2);
});

test('4 つの決め方（名前・文章・いつでも・条件式）を書き分ける', () => {
  const spec = model.normalizeProcedure({ name: 'x', machine: 'x', steps: [
    { kind: 'agent', detail: 'a', outcomes: [
      { when: 'label', label: 'OK2', to: 'done' },
      { when: 'text', text: '出力に「保留」が含まれる', to: 'step:2' },
      { when: 'rule', rule: 'startswith:last_output:RETRY;lt:retry_count:3', to: 'step:1' },
      { when: 'always', to: 'abort' },
    ] },
    { kind: 'agent', detail: 'b', check: 'python check.py', outcomes: [
      { when: 'label', label: 'DONE', to: 'done' },
      { when: 'text', text: 'まだ残っている', to: 'step:1' },
    ] },
  ] });
  const { workflow } = model.compile(spec);
  assert.deepStrictEqual(workflow.transitions.filter((t) => t.from === 'step_1'), [
    { from: 'step_1', to: 'complete', condition_rule: 'startswith:last_output:OK2', priority: 1 },
    { from: 'step_1', to: 'step_2', condition: '出力に「保留」が含まれる', priority: 2 },
    { from: 'step_1', to: 'step_1', condition_rule: 'startswith:last_output:RETRY;lt:retry_count:3', priority: 3 },
    { from: 'step_1', to: 'failed', priority: 4 },
  ]);
  // 検査のある工程では、決定論の側にだけ関門を足す（文章の条件と混ぜると文章が読まれない）
  assert.deepStrictEqual(workflow.transitions.filter((t) => t.from === 'step_2'), [
    { from: 'step_2', to: 'complete', condition_rule: 'equals:check_ok:true;startswith:last_output:DONE', priority: 1 },
    { from: 'step_2', to: 'step_1', condition: 'まだ残っている', priority: 2 },
  ]);
  // 往復しても同じ定義に戻る（行き先の書き方は「次へ」に揃うので、定義そのもので比べる）
  const { files } = model.compile(spec);
  const back = model.decompile({ workflowText: files['workflow.yaml'], files });
  assert.deepStrictEqual(back.warnings, []);
  assert.deepStrictEqual(back.raw.steps.map((s) => s.outcomes.map((o) => o.when)),
    [['label', 'text', 'rule', 'always'], ['label', 'text']]);
  const again = model.compile(model.normalizeProcedure({ ...back.raw, machine: 'x' }));
  assert.strictEqual(again.files['workflow.yaml'], files['workflow.yaml']);
  // 空の値は断る
  for (const bad of [{ when: 'text', text: '', to: 'next' }, { when: 'rule', rule: '', to: 'next' }, { when: 'label', label: '', to: 'next' }]) {
    assert.throws(() => model.normalizeProcedure({ name: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [bad] }] }), /空の行/);
  }
});

test('終わり方がいくつあっても行き先に選べる（2 つに決め打たない）', () => {
  const text = `
name: 仕分け
initial_state: classify
states:
  classify:
    action: 仕分ける
  handle_bug:
    description: バグとして扱う
    action: x
    terminal: true
  handle_feature:
    description: 要望として扱う
    action: y
    terminal: true
  unknown:
    description: 判別できない
    action: z
    terminal: true
transitions:
  - {from: classify, to: handle_bug, condition_rule: "startswith:last_output:BUG", priority: 1}
  - {from: classify, to: handle_feature, condition: "要望に見える", priority: 2}
  - {from: classify, to: unknown, priority: 3}
`;
  const { raw, warnings } = model.decompile({ workflowText: text, files: {} });
  assert.deepStrictEqual(warnings, []);
  assert.deepStrictEqual(raw.steps.map((s) => s.rawTransitions), [false]);
  assert.deepStrictEqual(raw.ends.map((e) => e.id), ['handle_feature', 'unknown'], '最初の 2 つは完了と中止に割り当てる');
  assert.deepStrictEqual(raw.steps[0].outcomes, [
    { when: 'label', label: 'BUG', to: 'next' },
    { when: 'text', text: '要望に見える', to: 'end:handle_feature' },
    { when: 'always', to: 'end:unknown' }]);
  const spec = model.normalizeProcedure({ ...raw, machine: 'triage' });
  const { workflow, files } = model.compile(spec);
  assert.deepStrictEqual(workflow.transitions.map((t) => t.to), ['handle_bug', 'handle_feature', 'unknown']);
  for (const id of ['handle_bug', 'handle_feature', 'unknown']) assert.ok(workflow.states[id].terminal, id);
  assert.deepStrictEqual(model.validateWorkflow(workflow, files), []);
  // 定義に無い終わり方は行き先にできない
  assert.throws(() => model.normalizeProcedure({ ...raw, machine: 'triage',
    steps: [{ ...raw.steps[0], outcomes: [{ when: 'always', to: 'end:nope' }] }] }), /行き先が不正/);
});

test('元の定義にあった終わり方は、行き先から外れても中身ごと残す', () => {
  const text = `
name: 仕分け
initial_state: classify
states:
  classify:
    action: 仕分ける
  handle_bug:
    description: バグとして扱う
    action: |
      バグとして記録してください。
    terminal: true
  handle_feature:
    description: 要望として扱う
    action: 要望として記録してください。
    terminal: true
transitions:
  - {from: classify, to: handle_bug, condition_rule: "startswith:last_output:BUG", priority: 1}
  - {from: classify, to: handle_feature, priority: 2}
`;
  const { raw } = model.decompile({ workflowText: text, files: {} });
  // どこからも行かなくなっても、終わり方は消えない（本文を持つ終端がある）
  raw.steps[0].outcomes = [{ when: 'always', to: 'end:handle_feature' }];
  const { workflow } = model.compile(model.normalizeProcedure({ ...raw, machine: 'triage' }));
  assert.ok(workflow.states.handle_bug, '行き先から外れた終わり方が消えた');
  assert.match(workflow.states.handle_bug.action, /バグとして記録/, '終わり方の本文が消えた');
  assert.strictEqual(workflow.states.handle_bug.terminal, true);
  // 新しく作った定義には、誰も行かない終わり方を足さない
  const fresh = model.normalizeProcedure({ name: 'x', machine: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [{ when: 'always', to: 'done' }] }] });
  assert.deepStrictEqual(Object.keys(model.compile(fresh).workflow.states), ['step_1', 'complete']);
});

test('構造検査: engine.validate_workflow と同じ規則で落とす', () => {
  const errors = model.validateWorkflow({
    initial_state: 'nope',
    states: { a: { action_file: 'actions/a.md' }, b: { check: 'ls | wc' }, t: { terminal: true, check: 'x' } },
    transitions: [{ from: 'a', to: 'zzz', condition_rule: 'equals:check_ok:true' }, { from: 'b', to: 't' }],
  }, { 'actions/x.md': '' });
  assert.ok(errors.some((e) => e.includes("initial_state 'nope'")));
  assert.ok(errors.some((e) => e.includes("未知のステート 'zzz'")));
  assert.ok(errors.some((e) => e.includes("'t' は check を持てません")));
  assert.ok(errors.some((e) => e.includes("'b' の check にシェル記号")));
  assert.ok(errors.some((e) => e.includes("'a' の action_file が見つかりません")));
  assert.ok(errors.some((e) => e.includes("ステート 'a' に check がありません")), errors.join('\n'));
});

test('移植性の注意: シェル・バックスラッシュ・exe・戻る遷移', () => {
  const spec = model.normalizeProcedure({ name: 'x', steps: [
    { kind: 'command', target: 'test -s out\\report.csv' },
    { kind: 'skill', target: 'redmine-use', detail: 'a', check: 'C:/tools/check.exe' },
    { kind: 'agent', detail: 'b', outcomes: [{ label: 'AGAIN', to: 'step:1' }] },
  ] });
  const warnings = model.portabilityWarnings(spec);
  assert.ok(warnings.some((w) => w.startsWith('工程 1 のコマンド: シェル組み込み')));
  assert.ok(warnings.some((w) => w.includes('区切りは `/`')));
  assert.ok(warnings.some((w) => w.includes('Windows 専用の実行ファイル')));
  assert.ok(warnings.some((w) => w.includes('「AGAIN」は工程 1 へ戻ります')));
});
