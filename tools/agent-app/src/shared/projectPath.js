'use strict';

// 保存処理と画面の保存先プレビューで同じフォルダ名を使う。
(function exposeProjectPath() {
function folderName(name) {
  const cleaned = String(name || '').trim()
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/^\.+/, '')
    .slice(0, 60)
    .replace(/[-.]+$/, '');
  return cleaned || 'project';
}

  const api = { folderName };
  if (typeof window === 'undefined') module.exports = api;
  else window.ProjectPath = api;
}());
