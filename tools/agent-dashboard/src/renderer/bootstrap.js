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
  state.config = await guard('設定読込', () => api.getConfig());
  initTabs();
  $('btn-refresh').addEventListener('click', () => refreshAll({ sync: false }));
  $('btn-cli-chat').addEventListener('click', openCliChat);
  $('btn-doctor').addEventListener('click', openDoctor);
  $('btn-doctor-submit').addEventListener('click', askDoctor);
  $('btn-doctor-apply-feedback').addEventListener('click', applyDoctorFeedbackDraft);
  $('btn-doctor-close').addEventListener('click', () => $('dlg-doctor').close());
  $('btn-need-output-close').addEventListener('click', () => $('dlg-need-output').close());
  $('btn-delivery-review-close').addEventListener('click', () => $('dlg-delivery-review').close());
  $('btn-settings').addEventListener('click', () => openGlobalSettings('app'));
  $('btn-technical-info-close').addEventListener('click', () => $('dlg-technical-info').close());
  $('btn-cw-cancel').addEventListener('click', () => $('dlg-cowork-work').close());
  const chClose = $('btn-cowork-history-close');
  if (chClose) {
    chClose.addEventListener('click', () => {
      state.coworkHistory = null;
      $('dlg-cowork-history').close();
    });
  }
  $('btn-cw-ok').addEventListener('click', (ev) => { ev.preventDefault(); applyCoworkWorkDialog(); });
  setupAmigosDialogs();
  $('btn-cw-save-cancel').addEventListener('click', () => $('dlg-cowork-save').close());
  $('btn-cw-save-ok').addEventListener('click', (ev) => { ev.preventDefault(); saveCoworkDraft(); });
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
  $('btn-enq-ai').addEventListener('click', aiEnqueueAssist);
  $('btn-replan-cancel').addEventListener('click', () => $('dlg-replan').close());
  $('btn-replan-submit').addEventListener('click', () => requestReplan($('replan-charter').value));
  // 新規プロジェクト作成
  $('btn-new-project').addEventListener('click', openNewProject);
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

  await refreshDiscovery();
  await refreshCowork();
  await refreshOrchestration();
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.projects;
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  setupPolling();
}

init();
