'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const renderer = fs.readFileSync(path.join(root, 'renderer.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1"/);
assert.match(html, /data-tab="history"[^>]*>成果</);
assert.match(html, /data-tab="cowork"[^>]*[^>]*>定常業務</);
assert.match(html, /data-tab="amigos"[^>]*>ミッション</);
assert.ok(html.includes('id="dlg-kiro-loop"'), '実行状況はダイアログとして表示します');
assert.ok(!html.includes('id="tab-btn-kiro-loop"'), '実行状況をメインタブとして重複表示しません');
assert.ok(
  html.indexOf('data-tab="amigos"') < html.indexOf('data-tab="cowork"'),
  'ミッションタブは定常業務の左に置きます'
);
assert.ok(!html.includes('tab-scope-label'), '全体設定の左に補助ラベルを置きません');
assert.match(html, /data-tab="project-settings"[^>]*>プロジェクト設定</);
assert.match(html, /data-tab="orchestration"[^>]*>全体設定</);
assert.ok(
  html.indexOf('data-tab="project-settings"') < html.indexOf('data-tab="orchestration"'),
  'プロジェクト設定は全体設定の左に置きます'
);
assert.match(html, /id="tab-project-settings"[^>]*class="tabpane"/);
for (const id of ['btn-project-settings', 'dlg-project-settings', 'project-settings-body']) {
  assert.ok(!html.includes(`id="${id}"`), `${id} はフルページのプロジェクト設定へ移行後に残しません`);
}
assert.ok(!html.includes('>オーケストレーション</button>'), '内部用語をタブ名に出しません');
assert.ok(!html.includes('>Amigos</button>'), 'UI のタブ名に内部機能名 Amigos を出しません');
assert.ok(!html.includes('定期・定型作業'));
assert.ok(!renderer.includes('定期・定型作業'));
assert.ok(renderer.includes('function overviewVersionsHtml('), '概要画面に計画バージョン一覧が必要です');
assert.ok(renderer.includes('id="btn-overview-add-version"'), '概要画面から計画バージョンを追加できます');
assert.ok(renderer.includes('data-version-edit='), '概要画面から計画バージョンを編集できます');
assert.ok(renderer.includes('data-version-delete='), '概要画面から未使用の計画バージョンを削除できます');
const projectSettingsSource = renderer.slice(
  renderer.indexOf('function renderProjectSettings('),
  renderer.indexOf('\n// プロジェクトのリセット', renderer.indexOf('function renderProjectSettings('))
);
assert.ok(projectSettingsSource.includes('選択中のプロジェクトに適用'));
assert.ok(projectSettingsSource.includes('プロジェクト定義'));
assert.ok(projectSettingsSource.includes('調査と高度な設定'));
assert.ok(projectSettingsSource.includes('危険な操作'));
for (const file of ['charter.md', 'policy.md', 'rules.md', 'repos.json']) {
  assert.ok(projectSettingsSource.includes(file), `${file} の編集導線を維持します`);
}
assert.ok(!projectSettingsSource.includes('計画バージョン'), '計画バージョン管理をプロジェクト設定に重複表示しません');
// 新規版・見出し無しの版は、共通設定（マスター）の制約・前提を「継承値」としてフォームに表示し、
// 変更しない限り見出しを書かずマスターへの追従を維持する（コピーで固定しない）。
assert.ok(renderer.includes('inheritedConstraints'), '版フォームは共通の制約を継承値として表示します');
assert.ok(renderer.includes('inheritedAssumptions'), '版フォームは共通の前提を継承値として表示します');
assert.ok(renderer.includes('_constraintsDefined = cf.origConstraintsDefined'), '継承中は変更したときだけ明示値として保存します');
assert.ok(!renderer.includes("const showConstraints = !isVersion"), '版ごとの制約・前提を編集可能にします');
for (const id of ['enq-charter', 'dlg-replan', 'replan-charter', 'btn-replan-submit']) {
  assert.ok(html.includes(`id="${id}"`), `タスク操作の版指定に ${id} が必要です`);
}
assert.ok(renderer.includes("charter: $('enq-charter').value"), '追加タスクへ選択した版を付与します');
assert.ok(renderer.includes("api.requestReplan(p.dir, 'agent-dashboard から再分解を要求', charter)"), '再計画へ選択した版を渡します');
assert.ok(renderer.includes("charterAssistContext(p, $('enq-charter').value)"), 'タスク補助にも選択版の文脈を使います');

for (const id of ['dlg-amigos-detail', 'amigos-detail-body', 'btn-amigos-detail-close',
  'dlg-technical-info', 'technical-project-info']) {
  assert.ok(html.includes(`id="${id}"`), `${id} が必要です`);
}
for (const id of ['dlg-settings', 'dlg-advanced-settings', 'btn-open-advanced-settings']) {
  assert.ok(!html.includes(`id="${id}"`), `${id} は全体設定ページへの統合後に残しません`);
}

const technicalInfo = html.slice(html.indexOf('<dialog id="dlg-technical-info"'), html.indexOf('<dialog id="dlg-need-output"'));
for (const id of ['cfg-roots', 'cfg-refresh', 'cfg-notify', 'cfg-flow-bus', 'cfg-flow-lockdir',
  'cfg-project-command', 'cfg-agent-cli', 'cfg-cowork-loop-provider', 'cfg-gl-url']) {
  assert.ok(renderer.includes(`id="${id}"`), `全体設定ページに ${id} が必要です`);
  assert.ok(!technicalInfo.includes(`id="${id}"`), `詳細情報に ${id} を出しません`);
}
for (const section of ['app', 'agents', 'sync', 'routine', 'integrations']) {
  assert.ok(renderer.includes(`id: '${section}'`), `全体設定に ${section} 分類が必要です`);
}
assert.ok(renderer.includes('data-global-settings-section="${item.id}"'), '分類タブに設定IDを付けます');
assert.ok(renderer.includes('role="tablist"'), '設定分類はアクセシブルなタブとして表示します');
assert.ok(renderer.includes('role="tabpanel"'), '設定内容と分類タブを関連付けます');
assert.ok(renderer.includes('id="global-settings-select"'), '狭幅用の設定分類セレクトが必要です');
for (const id of ['btn-save-app-settings', 'btn-save-agent-settings', 'btn-save-sync-settings',
  'btn-save-routine-settings', 'btn-save-integrations-settings']) {
  assert.ok(renderer.includes(`id="${id}"`), `${id} で分類単位に保存します`);
}
const renderAmigosSource = renderer.slice(
  renderer.indexOf('function renderAmigos('),
  renderer.indexOf('\nfunction workTypeLabel(', renderer.indexOf('function renderAmigos('))
);
assert.ok(!renderAmigosSource.includes('amigosBudgetPanelHtml('), 'ミッション画面には予算管理を表示しません');
const amigosVisibilitySource = renderer.slice(
  renderer.indexOf('function updateAmigosTabVisibility('),
  renderer.indexOf('\nfunction amigosMin(', renderer.indexOf('function updateAmigosTabVisibility('))
);
assert.ok(!amigosVisibilitySource.includes('budget.hasData'), '予算データだけでミッションタブを表示しません');
assert.ok(!renderer.includes('function renderAdvancedBudgetSettings('), '詳細設定用の旧予算管理処理を残しません');
assert.ok(!renderer.includes('id="btn-amigos-budget-save"'), '旧予算管理の保存操作を残しません');
for (const label of ['利用状況', '共通設定', 'エージェント一覧']) {
  assert.ok(renderer.includes(label), `エージェント分類に「${label}」が必要です`);
}
assert.ok(renderer.includes('すべてのプロジェクトに適用'), '全体設定であることはページ内で明示します');
assert.ok(!renderer.includes('function updateTabScope('), 'タブ切替で共通ヘッダーを変化させません');
assert.ok(!css.includes('#main.global-settings-active #project-header'), 'どのタブでもプロジェクトヘッダーを維持します');
for (const name of ['amigosMissionCardHtml', 'amigosMissionDetailHtml', 'openAmigosDetail']) {
  assert.ok(renderer.includes(`function ${name}(`), `ミッションUIに ${name} が必要です`);
}
assert.ok(renderAmigosSource.includes('amigos-mission-grid'), 'ミッション一覧は要約カードで表示します');
assert.ok(!renderAmigosSource.includes('<table'), 'ミッション一覧に過密な表を使いません');
assert.ok(renderer.includes('<h3>現在の状況</h3>'));
assert.ok(renderer.includes('<h3>メンバーの作業状況</h3>'));
assert.ok(renderer.includes('<h3>やりとり</h3>'));
assert.ok(renderer.includes('<details class="amigos-message'), '発言の全文は必要なときだけ展開します');
assert.ok(!renderAmigosSource.includes('owner='), 'ミッション一覧に内部の所有者IDを出しません');
assert.ok(!renderAmigosSource.includes('round '), 'ミッション一覧に内部ラウンドを出しません');
assert.ok(!renderAmigosSource.includes('amigos.busDirs'), '空状態に内部設定キーを出しません');
assert.ok(!renderAmigosSource.includes('agent-amigos post'), '空状態に内部コマンドを出しません');
const missionRequestDialog = html.slice(
  html.indexOf('<dialog id="dlg-amigos-post"'),
  html.indexOf('<dialog id="dlg-amigos-detail"')
);
assert.ok(!missionRequestDialog.includes('commands/'), '依頼画面に内部ディレクトリを出しません');
assert.ok(!missionRequestDialog.includes('schemas/mission.schema.json'), '依頼画面にスキーマ名を出しません');
assert.ok(!missionRequestDialog.includes('design doc'), '依頼画面に内部の成果物名を出しません');
assert.match(missionRequestDialog, /<details[^>]*class="amigos-team-settings"/);
assert.match(technicalInfo, /<h2 id="technical-info-title">詳細情報<\/h2>/);
assert.ok(!technicalInfo.includes('技術情報'));
assert.ok(!technicalInfo.includes('btn-save-advanced-settings'));

assert.ok(renderer.includes('function openGlobalSettings('));
assert.ok(!renderer.includes('function openAdvancedSettings()'));
assert.ok(!renderer.includes('function openSettings()'));
assert.ok(renderer.includes('function openTechnicalInfo()'));
assert.ok(renderer.includes('function technicalProjectInfoHtml()'));
assert.ok(!renderer.includes('function coworkTechnicalInfoHtml()'), '定常業務画面と重複する専用診断情報を残しません');
assert.ok(!renderer.includes('function developerProjectInfoHtml()'));
assert.ok(renderer.includes('data-open-technical-info'));
assert.ok(renderer.includes('内部ログを開く'));
assert.ok(renderer.includes('詳細情報を開く'));
assert.ok(!renderer.includes('技術情報を開く'));
assert.ok(!renderer.includes('data-open-developer'));
assert.ok(!renderer.includes('<div class="section-title">動作ログ（直近 80 行）</div>'));
assert.ok(!renderer.includes('<summary>実行環境</summary>'));

assert.match(css, /\.developer-facts/);
assert.match(css, /\.developer-log\s*\{[^}]*overflow-wrap:\s*anywhere/s);
assert.match(css, /\.developer-log\s*>\s*div\s*\{[^}]*word-break:\s*break-word/s);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
assert.match(css, /button:focus-visible/);
assert.match(css, /\.amigos-mission-grid\s*\{/);
assert.match(css, /\.amigos-mission-card\s*\{/);
assert.match(css, /\.amigos-detail-dialog\s*\{/);
assert.match(css, /\.amigos-conversation\s*\{/);
assert.match(css, /\.overview-version-grid\s*\{/);
assert.match(css, /\.overview-version-card\s*\{/);
assert.match(css, /\.tab-global-settings\s*\{[^}]*border-left:/s);
assert.match(css, /\.global-settings-tabs\s*\{/);
assert.match(css, /\.global-settings-select\s*\{/);
assert.match(css, /@media \(max-width: 680px\)[\s\S]*?\.global-settings-tabs\s*\{[^}]*display:\s*none/s);
const renderCoworkSource = renderer.slice(
  renderer.indexOf('function renderCowork('),
  renderer.indexOf('\n// ---------------------------------------------------------------------------\n// 定常業務の実行履歴', renderer.indexOf('function renderCowork('))
);
assert.ok(renderer.includes('function coworkRoutineSelectorHtml('), '定常業務の共通セレクターが必要です');
assert.ok(renderer.includes('data-cowork-search'), '件数が多い定常業務を名前で絞り込めます');
assert.ok(renderer.includes('function applyCoworkRoutineFilter('), '検索は一覧DOMだけを絞り込みます');
assert.ok(renderCoworkSource.includes('coworkRoutineSelectorHtml('), '定常業務画面の上部で業務を選択します');
assert.ok(renderCoworkSource.includes('coworkSelectedDetailHtml('), '下部には選択中の業務だけを表示します');
assert.ok(renderCoworkSource.includes('class="cowork-split-view"'), '一覧と選択中業務を上下の固定領域に分けます');
assert.ok(renderCoworkSource.includes('class="cowork-list-pane"'), '上段を一覧専用領域にします');
assert.ok(renderCoworkSource.includes('class="cowork-detail-pane"'), '下段を詳細専用領域にします');
assert.ok(renderCoworkSource.includes('<button id="btn-cowork-save">保存</button>'), '保存ボタンは省略されない短い文言にします');
assert.ok(!renderCoworkSource.includes('data-open-technical-info'), '定常業務画面に重複する診断情報の導線を置きません');
assert.ok(!renderCoworkSource.includes('openTechnicalInfo('), '定常業務から共通の詳細情報ダイアログを開きません');
assert.ok(!renderCoworkSource.includes('class="cowork-list"'), '全業務の大きなカードを並べません');
assert.ok(renderer.includes('function selectCoworkRoutine('), '選択状態を画面間で共有します');
const selectCoworkRoutineSource = renderer.slice(
  renderer.indexOf('function selectCoworkRoutine('),
  renderer.indexOf('\nfunction coworkRoutineSelectorHtml(', renderer.indexOf('function selectCoworkRoutine('))
);
assert.ok(!selectCoworkRoutineSource.includes('renderCowork()'), '選択時に一覧全体を再描画してスクロールを失いません');
assert.ok(selectCoworkRoutineSource.includes('updateCoworkSelectedDetail('), '選択時は下部詳細だけを更新します');
assert.ok(renderer.includes('id="cowork-routine-selector-${esc(searchKey)}"'), '定期更新後に一覧のスクロール位置を復元できる識別子が必要です');
assert.ok(renderer.includes('data-ui-scroll-key'), '再描画される内部スクロール領域を共通保存処理の対象にします');
assert.ok(renderer.includes("document.querySelectorAll('.tabpane, [data-ui-scroll-key]"), '共通保存処理が宣言済みの内部スクロール領域を走査します');
const restoreUiStateSource = renderer.slice(
  renderer.indexOf('function restoreUiState('),
  renderer.indexOf('\nfunction renderAllTabs(', renderer.indexOf('function restoreUiState('))
);
assert.ok(
  restoreUiStateSource.indexOf("document.querySelectorAll('details[data-ui-key]')")
    < restoreUiStateSource.indexOf('Object.entries(ui.scroll)'),
  '詳細を開いてレイアウトを確定してからスクロール位置を復元します'
);
const coworkSelectedDetailSource = renderer.slice(
  renderer.indexOf('function coworkSelectedDetailHtml('),
  renderer.indexOf('\nfunction updateCoworkSelectedDetail(', renderer.indexOf('function coworkSelectedDetailHtml('))
);
assert.ok(!coworkSelectedDetailSource.includes('<details'), '選択中業務の情報は折りたたまず常に表示します');
assert.ok(renderCoworkSource.includes('const ui = captureUiState();'), '定常業務の再描画前にUI状態を保存します');
assert.ok(renderCoworkSource.includes('restoreUiState(ui);'), '定常業務の再描画後にUI状態を復元します');
const renderKiroLoopSource = renderer.slice(
  renderer.indexOf('function renderKiroLoopTerminal('),
  renderer.indexOf('\n// ---------------------------------------------------------------------------\n// kiro-loop 構造化状態', renderer.indexOf('function renderKiroLoopTerminal('))
);
assert.ok(!renderKiroLoopSource.includes('coworkRoutineSelectorHtml('), 'ダイアログでは選択済みの定常業務だけを表示します');
assert.ok(!renderKiroLoopSource.includes('kiro-loop-target'), '表示中エージェントの選択UIを表示しません');
assert.ok(!renderKiroLoopSource.includes('<details'), '実行状況は折りたたまず常に表示します');
assert.ok(renderKiroLoopSource.includes('class="kiro-loop-agent-panel"'), '選択済み業務のエージェント画面を常時表示します');
assert.ok(renderer.includes('function kiroLoopRoutineSession('), '選択した定常業務に対応するエージェントだけを特定します');
const kiroLoopRoutineSessionSource = renderer.slice(
  renderer.indexOf('function kiroLoopRoutineSession('),
  renderer.indexOf('\nasync function openKiroLoopTerminal(', renderer.indexOf('function kiroLoopRoutineSession('))
);
// eslint-disable-next-line no-new-func
const kiroLoopRoutineSession = new Function(`${kiroLoopRoutineSessionSource}; return kiroLoopRoutineSession;`)();
assert.strictEqual(
  kiroLoopRoutineSession([{ name: '日次レビュー', target: '%1' }, { name: '月次集計', target: '%2' }], '月次集計').target,
  '%2',
  '選択した定常業務と同名のエージェントを表示します'
);
assert.strictEqual(
  kiroLoopRoutineSession([{ name: '日次レビュー' }, { name: '月次集計' }], '別の業務'),
  null,
  '複数候補から無関係なエージェントを推測して表示しません'
);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*overflow-x:\s*hidden/s);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*grid-template-columns:/s);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*overflow-y:\s*auto/s);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*minmax\(230px,\s*1fr\)/s);
assert.match(css, /\.cowork-routine-option\s*\{[^}]*height:\s*76px/s);
assert.match(css, /\.cowork-routine-option-head strong\s*\{[^}]*-webkit-line-clamp:\s*2/s);
assert.match(css, /\.cowork-routine-option-head strong\s*\{[^}]*white-space:\s*normal/s);
assert.match(css, /\.cowork-selected-detail\s*\{[^}]*min-width:\s*0/s);
assert.match(css, /#tab-cowork\.active\s*\{[^}]*overflow:\s*hidden/s);
assert.match(css, /\.cowork-split-view\s*\{[^}]*grid-template-rows:\s*240px\s+minmax\(0,\s*1fr\)/s);
assert.match(css, /\.cowork-list-pane\s*\{[^}]*overflow:\s*hidden/s);
assert.match(css, /\.cowork-detail-pane\s*\{[^}]*overflow-y:\s*auto/s);
assert.match(css, /\.kiro-loop-dialog\[open\]\s*\{[^}]*height:/s);
assert.match(css, /\.kiro-loop-agent-panel\s*\{[^}]*min-height:\s*0/s);
assert.ok(renderer.includes('function setupDialogLayouts()'), '全ダイアログを共通の固定ヘッダ・フッタ構造に整えます');
assert.match(css, /dialog\[open\]\s*\{[^}]*display:\s*flex/s);
assert.match(css, /\.dialog-scroll-body\s*\{[^}]*overflow-y:\s*auto/s);
assert.match(css, /\.dialog-heading\s*\{[^}]*flex:\s*0 0 auto/s);
assert.match(css, /\.dialog-actions\s*\{[^}]*flex:\s*0 0 auto/s);
assert.match(css, /\.dialog-actions\s*\{[^}]*border-top:/s);

// eslint-disable-next-line no-new-func
const strategyDisplayLabel = new Function(
  `${renderer.slice(renderer.indexOf('function strategyDisplayLabel('), renderer.indexOf('\n}', renderer.indexOf('function strategyDisplayLabel(')) + 2)}; return strategyDisplayLabel;`
)();
assert.strictEqual(
  strategyDisplayLabel({
    patterns: ['fan-out-and-synthesize', 'adversarial-verification'],
    parallelism: 3,
    review: true,
  }),
  'fan-out-and-synthesize + adversarial-verification / 並列 3 / レビューあり'
);
assert.strictEqual(strategyDisplayLabel('sequential'), 'sequential');
assert.ok(!strategyDisplayLabel({ patterns: ['map-reduce'] }).includes('[object Object]'));

console.log('user-centered-ui: all tests passed');
