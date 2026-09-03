'use strict';

// 手順ビルダー（定型手順）: 画面操作（ブラウザ / Windows アプリ）・コマンド実行・AI の処理を
// 並べた工程列を、statemachine-use の作成モードへ渡す指示文へ決定的に変換する。
//
// 護るもの:
//   1. 画面が組んだ工程列は main が検査する（分岐先・シェル記号・必須項目）。
//   2. 指示文はスキルの分解原則に沿う（移譲先スキルの名指し・check の宣言・分岐は遷移）。
//      **YAML は書かない**（書式の正典は statemachine-use スキル。C7）。
//   3. 作成の起動は自由文と同じ入口（generateStateMachine の payload.procedure）を通す。
//   4. 道具の確認は LLM を使わない診断コマンドだけを呼び、結果を人が読める 1 行にする。
//   5. 工程の種類の正典は main の種別カタログ 1 か所で、画面はそれを IPC で受け取って描く
//      （種類を足すときに画面へ写しを足さない）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'routine-procedure-home-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

const procedure = require('../src/features/cowork/main/procedure');
const cowork = require('../src/features/cowork/main/cowork');
const preload = require('../src/features/cowork/preload');
const rendererSrc = require('./helpers/renderer-src').read();
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');

let passed = 0;
function test(name, fn) {
  const done = fn();
  const finish = () => { passed += 1; console.log(`ok - ${name}`); };
  return done && typeof done.then === 'function' ? done.then(finish) : Promise.resolve(finish());
}

function sample() {
  return {
    purpose: '毎月 1 日に勤怠システムから月次集計を出し、差し戻し候補を一覧にする',
    notes: '承認・却下は押さない',
    steps: [
      { kind: 'windows', title: '集計を出力', target: '勤怠管理',
        detail: 'メニュー「集計」→「月次」を開き {{month}} を入れて「出力」を押す',
        check: 'winauto wait name:=完了 --app 勤怠管理' },
      { kind: 'browser', target: 'https://intra.example/list', detail: '申請一覧を開き {{month}} の行を読み取る' },
      { kind: 'command', target: 'python3 scripts/export.py --month {{month}}' },
      { kind: 'skill', target: 'redmine-use', detail: '{{month}} のチケット一覧を取得する' },
      { kind: 'agent', title: '判断', detail: '差し戻しが要るか判断する。{{last_output}} を材料にする',
        outcomes: [{ label: 'APPROVED', to: 'next' }, { label: 'RETRY ONCE', to: 'step:2' }, { label: 'NG', to: 'abort' }] },
    ],
  };
}

async function main() {
  test('工程列を正規化し、入力パラメータは予約語を除いて拾う', () => {
    const spec = procedure.normalizeProcedure(sample());
    assert.strictEqual(spec.version, procedure.PROCEDURE_VERSION, '保存する工程列には版を付ける');
    assert.deepStrictEqual(spec.steps.map((s) => s.id), ['step_1', 'step_2', 'step_3', 'step_4', 'step_5']);
    assert.strictEqual(spec.steps[1].title, '', '名前を省いた工程は空のまま（見出しは種類で代える）');
    assert.deepStrictEqual(spec.parameters, ['month'], '{{last_output}} は実行時変数なので入力にしない');
    assert.strictEqual(spec.steps[4].outcomes[1].label, 'RETRY_ONCE', 'ラベルの空白は _ にする（第 1 行の startswith 比較）');
    assert.strictEqual(spec.steps[4].outcomes[1].to, 'step:2');
    assert.strictEqual(procedure.skillFor(spec.steps[0]), 'windows-app-automation', '種類に固定の移譲先');
    assert.strictEqual(procedure.skillFor(spec.steps[3]), 'redmine-use', '工程が名前で指定する移譲先');
    assert.strictEqual(procedure.skillFor(spec.steps[2]), '', 'コマンド実行は移譲しない');
    assert.throws(() => procedure.normalizeProcedure({ ...sample(), version: 99 }), /対応していない版/);
  });

  test('不備は人が直せる文言で断る', () => {
    const cases = [
      [{ purpose: 'x', steps: [] }, /工程を 1 つ以上/],
      [{ purpose: '', steps: [{ kind: 'agent', detail: 'a' }] }, /目的を入力/],
      [{ purpose: 'x', steps: [{ kind: 'gui', detail: 'a' }] }, /種類が不正/],
      [{ purpose: 'x', steps: [{ kind: 'browser', detail: '' }] }, /内容を入力/],
      [{ purpose: 'x', steps: [{ kind: 'command', target: '' }] }, /コマンドを入力/],
      [{ purpose: 'x', steps: [{ kind: 'command', target: 'ls | wc -l' }] }, /シェル記号は使えません/],
      [{ purpose: 'x', steps: [{ kind: 'browser', detail: 'a', check: 'echo x > out' }] }, /シェル記号は使えません/],
      [{ purpose: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [{ label: 'A', to: 'step:9' }] }] }, /存在しない工程 9/],
      [{ purpose: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [{ label: 'A', to: 'jump' }] }] }, /行き先が不正/],
      [{ purpose: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [{ label: 'A', to: 'next' }, { label: 'A', to: 'done' }] }] }, /重複/],
      [{ purpose: 'x', steps: [{ kind: 'agent', detail: 'a', outcomes: [{ label: '', to: 'next' }] }] }, /ラベルの無い/],
      [{ purpose: 'x', steps: [{ kind: 'skill', target: '', detail: 'a' }] }, /スキル名を入力/],
      [{ purpose: 'x', steps: [{ kind: 'skill', target: 'Redmine Use', detail: 'a' }] }, /スキル名が不正/],
    ];
    for (const [raw, re] of cases) {
      assert.throws(() => procedure.normalizeProcedure(raw), re, JSON.stringify(raw));
    }
  });

  test('指示文はスキルの分解原則に沿い、YAML を書かない', () => {
    const spec = procedure.normalizeProcedure(sample());
    const text = procedure.procedureInstruction(spec);
    // 移譲先スキルの名指し（この記法が無いとハーネスはスキルを読み込まない）
    assert.ok(text.includes('`windows-app-automation` スキル'));
    assert.ok(text.includes('`playwright-cli` スキル'));
    assert.ok(text.includes('`winauto` コマンド'));
    assert.ok(text.includes('アクション本文で `redmine-use` スキルへ移譲すると明記する'), 'スキルに任せる工程は名前で名指し');
    assert.ok(!text.includes('対象（スキル名）'), 'スキル名は移譲先として書き、対象欄には出さない');
    // 決定的検査の宣言と、宣言した工程からの遷移条件
    assert.ok(text.includes('`check: winauto wait name:=完了 --app 勤怠管理`'));
    assert.ok(text.includes('equals:check_ok:true'));
    // 分岐は遷移に書く。行き先の言い換え
    assert.ok(text.includes('APPROVED / RETRY_ONCE / NG'));
    assert.ok(text.includes('RETRY_ONCE → 工程 2 へ進む'));
    assert.ok(text.includes('NG → 失敗として終了する'));
    assert.ok(text.includes('APPROVED → 完了として終了する'), '最後の工程の next は完了');
    assert.ok(text.includes('SKILL.md に書かれた使い方'), '使う種類の案内だけ載る');
    // 名前を省いた工程は種類だけの見出し
    assert.ok(text.includes('### 工程 2: 画面操作（ブラウザ）\n'));
    assert.ok(!text.includes('画面操作（ブラウザ）（画面操作'));
    // コマンド工程は argv をそのまま
    assert.ok(text.includes('`python3 scripts/export.py --month {{month}}`'));
    // 入力パラメータと注意事項
    assert.ok(text.includes('- {{month}}'));
    assert.ok(text.includes('注意事項: 承認・却下は押さない'));
    // YAML の骨組みは書かない（書式の正典はスキル）
    for (const yamlKey of ['initial_state:', '\nstates:', '\ntransitions:', 'action_file:']) {
      assert.ok(!text.includes(yamlKey), `指示文に YAML を書かない: ${yamlKey}`);
    }
  });

  test('画面操作の無い手順には道具の案内を出さない', () => {
    const spec = procedure.normalizeProcedure({ purpose: 'x', steps: [{ kind: 'agent', detail: '要約する' }] });
    const text = procedure.procedureInstruction(spec);
    assert.ok(!text.includes('playwright-cli') && !text.includes('winauto') && !text.includes('SKILL.md'));
    assert.ok(text.includes('OK（できた）または FAILED'), '判断の無い工程は OK / FAILED の契約');
    assert.ok(!text.includes('## 入力パラメータ'), 'パラメータが無ければ節を出さない');
  });

  test('確認（preview）は起動せず、自由文と同じ作成プロンプトへ載る', () => {
    const res = cowork.procedurePreview({}, { name: '月次集計', machine: 'monthly-summary', procedure: sample() });
    assert.deepStrictEqual(res.parameters, ['month']);
    assert.ok(res.prompt.startsWith('statemachine-use スキルの作成モードで、次の指示から「月次集計」ステートマシンを作成してください。'));
    assert.ok(res.prompt.includes('.statemachine/monthly-summary/'));
    assert.ok(res.prompt.includes(res.instruction));
  });

  test('作成の起動は自由文と同じ入口で、工程列の不備は Git を触る前に断る', () => {
    assert.throws(() => cowork.generateStateMachine({}, {
      repo: HOME_STUB, name: 'x', machine: 'x', procedure: { purpose: 'x', steps: [] },
    }), /工程を 1 つ以上/);
    assert.throws(() => cowork.generateStateMachine({}, {
      repo: HOME_STUB, name: 'x', machine: '../x', procedure: sample(),
    }), /識別名が不正/);
  });

  await test('道具の確認は診断コマンドだけを呼び、結果を 1 行に畳む', async () => {
    const calls = [];
    const capture = async (command, args) => {
      calls.push([command, ...args]);
      if (command === 'winauto') {
        return { ok: false, status: 1, stdout: JSON.stringify({ scope: 'wsl', ok: false,
          checks: [{ name: 'interop', ok: true }, { name: 'pywinauto', ok: false, detail: 'not installed' }] }), stderr: '' };
      }
      return { ok: true, status: 0, stdout: '0.4.2\n', stderr: '' };
    };
    const all = await procedure.toolStatus({ cwd: '', kinds: ['browser', 'windows'], capture });
    assert.deepStrictEqual(calls, [['playwright-cli', '--version'], ['winauto', 'doctor', '--output', 'json']]);
    assert.strictEqual(all[0].ok, true);
    assert.ok(all[0].summary.includes('0.4.2'));
    assert.strictEqual(all[1].ok, false);
    assert.ok(all[1].summary.includes('pywinauto'), all[1].summary);
    assert.ok(all[1].hint, '未準備の道具には入れ方を添える');

    const onlyBrowser = await procedure.toolStatus({ kinds: ['browser', 'skill', 'command', 'agent'], capture });
    assert.deepStrictEqual(onlyBrowser.map((t) => t.id), ['playwright-cli'], '工程が頼る道具だけを確かめる（診断の無い種類は飛ばす）');
    assert.deepStrictEqual(procedure.toolsFor([]).map((t) => t.id), ['playwright-cli', 'winauto'], '種類を渡さなければ診断できる道具すべて');

    const missing = await procedure.toolStatus({ kinds: ['windows'],
      capture: async () => ({ ok: false, status: -1, stdout: '', stderr: '', error: 'spawn winauto ENOENT' }) });
    assert.strictEqual(missing[0].ok, false);
    assert.ok(missing[0].summary.includes('起動できません'));
  });

  test('IPC の入口は確認・道具の 2 つだけで、作成は既存チャネルを使う', () => {
    const invoked = [];
    const invoke = (channel, payload) => { invoked.push([channel, payload]); return Promise.resolve(); };
    preload.coworkProcedureCatalog(invoke)();
    preload.coworkProcedurePreview(invoke)({ procedure: {} });
    preload.coworkProcedureTools(invoke)({ repo: '/r', kinds: ['browser'] });
    assert.deepStrictEqual(invoked.map(([c]) => c), ['cowork:procedureCatalog', 'cowork:procedurePreview', 'cowork:procedureTools']);
    assert.strictEqual(typeof preload.coworkProcedureCreate, 'undefined', '作成の入口を増やさない');
    const ipc = fs.readFileSync(path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'ipc.js'), 'utf8');
    assert.ok(ipc.includes("handle('cowork:procedureCatalog'") && ipc.includes("handle('cowork:procedurePreview'")
      && ipc.includes("handle('cowork:procedureTools'"));
    assert.ok(!ipc.includes('cowork:procedureCreate'));
  });

  test('画面の入口: 作業タブの「手順を組み立てる」・設定変更ダイアログ・工程列を持つ項目の作り直し', () => {
    assert.ok(html.includes('<dialog id="dlg-routine-procedure"'));
    assert.ok(html.includes('id="btn-cw-procedure"'), '手順付き作業の自由文欄から組み立て画面へ移れる');
    assert.ok(rendererSrc.includes('id="btn-cowork-procedure"'));
    assert.ok(rendererSrc.includes('data-cowork-procedure="${index}"'));
    assert.ok(rendererSrc.includes('function openRoutineProcedureDialog('));
    const builderFile = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'sections', 'procedure.js'), 'utf8');
    assert.ok(builderFile.includes('function openRoutineProcedureDialog('), 'ビルダーは独立したセクションファイルに置く');
    assert.ok(html.includes('<script src="sections/procedure.js"></script>'));
    for (const forbidden of ['loopProvider', 'agent-loop.yml', 'coworkRunStateMachine', 'coworkSaveWork']) {
      assert.ok(!builderFile.includes(forbidden), `ビルダーは実行・保存の経路に触れない: ${forbidden}`);
    }
    assert.ok(rendererSrc.includes('function createRoutineProcedure('));
    // 作成は自由文と同じ API を通す（payload.procedure）
    const create = rendererSrc.slice(rendererSrc.indexOf('function routineProcedureItem('),
      rendererSrc.indexOf('function openRoutineProcedureDialog('));
    assert.ok(create.includes('api.coworkGenerateStateMachine({'));
    assert.ok(create.includes('procedure: routineProcedurePayload(draft)'));
    assert.ok(create.includes("type: 'state-machine'"));
    assert.ok(create.includes('coworkDraft().push(item)'), '起動後は作業項目として残し「変更を保存」で予定を登録できる');
    assert.ok(!create.includes('workflow.yaml'), '画面は YAML を書かない');
    // 内部名を UI の文言に出さない
    const dialog = html.slice(html.indexOf('<dialog id="dlg-routine-procedure"'),
      html.indexOf('</dialog>', html.indexOf('<dialog id="dlg-routine-procedure"')));
    assert.ok(!dialog.includes('ステートマシン') && !dialog.includes('statemachine'));
  });

  test('工程の種類の正典は main の種別カタログで、画面は写しを持たない', () => {
    const cat = cowork.procedureCatalog();
    assert.strictEqual(cat.version, procedure.PROCEDURE_VERSION);
    assert.deepStrictEqual(cat.kinds.map((k) => k.id), procedure.STEP_KIND_IDS);
    for (const kind of cat.kinds) {
      assert.ok(kind.label && kind.description && kind.detail && typeof kind.detail.required === 'boolean', kind.id);
      for (const hidden of ['skill', 'guidance', 'tool', 'skillFromTarget']) {
        assert.ok(!(hidden in kind), `画面へ渡すカタログに ${hidden} は含めない（${kind.id}）`);
      }
    }
    assert.strictEqual(cat.kinds.find((k) => k.id === 'agent').check, null, 'AI の処理に確認コマンド欄は無い');
    assert.strictEqual(cat.kinds.find((k) => k.id === 'skill').target.required, true);
    // 画面は種類を書き写さず、カタログ（state.procedureCatalog）から描く
    const builder = rendererSrc.slice(rendererSrc.indexOf('function routineProcedureCatalog('));
    assert.ok(!/const ROUTINE_STEP_KINDS\b/.test(rendererSrc));
    assert.ok(builder.includes('api.coworkProcedureCatalog()'));
    for (const label of ['画面操作（ブラウザ）', 'Windows アプリ', 'スキルに任せる', 'コマンド実行']) {
      assert.ok(!builder.includes(`'${label}`), `種類の表示名を画面に書かない: ${label}`);
    }
    assert.ok(builder.includes('kind.target.label') && builder.includes('kind.detail.label') && builder.includes('kind.check.placeholder'));
  });

  test('判断欄の 1 行は「ラベル: 行き先」で、行き先を省けば次へ進む', () => {
    const at = rendererSrc.indexOf('function parseRoutineOutcomes(');
    const end = rendererSrc.indexOf('\n}\n', at) + 3;
    // eslint-disable-next-line no-new-func
    const parse = new Function(`${rendererSrc.slice(at, end)}\nreturn parseRoutineOutcomes;`)();
    assert.deepStrictEqual(parse('APPROVED: next\nREJECTED → step:1\nERROR=abort\nSKIP\n'), [
      { label: 'APPROVED', to: 'next' },
      { label: 'REJECTED', to: 'step:1' },
      { label: 'ERROR', to: 'abort' },
      { label: 'SKIP', to: 'next' },
    ]);
  });

  console.log(`\n${passed} routine-procedure tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
