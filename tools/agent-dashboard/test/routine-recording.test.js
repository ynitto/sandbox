'use strict';

// 操作の記録（Recording）: 人が画面でやった操作の記録（playwright-cli の recording-stop の出力 /
// winauto の操作イベント JSONL）を、手順ビルダーの工程列へ決定的に変換する。
//
// 護るもの:
//   1. 記録の読み取りは決定的で、要素は role と名前で残す（ref・座標は残さない）。
//   2. 入力した値は `{{key}}` の入力パラメータになり、パスワードらしい欄の値は例にも残らない。
//   3. 工程の区切りは goto / ウィンドウの切り替わり / 確定の操作（ボタン・リンク・Enter）で置く。
//   4. 記録を持つ工程の指示文には「記録した操作」と汎用化の案内が載り、YAML は書かない。
//   5. 記録の開始・終了は playwright-cli の記録コマンドだけを呼び、返すのは工程列だけ
//      （作成の入口は generateStateMachine の 1 本のまま）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'routine-recording-home-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

const recording = require('../src/features/cowork/main/recording');
const procedure = require('../src/features/cowork/main/procedure');
const cowork = require('../src/features/cowork/main/cowork');
const preload = require('../src/features/cowork/preload');
const rendererSrc = require('./helpers/renderer-src').read();

let passed = 0;
function test(name, fn) {
  const done = fn();
  const finish = () => { passed += 1; console.log(`ok - ${name}`); };
  return done && typeof done.then === 'function' ? done.then(finish) : Promise.resolve(finish());
}

// playwright-cli 0.1.19 の `recording-stop` が実際に印字した形（検証時の実測）。
const BROWSER_RECORDING = `### Result
Recording stopped. Recorded actions:

\`\`\`js
await page.goto('https://intra.example/login');
await page.getByRole('textbox', { name: 'ユーザー名' }).click();
await page.getByRole('textbox', { name: 'ユーザー名' }).fill('taro');
await page.getByRole('textbox', { name: 'パスワード' }).fill('p@ss');
await page.getByRole('button', { name: 'ログイン' }).click();
await page.getByRole('button', { name: 'ログイン' }).press('Enter');
await page.getByRole('button', { name: 'ログイン' }).click();
await page.goto('https://intra.example/list');
await page.getByRole('textbox', { name: '対象月' }).click();
await page.getByRole('textbox', { name: '対象月' }).fill('2026-09');
await page.getByLabel('種別 通常緊急').selectOption('緊急');
await page.getByRole('checkbox', { name: '同意' }).check();
await page.getByRole('link', { name: '次へ' }).click();
await page.getByRole('row', { name: '山田 太郎' }).first().click();
\`\`\`
### Page
- Page URL: https://intra.example/list
`;

const WINAUTO_RECORDING = [
  '{"event":"launch","app":"勤怠管理","path":"C:\\\\Apps\\\\kintai.exe"}',
  '{"event":"window","app":"勤怠管理","window":"月次集計"}',
  '{"event":"value","app":"勤怠管理","window":"月次集計","control_type":"Edit","name":"対象月","auto_id":"txtMonth","value":"2026-09"}',
  '{"event":"select","app":"勤怠管理","window":"月次集計","control_type":"ComboBox","name":"種別","value":"通常"}',
  '{"event":"toggle","app":"勤怠管理","window":"月次集計","control_type":"CheckBox","name":"確定済みを含む","value":"on"}',
  '{"event":"invoke","app":"勤怠管理","window":"月次集計","control_type":"Button","name":"出力","auto_id":"btnExport"}',
  '{"event":"window","app":"勤怠管理","window":"完了"}',
  '{"event":"invoke","app":"勤怠管理","window":"完了","control_type":"Button","name":"OK"}',
  '# コメント行と壊れた行は捨てる',
  'not json',
].join('\n');

async function main() {
  test('Playwright コード行を操作へ読む（role と名前を残し、ref を作らない）', () => {
    const ops = recording.parsePlaywrightRecording(BROWSER_RECORDING);
    assert.strictEqual(ops.length, 14);
    assert.deepStrictEqual(ops[0], { op: 'goto', target: 'https://intra.example/login', label: 'https://intra.example/login', value: '' });
    assert.deepStrictEqual(ops[2], { op: 'fill', target: "getByRole('textbox', { name: 'ユーザー名' })", role: 'textbox', label: 'ユーザー名', value: 'taro' });
    assert.strictEqual(ops[10].op, 'select');
    assert.strictEqual(ops[10].label, '種別 通常緊急');
    assert.strictEqual(ops[13].target, "getByRole('row', { name: '山田 太郎' }).first()", 'ロケータの連鎖はそのまま残す');
    assert.strictEqual(recording.parsePlaywrightLine("await expect(page).toHaveURL(/list/);"), null, '断言や読めない行は捨てる');
    assert.strictEqual(recording.parsePlaywrightLine("await page.getByRole('button', { name: 'x' }).click();").value, '');
    assert.deepStrictEqual(recording.parsePlaywrightLine("await page.keyboard.press('Enter');"),
      { op: 'press', target: '', label: '', value: 'Enter' });
    assert.strictEqual(recording.parsePlaywrightLine("await page.getByText('It\\'s').click();").label, "It's", 'エスケープを解く');
  });

  test('工程は goto と確定の操作で切れ、値は {{key}} になり、パスワードは例にも残らない', () => {
    const res = recording.stepsFromRecording({ source: 'browser', text: BROWSER_RECORDING });
    assert.strictEqual(res.source, 'browser');
    assert.deepStrictEqual(res.steps.map((s) => s.title), ['「ログイン」ボタンを押す', '「次へ」リンクを押す', '「山田 太郎」を押す']);
    assert.deepStrictEqual(res.steps.map((s) => s.target), ['https://intra.example/login', 'https://intra.example/list', 'https://intra.example/list']);
    const login = res.steps[0];
    assert.strictEqual(login.kind, 'browser');
    assert.ok(login.detail.includes('{{input_1}}') && login.detail.includes('（記録時の例: taro）'), login.detail);
    assert.ok(login.detail.includes('{{input_2}}') && !login.detail.includes('p@ss'), 'パスワードは例にも残らない');
    assert.ok(!JSON.stringify(res).includes('p@ss'));
    assert.deepStrictEqual(login.recorded.map((o) => o.op), ['goto', 'fill', 'fill', 'click'],
      'fill 直前の click（フォーカス）と、click 直後の同じボタンへの Enter・click の連打は落とす');
    assert.strictEqual(login.recorded[1].example, 'taro');
    assert.strictEqual(login.recorded[2].example, undefined);
    assert.deepStrictEqual(res.parameters, ['input_1', 'input_2', 'input_3']);
    assert.strictEqual(res.steps[1].recorded.find((o) => o.op === 'select').value, '緊急', 'select / check は選択肢＝定数のまま');
    assert.strictEqual(login.check, '', 'ブラウザには argv で測れる確認コマンドが無いので空のまま');
    assert.throws(() => recording.stepsFromRecording({ source: 'browser', text: '### Page\n- Page URL: x\n' }), /読み取れませんでした/);
    assert.throws(() => recording.stepsFromRecording({ source: 'gui', text: 'x' }), /ブラウザか Windows アプリ/);
  });

  test('winauto の操作イベント（JSONL）はウィンドウの切り替わりで切れ、確認コマンドに wait を置く', () => {
    const res = recording.stepsFromRecording({ source: 'windows', text: WINAUTO_RECORDING, app: '勤怠管理' });
    assert.strictEqual(res.steps.length, 3);
    assert.deepStrictEqual(res.steps.map((s) => s.kind), ['windows', 'windows', 'windows']);
    assert.deepStrictEqual(res.steps.map((s) => s.target), ['勤怠管理', '勤怠管理', '勤怠管理']);
    const summary = res.steps[1];
    assert.strictEqual(summary.title, '「出力」ボタンを押す');
    assert.ok(summary.detail.startsWith('1. 「月次集計」ウィンドウが前面になる\n2. 「対象月」入力欄に {{input_1}} を入力する（記録時の例: 2026-09）'), summary.detail);
    assert.strictEqual(summary.recorded[1].target, 'auto_id:=txtMonth', 'auto_id があればそれを優先する');
    assert.strictEqual(summary.recorded[2].target, 'name:=種別 >> control:=ComboBox', '無ければ name と control');
    assert.strictEqual(summary.recorded[3].op, 'check');
    assert.strictEqual(res.steps[0].recorded[0].op, 'launch');
    // 確認コマンドは**この工程がもたらした変化**を測る。押す前から出ているウィンドウを
    // 待っても何も検知しないので、見るのは「次の工程が始まったときのウィンドウ」。
    assert.strictEqual(res.steps[0].check, 'winauto wait name:=月次集計 --app 勤怠管理', '起動は主画面が出たことで測る');
    assert.strictEqual(summary.check, 'winauto wait name:=完了 --app 勤怠管理', '「出力」を押した結果は完了画面で測る');
    assert.strictEqual(res.steps[2].check, '', '次が無い最後の工程には確認を置かない');
    // ウィンドウ名にシェル記号があると工程列ごと投入前に落ちるので、確認を作らない。
    const meta = recording.stepsFromRecording({ source: 'windows', app: '勤怠管理', text: [
      '{"event":"invoke","app":"a","window":"w1","control_type":"Button","name":"実行"}',
      '{"event":"window","app":"a","window":"完了 | 集計"}',
      '{"event":"invoke","app":"a","window":"完了 | 集計","control_type":"Button","name":"OK"}',
    ].join('\n') });
    assert.strictEqual(meta.steps[0].check, '');
  });

  test('記録を持つ工程は正規形に残り、指示文に「記録した操作」と汎用化の案内が載る（YAML は書かない）', () => {
    const rec = recording.stepsFromRecording({ source: 'browser', text: BROWSER_RECORDING });
    const spec = procedure.normalizeProcedure({ purpose: '月次の申請一覧を読む', steps: rec.steps });
    assert.strictEqual(spec.version, 2, '記録を持てる版');
    assert.deepStrictEqual(spec.parameters, ['input_1', 'input_2', 'input_3'], '記録の値の {{key}} も入力パラメータ');
    assert.strictEqual(spec.steps[0].recorded.length, 4);
    const text = procedure.procedureInstruction(spec);
    assert.ok(text.includes('- 記録した操作（人がやった順。要素は記録どおり role と名前で指す）:'));
    assert.ok(text.includes("- `fill getByRole('textbox', { name: 'ユーザー名' }) \"{{input_1}}\"` （記録時の値の例: taro）"), text);
    assert.ok(text.includes("- `click getByRole('button', { name: 'ログイン' })`"));
    assert.ok(text.includes('`playwright-cli <操作> "<ロケータ式>" [値]` として実行できる'), 'ブラウザは記録の行がそのまま argv になる');
    assert.ok(!text.includes('winauto click'), 'ブラウザだけの手順に Windows の案内は出さない');
    assert.ok(!text.includes('p@ss'));
    assert.ok(text.includes('記録に無い操作を足さない'));
    assert.ok(text.includes('snapshot の ref（e15 など）や座標を定義に書かない'));
    assert.ok(text.includes('例を既定値にしない'));
    for (const yamlKey of ['initial_state:', '\nstates:', '\ntransitions:', 'action_file:']) {
      assert.ok(!text.includes(yamlKey), `指示文に YAML を書かない: ${yamlKey}`);
    }
    // 記録の無い手順には記録の案内を載せない
    const plain = procedure.procedureInstruction(procedure.normalizeProcedure({ purpose: 'x', steps: [{ kind: 'browser', detail: 'a' }] }));
    assert.ok(!plain.includes('記録した操作') && !plain.includes('記録に無い操作を足さない'));
    // Windows の記録は winauto の綴りで載る
    const win = recording.stepsFromRecording({ source: 'windows', text: WINAUTO_RECORDING, app: '勤怠管理' });
    const winText = procedure.procedureInstruction(procedure.normalizeProcedure({ purpose: 'x', steps: win.steps }));
    // 記録の行は「何が起きたか」で、そのまま打てる argv ではない（綴りを騙らない）。
    assert.ok(winText.includes('- `fill "auto_id:=txtMonth" "{{input_1}}"` （記録時の値の例: 2026-09）'), winText);
    assert.ok(winText.includes('- `click "auto_id:=btnExport"`'));
    assert.ok(winText.includes('- `select "name:=種別 >> control:=ComboBox" "通常"`'));
    assert.ok(winText.includes('`check: winauto wait name:=完了 --app 勤怠管理`'));
    // 種類ごとの対応表が載り、無いコマンドは無いと書く
    assert.ok(winText.includes('`click` / `check` / `uncheck` → `winauto click`'), winText);
    assert.ok(winText.includes('`select` → `winauto select <セレクタ> <項目名>`'), winText);
    assert.ok(!winText.includes('`playwright-cli <操作>'), 'ブラウザの案内は Windows だけの手順に出さない');
  });

  test('記録の不備は投入前に断る（ref・座標・記録を持てない種類・件数）', () => {
    const base = (recorded, kind = 'browser') => ({ purpose: 'x', steps: [{ kind, detail: 'a', recorded }] });
    assert.throws(() => procedure.normalizeProcedure(base([{ op: 'click', target: 'e15' }])), /ref（e15）/);
    assert.throws(() => procedure.normalizeProcedure(base([{ op: 'tap', target: 'x' }])), /操作が不正/);
    assert.throws(() => procedure.normalizeProcedure(base([{ op: 'click', target: 'x' }], 'agent')), /記録を持てません/);
    assert.throws(() => procedure.normalizeProcedure(base(Array.from({ length: procedure.MAX_RECORDED + 1 }, () => ({ op: 'click', target: 'x' })))), /件までです/);
    const ok = procedure.normalizeProcedure(base([{ op: 'click', target: "getByRole('button', { name: 'x' })", label: 'x', role: 'button', extra: 'dropped' }]));
    assert.deepStrictEqual(ok.steps[0].recorded, [{ op: 'click', target: "getByRole('button', { name: 'x' })", label: 'x', role: 'button' }]);
    assert.deepStrictEqual(procedure.normalizeProcedure({ purpose: 'x', steps: [{ kind: 'agent', detail: 'a' }] }).steps[0].recorded, [], '記録の無い工程は空配列');
    assert.deepStrictEqual(procedure.normalizeProcedure({ version: 1, purpose: 'x', steps: [{ kind: 'browser', detail: 'a' }] }).steps[0].recorded, [], '版 1 の項目も読める');
  });

  await test('記録の開始・終了は playwright-cli の記録コマンドだけを呼び、工程列を返す', async () => {
    const calls = [];
    const capture = async (command, args) => {
      calls.push([command, ...args]);
      if (args.includes('recording-stop')) return { ok: true, status: 0, stdout: BROWSER_RECORDING, stderr: '' };
      return { ok: true, status: 0, stdout: '', stderr: '' };
    };
    const started = await recording.recordBrowserStart({ cwd: '/r', url: 'https://intra.example/login', capture });
    assert.strictEqual(started.ok, true);
    assert.deepStrictEqual(calls, [
      ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'open', '--headed', 'https://intra.example/login'],
      ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'recording-start'],
    ], '人が操作するので見える形で開き、記録専用のセッション名で作業のセッションと混ぜない');
    calls.length = 0;
    const stopped = await recording.recordBrowserStop({ cwd: '/r', capture });
    assert.deepStrictEqual(calls.map((c) => c[2]), ['recording-stop', 'close']);
    assert.strictEqual(stopped.steps.length, 3);
    assert.deepStrictEqual(stopped.parameters, ['input_1', 'input_2', 'input_3']);

    await assert.rejects(recording.recordBrowserStop({ capture: async () => ({ ok: true, status: 0, stdout: 'Recording stopped. No actions were recorded.', stderr: '' }) }),
      /操作が記録されていません/);
    await assert.rejects(recording.recordBrowserStart({ capture: async () => ({ ok: false, status: -1, stdout: '', stderr: '', error: 'spawn playwright-cli ENOENT' }) }),
      /ブラウザを開けませんでした: spawn playwright-cli ENOENT/);
    // 開けない原因ごとに次の一手を添える（win32 の dashboard は WSL 側の CLI を呼ぶので、
    // 表示先が無い環境では必ずここで落ちる。「開けませんでした」だけでは動きようがない）。
    await assert.rejects(recording.recordBrowserStart({ capture: async () => ({
      ok: false, status: 1, stdout: '', stderr: 'Looks like you launched a headed browser without having a XServer running.' }) }),
    /WSLg/);
    assert.ok(recording.browserOpenHint('Executable doesn\'t exist at /root/.cache/ms-playwright').includes('install-browser'));
    assert.strictEqual(recording.browserOpenHint('なにか別の失敗'), '', '心当たりが無いときは足さない');
    await assert.rejects(cowork.procedureRecording({}, { action: 'dance' }), /記録の操作が不正/);
    const imported = await cowork.procedureRecording({}, { action: 'import', source: 'windows', text: WINAUTO_RECORDING, app: '勤怠管理' });
    assert.strictEqual(imported.steps.length, 3, '貼り付けは CLI を呼ばずに変換だけ行う');
  });

  await test('Windows の記録は winauto record を別ウィンドウで起こし、停止ファイルで止める', async () => {
    recording.resetWindowsRecording();
    const calls = [];
    const windows = [];
    const capture = async (command, args) => {
      calls.push([command, ...args]);
      if (command === 'cat') return { ok: true, status: 0, stdout: WINAUTO_RECORDING, stderr: '' };
      return { ok: true, status: 0, stdout: '', stderr: '' };
    };
    const openWindow = (spec) => { windows.push(spec); return { ok: true }; };
    const started = await recording.recordWindowsStart({
      cwd: '/r', app: '勤怠管理', capture, openWindow, id: 'test1' });
    assert.strictEqual(started.ok, true);
    assert.strictEqual(started.app, '勤怠管理');
    assert.deepStrictEqual(calls, [['mkdir', '-p', recording.RECORD_DIR]], '置き場を作ってから起こす');
    assert.deepStrictEqual(windows[0].args, ['record', '--app', '勤怠管理',
      '--output', `${recording.RECORD_DIR}/record-test1.jsonl`,
      '--stop-file', `${recording.RECORD_DIR}/record-test1.stop`]);
    assert.strictEqual(windows[0].command, 'winauto');
    assert.strictEqual(windows[0].cwd, '/r');
    // 一時ファイルの綴りは main が決めて覚える（画面から来たパスで読み書きしない）
    assert.strictEqual(recording.windowsRecordingState().out, `${recording.RECORD_DIR}/record-test1.jsonl`);
    await assert.rejects(recording.recordWindowsStart({ app: 'x', capture, openWindow }), /すでに記録中/);

    calls.length = 0;
    const stopped = await recording.recordWindowsStop({ capture, wait: async () => {} });
    assert.deepStrictEqual(calls.map((c) => c[0]), ['touch', 'cat', 'cat', 'rm'],
      '停止ファイルを置き、2 回続けて同じ中身になるまで読み、後片付けする');
    assert.strictEqual(calls[0][1], `${recording.RECORD_DIR}/record-test1.stop`);
    assert.strictEqual(stopped.steps.length, 3, '読めた JSONL はそのまま工程列になる');
    assert.strictEqual(recording.windowsRecordingState(), null);
    await assert.rejects(recording.recordWindowsStop({ capture }), /記録が始まっていません/);
  });

  await test('Windows の記録: 開始に失敗したら記録中にしない。空の記録は書き出し先を添えて断る', async () => {
    recording.resetWindowsRecording();
    const capture = async () => ({ ok: true, status: 0, stdout: '', stderr: '' });
    await assert.rejects(recording.recordWindowsStart({ app: 'x', capture,
      openWindow: () => ({ ok: false, error: '端末が見つかりません' }) }), /端末が見つかりません/);
    assert.strictEqual(recording.windowsRecordingState(), null, '失敗したら枠を返す（もう一度押せる）');

    await recording.recordWindowsStart({ app: 'x', capture, openWindow: () => ({ ok: true }), id: 'empty' });
    await assert.rejects(recording.recordWindowsStop({ capture, wait: async () => {} }),
      /記録が空です[\s\S]*record-empty\.jsonl/);
    assert.strictEqual(recording.windowsRecordingState(), null);

    recording.resetWindowsRecording();
    await assert.rejects(recording.recordWindowsStart({ app: '', capture, openWindow: () => ({ ok: true }) }),
      /アプリ（ウィンドウ名・プロセス名・PID）を入力してください/);
  });

  await test('main の振り分け: source で記録の種類を分け、cwd は登録済みフォルダに限る', async () => {
    recording.resetWindowsRecording();
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'cowork.js'), 'utf8');
    const fn = src.slice(src.indexOf('function procedureRecording('), src.indexOf('function generateStateMachine('));
    assert.ok(fn.includes("source === 'windows'"));
    assert.ok(fn.includes('recording.recordWindowsStart({ cwd, app: payload.app,'));
    assert.ok(fn.includes('openWindow: runCommandWindow'));
    assert.ok(fn.includes('recording.recordWindowsStop({ capture: runCommandCapture })'));
    assert.ok(!/payload\.(out|stop|output|file)/.test(fn), '一時ファイルのパスを画面から受け取らない');
    await assert.rejects(cowork.procedureRecording({}, { action: 'stop', source: 'windows' }),
      /記録が始まっていません/);
  });

  test('配線: IPC は記録の 1 チャネルだけ増え、作成の入口は増えない。画面は記録の入口と工程の記録表示を持つ', () => {
    const invoked = [];
    const invoke = (channel, payload) => { invoked.push([channel, payload]); return Promise.resolve(); };
    preload.coworkProcedureRecording(invoke)({ action: 'start', repo: '/r', url: 'https://x' });
    assert.deepStrictEqual(invoked.map(([c]) => c), ['cowork:procedureRecording']);
    assert.strictEqual(typeof preload.coworkProcedureCreate, 'undefined');
    const ipc = fs.readFileSync(path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'ipc.js'), 'utf8');
    assert.ok(ipc.includes("handle('cowork:procedureRecording'"));
    assert.ok(!ipc.includes('cowork:procedureCreate'));
    // 画面のカタログには記録を持てる種類の印だけ渡す（記録の綴りは main に残す）
    const cat = cowork.procedureCatalog();
    assert.deepStrictEqual(cat.kinds.filter((k) => k.recordable).map((k) => k.id), ['browser', 'windows']);
    for (const kind of cat.kinds) assert.ok(!('recordedLine' in kind), '綴りの関数は画面へ渡さない');
    const builder = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'sections', 'procedure.js'), 'utf8');
    for (const needle of ['id="btn-rp-rec-start"', 'id="btn-rp-rec-stop"', 'id="btn-rp-rec-import"', 'function routineRecording(',
      'routine-procedure-recorded', 'data-rp-unrecord', 'api.coworkProcedureRecording(',
      'id="rp-rec-source"', 'id="rp-rec-target"']) {
      assert.ok(builder.includes(needle), needle);
    }
    // 開始・終了・貼り付けはどれも記録の種類を送る（main が種類で振り分ける）
    const send = builder.slice(builder.indexOf('async function routineRecording('));
    assert.ok(send.includes('const payload = { action, repo: draft.repo, source: rec.source, url: rec.url, app: rec.app };'));
    assert.ok(builder.includes('winauto record --app'), '貼り付けの案内に取り方を書く');
    assert.ok(builder.includes('recorded: Array.isArray(step.recorded) ? step.recorded : []'), '記録は工程と一緒に持ち回る');
    for (const forbidden of ['loopProvider', 'agent-loop.yml', 'coworkRunStateMachine', 'coworkSaveWork', 'workflow.yaml']) {
      assert.ok(!builder.includes(forbidden), `ビルダーは実行・保存の経路に触れない: ${forbidden}`);
    }
    assert.ok(rendererSrc.includes('function routineStepFromRecorded('));
    const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');
    assert.ok(css.includes('.routine-procedure-recording') && css.includes('.routine-procedure-recorded'));
  });

  console.log(`\n${passed} routine-recording tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
