'use strict';

async function openPath(shellApi, target) {
  const value = String(target || '').trim();
  if (!value) throw new Error('開くファイルが指定されていません');
  const error = await shellApi.openPath(value);
  if (error) throw new Error(`ファイルを開けません: ${error}`);
  return { opened: true };
}

module.exports = { openPath };
