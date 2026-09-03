'use strict';

// ドキュメント制御面（src/features/documents）の契約テスト。
//   ・文書ルールは 1 物理ファイル。節の並びと front matter を正典どおりに読み書きする
//   ・文書は 1 フォルダ。定義（document.json）・サイドカー（改訂履歴）・入力の写しを持つ
//   ・作成・検証は文書フォルダを cwd にした書き込み可の対話ウィンドウで起動する
//     （cowork-adhoc.test と同じく platform=win32 を強制し、組み立てたスクリプトを検証する）
//   ・ルールの下書きは読み取り専用の助言で、返った本文を正規化して画面へ返す（保存は別）

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-home-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

const wslMain = require('../src/base/main/wsl');
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

const rules = require('../src/features/documents/main/rules');
const prompts = require('../src/features/documents/main/prompts');
const documents = require('../src/features/documents/main/documents');
const agent = require('../src/features/agent-project/main/agent');

const SAMPLE_RULE = [
  '---', 'name: 提案書', 'formats: docx, pptx', '---', '# 文書ルール: 提案書', '',
  '## 対象と目的', '', '顧客の決裁者向け。投資判断をしてもらう。', '',
  '## テンプレート', '', '社内の提案書雛形 v3。', '',
  '## 定型と体裁', '', 'A4 縦・10 ページ以内。敬体。', '',
  '## 記述内容', '', '課題・提案・効果・費用・体制。', '',
  '## 注意点', '', '- 効果は根拠付きで書く', '',
  '## 区分', '', '- 概要 — 1 ページで全体像', '- 課題: 現状の困りごと', '- 提案 — 何をどう変えるか', '',
].join('\n');

function makeEnv() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-'));
  const workspaceDir = path.join(root, 'documents');
  const rulesDir = path.join(root, 'rules');
  fs.mkdirSync(rulesDir, { recursive: true });
  fs.writeFileSync(path.join(rulesDir, 'proposal.md'), SAMPLE_RULE, 'utf8');
  const inputFile = path.join(root, '集計表.csv');
  fs.writeFileSync(inputFile, 'a,b\n1,2\n', 'utf8');
  return { root, workspaceDir, rulesDir, inputFile, config: { documents: { workspaceDir, rulesDir } } };
}

function withWin32(fn) {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    return fn();
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
}

test('文書ルールは front matter と 6 節を読み、区分の箇条書きを構造化する', () => {
  const parsed = rules.parseRule(SAMPLE_RULE);
  assert.equal(parsed.name, '提案書');
  assert.deepEqual(parsed.formats, ['docx', 'pptx']);
  assert.deepEqual(parsed.missing, []);
  assert.deepEqual(parsed.divisions.map((d) => d.title), ['概要', '課題', '提案']);
  assert.equal(parsed.divisions[1].note, '現状の困りごと');
  assert.match(parsed.sections.purpose, /決裁者/);
});

test('節が欠けたルールも読め、正規化で欠けた節を補って保存する', () => {
  const partial = '# 文書ルール: 議事録\n\n## 対象と目的\n\n参加者向け。\n';
  const parsed = rules.parseRule(partial);
  assert.equal(parsed.name, '議事録');
  assert.ok(parsed.missing.includes('divisions'));
  const normalized = rules.normalizeRuleText(partial, { formats: ['md'] });
  assert.match(normalized, /^---\nname: 議事録\nformats: md\n---/);
  for (const [, label] of rules.RULE_SECTIONS) assert.ok(normalized.includes(`## ${label}`), label);
  assert.match(normalized, /参加者向け。/);
});

test('ルールの保存は rulesDir の中だけで、同名は -2 を付け、外のパスは断る', () => {
  const { rulesDir } = makeEnv();
  const a = rules.saveRule(rulesDir, { name: '提案書', content: SAMPLE_RULE });
  assert.equal(path.basename(a.file), '提案書.md');
  assert.equal(a.created, true);
  const b = rules.saveRule(rulesDir, { name: '提案書', content: SAMPLE_RULE });
  assert.equal(path.basename(b.file), '提案書-2.md');
  const updated = rules.saveRule(rulesDir, { file: 'proposal.md', name: '提案書', content: SAMPLE_RULE.replace('敬体', '常体') });
  assert.equal(updated.created, false);
  assert.match(fs.readFileSync(path.join(rulesDir, 'proposal.md'), 'utf8'), /常体/);
  assert.throws(() => rules.readRule(rulesDir, path.join(os.tmpdir(), 'outside.md')), /フォルダにあるファイルだけ/);
  assert.throws(() => rules.readRule(rulesDir, '../x.md'), /フォルダにあるファイルだけ/);
  assert.equal(rules.listRules(rulesDir).length, 3);
});

test('作成は文書フォルダ・定義・サイドカー・入力の写しを作り、対話ウィンドウを起動する', () => {
  const { config, workspaceDir, inputFile } = makeEnv();
  const res = withWin32(() => documents.create(config, {
    name: '2026 提案書', formats: ['docx', 'drawio.svg'], ruleFile: 'proposal.md', mode: 'section',
    prompt: 'A 社向けに刷新提案をまとめる', inputs: [inputFile],
  }));
  assert.equal(res.launch.ok, true, res.launch.error || '');
  assert.match(res.launch.session || '', /^agent-doc-/);
  const set = res.set;
  assert.equal(set.id, '2026-提案書');
  assert.equal(set.dir, path.join(workspaceDir, '2026-提案書'));
  assert.deepEqual(set.formats, ['docx', 'drawio.svg']);
  assert.equal(set.mode, 'section');
  assert.equal(set.rule.file, 'proposal.md');
  assert.deepEqual(set.inputs.map((i) => i.name), ['集計表.csv']);
  assert.ok(fs.existsSync(path.join(set.dir, 'inputs', '集計表.csv')));
  const manifest = JSON.parse(fs.readFileSync(path.join(set.dir, 'document.json'), 'utf8'));
  assert.equal(manifest.request, 'A 社向けに刷新提案をまとめる');
  assert.deepEqual(manifest.outputs, []);
  // サイドカーは利用者の意図（依頼・形式・進め方・ルール・入力）を最初の項目として持つ
  const history = fs.readFileSync(path.join(set.dir, '2026-提案書.history.md'), 'utf8');
  assert.match(history, /^# 改訂履歴: 2026-提案書/);
  assert.match(history, /— 作成の依頼（利用者）/);
  assert.match(history, /### 利用者の意図[\s\S]*A 社向けに刷新提案をまとめる/);
  assert.match(history, /進め方: 区分ごとに作る/);
  assert.match(history, /文書ルール: 提案書（proposal\.md）/);
  // 起動スクリプトへ渡る依頼文: ルール全文・徹底的な質問・区分の順・記録の約束
  const body = fs.readFileSync(res.launch.scriptFile, 'utf8');
  assert.ok(body.includes('顧客の決裁者向け'), 'ルール本文が入る');
  assert.ok(body.includes('まず徹底的に質問する'), '質問の手順が入る');
  assert.ok(body.includes('区分ごとに作る'), '区分ごとの進め方が入る');
  assert.ok(body.includes('1. 概要') && body.includes('3. 提案'), '区分の順が入る');
  assert.ok(body.includes('2026-提案書.history.md'), 'サイドカーの約束が入る');
  assert.ok(body.includes('draw.io'), '形式ごとの手掛かりが入る');
  assert.ok(body.includes('inputs/集計表.csv'), '入力の写しを相対パスで示す');
  // 一覧に載る
  const ov = documents.overview(config);
  assert.equal(ov.sets.length, 1);
  assert.equal(ov.sets[0].name, '2026 提案書');
  assert.equal(ov.rules.length, 1);
});

test('作成の入力検証: 名前・形式・依頼か入力ファイルのどれかが要る', () => {
  const { config, inputFile } = makeEnv();
  assert.throws(() => documents.create(config, { name: '', formats: ['docx'], prompt: 'x' }), /名前/);
  assert.throws(() => documents.create(config, { name: 'a', formats: [], prompt: 'x' }), /形式/);
  assert.throws(() => documents.create(config, { name: 'a', formats: ['docx'] }), /依頼内容か入力ファイル/);
  assert.throws(() => documents.create(config, { name: 'a', formats: ['docx'], prompt: 'x', ruleFile: '../etc.md' }), /フォルダにあるファイル/);
  assert.throws(() => documents.create(config, { name: 'a', formats: ['docx'], inputs: [`${inputFile}.missing`] }), /見つかりません/);
});

test('成果物はフォルダの実ファイルから数え、document.json の outputs は役割と関係を補う', () => {
  const { config } = makeEnv();
  const res = withWin32(() => documents.create(config, { name: '報告書', formats: ['docx', 'xlsx'], prompt: '月次報告' }));
  const dir = res.set.dir;
  fs.writeFileSync(path.join(dir, '報告書.docx'), 'x');
  fs.writeFileSync(path.join(dir, '集計.xlsx'), 'y');
  fs.writeFileSync(path.join(dir, '図.drawio.svg'), '<svg/>');
  fs.writeFileSync(path.join(dir, 'memo.txt'), 'z');
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'document.json'), 'utf8'));
  manifest.outputs = [{ file: '集計.xlsx', format: 'xlsx', role: '根拠データ', relatedTo: ['報告書.docx'], relation: '第2章の表' }];
  fs.writeFileSync(path.join(dir, 'document.json'), JSON.stringify(manifest));
  const detail = documents.get(config, { id: res.set.id });
  const byFile = Object.fromEntries(detail.outputs.map((o) => [o.file, o]));
  assert.deepEqual(Object.keys(byFile).sort(), ['memo.txt', '図.drawio.svg', '報告書.docx', '集計.xlsx'].sort());
  assert.equal(byFile['報告書.docx'].format, 'docx');
  assert.equal(byFile['図.drawio.svg'].format, 'drawio.svg');
  assert.equal(byFile['memo.txt'].format, '');
  assert.equal(byFile['集計.xlsx'].role, '根拠データ');
  assert.deepEqual(byFile['集計.xlsx'].relatedTo, ['報告書.docx']);
  assert.ok(!('document.json' in byFile) && !('報告書.history.md' in byFile), '定義とサイドカーは成果物に数えない');
});

test('検証はドメインのレビュー結果をサイドカーへ残し、汎用観点つきで起動する', () => {
  const { config } = makeEnv();
  const created = withWin32(() => documents.create(config, { name: '仕様書', formats: ['md'], prompt: 'API 仕様' }));
  const res = withWin32(() => documents.verify(config, { id: created.set.id, review: '3.2 節の前提が古い' }));
  assert.equal(res.launch.ok, true, res.launch.error || '');
  const history = fs.readFileSync(res.set.sidecar, 'utf8');
  assert.match(history, /— 検証の依頼（利用者）/);
  assert.match(history, /### 指摘事項[\s\S]*3\.2 節の前提が古い/);
  const body = fs.readFileSync(res.launch.scriptFile, 'utf8');
  for (const [label] of prompts.VERIFY_CHECKS) assert.ok(body.includes(label), label);
  assert.ok(body.includes('3.2 節の前提が古い'));
  assert.equal(res.set.lastAction.kind, 'verify');
});

test('続きの依頼は指示を必須にし、サイドカーへ意図として残す', () => {
  const { config } = makeEnv();
  const created = withWin32(() => documents.create(config, { name: '手順書', formats: ['md'], prompt: '導入手順' }));
  assert.throws(() => documents.resume(config, { id: created.set.id, instruction: ' ' }), /続けて依頼する内容/);
  const res = withWin32(() => documents.resume(config, { id: created.set.id, instruction: '次の区分へ' }));
  assert.equal(res.launch.ok, true, res.launch.error || '');
  assert.match(fs.readFileSync(res.set.sidecar, 'utf8'), /— 続きの依頼（利用者）[\s\S]*次の区分へ/);
  assert.throws(() => documents.resume(config, { id: '../x', instruction: 'a' }), /識別子が不正/);
});

test('ルールの下書きは読み取り専用の助言で、応答を正典の書式へ正規化して返す（保存はしない）', async () => {
  const { config, rulesDir } = makeEnv();
  const origRun = agent.runAgent;
  const origResolve = agent.resolveDashboardAgent;
  const origAllowed = agent.runDashboardAgent;
  const calls = [];
  agent.resolveDashboardAgent = () => ({ cli: 'stub', model: 'm', source: 'settings', spec: {}, timeoutMs: 1000 });
  agent.runDashboardAgent = async (_cfg, _resolved, _purpose, op) => op();
  agent.runAgent = async (_resolved, prompt) => {
    calls.push(prompt);
    return '```markdown\n# 文書ルール: 議事録\n\n## 対象と目的\n\n参加者と欠席者向け。（要確認）\n\n## 区分\n\n- 決定事項 — 決まったこと\n```';
  };
  try {
    const res = await documents.draftRule(config, { name: '議事録', formats: ['md'], draft: '会議のあとに配る' });
    assert.equal(res.cli, 'stub');
    assert.match(res.content, /^---\nname: 議事録\nformats: md\n---/);
    assert.match(res.content, /## 注意点/);
    assert.equal(res.parsed.divisions.length, 1);
    assert.ok(calls[0].includes('会議のあとに配る'), '原案が依頼文に入る');
    assert.ok(calls[0].includes('出力は**ルール本文だけ**'), '書式の指示が入る');
    assert.equal(rules.listRules(rulesDir).length, 1, '下書きの段階では保存しない');
    await assert.rejects(documents.draftRule(config, { name: 'x' }), /原案かテンプレート/);
  } finally {
    agent.runAgent = origRun;
    agent.resolveDashboardAgent = origResolve;
    agent.runDashboardAgent = origAllowed;
  }
});

test('完成後のフィードバックはサイドカーへ残し、既存ルールの更新案か新規ルール案を返す', async () => {
  const { config } = makeEnv();
  const created = withWin32(() => documents.create(config, {
    name: '提案書 A 社', formats: ['docx'], ruleFile: 'proposal.md', prompt: '刷新提案',
  }));
  const origRun = agent.runAgent;
  const origResolve = agent.resolveDashboardAgent;
  const origAllowed = agent.runDashboardAgent;
  const seen = [];
  agent.resolveDashboardAgent = () => ({ cli: 'stub', model: '', source: 'settings', spec: {}, timeoutMs: 1000 });
  agent.runDashboardAgent = async (_cfg, _resolved, purpose, op) => { seen.push(purpose); return op(); };
  agent.runAgent = async (_resolved, prompt) => {
    seen.push(prompt);
    return `${SAMPLE_RULE}\n- 結論を先に 1 段落で書く（更新）\n`;
  };
  try {
    const existing = await documents.feedback(config, { id: created.set.id, feedback: '結論が長い', target: 'existing' });
    assert.equal(existing.target, 'existing');
    assert.equal(existing.file, 'proposal.md');
    assert.equal(existing.name, '提案書');
    assert.match(existing.content, /結論を先に/);
    assert.ok(seen.includes('document-rule-feedback'));
    assert.ok(seen.some((p) => typeof p === 'string' && p.includes('結論が長い') && p.includes('更新した全文')));
    const fresh = await documents.feedback(config, { id: created.set.id, feedback: '別種の文書にも使いたい', target: 'new', name: '刷新提案' });
    assert.equal(fresh.file, '');
    assert.equal(fresh.name, '刷新提案');
    const history = fs.readFileSync(created.set.sidecar, 'utf8');
    assert.match(history, /— 完成後のフィードバック（利用者）[\s\S]*結論が長い/);
    assert.match(history, /文書ルール「提案書」の更新案を作成/);
    // 改訂履歴からのルール起こしは履歴と定義を依頼文へ渡す
    const fromHistory = await documents.ruleFromHistory(config, { id: created.set.id });
    assert.equal(fromHistory.name, '提案書 A 社のルール');
    assert.ok(seen.some((p) => typeof p === 'string' && p.includes('改訂履歴から') && p.includes('"request": "刷新提案"')));
    assert.rejects(documents.feedback(config, { id: created.set.id, feedback: '' }), /フィードバックを入力/);
  } finally {
    agent.runAgent = origRun;
    agent.resolveDashboardAgent = origResolve;
    agent.runDashboardAgent = origAllowed;
  }
});

test('設定の保存は置き場だけを書き換える', () => {
  const saved = [];
  const out = documents.saveSettings({ documents: { workspaceDir: '', rulesDir: '' }, other: 1 },
    (cfg) => { saved.push(cfg); return cfg; }, { workspaceDir: ' /tmp/docs ', rulesDir: '~/rules' });
  assert.equal(out.documents.workspaceDir, '/tmp/docs');
  assert.equal(out.documents.rulesDir, '~/rules');
  assert.equal(out.other, 1);
  assert.equal(saved.length, 1);
  assert.ok(documents.workspaceDir({ documents: { workspaceDir: '' } }).endsWith(path.join('.agents', 'documents')));
});

test('feature 登録: id / IPC 入口 / preload API', () => {
  const { loadFeatures } = require('../src/features');
  const feature = loadFeatures().find((f) => f.id === 'documents');
  assert.ok(feature, 'documents が features に並ぶ');
  const api = feature.preloadApi();
  const calls = [];
  const invoke = (channel, payload) => { calls.push([channel, payload]); return 'ok'; };
  for (const name of ['documentsOverview', 'documentsGet', 'documentsCreate', 'documentsResume', 'documentsVerify',
    'documentsFeedback', 'documentsRuleFromHistory', 'documentsRuleRead', 'documentsRuleDraft', 'documentsRuleSave',
    'documentsPickInputs', 'documentsPickFolder', 'documentsSaveSettings']) {
    assert.equal(typeof api[name], 'function', name);
  }
  api.documentsCreate(invoke)({ name: 'x' });
  api.documentsPickInputs(invoke)();
  assert.deepEqual(calls, [['documents:create', { name: 'x' }], ['documents:pickInputs', {}]]);
  const channels = [];
  feature.registerIpc({ handle: (ch) => channels.push(ch), loadConfig: () => ({}), saveConfig: () => ({}) });
  for (const ch of ['documents:overview', 'documents:create', 'documents:verify', 'documents:feedback',
    'documents:ruleDraft', 'documents:ruleSave', 'documents:pickInputs', 'documents:pickFolder']) {
    assert.ok(channels.includes(ch), ch);
  }
});
