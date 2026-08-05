'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const renderer = require('./helpers/renderer-src').read();
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
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

{
  const state = { kiroLoopTerm: { id: 'removed', name: '前の業務', target: '%1' }, cowork: {} };
  const body = { innerHTML: '前の情報' };
  let visible = true;
  // eslint-disable-next-line no-new-func
  const renderKiroLoopTerminal = new Function(
    'state', '$', 'captureUiState', 'selectedProjectFolder', 'coworkHasProjectConfig',
    'coworkVisibleEntries', 'coworkDraft', 'coworkEntryId', 'setKiroLoopDialogVisible',
    'stopKiroLoopCapturePoll', 'kiroLoopCancelWait',
    `${grab('renderKiroLoopTerminal')}; return renderKiroLoopTerminal;`
  )(
    state, () => body, () => ({}), () => '/project', () => true,
    () => [], () => [], () => '', (show) => { visible = show; }, () => {}, () => {}
  );
  renderKiroLoopTerminal();
  assert.strictEqual(state.kiroLoopTerm, null, '選択した定常業務が消えたら旧端末情報を破棄する');
  assert.strictEqual(body.innerHTML, '', '消えた定常業務の詳細を表示し続けない');
  assert.strictEqual(visible, false, '対象の無い実行状況ダイアログを閉じる');
}

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
const projectHeader = html.slice(html.indexOf('<header id="project-header">'), html.indexOf('<nav id="tabs">'));
assert.match(projectHeader, /id="btn-cli-chat"[^>]*disabled/);
assert.ok(projectHeader.includes('この作業を相談'), '相談は対象が分かるプロジェクトヘッダーに置きます');
assert.ok(renderer.includes('function openCliChat('));
assert.ok(renderer.includes('api.agentOpenChat({ dir, cwd })'),
  '選択中ワークスペースと起動先フォルダをCLIチャット起動へ渡します');
// 起動先（cwd）の選択（S3）。プロジェクトのフォルダは S1 以降「状態リポジトリの clone」なので、
// コードを触りたくて CLI を開いてもそこには 1 行もコードが無い。成果物リポジトリを選べること。
assert.ok(projectHeader.includes('id="cli-chat-cwd"'), 'CLIチャットの起動先を選べます');
assert.ok(renderer.includes('function refreshCliChatCwdChoices('));
assert.match(css, /\.project-cli-chat\s*\{[^}]*min-height:\s*44px/s);
assert.match(css, /@media \(pointer:\s*coarse\)[\s\S]*?\.project-cli-chat\s*\{[^}]*min-height:\s*44px/s);
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
const coworkDialog = html.slice(
  html.indexOf('<dialog id="dlg-cowork-work"'),
  html.indexOf('<dialog id="dlg-cowork-save"')
);
assert.ok(coworkDialog.includes('<option value="loop">繰り返し作業</option>'));
assert.ok(coworkDialog.includes('<option value="state-machine">手順付き作業</option>'));
for (const removed of ['cw-repo', 'cw-workflow', 'cw-description', 'cw-enabled']) {
  assert.ok(!coworkDialog.includes(`id="${removed}"`), `${removed} は作業ダイアログに表示しません`);
}
assert.ok(coworkDialog.includes('id="cw-prompt-field"'));
assert.ok(coworkDialog.includes('id="cw-instruction-field"'));
assert.strictEqual((coworkDialog.match(/cowork-kind-help/g) || []).length, 2,
  '種類を切り替えても補足欄の高さを揃える');
assert.match(css, /#dlg-cowork-work \.cowork-kind-help\s*\{[^}]*min-height:\s*2\.9em/s);
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
for (const id of ['cfg-refresh', 'cfg-notify', 'cfg-flow-bus', 'cfg-engine-distro', 'cfg-engine-home',
  'cfg-agent-cli', 'cfg-cowork-loop-provider', 'cfg-gl-url']) {
  assert.ok(renderer.includes(`id="${id}"`), `全体設定ページに ${id} が必要です`);
  assert.ok(!technicalInfo.includes(`id="${id}"`), `詳細情報に ${id} を出しません`);
}
// プロジェクトの登録・本体起動・ロックの置き場は設定から消えた（W2-2〜W2-4）
for (const id of ['cfg-roots', 'cfg-autodiscover', 'cfg-project-command', 'cfg-flow-lockdir',
  'cfg-git-pull', 'cfg-git-autopush']) {
  assert.ok(!renderer.includes(`id="${id}"`), `${id} は削除済みのはず`);
}
for (const section of ['app', 'usage', 'agents', 'instructions', 'control', 'sync', 'routine', 'integrations']) {
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
assert.ok(renderer.includes('まず「使用するエージェント」を選んでください'),
  'エージェント設定で最初に入力する項目を明示します');
assert.ok(renderer.includes('必要な場合だけ変更します'),
  'エージェントの追加設定が任意であることを明示します');
assert.ok(renderer.includes('class="settings-save-actions"'), 'カードの保存位置を共通化します');
assert.ok(css.includes('.settings-save-actions'), '保存フッターを同じ配置で描画します');
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
for (const label of ['利用状況', 'エージェント', '共通指示', '実行制御']) {
  assert.ok(renderer.includes(`label: '${label}'`), `全体設定に「${label}」タブが必要です`);
}
const usageSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsUsageHtml('),
  renderer.indexOf('\nfunction globalSettingsInstructionsHtml(')
);
const agentSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsAgentsHtml('),
  renderer.indexOf('\nfunction globalSettingsUsageHtml(')
);
const instructionSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsInstructionsHtml('),
  renderer.indexOf('\nfunction globalSettingsControlHtml(')
);
const controlSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsControlHtml('),
  renderer.indexOf('\nfunction renderOrchestration(')
);
// 利用状況の数字は agent-audit の集計へ一本化した（同じ話題の集計を画面が持たない）。
// 節は差し込み面へ委ね、agent-audit が使えない端末向けの台帳集計フォールバックは面が持つ。
assert.ok(usageSettingsSource.includes("globalSettingsPanelsHtml('usage')"),
  '利用状況タブは監査の面へ委ねます');
assert.ok(!usageSettingsSource.includes('orchBudgetPanelHtml('),
  '利用状況タブで台帳の再集計を並べません');
const auditFeatureSource = fs.readFileSync(
  path.join(root, 'features', 'agent-audit.js'), 'utf8');
assert.ok(auditFeatureSource.includes('orchBudgetPanelHtml('),
  '監査の面は集計が取れないとき台帳集計へフォールバックします');
assert.ok(agentSettingsSource.includes('orchMatrixPanelHtml(') && agentSettingsSource.includes('orchInventoryPanelHtml('),
  'エージェントタブに担当設定と一覧を表示します');
assert.ok(instructionSettingsSource.includes('orchInstructionsPanelHtml(')
  && instructionSettingsSource.includes('orchSessionCommandsPanelHtml('), '共通指示タブに指示と開始コマンドを表示します');
assert.ok(controlSettingsSource.includes('orchAllocationPanelHtml(') && controlSettingsSource.includes('orchStatusPanelHtml('),
  '実行制御タブに上限と稼働制御を表示します');
assert.ok(controlSettingsSource.includes('orchConcurrencyPanelHtml('),
  '実行制御タブで自動実行の同時実行数を設定します');
assert.ok(agentSettingsSource.includes('orchOllamaPanelHtml('),
  'エージェントタブでローカルモデルの接続先を設定します');
assert.ok(renderer.includes('推定できない記録'), '推定不能を0トークンと区別します');
assert.ok(renderer.includes('実測トークンが記録されず、推定レートもない実行'),
  '推定不可の理由を利用状況の近くで説明します');
assert.ok(renderer.includes('利用記録なし'), '未使用は利用記録なしと表示します');
assert.ok(renderer.includes('budget.agents || {}'), 'エージェント別の利用量を台帳集計から表示します');
assert.ok(renderer.includes('<h4 class="orch-usage-subheading">エージェント別</h4>'),
  '利用状況にエージェント別内訳が必要です');
assert.ok(renderer.includes('トークン上限は設定されていません。利用量は引き続き記録されます。'),
  '上限なしでも利用量を表示することを明示します');
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
assert.match(missionRequestDialog, /<form[^>]*class="dialog-shell"/,
  '項目が増えても依頼ダイアログ全体が画面外へはみ出さない');
assert.ok(missionRequestDialog.includes('class="dialog-scroll-body"'),
  '入力欄だけをスクロールできる');
assert.ok(missionRequestDialog.includes('おまかせ編成'));
assert.ok(missionRequestDialog.includes('自分で役割を指定'));
assert.ok(!missionRequestDialog.includes('<select id="amigos-post-home"'),
  '対象チームは固定表示にし、選択操作を出さない');
assert.match(missionRequestDialog, /<strong id="amigos-post-home-label"/,
  '固定された対象チーム名を表示する');
assert.match(css, /\.amigos-request-dialog\[open\]\s*\{[^}]*height:/s,
  'モード切替でダイアログ外形を変えない');
assert.match(css, /\.amigos-mode input\s*\{[^}]*position:\s*absolute;[^}]*opacity:\s*0/s,
  'ラジオ入力はカードへ視覚的に統合する');
assert.ok(!missionRequestDialog.includes('team-builder スキル'), '利用者向け説明に内部スキル名を出さない');
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
assert.match(html, /id="toast"[^>]+role="status"[^>]+aria-live="polite"[^>]+aria-atomic="true"/,
  '操作結果を支援技術にも通知します');
assert.ok(renderer.includes('<button type="button" class="project-item'),
  'プロジェクト選択はキーボード操作できるボタンにします');
assert.ok(renderer.includes('aria-current="${state.selectedDir === p.dir'),
  '選択中のプロジェクトを支援技術へ伝えます');
const flowOverview = renderer.slice(renderer.indexOf('const overviewView ='), renderer.indexOf('const graphView ='));
assert.ok(flowOverview.indexOf('${adviceBanner}') < flowOverview.indexOf('flow-progress-block'),
  '次に必要な対応を進捗より先に表示します');
assert.ok(flowOverview.indexOf('flow-progress-block') < flowOverview.indexOf('flow-request-details'),
  '長い依頼内容は進捗と操作の後ろへ移します');
assert.ok(flowOverview.includes('<details class="flow-request-details">'),
  '依頼内容は必要なときだけ展開します');
assert.match(css, /\.sync-action\s*\{[^}]*min-height:\s*44px/s);
assert.match(css, /@media \(max-width: 900px\)[\s\S]*?#tabs\s*\{[^}]*flex-wrap:\s*wrap/s,
  '狭い幅でも設定タブを横スクロールの外へ隠しません');
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
assert.ok(renderer.includes('function detailsUiKey('),
  '明示キーのない折りたたみも再描画後に照合できる共通キーを持ちます');
assert.match(renderer, /document\.querySelectorAll\('details'\)/,
  '再描画される全折りたたみの開閉状態を保存します');
assert.ok(
  restoreUiStateSource.indexOf("document.querySelectorAll('details')")
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

assert.ok(!html.includes('>（案'), '案番号などの設計用語をラベルに出しません');
assert.ok(!renderer.includes('この PC の役割（案'), '設定ラベルに設計案番号を出しません');
assert.ok(!renderer.includes('engineer（本体も動かす'), '役割選択肢に内部ロール名を前面に出しません');
assert.ok(!renderer.includes('viewer（閲覧・レビュー専用）'), '役割選択肢に内部ロール名を前面に出しません');
assert.ok(renderer.includes('>実行も行う（すべての機能）</option>'), '実行ロールは平易な日本語で示します');
assert.ok(renderer.includes('>閲覧・レビュー専用</option>'), '閲覧ロールは平易な日本語で示します');
assert.ok(!renderer.includes('登録された clone がありません'), '診断結果に clone などの開発用語を出しません');
assert.ok(!renderer.includes('（役割: viewer）'), '診断結果に内部ロール名を括弧付きで出しません');
assert.ok(!renderer.includes('再配分しました（computed を更新）'), '内部実装語をトーストに出しません');
assert.ok(!html.includes('なぜ（why）'), 'スキーマフィールド名 why をラベルに出しません');
assert.ok(!html.includes('（scope）'), 'スキーマフィールド名 scope をラベルに出しません');
assert.ok(!html.includes('（out_of_scope）'), 'スキーマフィールド名 out_of_scope をラベルに出しません');
assert.ok(!html.includes('（desc）'), 'スキーマフィールド名 desc をラベルに出しません');
assert.ok(!html.includes('決定的に展開'), '実装寄りの用語を完了条件ラベルに出しません');
assert.ok(!html.includes('生コマンド不要'), '実装寄りの用語を完了条件ラベルに出しません');
assert.ok(html.includes('>定型の確認方法</label>'), '利用者には設定の目的が分かる名前を表示します');
assert.ok(html.includes('>目的・背景</label>'), 'why 欄は目的・背景と表示します');
assert.ok(html.includes('>変更してよい範囲</label>'), 'scope 欄は変更範囲として表示します');
assert.ok(renderer.includes("why: '目的・背景'"), '誘導フィールド表示名から冗長な括弧説明を除きます');
assert.ok(!renderer.includes('空にすると削除）'), '修正フォームのラベルに削除規約の括弧説明を詰め込みません');
assert.ok(!renderer.includes('概要（desc）・目的（why）'), '要対応の案内にスキーマ名を出しません');

console.log('user-centered-ui: all tests passed');
