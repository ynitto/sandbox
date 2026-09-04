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
  assert.deepStrictEqual(model.stepTransitions(spec, 0).map((t) => [t.label, t.gated, t.target]), [['', true, 'step_2']]);
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
  assert.deepStrictEqual(back.steps[4].outcomes, [{ label: 'APPROVED', to: 'next' }, { label: 'RETRY_ONCE', to: 'step:2' }, { label: 'NG', to: 'abort' }]);
  assert.deepStrictEqual(back.steps[1].outcomes, [], '既定の OK/FAILED は判断にしない');
  assert.strictEqual(back.steps[0].checkRetries, 2);
  // 読み戻したものを再コンパイルしても同じ YAML になる（安定）
  const again = model.compile(back);
  assert.strictEqual(again.files['workflow.yaml'], files['workflow.yaml']);
});

test('読み戻し: 画面で表せないステート・遷移・context は原文のまま保持して書き戻す', () => {
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
    condition_rule: "startswith:analysis_result:PASS"
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
  assert.deepStrictEqual(raw.steps.map((s) => [s.id, s.kind, s.title]), [['analyze', 'agent', '分析する'], ['fix', 'agent', '直す']]);
  // 自然言語条件が 1 つでもある工程は、遷移をすべて原文で持つ（半分だけ生成すると意味が変わる）
  assert.deepStrictEqual(raw.steps.map((s) => [s.rawTransitions, s.outcomes]), [[true, []], [true, []]]);
  assert.deepStrictEqual(raw.terminals, { done: { id: 'approve', description: '承認' }, abort: { id: 'rejected', description: '差し戻し' } });
  assert.ok(warnings.some((w) => w.includes("'analyze' の遷移は画面で編集できない")), warnings.join('\n'));
  assert.ok(warnings.some((w) => w.includes("'error' は画面で編集できない")));
  assert.strictEqual(raw.preserved.transitions.length, 4, '原文の遷移はすべて保持');
  assert.strictEqual(raw.preserved.files.stateExtras.analyze.output_key, 'analysis_result');
  assert.deepStrictEqual(raw.preserved.context, { retry_count: 0 });
  assert.strictEqual(raw.preserved.files.stateExtras.analyze.output_key, 'analysis_result');
  assert.strictEqual(raw.preserved.files.stateExtras.fix.write, 'src/x.py');

  const spec = model.normalizeProcedure({ ...raw, machine: 'review' });
  const { workflow, files } = model.compile(spec);
  assert.ok(workflow.states.error && workflow.states.error.terminal, '保持したステートが残る');
  assert.deepStrictEqual(workflow.context, { retry_count: 0 });
  assert.strictEqual(workflow.config.verbose, true);
  assert.strictEqual(workflow.config.max_steps, 12);
  assert.strictEqual(workflow.states.analyze.output_key, 'analysis_result');
  assert.strictEqual(workflow.states.fix.write, 'src/x.py');
  assert.ok(workflow.transitions.some((t) => t.from === '*' && t.to === 'error'));
  assert.ok(workflow.transitions.some((t) => t.from === 'analyze' && t.to === 'fix' && t.condition));
  assert.strictEqual(workflow.transitions.length, 4, '遷移は増えない（既定の OK/FAILED を足さない）');
  assert.strictEqual(workflow.states.analyze.output_validator, undefined, '原文に無い出力契約を足さない');
  assert.deepStrictEqual(model.validateWorkflow(workflow, files), []);
  // 画面で編集できる形へ戻すと、既定の遷移が付く
  const editable = model.normalizeProcedure({ ...raw, machine: 'review', preserved: { ...raw.preserved, transitions: raw.preserved.transitions.filter((t) => t.from !== 'fix') },
    steps: raw.steps.map((s) => (s.id === 'fix' ? { ...s, rawTransitions: false } : s)) });
  const w2 = model.buildWorkflow(editable);
  assert.deepStrictEqual(w2.transitions.filter((t) => t.from === 'fix').map((t) => t.to), ['approve', 'rejected']);
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
