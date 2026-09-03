'use strict';

module.exports = {
  documents: {
    // 文書（成果物とサイドカー）を置くフォルダ。空なら共有ホームの ~/.agents/documents。
    // 1 文書 = 1 サブフォルダ（成果物・inputs/・document.json・<id>.history.md）。
    workspaceDir: '',
    // 文書ルール（1 ルール = 1 Markdown ファイル）を置くフォルダ。空なら ~/.agents/document-rules。
    // コピー・削除は OS のファイル操作で行う（アプリは作成・更新・閲覧だけ）。
    rulesDir: '',
    // 起動した文書作成・検証ウィンドウの件数上限などは持たない。実行の記録は各文書の
    // サイドカー（改訂履歴）が正で、dashboard 側に別の台帳を作らない。
  },
};
