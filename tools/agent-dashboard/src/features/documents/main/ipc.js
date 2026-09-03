'use strict';

const documents = require('./documents');
const rules = require('./rules');

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  handle('documents:overview', () => documents.overview(loadConfig()));
  handle('documents:get', ({ id }) => documents.get(loadConfig(), { id }));
  handle('documents:create', (payload) => documents.create(loadConfig(), payload || {}));
  handle('documents:resume', (payload) => documents.resume(loadConfig(), payload || {}));
  handle('documents:verify', (payload) => documents.verify(loadConfig(), payload || {}));
  handle('documents:feedback', (payload) => documents.feedback(loadConfig(), payload || {}));
  handle('documents:ruleFromHistory', (payload) => documents.ruleFromHistory(loadConfig(), payload || {}));
  handle('documents:ruleRead', ({ file }) => rules.readRule(documents.rulesDir(loadConfig()), file));
  handle('documents:ruleDraft', (payload) => documents.draftRule(loadConfig(), payload || {}));
  handle('documents:ruleSave', (payload) => rules.saveRule(documents.rulesDir(loadConfig()), payload || {}));
  handle('documents:saveSettings', (payload) => documents.saveSettings(loadConfig(), saveConfig, payload || {}));

  // 入力ファイルの選択（複数可）。パスだけ返し、写しを作るのは create のとき。
  handle('documents:pickInputs', async () => {
    const { dialog } = require('electron');
    if (!dialog || typeof dialog.showOpenDialog !== 'function') throw new Error('ファイル選択を利用できません');
    const picked = await dialog.showOpenDialog({
      title: '入力ファイルを選択', properties: ['openFile', 'multiSelections'],
    });
    if (picked.canceled || !picked.filePaths || !picked.filePaths.length) return { canceled: true, files: [] };
    return { canceled: false, files: picked.filePaths };
  });

  // 置き場（文書フォルダ / ルールフォルダ）の選択。保存は saveSettings で別に行う
  // （選んだ直後に設定画面の他の欄が飛ばないよう、画面側がまとめて保存する）。
  handle('documents:pickFolder', async ({ kind } = {}) => {
    const { dialog } = require('electron');
    if (!dialog || typeof dialog.showOpenDialog !== 'function') throw new Error('フォルダ選択を利用できません');
    const picked = await dialog.showOpenDialog({
      title: kind === 'rules' ? '文書ルールのフォルダを選択' : '文書を置くフォルダを選択',
      properties: ['openDirectory', 'createDirectory'],
    });
    if (picked.canceled || !picked.filePaths || !picked.filePaths[0]) return { canceled: true };
    return { canceled: false, dir: picked.filePaths[0] };
  });
}

module.exports = { registerIpc };
