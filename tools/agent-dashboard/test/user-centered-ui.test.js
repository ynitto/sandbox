'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const renderer = require('./helpers/renderer-src').read();
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');
const workflowFeature = fs.readFileSync(path.join(root, 'features', 'adhoc-flow.js'), 'utf8');

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

const pollingSource = grab('setupPolling');
assert.ok(pollingSource.includes('refreshNeedsOnly()') && !pollingSource.includes('refreshAll()'),
  '定期更新は全画面更新ではなく要対応専用経路だけを呼びます');
const needsPollingSource = grab('refreshNeedsOnly');
assert.ok(needsPollingSource.includes('reloadProjectNeeds()') && needsPollingSource.includes('refreshNeeds'),
  '定期更新はプロジェクトとワークフローの要対応を更新します');
for (const redraw of ['renderAllTabs(', 'renderCowork(', 'renderAmigos(', 'renderOrchestration(', 'renderFeatureTab(']) {
  assert.ok(!needsPollingSource.includes(redraw), `定期更新で ${redraw} を呼びません`);
}
assert.ok(workflowFeature.includes('refreshNeeds: feature.refreshNeeds')
  && workflowFeature.includes('loadOverview({ includeRun: false })'),
  'ワークフローの定期更新は要対応だけを取得し、選択中runを更新しません');
assert.ok(!grab('renderNeeds').includes('queue-summary') && !workflowFeature.includes('queue-summary'),
  '要対応タブに状態別件数バーを表示しません');
assert.ok(!workflowFeature.includes('<details class="wf-run-overrides">'),
  '今回だけの実行方針は折りたたみません');
for (const [value, label] of [['recommended', 'おすすめ'], ['saving', '節約'], ['quality', '品質優先'],
  ['cost', 'コスト優先'], ['custom', 'カスタム']]) {
  assert.ok(workflowFeature.includes(`value="${value}"`) && workflowFeature.includes(`<strong>${label}</strong>`),
    `今回だけの実行方針に「${label}」を表示します`);
}
assert.match(workflowFeature, /name="wf-run-policy-mode" value="recommended" checked/,
  '今回だけの実行方針はおすすめを初期選択します');
assert.ok(workflowFeature.includes('id="wf-run-policy-custom" hidden'),
  '役割・機能ごとの指定はカスタム選択時だけ表示します');
assert.ok(workflowFeature.includes('id="wf-create-task"') && workflowFeature.includes('class="wf-queue"'),
  '実行タブはタスク作成と実行待ち一覧を分離します');
assert.match(renderer, /\{ id: 'workflows', label: 'ワークフロー', list: 'workflows',[\s\S]*?desc: '実行待ちのタスクを作成/,
  'ワークフローは共通ヘッダーに領域タイトルを表示します');
assert.match(renderer, /\{ id: 'missions', label: 'ミッション',[\s\S]*?desc: '複数のエージェントで役割を分担し、まとまった作業を進めます。' \}/,
  'ミッションは共通ヘッダーに領域タイトルと簡単な説明を表示します');
for (const title of ['<h2>実行中のワークフロー</h2>', '<h2>ワークフロー</h2>', '<h2>要対応</h2>', '<h2>設定</h2>']) {
  assert.ok(!workflowFeature.includes(title), `ワークフローのタブ内に重複タイトル ${title} を表示しません`);
}
assert.ok(!renderer.includes('<h2>ミッションを実行</h2>') && !renderer.includes('<h2>ミッション</h2>'),
  'ミッションのタブ内に独自タイトルを置かず、共通ヘッダーへ揃えます');
assert.ok(workflowFeature.includes('class="dialog-scroll-body task-create-scroll"'),
  'タスク作成ダイアログは共通のスクロール本文と余白を使います');
assert.match(css, /\.task-create-dialog\[open\]\s*\{[^}]*height:\s*min\(720px,\s*calc\(100vh\s*-\s*32px\)\)/s,
  'タスク追加ダイアログはステップが変わっても同じ高さを保ちます');
assert.match(css, /input\[type="checkbox"\]\s*\{[^}]*appearance:\s*none/s,
  'OSネイティブのチェックボックス表示をDashboard共通スタイルへ置き換えます');
const projectTaskWizardSource = renderer.slice(
  renderer.indexOf('function renderProjectTaskWizard('),
  renderer.indexOf('async function advanceProjectTaskWizard(')
);
assert.ok(!projectTaskWizardSource.includes('type="checkbox"')
  && projectTaskWizardSource.includes('data-project-source-version=')
  && projectTaskWizardSource.includes('aria-pressed='),
  'プロジェクトのタスク追加はネイティブチェックボックスではなく選択カードを使います');
assert.ok(!workflowFeature.includes('name="wf-task-input"') && !workflowFeature.includes('name="wf-task-flow"'),
  'タスク作成の選択カードにラジオボタンを重ねません');
assert.ok(workflowFeature.includes('data-wf-task-input-option') && workflowFeature.includes('aria-pressed='),
  '選択カードは押下状態を支援技術にも伝えます');
const workflowTaskDialogSource = workflowFeature.slice(
  workflowFeature.indexOf('function workflowTaskDialogHtml('),
  workflowFeature.indexOf('\n  function boundaryPositions(', workflowFeature.indexOf('function workflowTaskDialogHtml('))
);
assert.ok(workflowTaskDialogSource.indexOf('id="wf-task-cwd"') < workflowTaskDialogSource.indexOf('<legend>入力を選択</legend>'),
  '対象フォルダは入力方法の選択より先に表示します');
assert.ok(workflowTaskDialogSource.includes("const inputField = wizard.inputMode === 'document'")
  && workflowTaskDialogSource.includes('${inputField}')
  && !workflowTaskDialogSource.includes('data-wf-task-text')
  && !workflowTaskDialogSource.includes('data-wf-task-file'),
  '選択中の入力方法に必要な欄だけを生成します');
assert.ok(workflowFeature.includes('class="flow-view-tab') && workflowFeature.includes('class="flow-graph-workspace"')
  && workflowFeature.includes('class="flow-overview-view"') && workflowFeature.includes('class="flow-history-view"'),
  'ワークフロー実行詳細はプロジェクト実行と同じ概要・工程・履歴の構造を使います');
assert.ok(workflowFeature.includes('id="wf-new-run">← 実行待ちへ戻る'),
  '実行詳細から新規タスクと実行待ちへ戻れます');
assert.ok(workflowFeature.includes('workflowTaskCreate') && workflowFeature.includes('workflowTaskExecute'),
  '作成時は実行待ちへ保存し、一覧の明示操作で実行します');
assert.ok(workflowFeature.includes('task-create-steps') && workflowFeature.includes('設計フロー'),
  'タスク作成ダイアログは共通の段階表示と設計フロー選択を持ちます');
assert.ok(workflowFeature.includes('class="global-settings-card wf-settings-card"')
  && workflowFeature.includes('<header class="global-settings-card-heading">')
  && workflowFeature.includes('class="settings-save-actions wf-settings-actions"'),
  'ワークフロー設定は全体設定と同じカード見出し・保存アクションを使います');
assert.match(css, /\.wf-flow-summary\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/s,
  '選択フローの説明は実行フォームの全幅を使います');
assert.match(css, /\.wf-selected-flow-card\s*\{[^}]*display:\s*grid/s,
  '選択フローの説明は編集タブを踏襲したカードとして表示します');
assert.match(css, /\.wf-selected-flow-heading\s*\{[^}]*justify-content:\s*flex-start/s,
  '選択フローのバッジとタイトルはカード左上に揃えます');
assert.match(css, /\.wf-run-card \.orch-policy-card\s*\{[^}]*display:\s*flex/s,
  '実行カード内でも方針カードの横並びを保ちます');
assert.match(css, /\.wf-run-card \.orch-policy-card input\s*\{[^}]*width:\s*1px/s,
  '実行カードの汎用入力幅で方針ラジオを広げません');

{
  const state = { routineAgentTerm: { id: 'removed', name: '前の業務', target: '%1' }, cowork: {} };
  const body = { innerHTML: '前の情報' };
  let visible = true;
  // eslint-disable-next-line no-new-func
  const renderRoutineAgentTerminal = new Function(
    'state', '$', 'captureUiState', 'selectedProjectFolder', 'coworkHasProjectConfig',
    'coworkVisibleEntries', 'coworkDraft', 'coworkEntryId', 'setRoutineAgentDialogVisible',
    'stopRoutineAgentCapturePoll', 'routineAgentCancelWait',
    `${grab('renderRoutineAgentTerminal')}; return renderRoutineAgentTerminal;`
  )(
    state, () => body, () => ({}), () => '/project', () => true,
    () => [], () => [], () => '', (show) => { visible = show; }, () => {}, () => {}
  );
  renderRoutineAgentTerminal();
  assert.strictEqual(state.routineAgentTerm, null, '選択した定常業務が消えたら旧端末情報を破棄する');
  assert.strictEqual(body.innerHTML, '', '消えた定常業務の詳細を表示し続けない');
  assert.strictEqual(visible, false, '対象の無い実行状況ダイアログを閉じる');
}

assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1"/);
assert.ok(workflowFeature.includes('id="wf-new"'), '一から作るカードが必要です');
assert.ok(!workflowFeature.includes('id="wf-use-template"') && !workflowFeature.includes('function libraryHtml('),
  '雛形選択後の複製操作と左ペインを残しません');
assert.match(css, /\.wf-template-grid\s*\{[^}]*align-items:\s*start/s,
  'カードを行の高さまで引き伸ばしません');
assert.match(css, /\.wf-template-card\s*\{[^}]*height:\s*220px[^}]*cursor:\s*pointer/s,
  '雛形カードは短い高さに統一し、クリック可能と示します');
assert.match(css, /\.wf-template-card > small\s*\{[^}]*-webkit-line-clamp:\s*2/s,
  '短い雛形カードでも説明を2行まで表示します');
assert.match(css, /\.wf-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s,
  'ノードグラフをワークスペースの全幅へ広げます');
assert.ok(!css.includes('.wf-workspace.has-inspector'), '工程設定用の予約カラムを残しません');
assert.match(css, /\.wf-properties\s*\{[^}]*position:\s*absolute/s,
  '工程設定はグラフの幅を削らないフローティングパネルにします');
assert.ok(workflowFeature.includes('data-drag-inspector'), '工程の設定パネルをドラッグ移動できます');
assert.ok(workflowFeature.includes('data-pattern-palette'), '複数工程は接続済みの工程セットとして追加できます');
assert.ok(!workflowFeature.includes('data-method-palette'), '実行手法を工程ノードに見せかけません');
assert.ok(workflowFeature.includes('data-method-pattern-id'), '複数ロール手法を雛形から選べます');
assert.ok(!workflowFeature.includes('wf-flow-rules'), '廃止した全体ルールを表示しません');
assert.ok(!workflowFeature.includes('wf-execution-guide'), '実体のない計画確認・工程数・完了判定の帯を表示しません');
assert.ok(workflowFeature.includes('この工程の目的'), 'ノードにはシステムプロンプトではなく工程の目的を書かせます');
assert.ok(workflowFeature.includes('この工程で達成したいことを自然文で書きます'),
  '目的欄には利用者が書く内容を自然文で説明します');
assert.ok(!workflowFeature.includes('{{request}} は実行時に依頼全文へ置き換わります'),
  '目的欄へテンプレート記号の入力を求めません');
assert.ok(workflowFeature.includes('agent-flow が自動で追加'),
  '役割・依頼・依存成果・出力形式を自分で書かなくてよいことを示します');
assert.ok(workflowFeature.includes('未完了なら修正工程を追加して再検証'),
  '反復は検証ノードの設定として見せます');
assert.ok(workflowFeature.includes('分類後に専門工程を追加'),
  '動的なルーティングは分類ノードの設定として見せます');
assert.ok(workflowFeature.includes('wf-continuation'),
  '動的な継続動作は設定パネルを閉じてもノード上で判別できます');
assert.ok(workflowFeature.includes('wf-runtime-node'),
  '実行時に増える工程も編集グラフへ読み取り専用で表示します');
assert.ok(workflowFeature.includes('${esc(role)}ロール'),
  '編集ノードはロールとノード名を混同せず別々に表示します');
assert.ok(workflowFeature.includes('updateEdgePaths(workflow, pane)'), 'ドラッグ中も接続線を再計算します');
assert.ok(workflowFeature.includes('wf-icon-button'), 'アイコン操作は共通の見た目と押下領域に揃えます');
assert.match(css, /\.wf-editor-layout\s*\{[^}]*display:\s*block/s,
  '編集画面は左ペインを除いた1カラムにします');
assert.match(html, /data-tab="history"[^>]*>成果</);
// 道具の名前（定常業務・ミッション…）は**左メニューの領域名**が持つ。右のタブはその領域の
// 中の画面なので、領域名を繰り返さずに中身の名前を付ける（定常業務 → 作業／実行の記録／設定）。
assert.match(html, /data-tab="cowork"[^>]*>定常業務</);
assert.ok(!html.includes('data-tab="routine-runs"'));
assert.match(html, /data-tab="routine-settings"[^>]*>設定</);
assert.match(html, /data-tab="amigos-run"[^>]*>実行</);
assert.match(html, /data-tab="amigos"[^>]*>ミッション</);
assert.ok(html.includes('id="dlg-routine-agent"'), '実行状況はダイアログとして表示します');
assert.ok(!html.includes('id="tab-btn-routine-agent"'), '実行状況をメインタブとして重複表示しません');
assert.ok(
  html.indexOf('data-tab="amigos-run"') < html.indexOf('data-tab="amigos"')
    && html.indexOf('data-tab="amigos"') < html.indexOf('data-tab="cowork"'),
  'ミッション領域は実行、ミッションの順で定常業務の左に置きます'
);
assert.ok(!html.includes('tab-scope-label'), '全体設定の左に補助ラベルを置きません');
// タブは領域の中の画面なので、領域名を繰り返さない短い名前にする（設定）。
// どの設定かは左メニュー（プロジェクト領域 or 全体設定）が示す。
assert.match(html, /data-tab="project-settings"[^>]*>設定</);
const projectTabs = html.slice(html.indexOf('<nav id="tabs">'), html.indexOf('</nav>', html.indexOf('<nav id="tabs">')));
const utilityTabs = projectTabs.slice(projectTabs.indexOf('<span class="tab-utility-group">'));
assert.ok(!utilityTabs.includes('data-tab="project-settings"'),
  'プロジェクト設定を右端のユーティリティ枠へ分離しません');
assert.ok(!renderer.includes('<h2>プロジェクト設定</h2>'),
  '設定タブ内にタブ名と重複する画面タイトルを表示しません');
assert.match(html, /data-tab="orchestration"[^>]*>全体設定</);
// プロジェクトと定常業務はタイトル行右端、ワークフローとミッションはフォルダ入力の右へ置く。
const projectHeader = html.slice(html.indexOf('<header id="project-header">'), html.indexOf('<nav id="tabs">'));
assert.match(projectHeader, /id="btn-cli-chat"[^>]*disabled/);
assert.ok(projectHeader.includes('AIに相談'), '相談の名称を全画面で統一します');
assert.match(projectHeader, /class="project-cli-chat-group"/);
assert.match(projectHeader, /data-consult-group="projects"/,
  'ヘッダーの 1 組はプロジェクト領域の対象と対にします');
// 起動先（cwd）の選択（S3）。プロジェクトのフォルダは S1 以降「状態リポジトリの clone」なので、
// コードを触りたくて CLI を開いてもそこには 1 行もコードが無い。成果物リポジトリを選べること。
assert.ok(projectHeader.includes('id="cli-chat-cwd"'), 'CLIチャットの起動先を選べます');
assert.ok(renderer.includes('function refreshCliChatCwdChoices('));

assert.match(projectHeader, /id="area-header"[\s\S]*data-consult-group="routines"/,
  '定常業務はタイトル行の右端へ相談を置きます');
const workflowQueue = workflowFeature.slice(workflowFeature.indexOf('class="wf-queue"'),
  workflowFeature.indexOf('${workflowTaskDialogHtml(ov)}'));
assert.ok(!workflowQueue.includes("consultControlHtml('workflows')"),
  '実行待ちには作業フォルダが確定していないため相談を置きません');
assert.ok(/const folder = runFolder\(st\.runDetail\);[\s\S]{0,400}\$\{folder \? consultControlHtml\('workflows'\) : ''\}/.test(workflowFeature),
  '実行詳細では記録から作業フォルダを解決できる場合だけ相談を置きます');
assert.ok(/function folderPath\(value\)[\s\S]{0,300}\['url', 'cwd', 'root', 'dir', 'path'\]/.test(workflowFeature),
  'workspace オブジェクトは文字列化せず記録された実フォルダを解決します');
assert.ok(workflowFeature.includes('class="flow-outcome-status"')
  && workflowFeature.includes('class="advice-banner advice-${advice.cls}"')
  && workflowFeature.includes('class="flow-progress-block"')
  && workflowFeature.includes('class="flow-counts"'),
  'ワークフロー概要はプロジェクト実行と同じ順序で状態・次の動き・進捗を示します');
assert.ok(/amigos-run-cwd-action[\s\S]{0,400}consultControlHtml\('missions'\)/.test(renderer),
  'ミッションはフォルダ入力の右へ相談を置きます');
assert.ok(/amigos-detail-content[\s\S]{0,200}consultControlHtml\('missions'\)/.test(renderer),
  'ミッション詳細にも相談を置きます');

// ボタン・フォルダ選択・起動処理は 1 実装のまま。領域は対象フォルダの出しかたを登録するだけ。
assert.ok(renderer.includes('function openConsult('));
assert.ok(renderer.includes('function consultControlHtml('));
assert.ok(renderer.includes('function registerConsultSource('));
assert.ok(renderer.includes('api.agentOpenChat({ dir: target.dir, cwd })'),
  '領域別に解決した対象フォルダと起動先フォルダをCLIチャット起動へ渡します');
for (const area of ['projects', 'routines', 'missions']) {
  assert.ok(renderer.includes(`registerConsultSource('${area}'`), `${area} の対象フォルダを登録します`);
}
assert.ok(workflowFeature.includes("registerConsultSource('workflows'"),
  'ワークフローの対象フォルダを登録します');
// 前の領域の対象が residual に残ると、画面で見ているものとは別のフォルダで CLI が開く。
assert.ok(renderer.includes('group.hidden = group.dataset.consultGroup !== state.area'),
  '相談コントロールは領域につき 1 組だけ出します');
assert.match(css, /\.project-cli-chat-group\[hidden\]\s*\{[^}]*display:\s*none\s*!important/s,
  '非表示の相談コントロールが領域タイトルを押し出しません');
// 無効の理由と実効エージェントは色や非活性ではなく文字で出す。
assert.ok(renderer.includes('function consultNoteText('));
assert.match(html, /data-consult-note[^>]*aria-live="polite"/);
assert.match(css, /\.wf-cwd-action \.project-cli-chat-note:not\(:empty\)[\s\S]*?color:\s*var\(--yellow\)/,
  'ワークフローとミッションのフォルダ警告は入力行の下へ警告色で表示します');
assert.match(css, /\.project-cli-chat\s*\{[^}]*min-height:\s*44px/s);
assert.match(css, /\.amigos-run-team-field select, \.amigos-run-cwd-field input\s*\{[^}]*min-height:\s*44px/s,
  'ミッションのチームとフォルダは同じコントロール高で下端を揃えます');
assert.match(css, /\.amigos-run-team-field, \.amigos-run-cwd-action\s*\{[^}]*align-self:\s*start/s,
  'フォルダ警告用の余白があってもラベル上端を基準に入力欄を揃えます');
assert.match(css, /\.amigos-run-team-field select, \.amigos-run-cwd-field input\s*\{[^}]*height:\s*44px/s,
  'チームとフォルダのコントロールは同じ固定高を使います');
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
assert.ok(!projectSettingsSource.includes('選択中のプロジェクトに適用'),
  '設定タブ内に補助的なページ見出しを重ねません');
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
  'cfg-agent-timeout', 'cfg-cowork-loop-command', 'cfg-gl-url']) {
  assert.ok(renderer.includes(`id="${id}"`), `全体設定ページに ${id} が必要です`);
  assert.ok(!technicalInfo.includes(`id="${id}"`), `詳細情報に ${id} を出しません`);
}
// プロジェクトの登録・本体起動・ロックの置き場は設定から消えた（W2-2〜W2-4）
for (const id of ['cfg-roots', 'cfg-autodiscover', 'cfg-project-command', 'cfg-flow-lockdir',
  'cfg-git-pull', 'cfg-git-autopush']) {
  assert.ok(!renderer.includes(`id="${id}"`), `${id} は削除済みのはず`);
}
// 全体設定に残すのは「端末のすべてのワークロードに効くもの」と「このアプリ自身の設定」だけ。
// 特定の agent-* だけに効く設定はその道具の領域へ移した（利用状況 → 利用状況領域、
// 定常業務 → 定常業務領域の設定タブ）。どこを触ればどこに効くかを置き場所で示す。
for (const section of ['app', 'agents', 'instructions', 'control', 'sync', 'integrations']) {
  assert.ok(renderer.includes(`id: '${section}'`), `全体設定に ${section} 分類が必要です`);
}
const globalSections = renderer.slice(
  renderer.indexOf('const GLOBAL_SETTINGS_SECTIONS = ['),
  renderer.indexOf('];', renderer.indexOf('const GLOBAL_SETTINGS_SECTIONS = ['))
);
for (const section of ['usage', 'routine']) {
  assert.ok(!globalSections.includes(`id: '${section}'`),
    `${section} は道具ごとの設定なので全体設定に置きません`);
}
assert.ok(renderer.includes('function renderRoutineSettings('), '定常業務の設定は他領域と同じタブが持ちます');
assert.ok(renderer.includes('function renderUsage('), '利用状況は自分の領域が持ちます');
assert.ok(renderer.includes('data-global-settings-section="${item.id}"'), '分類タブに設定IDを付けます');
assert.ok(renderer.includes('role="tablist"'), '設定分類はアクセシブルなタブとして表示します');
assert.ok(renderer.includes('role="tabpanel"'), '設定内容と分類タブを関連付けます');
assert.ok(renderer.includes('id="global-settings-select"'), '狭幅用の設定分類セレクトが必要です');
for (const id of ['btn-save-app-settings', 'btn-save-sync-settings',
  'btn-save-routine-settings', 'btn-save-integrations-settings']) {
  assert.ok(renderer.includes(`id="${id}"`), `${id} で分類単位に保存します`);
}
assert.ok(renderer.includes('Dashboard AI')
  && renderer.includes('id="cfg-consult-agent"') && renderer.includes('id="cfg-consult-model"'),
  'Dashboard 固有のAI上書きはアプリ設定へまとめます');
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
assert.ok(!renderer.includes("{ id: 'methods', label: 'ワークフロー' }"),
  '全体設定にはワークフロータブを置きません');
// 利用状況の受け側は全体設定ではなく利用状況領域（sections/usage.js）。
const usageSettingsSource = renderer.slice(
  renderer.indexOf('function renderUsage('),
  renderer.indexOf('}', renderer.indexOf("revealGlobalSettingsPanels(pane, 'usage')"))
);
const agentSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsAgentsHtml('),
  renderer.indexOf('\nfunction globalSettingsInstructionsHtml(')
);
const instructionSettingsSource = renderer.slice(
  renderer.indexOf('function globalSettingsInstructionsHtml('),
  renderer.indexOf('\nfunction orchMethodRoles(')
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
assert.ok(!agentSettingsSource.includes('orchMatrixPanelHtml(') && agentSettingsSource.includes('orchInventoryPanelHtml('),
  'エージェントタブは自動設定対象外の待ち時間とエージェント定義だけを表示します');
assert.ok(instructionSettingsSource.includes('orchInstructionsPanelHtml(')
  && instructionSettingsSource.includes('orchSessionCommandsPanelHtml('), '共通指示タブに指示と開始コマンドを表示します');
assert.ok(workflowFeature.includes("root.orchMethodsPanelHtml({ tuning: ov.tuning, methodsCatalog: ov.methodsCatalog })"),
  '作業ルールはワークフロー画面の設定に表示します');
assert.ok(renderer.includes("const ORCH_TIER_LABELS = { basic: '単純作業', small: '軽量', medium: '標準', large: '高性能' };"),
  '内部tierを利用者向けの実行レベル名で表示します');
assert.ok(renderer.includes("const ORCH_POLICY_TIER_KEYS = ['small', 'medium', 'large'];"),
  '単純作業は機能全体の実行方針では選ばせません');
// eslint-disable-next-line no-new-func
const orchTierValue = new Function('ORCH_TIER_KEYS', 'ORCH_TIER_LABELS',
  `${grab('orchTierValue')}; return orchTierValue;`)(
  ['basic', 'small', 'medium', 'large'],
  { basic: '単純作業', small: '軽量', medium: '標準', large: '高性能' });
assert.deepStrictEqual(['単純作業', '軽量', '標準', '高性能'].map(orchTierValue),
  ['basic', 'small', 'medium', 'large'],
  '日本語の入力値は保存前に内部tierへ戻します');
const policySource = grab('orchExecutionPolicyPanelHtml');
assert.ok(policySource.includes('<dt>通常時</dt>') && policySource.includes('<dt>残量低下時</dt>')
  && policySource.includes('<dt>候補選択</dt>'), '通常表示は候補表でなく方針を3行で要約します');
assert.ok(policySource.includes('<details class="orch-policy-candidates">')
  && policySource.includes('<summary>候補の使い分けを見る</summary>'),
  '候補の組み合わせと理由は閉じた詳細へ置きます');
assert.ok(!policySource.includes('<th>エージェント / モデル</th>'),
  '通常表示に機能別agent/model表を戻しません');
assert.ok(!renderer.includes('id="btn-orch-tier-add"') && !renderer.includes('class="orch-tier-remove"'),
  '固定された実行レベルの追加・削除操作を表示しません');
assert.ok(renderer.includes('<h3>実行レベルの構成</h3>')
  && renderer.includes('使う候補（上から優先）'), '候補編集の見出しを実行レベルへ統一します');
assert.ok(renderer.includes('候補は上から優先。つまみで並び替えやレベル間の移動ができます。'),
  '実行レベルの編集方法を一文で示します');
assert.ok(renderer.includes('class="orch-tier-candidate-drag" draggable="true"')
  && renderer.includes("tierList.addEventListener('dragover'")
  && renderer.includes("list.insertBefore(draggedCandidate"),
  '候補は同じ実行レベル内と実行レベル間でドラッグして並び替えられます');
assert.ok(renderer.includes('await api.orchestrationProfilesApply({ force: true })')
  && renderer.includes('保存し、agent-tools ファミリーへ反映しました'),
  '候補の変更を保存直後に再評価し、ファミリー共通設定へ反映します');
assert.ok(html.includes('<label for="cowork-routine-tier">今回の実行レベル</label>'),
  '単発実行でも同じ用語を使います');
// eslint-disable-next-line no-new-func
const orchMethodTargetLabels = new Function(
  'ORCH_METHOD_PURPOSE_LABELS', 'ORCH_METHOD_ROLE_LABELS', 'orchMethodRoles',
  `${grab('orchMethodTargetLabels')}; return orchMethodTargetLabels;`
)(
  { work: '実装・作業', verify: '検証' }, { worker: '作業', verify: '検証' },
  (method) => (method.fragments || []).map((fragment) => fragment.role)
);
assert.deepStrictEqual(orchMethodTargetLabels({
  fragments: [{ role: 'worker' }], when: { purposes: ['work'] },
}), ['実装・作業'], '工程と処理種別を利用者向けの適用先へまとめます');
// eslint-disable-next-line no-new-func
const orchMethodConstraintLabels = new Function(
  'orchTierLabel', 'amigosWorkloadLabel', 'ORCH_METHOD_ENGINE_LABELS',
  `${grab('orchMethodConstraintLabels')}; return orchMethodConstraintLabels;`
)((value) => ({ basic: '単純作業', small: '軽量', medium: '標準', large: '高性能' })[value] || value,
  (value) => value, { 'agent-flow': 'ワークフロー' });
assert.deepStrictEqual(orchMethodConstraintLabels({
  when: { tiers: ['small'], max_relative_cost: 0 },
}), ['軽量向け', 'ローカル実行向け'], '相対コストの数値はローカル実行という意味へ置き換えます');
assert.ok(!grab('orchMethodConditionsHtml').includes('相対コスト'),
  '利用者向けの適用条件に内部の相対コスト値を表示しません');
assert.ok(grab('orchMethodCardHtml').includes('const effective = enabled ? current : method')
  && grab('orchMethodCardHtml').includes('orchMethodConditionsHtml(effective)'),
  '自動適用中のカードはカタログ最新版ではなく実際に保存された条件を表示します');
assert.ok(grab('orchMethodCardHtml').includes('source !== method.catalog_source')
  && grab('orchMethodCardHtml').includes('最新版に更新'),
  '保存済みの作業ルールとカタログ条件が違う場合だけ更新操作を表示します');
const workflowDraftState = {};
const workflowDialog = { open: false };
// eslint-disable-next-line no-new-func
const orchestrationDraftActive = new Function(
  'state', '$', `${grab('orchestrationDraftActive')}; return orchestrationDraftActive;`
)(workflowDraftState, (id) => (id === 'dlg-orch-method-add' ? workflowDialog : null));
assert.strictEqual(orchestrationDraftActive(), false, '未編集なら自動更新できます');
workflowDraftState.orchWorkflowDirty = true;
assert.strictEqual(orchestrationDraftActive(), true, '実行レベルの入力中は自動更新から保護します');
workflowDraftState.orchWorkflowDirty = false;
workflowDraftState.orchPolicyDirty = true;
assert.strictEqual(orchestrationDraftActive(), true, '実行方針の入力中は自動更新から保護します');
workflowDraftState.orchPolicyDirty = false;
workflowDialog.open = true;
assert.strictEqual(orchestrationDraftActive(), true, 'カスタム作業ルールのダイアログを開いている間も保護します');
assert.ok(grab('renderAllTabs').includes('!orchestrationDraftActive()')
  && grab('refreshAll').includes('!orchestrationDraftActive()'), '全体再描画と定期更新の両方で入力を保護します');
assert.ok(renderer.includes("tierList.addEventListener('input', markWorkflowDirty)"),
  '実行レベルの入力開始を未保存状態として記録します');
assert.ok(renderer.includes("policyPanel.addEventListener('input', markPolicyDirty)")
  && renderer.includes('state.orchPolicyDirty = false'),
  '実行方針の入力を自動更新から保護し、保存後に保護を解除します');
assert.ok(controlSettingsSource.includes('orchExecutionPolicyPanelHtml(') && controlSettingsSource.includes('orchStatusPanelHtml('),
  '実行制御タブに統一した実行方針と稼働制御を表示します');
assert.ok(controlSettingsSource.includes('orchConcurrencyPanelHtml('),
  '実行制御タブで自動実行の同時実行数を設定します');
assert.ok(controlSettingsSource.indexOf('orchTiersPanelHtml(') < controlSettingsSource.indexOf('orchConcurrencyPanelHtml('),
  '実行レベルの構成は実行のキャパシティより前に表示します');
assert.ok(!controlSettingsSource.includes('orchAllocationPanelHtml(')
  && !controlSettingsSource.includes('orchProfilePolicyPanelHtml('),
  '利用量と切り替え条件を別々の設定として表示しません');
assert.ok(!renderer.includes('function orchProfilePolicyPanelHtml(')
  && !renderer.includes('btn-orch-profile-policy-save')
  && !renderer.includes('btn-orch-alloc-save'),
  '旧配分・切り替え条件UIを残しません');
for (const copy of ['おまかせ（推奨）', '節約', '品質優先', 'カスタム', 'この方針を保存して反映']) {
  assert.ok(renderer.includes(copy), `統一実行方針に「${copy}」が必要です`);
}
assert.match(css, /\.amigos-mode-option:has\(input:checked\),\s*\.orch-policy-card:has\(input:checked\)/,
  '実行方針は既存の選択カードと同じ選択表現を使います');
assert.ok(!controlSettingsSource.includes('orchMethodsPanelHtml('),
  '実行制御タブには作業ルールを表示しません');
assert.ok(workflowFeature.includes('自分用にコピー') && workflowFeature.includes('DashboardはGit操作を行いません'),
  '共有フローは読み取り専用とし、自分用コピーとGit非操作を案内します');
for (const copy of ['作業ルールを検索', 'カスタム作業ルールを追加', 'AIで補完']) {
  assert.ok(renderer.includes(copy), `作業ルール設定に「${copy}」が必要です`);
}
assert.ok(renderer.includes('使用するエージェントや実行レベルは変更しません')
  && renderer.includes('複数工程・並列・合議が必要な進め方はワークフローで構成します'),
  '作業ルールが変えるものと、ワークフローへ分けるものを説明します');
assert.ok(renderer.includes('<summary>個別の作業ルールを設定</summary>'),
  '個別ルールは詳細設定として段階的に開示します');
assert.ok(renderer.includes("stale ? '更新あり' : enabled ? '自動適用中' : '未使用'")
  && renderer.includes("${enabled ? '適用しない' : '自動適用する'}"),
  '作業ルールの状態とボタンの操作をON/OFFではなく明示します');
assert.match(css, /\.empty\[hidden\]\s*\{[^}]*display:\s*none/s,
  '候補がある間は一致なしメッセージを非表示にします');
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
assert.match(css, /\.amigos-mode input,\s*\.orch-policy-card input\s*\{[^}]*position:\s*absolute;[^}]*opacity:\s*0/s,
  'ラジオ入力は共通の選択カードへ視覚的に統合する');
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
  renderer.indexOf('function renderCoworkWorkspace('),
  renderer.indexOf('\nfunction renderCowork()', renderer.indexOf('function renderCoworkWorkspace('))
);
assert.ok(renderer.includes('function coworkRoutineSelectorHtml('), '定常業務の共通セレクターが必要です');
assert.ok(renderer.includes('data-cowork-search'), '件数が多い定常業務を名前で絞り込めます');
assert.ok(renderer.includes('function applyCoworkRoutineFilter('), '検索は一覧DOMだけを絞り込みます');
assert.ok(renderCoworkSource.includes('coworkRoutineSelectorHtml('), '定常業務画面の左で業務を選択します');
assert.ok(renderCoworkSource.includes('coworkSelectedDetailHtml('), '右には選択中の業務だけを表示します');
assert.ok(renderCoworkSource.includes('class="cowork-split-view"'), '一覧と選択中業務を左右の固定領域に分けます');
assert.ok(renderCoworkSource.includes('class="cowork-list-pane"'), '左を一覧専用領域にします');
assert.ok(renderCoworkSource.includes('class="cowork-detail-pane"'), '右を詳細専用領域にします');
assert.ok(renderCoworkSource.includes('<button id="btn-cowork-save">変更を保存</button>'), '下書きを永続化する保存操作だと明示します');
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
assert.ok(coworkSelectedDetailSource.includes('class="cowork-prompt-preview"'), '選択した作業のプロンプトを下ペインに表示します');
assert.ok(coworkSelectedDetailSource.includes("item.prompt || item.instruction"), '定期実行と定型処理の入力内容を同じ欄で確認できます');
assert.ok(renderCoworkSource.includes('const ui = captureUiState();'), '定常業務の再描画前にUI状態を保存します');
assert.ok(renderCoworkSource.includes('restoreUiState(ui);'), '定常業務の再描画後にUI状態を復元します');
const renderRoutineAgentSource = renderer.slice(
  renderer.indexOf('function renderRoutineAgentTerminal('),
  renderer.indexOf('\n// ---------------------------------------------------------------------------\n// routine-agent 構造化状態', renderer.indexOf('function renderRoutineAgentTerminal('))
);
assert.ok(!renderRoutineAgentSource.includes('coworkRoutineSelectorHtml('), 'ダイアログでは選択済みの定常業務だけを表示します');
assert.ok(!renderRoutineAgentSource.includes('routine-agent-target'), '表示中エージェントの選択UIを表示しません');
assert.ok(!renderRoutineAgentSource.includes('<details'), '実行状況は折りたたまず常に表示します');
assert.ok(renderRoutineAgentSource.includes('class="routine-agent-panel"'), '選択済み業務のエージェント画面を常時表示します');
assert.ok(renderer.includes('function routineAgentRoutineSession('), '選択した定常業務に対応するエージェントだけを特定します');
const routineAgentRoutineSessionSource = renderer.slice(
  renderer.indexOf('function routineAgentRoutineSession('),
  renderer.indexOf('\nasync function openRoutineAgentTerminal(', renderer.indexOf('function routineAgentRoutineSession('))
);
// eslint-disable-next-line no-new-func
const routineAgentRoutineSession = new Function(`${routineAgentRoutineSessionSource}; return routineAgentRoutineSession;`)();
assert.strictEqual(
  routineAgentRoutineSession([{ name: '日次レビュー', target: '%1' }, { name: '月次集計', target: '%2' }], '月次集計').target,
  '%2',
  '選択した定常業務と同名のエージェントを表示します'
);
assert.strictEqual(
  routineAgentRoutineSession([{ name: '日次レビュー' }, { name: '月次集計' }], '別の業務'),
  null,
  '複数候補から無関係なエージェントを推測して表示しません'
);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*overflow-x:\s*hidden/s);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*flex-direction:\s*column/s);
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*overflow-y:\s*auto/s);
assert.match(css, /\.cowork-routine-option\s*\{[^}]*height:\s*58px/s);
assert.match(css, /\.cowork-routine-option-head strong\s*\{[^}]*white-space:\s*nowrap/s);
assert.match(css, /\.cowork-selected-detail\s*\{[^}]*min-width:\s*0/s);
assert.match(css, /#tab-cowork\.active\s*\{[^}]*overflow:\s*hidden/s);
assert.match(css, /\.cowork-split-view\s*\{[^}]*grid-template-columns:\s*minmax\(240px,\s*320px\)\s+minmax\(0,\s*1fr\)/s);
assert.match(css, /\.cowork-list-pane\s*\{[^}]*overflow:\s*hidden/s);
assert.match(css, /\.cowork-detail-pane\s*\{[^}]*overflow-y:\s*auto/s);
assert.match(css, /\.routine-agent-dialog\[open\]\s*\{[^}]*height:/s);
assert.match(css, /\.routine-agent-panel\s*\{[^}]*min-height:\s*0/s);
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
assert.ok(renderer.includes('>実行も行う</option>'), '実行ロールは括弧なしの平易な日本語で示します');
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
