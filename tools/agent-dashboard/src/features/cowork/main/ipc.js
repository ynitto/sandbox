'use strict';

const cowork = require('./cowork');

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  handle('cowork:overview', (opts) => cowork.overview(loadConfig(), opts || {}));
  handle('cowork:runLoop', ({ itemId, jobId, parameters, executionChoice, tier }) =>
    cowork.runLoop(loadConfig(), itemId || jobId, parameters, executionChoice || tier)
  );
  handle('cowork:runStateMachine', ({ itemId, machineId, parameters, executionChoice, tier }) =>
    cowork.runStateMachine(loadConfig(), itemId || machineId, parameters, executionChoice || tier)
  );
  handle('cowork:generateStateMachine', (payload) => cowork.generateStateMachine(loadConfig(), payload || {}));
  // 手順ビルダー（定型手順）: 工程列 → 指示文の確認と、画面操作が頼る CLI の診断。
  // 作成の起動は上の generateStateMachine（payload.procedure）を通す——入口を増やさない。
  handle('cowork:procedureCatalog', () => cowork.procedureCatalog());
  handle('cowork:procedurePreview', (payload) => cowork.procedurePreview(loadConfig(), payload || {}));
  handle('cowork:procedureTools', (payload) => cowork.procedureTools(loadConfig(), payload || {}));
  // アドホック起動（M2）: 登録フォルダ + 自由文で対話 CLI を起動する（項目非依存）
  handle('cowork:runAdhoc', ({ root, prompt }) => cowork.runAdhoc(loadConfig(), { root, prompt }));
  handle('cowork:saveWork', (payload) => cowork.saveWork(loadConfig(), saveConfig, payload || {}));
  // 定常業務ワークスペースの登録（S2）。宣言は dashboard 設定 cowork.roots が持つ
  // （実行側が宣言を持つ原則。定常業務の実行側はこの dashboard 自身）。
  handle('cowork:inspectRoot', ({ dir }) => cowork.inspectCoworkRoot(loadConfig(), dir));
  // フォルダ選択 → そのまま検査結果を返す（選ぶ / 見る の 2 往復にしない）。
  handle('cowork:pickRoot', async () => {
    const { dialog } = require('electron');
    if (!dialog || typeof dialog.showOpenDialog !== 'function') {
      throw new Error('フォルダ選択を利用できません');
    }
    const picked = await dialog.showOpenDialog({
      title: '定常業務のフォルダを選択',
      properties: ['openDirectory'],
    });
    if (picked.canceled || !picked.filePaths || !picked.filePaths[0]) return { canceled: true };
    return { canceled: false, ...cowork.inspectCoworkRoot(loadConfig(), picked.filePaths[0]) };
  });
  handle('cowork:setRoot', ({ dir, remove }) =>
    cowork.setCoworkRoot(loadConfig(), saveConfig, { dir, remove })
  );
  // 項目ごとの実行履歴（dashboard 発の実行記録）とリポジトリのログ候補
  handle('cowork:itemLogs', ({ itemId }) => cowork.itemLogs(loadConfig(), itemId));
  handle('cowork:readLog', ({ itemId, file, maxBytes }) =>
    cowork.readLog(loadConfig(), itemId, file, maxBytes)
  );
}

module.exports = { registerIpc };
