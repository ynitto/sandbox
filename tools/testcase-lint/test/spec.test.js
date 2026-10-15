'use strict';
// 仕様のフォルダの読み取り（spec.js）と、仕様・実装との突き合わせ（S / I）
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const tx = require('../src/text');
const { loadSpec, parseRange } = require('../src/spec');
const { run, tmp, byCode, codes } = require('./helpers');

const LOGIN_MD = `
# ログイン

## 要件

- REQ-01 登録済みのメールアドレスとパスワードでログインできる
- REQ-02 パスワードの誤りが 5 回続くとアカウントをロックする

## 画面項目

| 項目名 | 必須 | 型 | 桁数 | 形式 |
|---|---|---|---|---|
| メールアドレス | ○ | 文字列 | 254文字 | メールアドレス形式 |
| パスワード | ○ | 文字列 | 8〜20文字 | 英数字 |

## メッセージ

| ID | 表示内容 |
|---|---|
| E001 | メールアドレスかパスワードが違います |
`;

const ORDERS_MD = `
# 注文

| ID | 内容 |
|---|---|
| REQ-10 | 注文を取り消せるのは発送前だけ |

## 状態

\`\`\`mermaid
stateDiagram-v2
    [*] --> 受付
    受付 --> 発送済み : 発送する
    受付 --> 取消済み : 取り消す
\`\`\`

## 権限

| 操作 | 管理者 | 一般会員 | ゲスト |
|---|---|---|---|
| 注文を取り消す | ○ | ○ | × |

## API

- \`POST /orders/{id}/cancel\` — 200: 取り消した / 409: 発送済み
`;

test('仕様の読み取り: Markdown の要件・項目定義・状態遷移・権限・API・文言', async () => {
  await tx.init();
  const dir = tmp({ 'login.md': LOGIN_MD, 'orders.md': ORDERS_MD });
  const m = loadSpec([dir]);
  assert.deepEqual(m.requirements.map((r) => r.id).sort(), ['REQ-01', 'REQ-02', 'REQ-10']);
  assert.equal(m.requirements.find((r) => r.id === 'REQ-02').line, 6);
  const pw = m.fields.find((f) => f.name === 'パスワード');
  assert.deepEqual([pw.required, pw.min, pw.max, pw.unit], [true, 8, 20, '文字']);
  assert.deepEqual(m.machines[0].transitions.map((t) => `${t.from}-${t.event}-${t.to}`), ['受付-発送する-発送済み', '受付-取り消す-取消済み']);
  assert.deepEqual(m.permissions[0].roles, ['管理者', '一般会員', 'ゲスト']);
  assert.equal(m.permissions[0].cells.filter((c) => !c.allowed).length, 1);
  assert.deepEqual(m.endpoints.map((e) => [e.method, e.path, e.codes]), [['POST', '/orders/{id}/cancel', ['200', '409']]]);
  assert.deepEqual(m.messages.map((x) => x.text), ['メールアドレスかパスワードが違います']);
});

test('仕様の読み取り: OpenAPI・JSON Schema・状態遷移の YAML・要件の YAML・CSV', async () => {
  await tx.init();
  const dir = tmp({
    'api.yaml': `
openapi: 3.0.0
paths:
  /users:
    post:
      requestBody:
        content:
          application/json:
            schema: { $ref: '#/components/schemas/User' }
      responses: { '201': {}, '400': {}, '409': {} }
components:
  schemas:
    User:
      type: object
      required: [name]
      properties:
        name: { type: string, minLength: 1, maxLength: 30 }
        role: { type: string, enum: [admin, member] }
`,
    'machine.yaml': 'name: 申請\ntransitions:\n  - { from: 下書き, event: 提出する, to: 審査中 }\n',
    'reqs.yaml': 'requirements:\n  - { id: FR-1, text: 利用者を登録できる }\n',
    'fields.csv': '項目名,必須,最大文字数\n氏名,○,30\n',
  });
  const m = loadSpec([dir]);
  assert.deepEqual(m.endpoints[0].codes, ['201', '400', '409']);
  const name = m.fields.find((f) => f.name === 'name');
  assert.deepEqual([name.required, name.min, name.max, name.unit], [true, 1, 30, '文字']);
  assert.deepEqual(m.fields.find((f) => f.name === 'role').enum, ['admin', 'member']);
  assert.equal(m.machines[0].transitions[0].to, '審査中');
  assert.deepEqual(m.requirements.map((r) => r.id), ['FR-1']);
  const shimei = m.fields.find((f) => f.name === '氏名');
  assert.deepEqual([shimei.required, shimei.max, shimei.unit], [true, 30, '文字']);
});

test('範囲の読み取り', () => {
  assert.deepEqual(parseRange('8〜20文字'), [8, 20, '文字']);
  assert.deepEqual(parseRange('0以上100以下'), [0, 100, '']);
  assert.deepEqual(parseRange('最大20桁'), [null, 20, '桁']);
});

test('突き合わせ: 要件・項目の制約・文言（ケースが触れている仕様だけ）', async () => {
  const f = await run(`
feature: ログイン
cases:
  - id: LG-01
    title: ログインできる
    requirement: REQ-01
    preconditions: [ログイン画面を開いている]
    steps: [メールアドレスに「user@example.com」を入力する, パスワードに「Passw0rd」を入力する, ログインボタンを押す]
    expected: [ホーム画面に「ようこそ」と表示される]
  - id: LG-02
    title: 7 文字
    requirement: REQ-01
    preconditions: [ログイン画面を開いている]
    steps: [メールアドレスに「user@example.com」を入力する, パスワードに「abcdefg」を入力する, ログインボタンを押す]
    expected: [「メールアドレスかパスワードが違いました」と表示される]
`, { spec: { 'login.md': LOGIN_MD, 'orders.md': ORDERS_MD } });
  assert.deepEqual(byCode(f, 'S01').map((x) => x.message), ['要件「REQ-02」を確かめるケースがありません'], '触れていない orders.md の REQ-10 は数えない');
  const pw = byCode(f, 'S02').find((x) => x.message.includes('パスワード'));
  assert.match(pw.message, /未入力/);
  assert.match(pw.message, /境界 20文字・21文字/);
  assert.doesNotMatch(pw.message, /7文字|8文字・/);
  assert.match(byCode(f, 'S06')[0].message, /仕様では「メールアドレスかパスワードが違います」/);
  assert.deepEqual(byCode(f, 'G03'), [], '仕様の項目の境界は S02 だけが出す');
});

test('突き合わせ: 状態遷移・仕様にない遷移・権限・API の応答', async () => {
  const f = await run(`
cases:
  - id: O-1
    title: 一般会員が受付の注文を取り消す
    requirement: REQ-10
    preconditions: [一般会員でログイン済み, 状態が受付の注文 A-1 がある]
    steps: [注文 A-1 を取り消す]
    expected: [注文 A-1 の状態が発送済みになる]
  - id: O-2
    title: API で取り消す
    requirement: REQ-10
    preconditions: [状態が受付の注文 A-2 がある]
    steps: [POST /orders/A-2/cancel を送る]
    expected: [ステータス 200 が返る]
`, { spec: { 'orders.md': ORDERS_MD } });
  assert.match(byCode(f, 'S03')[0].message, /2 本/);
  assert.match(byCode(f, 'S08')[0].message, /仕様では「取消済み」/);
  const s04 = byCode(f, 'S04')[0].message;
  assert.match(s04, /ゲスト×注文を取り消す（できない）/);
  assert.match(s04, /管理者×注文を取り消す/);
  assert.match(byCode(f, 'S05')[0].message, /409/);
  assert.doesNotMatch(byCode(f, 'S05')[0].message, /200/);
});

test('突き合わせ: 実装の文言（差し込みのある文字列も照らす）', async () => {
  const f = await run(`
- id: A
  title: t
  preconditions: [ログイン画面を開いている]
  steps: [ログインする]
  expected: [「ようこそ、山田 さん」と表示される, 「メールアドレスかパスワードが違いました」と表示される, 「全然ない文言です」と表示される]
`, { source: { 'messages.json': '{"a": "ようこそ、{name} さん", "b": "メールアドレスかパスワードが違います"}' } });
  const i01 = byCode(f, 'I01').map((x) => x.message);
  assert.equal(i01.length, 2, i01.join('\n'));
  assert.ok(i01.some((m) => m.includes('実装には「メールアドレスかパスワードが違います」')));
  assert.ok(i01.some((m) => m.includes('全然ない文言です')));
});

test('仕様のフォルダが無ければ知らせる（落とさない）', async () => {
  const dir = tmp({ 'cases.yaml': '- {id: A, title: t, preconditions: [画面を開いている], steps: [押す], expected: [「出る」と表示される]}\n' });
  const { audit } = require('../src/audit');
  const { defaults } = require('../src/config');
  const r = await audit({ cases: [path.join(dir, 'cases.yaml')], spec: [path.join(dir, 'nothing')], source: [], config: defaults() });
  assert.match(r.spec.errors[0], /ありません/);
  assert.ok(!codes(r.findings).some((c) => c.startsWith('S')));
});
