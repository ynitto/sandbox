'use strict';

// ドキュメント領域の画面契約。
//   ・領域ナビに「ドキュメント」があり、タブは 文書 / 文書ルール / 設定 の 3 つ
//   ・作成・続き・検証・フィードバック・ルール編集のダイアログが index.html にある
//   ・画面は内部語（workload・cwd・tmux 等）を出さず、成果物と改訂履歴を人の言葉で見せる

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const rendererDir = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(rendererDir, 'index.html'), 'utf8');
const core = fs.readFileSync(path.join(rendererDir, 'renderer.js'), 'utf8');
const css = fs.readFileSync(path.join(rendererDir, 'styles.css'), 'utf8');
const ui = require('../src/renderer/features/documents');

test('ドキュメント領域はタブ 3 つとペインを持ち、スクリプトを読み込む', () => {
  assert.match(core, /id: 'documents', label: 'ドキュメント'/);
  for (const tab of ['document-list', 'document-rules', 'document-settings']) {
    assert.ok(html.includes(`data-tab="${tab}" data-area="documents" data-feature="documents"`), tab);
    assert.ok(html.includes(`id="tab-${tab}" class="tabpane" data-feature="documents"`), `pane ${tab}`);
  }
  assert.ok(html.includes('features/documents.js'));
  assert.ok(html.indexOf('features/documents.js') < html.indexOf('bootstrap.js'), 'bootstrap より前に読む');
});

test('作成・続き・検証・フィードバック・ルール編集のダイアログがある', () => {
  for (const id of ['dlg-docs-create', 'dlg-docs-resume', 'dlg-docs-verify', 'dlg-docs-feedback', 'dlg-docs-rule']) {
    assert.ok(html.includes(`<dialog id="${id}"`), id);
  }
  assert.ok(html.includes('name="docs-create-mode" value="whole"') && html.includes('name="docs-create-mode" value="section"'),
    '一気に作る／区分ごとに作るを選べる');
  assert.ok(html.includes('id="btn-docs-create-add-input"'), '入力ファイルを足せる');
  assert.ok(html.includes('id="docs-verify-review"'), 'ドメインのレビュー結果を入力できる');
  assert.ok(html.includes('name="docs-feedback-target" value="existing"') && html.includes('name="docs-feedback-target" value="new"'),
    '既存ルールの更新と新規ルールを選べる');
  assert.ok(html.includes('id="btn-docs-rule-expand"'), '原案を AI で膨らませる');
  for (const word of ['workload', 'tmux', 'cwd', 'runChatWindow']) {
    const docsBlock = html.slice(html.indexOf('<dialog id="dlg-docs-create"'), html.indexOf('<dialog id="dlg-cowork-work"'));
    assert.ok(!docsBlock.includes(word), `ダイアログに内部語を出さない: ${word}`);
  }
});

test('成果物の表は形式・役割と関係・更新を見せ、ファイルは開ける', () => {
  ui.state.overview = { formats: [{ id: 'docx', label: 'Word' }, { id: 'drawio.svg', label: 'draw.io 図（SVG）' }], sets: [], rules: [] };
  const out = ui.outputsHtml([
    { file: '報告書.docx', path: '/w/報告書.docx', format: 'docx', role: '本文', relatedTo: [], relation: '', size: 2048, updatedAt: '2026-09-03T01:00:00Z' },
    { file: '図.drawio.svg', path: '/w/図.drawio.svg', format: 'drawio.svg', role: '構成図', relatedTo: ['報告書.docx'], relation: '第2章', size: 10, updatedAt: '' },
  ]);
  assert.match(out, /data-docs-open="\/w\/報告書\.docx"/);
  assert.match(out, /Word/);
  assert.match(out, /draw\.io 図/);
  assert.match(out, /関連: 報告書\.docx — 第2章/);
  assert.match(out, /2 KB/);
  assert.match(ui.outputsHtml([]), /成果物はまだありません/);
});

test('文書の詳細は続き・検証・フィードバック・履歴からルールの入口と改訂履歴を持つ', () => {
  ui.state.overview = {
    formats: [{ id: 'md', label: 'Markdown' }], sets: [], rules: [],
    modes: [{ id: 'whole', label: '一気に作る' }, { id: 'section', label: '区分ごとに作る' }],
    actions: [{ kind: 'verify', label: '検証を依頼' }],
  };
  ui.state.selected = 'spec';
  ui.state.detail = {
    id: 'spec', name: '仕様書', mode: 'section', formats: ['md'], dir: '/w/spec', sidecar: '/w/spec/spec.history.md',
    rule: { file: 'api.md', name: 'API 仕様' }, request: 'API を書く', inputs: [], outputs: [], history: '## 2026-09-03 10:00 — 作成の依頼（利用者）',
    createdAt: '2026-09-03T01:00:00Z',
  };
  const out = ui.detailHtml();
  for (const action of ['resume', 'verify', 'feedback', 'rule-from-history']) {
    assert.ok(out.includes(`data-docs-action="${action}"`), action);
  }
  assert.match(out, /区分ごとに作る/);
  assert.match(out, /data-docs-rule-jump="api\.md"/);
  assert.match(out, /spec\.history\.md/);
  assert.match(out, /作成の依頼/);
  ui.state.selected = '';
  assert.match(ui.detailHtml(), /左の文書を選ぶ/);
});

test('操作名と進め方の表示名は main の表から受け取る（画面に複製しない）', () => {
  const src = fs.readFileSync(path.join(rendererDir, 'features', 'documents.js'), 'utf8');
  assert.ok(!/ACTION_LABELS|MODE_LABELS/.test(src), '表示名の表を画面側に持たない');
  ui.state.loaded = true;
  ui.state.overview = { formats: [], sets: [{ id: 'a', name: 'A', formats: [], lastAction: { kind: 'resume', at: '' } }],
    rules: [], modes: [], actions: [{ kind: 'resume', label: '続きを依頼' }] };
  assert.match(ui.listPaneHtml(), /続きを依頼/);
});

test('ホームのカードは件数と入口だけを出す', () => {
  ui.state.overview = { formats: [], sets: [{ id: 'a' }, { id: 'b' }], rules: [] };
  const card = ui.portalCardHtml();
  assert.match(card, /<h3>ドキュメント<\/h3>/);
  assert.match(card, /<strong>2<\/strong> 件の文書/);
  assert.match(card, /data-portal-area="documents"/);
});

test('画面の部品に専用スタイルがある', () => {
  for (const cls of ['.docs-shell', '.docs-list-item', '.docs-table', '.docs-history', '.docs-checks']) {
    assert.ok(css.includes(cls), cls);
  }
});
