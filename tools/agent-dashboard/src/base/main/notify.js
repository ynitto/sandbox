'use strict';

// OS 通知プリミティブ（制御スタックに依存しない汎用シェル機能）。
// 「何を・なぜ通知するか」の判断は renderer（agent-project の意味を知る側）が持ち、
// ここは受け取った文言で OS 通知・タスクバーバッジ・ウィンドウフラッシュを出すだけ。
//
// ウィンドウがフォーカス中はポップアップとフラッシュを抑制する（画面を見ている人への
// 騒音を避ける）。バッジ（未対応の総数）だけは常に更新する。通知をクリックすると窓を
// 前面化し、既存のディープリンク経路（app:openTarget）で対象プロジェクトを開く
// ＝ main.js の handleDeepLink / renderer の handleOpenTarget と同じ配線を再利用する。
//
// electron は関数内で遅延 require する（純関数 targetUrl をテストから electron 無しで
// 読めるようにするため。他の main モジュールも electron を持ち込まない方針に揃える）。

// single-instance なので最初のウィンドウが本体。
function mainWindow() {
  const { BrowserWindow } = require('electron');
  const wins = BrowserWindow.getAllWindows();
  return wins.length ? wins[0] : null;
}

// タスクバー / Dock のバッジに未対応の総数を出す（対応 OS のみ）。
// Windows は setBadgeCount 非対応だが黙って無視される（フラッシュと通知で補う）。
function setBadge(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  try {
    const { app } = require('electron');
    if (app && typeof app.setBadgeCount === 'function') app.setBadgeCount(n);
  } catch {
    /* 非対応プラットフォームは無視 */
  }
}

// target（{ root, name }）から既存のディープリンク URL を組み立てる。
// 空 target なら空文字（クリックしても窓を前面化するだけ）。
function targetUrl(target) {
  if (!target || !target.root) return '';
  const params = new URLSearchParams();
  params.set('root', String(target.root));
  if (target.name) params.set('project', String(target.name));
  return `agent-dashboard://open?${params.toString()}`;
}

// renderer からの 1 回の通知要求。
//   { title, body, target:{root,name}, badgeCount, flash, silent }
// silent=true はバッジ更新だけ（差分の無いポーリングで総数だけ合わせる）。
function notify(payload = {}) {
  const { title, body, target, badgeCount, flash, silent } = payload || {};
  if (badgeCount !== undefined) setBadge(badgeCount);
  if (silent || !title) return { shown: false };

  const win = mainWindow();
  // 見ている最中はポップアップもフラッシュも出さない（バッジは上で更新済み）。
  if (win && win.isFocused()) return { shown: false, focused: true };

  if (flash && win && typeof win.flashFrame === 'function') {
    try {
      win.flashFrame(true);
    } catch {
      /* 無視 */
    }
  }

  const { Notification } = require('electron');
  if (!Notification || typeof Notification.isSupported !== 'function' || !Notification.isSupported()) {
    return { shown: false, unsupported: true };
  }

  const url = targetUrl(target);
  const n = new Notification({ title: String(title), body: String(body || '') });
  n.on('click', () => {
    const w = mainWindow();
    if (!w) return;
    if (w.isMinimized()) w.restore();
    w.focus();
    if (typeof w.flashFrame === 'function') {
      try {
        w.flashFrame(false);
      } catch {
        /* 無視 */
      }
    }
    if (url) w.webContents.send('app:openTarget', { url });
  });
  n.show();
  return { shown: true, url };
}

module.exports = { notify, setBadge, targetUrl };
