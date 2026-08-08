'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

async function init() {
  setupDialogLayouts();
  setupKiroLoopDialog();
  // ホーム（ポータル）はコアのフィーチャータブ登録簿を自分でも使う（sections/home.js は
  // 関数宣言のみ、という読み込み順契約を守るため、登録はここで行う）。features/*.js の
  // カード（参加・利用状況）は各モジュールが load 時に登録済み。
  registerFeatureTab('home', { render: renderHome });
  registerCorePortalCards();
  state.config = await guard('設定読込', () => api.getConfig());
  initTabs();
  $('btn-refresh').addEventListener('click', () => refreshAll());
  $('btn-cli-chat').addEventListener('click', openCliChat);
  $('btn-doctor').addEventListener('click', openDoctor);
  $('btn-doctor-submit').addEventListener('click', askDoctor);
  $('btn-doctor-apply-feedback').addEventListener('click', applyDoctorFeedbackDraft);
  $('btn-doctor-close').addEventListener('click', () => $('dlg-doctor').close());
  $('btn-need-output-close').addEventListener('click', () => $('dlg-need-output').close());
  $('btn-delivery-review-close').addEventListener('click', () => $('dlg-delivery-review').close());
  $('btn-technical-info-close').addEventListener('click', () => $('dlg-technical-info').close());
  $('btn-node-chat-close').addEventListener('click', () => $('dlg-node-chat').close());
  $('btn-cw-cancel').addEventListener('click', () => $('dlg-cowork-work').close());
  $('cw-type').addEventListener('change', updateCoworkWorkFields);
  $('btn-cw-ok').addEventListener('click', async (ev) => { ev.preventDefault(); await applyCoworkWorkDialog(); });
  setupAmigosDialogs();
  $('btn-cw-save-cancel').addEventListener('click', () => $('dlg-cowork-save').close());
  $('btn-cw-save-ok').addEventListener('click', (ev) => { ev.preventDefault(); saveCoworkDraft(); });
  $('btn-task-close').addEventListener('click', requestTaskDialogClose);
  $('dlg-task').addEventListener('cancel', (event) => {
    event.preventDefault();
    requestTaskDialogClose();
  });
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
  $('btn-enq-ai').addEventListener('click', aiEnqueueAssist);
  $('btn-enq-guide-assist').addEventListener('click', aiEnqueueGuideAssist);
  $('enq-charter').addEventListener('change', () => updateCharterSelectContext('enq-charter', 'enq-charter-description'));
  $('btn-replan-cancel').addEventListener('click', () => $('dlg-replan').close());
  $('btn-replan-submit').addEventListener('click', () => requestReplan($('replan-charter').value));
  $('btn-notes-close').addEventListener('click', closeNotesDialog);
  $('dlg-notes').addEventListener('cancel', (event) => {
    event.preventDefault();
    closeNotesDialog();
  });
  $('btn-note-new').addEventListener('click', newNote);
  $('btn-note-save').addEventListener('click', saveNote);
  $('notes-charter').addEventListener('change', () => updateCharterSelectContext('notes-charter', 'notes-charter-description'));
  $('notes-mode-edit').addEventListener('click', () => setNotesMode('edit'));
  $('notes-mode-task').addEventListener('click', () => setNotesMode('task'));
  $('btn-note-candidates').addEventListener('click', buildNoteCandidates);
  $('btn-note-candidates-close').addEventListener('click', () => $('dlg-note-candidates').close());
  $('btn-note-candidates-submit').addEventListener('click', submitNoteCandidates);
  $('btn-document-pick').addEventListener('click', pickDocumentForTasks);
  $('btn-document-candidates').addEventListener('click', buildDocumentCandidates);
  $('document-charter').addEventListener('change', () => updateCharterSelectContext('document-charter', 'document-charter-description'));
  $('btn-document-close').addEventListener('click', closeDocumentTaskDialog);
  $('dlg-document-task').addEventListener('cancel', (event) => {
    event.preventDefault();
    closeDocumentTaskDialog();
  });
  // 新規プロジェクト作成
  $('btn-new-project').addEventListener('click', openNewProject);
  // 定常業務の作業フォルダ登録（定常業務領域のサイドバー ＋）。設定タブの「フォルダを登録」と
  // 同じ入口で、宣言の置き場（cowork.roots）も 1 つのまま。
  const addRoutineRoot = $('btn-add-routine-root');
  if (addRoutineRoot) addRoutineRoot.addEventListener('click', () => addCoworkRoot());
  $('btn-np-cancel').addEventListener('click', () => $('dlg-new-project').close());
  $('btn-np-submit').addEventListener('click', submitNewProject);
  $('np-add-repo').addEventListener('click', () => addRepoRow());
  $('btn-np-ai').addEventListener('click', aiDraftCharter);
  // charter バージョン追加（既存プロジェクトに charters/<名前>.md を後から追加する）
  $('btn-nc-cancel').addEventListener('click', () => $('dlg-new-charter').close());
  $('btn-nc-ok').addEventListener('click', submitNewCharterVersion);
  // プロジェクトファイル編集
  $('btn-ef-cancel').addEventListener('click', () => $('dlg-edit-file').close());
  $('btn-ef-save').addEventListener('click', saveEditFile);
  $('btn-ef-template').addEventListener('click', insertCharterTemplate);
  $('btn-ef-ai').addEventListener('click', aiRefineCharter);
  $('btn-ef-ai-undo').addEventListener('click', undoAiRefine);
  $('btn-ef-open').addEventListener('click', () => {
    if (state.editFile) guard('ファイルを開く', () => api.openPath(state.editFile.file));
  });
  // フォーム編集（憲章 / 運用ルール / リポジトリ一覧）
  $('btn-ec-cancel').addEventListener('click', () => $('dlg-edit-charter').close());
  $('btn-ec-save').addEventListener('click', saveCharterForm);
  $('btn-ec-raw').addEventListener('click', charterFormToRaw);
  $('btn-ep-cancel').addEventListener('click', () => $('dlg-edit-policy').close());
  $('btn-ep-save').addEventListener('click', savePolicyForm);
  $('btn-ep-add').addEventListener('click', () => $('ep-rules')._add && $('ep-rules')._add());
  $('btn-ep-raw').addEventListener('click', () => {
    $('dlg-edit-policy').close();
    openEditFile('policy.md');
  });
  $('btn-er-cancel').addEventListener('click', () => $('dlg-edit-repos').close());
  $('btn-er-save').addEventListener('click', saveReposForm);
  $('btn-er-add').addEventListener('click', () => $('er-rows')._add && $('er-rows')._add());
  $('btn-er-raw').addEventListener('click', () => {
    $('dlg-edit-repos').close();
    openEditFile('repos.json');
  });
  // list-editor の「＋ 追加」ボタン（憲章フォームの各リスト）
  for (const btn of document.querySelectorAll('button[data-add-list]')) {
    btn.addEventListener('click', () => {
      const c = $(btn.dataset.addList);
      if (c && c._add) c._add('');
    });
  }
  api.onOpenTarget(handleOpenTarget);

  // 左メニューに並ぶ領域は、それぞれの取得結果から出し分ける。**起動時に全部そろえる**
  // ——1 つでも取り忘れると、その領域は最初の巡回まで（自動更新を切っていれば永久に）
  // 左メニューから消えたままになる。
  await refreshDiscovery();
  await refreshCowork();
  await refreshAmigos();
  await refreshOrchestration();
  // 前回の対象を裏で復元しておく（右ペインの中身が温まる）。**領域は復元しない**——
  // 起動の着地点はホーム（横断ビュー）で、そこから人がどこへ行くかを選ぶ。
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.projects;
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  switchArea('home');
  setupPolling();
}

init();
