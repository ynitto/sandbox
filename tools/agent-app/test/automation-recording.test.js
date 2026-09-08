'use strict';

// 記録 → 工程列。規則は agent-dashboard と同じ（要素は role と名前・値は {{key}}・確定の操作で切る）。
// 記録の開始と終了は PATH の playwright-cli / winauto を直接呼ぶ（差し替えた関数で確かめる）。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const recording = require('../src/main/automation/recording');
const model = require('../src/main/automation/model');

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

test('Playwright のコード行を操作へ読む（role と名前を残す）', () => {
  const ops = recording.parsePlaywrightRecording(BROWSER_RECORDING);
  assert.strictEqual(ops.length, 14);
  assert.deepStrictEqual(ops[2], { op: 'fill', target: "getByRole('textbox', { name: 'ユーザー名' })", role: 'textbox', label: 'ユーザー名', value: 'taro' });
  assert.strictEqual(ops[13].target, "getByRole('row', { name: '山田 太郎' }).first()");
  assert.strictEqual(recording.parsePlaywrightLine('await expect(page).toHaveURL(/list/);'), null);
});

test('ブラウザの記録は goto と確定で切れ、値は {{key}} になり、パスワードは例にも残らない', () => {
  const res = recording.stepsFromRecording({ source: 'browser', text: BROWSER_RECORDING });
  assert.deepStrictEqual(res.steps.map((s) => s.title), ['「ログイン」ボタンを押す', '「次へ」リンクを押す', '「山田 太郎」を押す']);
  const login = res.steps[0];
  assert.ok(login.detail.includes('{{input_1}}') && login.detail.includes('（記録時の例: taro）'));
  assert.ok(!JSON.stringify(res).includes('p@ss'));
  assert.deepStrictEqual(login.recorded.map((o) => o.op), ['goto', 'fill', 'fill', 'click']);
  assert.deepStrictEqual(res.parameters, ['input_1', 'input_2', 'input_3']);
  assert.strictEqual(login.check, '');
  // そのまま正規化・コンパイルに載る
  const spec = model.normalizeProcedure({ name: 'x', steps: res.steps });
  assert.deepStrictEqual(spec.parameters, ['input_1', 'input_2', 'input_3']);
  const { files } = model.compile(spec);
  assert.ok(files['actions/step_1.md'].includes("`fill getByRole('textbox', { name: 'ユーザー名' }) \"{{input_1}}\"`（記録時の値の例: taro）"));
  assert.ok(!files['actions/step_1.md'].includes('p@ss'));
});

test('winauto の JSONL はウィンドウの切り替わりで切れ、次のウィンドウを wait で測る', () => {
  const res = recording.stepsFromRecording({ source: 'windows', text: WINAUTO_RECORDING, app: '勤怠管理' });
  assert.strictEqual(res.steps.length, 3);
  assert.strictEqual(res.steps[0].check, 'winauto wait name:=月次集計 --app 勤怠管理');
  assert.strictEqual(res.steps[1].check, 'winauto wait name:=完了 --app 勤怠管理');
  assert.strictEqual(res.steps[2].check, '');
  assert.strictEqual(res.steps[1].recorded[1].target, 'auto_id:=txtMonth');
  assert.strictEqual(res.steps[1].recorded[2].target, 'name:=種別 >> control:=ComboBox');
  assert.throws(() => recording.stepsFromRecording({ source: 'gui', text: 'x' }), /ブラウザか Windows アプリ/);
});

test('ブラウザの記録の開始・終了は playwright-cli を記録専用セッションで直接呼ぶ', async () => {
  const calls = [];
  const capture = async (command, args) => {
    calls.push([command, ...args]);
    if (args.includes('--help')) return { ok: true, status: 0, stdout: 'recording-start\nrecording-stop\n', stderr: '' };
    if (args.includes('recording-stop')) return { ok: true, status: 0, stdout: BROWSER_RECORDING, stderr: '' };
    return { ok: true, status: 0, stdout: '', stderr: '' };
  };
  const started = await recording.recordBrowserStart({ url: 'https://intra.example/login', capture });
  assert.strictEqual(started.session, recording.RECORD_SESSION);
  const res = await recording.recordBrowserStop({ url: 'https://intra.example/login', capture });
  assert.strictEqual(res.steps.length, 3);
  assert.deepStrictEqual(calls.map((c) => c.slice(0, 3)), [
    ['playwright-cli', '--help'],
    ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'open'],
    ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'recording-start'],
    ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'recording-stop'],
    ['playwright-cli', `-s=${recording.RECORD_SESSION}`, 'close'],
  ]);
  assert.ok(calls[1].includes('--headed'));
  const failing = async (_command, args) => args.includes('--help')
    ? { ok: true, status: 0, stdout: 'recording-start\nrecording-stop\n', stderr: '' }
    : { ok: false, status: 1, stdout: '', stderr: 'Missing X server or $DISPLAY' };
  await assert.rejects(() => recording.recordBrowserStart({ capture: failing }), /画面の無い環境/);
});

test('記録に未対応の playwright-cli はブラウザを開く前に更新方法を示して止める', async () => {
  const calls = [];
  const capture = async (command, args) => {
    calls.push([command, ...args]);
    return { ok: true, status: 0, stdout: 'open [url]\ntracing-start\n', stderr: '' };
  };
  await assert.rejects(
    () => recording.recordBrowserStart({ url: 'https://example.com', capture }),
    /操作記録に対応していません.*@latest/,
  );
  assert.deepStrictEqual(calls, [['playwright-cli', '--help']], '未対応と分かった後にブラウザを開かない');
});

test('Windows の記録は winauto record を子プロセスで走らせ、停止ファイルで止めて JSONL を読む', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-rec-'));
  let spawned = null;
  const spawnRecorder = ({ command, args }) => {
    spawned = { command, args };
    const out = args[args.indexOf('--output') + 1];
    const stop = args[args.indexOf('--stop-file') + 1];
    return {
      wait: () => new Promise((resolve) => {
        const timer = setInterval(() => {
          if (fs.existsSync(stop)) { clearInterval(timer); fs.writeFileSync(out, WINAUTO_RECORDING); resolve({ code: 0, stderr: '' }); }
        }, 20);
      }),
    };
  };
  recording.resetWindowsRecording();
  const started = await recording.recordWindowsStart({ app: '勤怠管理', tmpDir: tmp, spawnRecorder });
  assert.strictEqual(started.source, 'windows');
  assert.deepStrictEqual(spawned.args.slice(0, 3), ['record', '--app', '勤怠管理']);
  assert.ok(recording.windowsRecordingState());
  await assert.rejects(() => recording.recordWindowsStart({ app: 'x', tmpDir: tmp, spawnRecorder }), /すでに記録中/);
  const res = await recording.recordWindowsStop({});
  assert.strictEqual(res.steps.length, 3);
  assert.strictEqual(recording.windowsRecordingState(), null);
  assert.deepStrictEqual(fs.readdirSync(tmp), [], '一時ファイルは消す');
  await assert.rejects(() => recording.recordWindowsStop({}), /始まっていません/);
});

const SNAPSHOT = `### Page
- Page URL: https://qiita.com/items/1
### Snapshot
\`\`\`yaml
- generic [ref=e2]:
  - heading "記事のタイトル" [level=1] [ref=e3]
  - article [ref=e4]:
    - paragraph [ref=e5]: 本文
    - link "Learn more" [ref=e6] [cursor=pointer]
  - table [ref=e7]
\`\`\`
`;

test('snapshot の行を role と名前の候補にし、ref は残さずロケータ式に写す', () => {
  const list = recording.parseSnapshot(SNAPSHOT);
  assert.deepStrictEqual(list.map((c) => c.ref), ['e3', 'e4', 'e5', 'e6', 'e7'], 'generic は候補にしない');
  assert.strictEqual(list[0].target, "getByRole('heading', { name: '記事のタイトル' })");
  assert.strictEqual(list[1].target, "getByRole('article')");
  assert.strictEqual(list[1].depth, 1);
  assert.strictEqual(recording.roleLocator('link', "It's"), "getByRole('link', { name: 'It\\'s' })");
});

test('記録中の読み取りは stop → start で区切って順に積み、直前の工程に属する', async () => {
  const calls = [];
  let stops = 0;
  const capture = async (command, args) => {
    calls.push(args[args.length - 1] === '--help' ? '--help' : args[1]);
    if (args.includes('--help')) return { ok: true, status: 0, stdout: 'recording-start\nrecording-stop\n', stderr: '' };
    if (args.includes('snapshot')) return { ok: true, status: 0, stdout: SNAPSHOT, stderr: '' };
    if (args.includes('recording-stop')) {
      stops += 1;
      const body = stops === 1
        ? "```js\nawait page.goto('https://qiita.com/');\nawait page.getByRole('link', { name: 'ある記事' }).click();\n```"
        : 'Recording stopped. No actions were recorded.';
      return { ok: true, status: 0, stdout: body, stderr: '' };
    }
    return { ok: true, status: 0, stdout: '', stderr: '' };
  };
  recording.resetBrowserRecording();
  await assert.rejects(() => recording.recordBrowserSnapshot({ capture }), /始まっていません/);
  await recording.recordBrowserStart({ url: 'https://qiita.com/', capture });
  const snap = await recording.recordBrowserSnapshot({ capture });
  assert.strictEqual(snap.candidates.length, 5);
  await assert.rejects(() => recording.recordBrowserExtract({ ref: 'e99', capture }), /一覧から選んで/);
  const ins = await recording.recordBrowserExtract({ ref: 'e4', mode: 'text', key: 'Body Text', capture });
  assert.deepStrictEqual(ins.op, { op: 'extract', target: "getByRole('article')", role: 'article', label: '', mode: 'text', key: 'body_text' });
  assert.deepStrictEqual(recording.browserRecordingState(), { url: 'https://qiita.com/', extracts: 1 });
  const res = await recording.recordBrowserStop({ capture });
  assert.strictEqual(recording.browserRecordingState(), null);
  assert.deepStrictEqual(res.steps.map((s) => s.title), ['「ある記事」リンクを押す']);
  assert.deepStrictEqual(res.steps[0].recorded.map((op) => op.op), ['goto', 'click', 'extract'], '読み取りは確定の後でも同じ工程に入る');
  assert.strictEqual(res.steps[0].recorded[2].key, 'body_text');
  assert.ok(res.steps[0].detail.includes('全文を読み取る（body_text）'));
  assert.deepStrictEqual(calls, ['--help', 'open', 'recording-start', 'snapshot', 'recording-stop', 'recording-start', 'recording-stop', 'close']);
  // 読み取りだけで操作が無い記録は断る
  recording.resetBrowserRecording();
  const empty = async (_c, args) => args.includes('--help')
    ? { ok: true, status: 0, stdout: 'recording-start\nrecording-stop\n', stderr: '' }
    : { ok: true, status: 0, stdout: args.includes('snapshot') ? SNAPSHOT : 'No actions were recorded', stderr: '' };
  await recording.recordBrowserStart({ capture: empty });
  await recording.recordBrowserSnapshot({ capture: empty });
  await recording.recordBrowserExtract({ ref: 'e4', capture: empty });
  await assert.rejects(() => recording.recordBrowserStop({ capture: empty }), /操作が記録されていません/);
});
