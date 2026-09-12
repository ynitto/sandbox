'use strict';

// 画面が前面に無いときだけ、OS の通知で 1 行知らせる。
//
// 出すのは「どの会話（どのタスク）が、どうなったか」だけ。依頼や応答の本文は通知へ出さない
// （通知は画面の外に残り、消せない）。画面に出す言葉と同じく、内部の綴り（phase の名前や
// チャネル名）も出さない——状態は下の LABEL だけを通す。
//
// 通知を押したら、その会話を開く（`notify:open`）。Windows ではタスクバーも点滅させる
// （`flashFrame`）。前面にあるときは画面そのものが答えを出しているので、何も出さない。

// 会話・タスクの状態 → 通知に出す語。renderer の PHASE_LABEL と同じ言い回しにそろえる。
const LABEL = {
  attention: '確認待ち',
  done: '応答が終わりました',
  failed: '応答に失敗しました',
  taskDone: '実行が完了しました',
  taskAttention: '実行の確認が必要です',
  taskFailed: '実行に失敗しました',
};

const MAX_NAME_CHARS = 60;

// 通知の 1 行。名前が無いものは知らせない（何のことか分からない通知は邪魔になるだけ）。
function notificationText({ kind, name } = {}) {
  const label = LABEL[String(kind || '')];
  const title = String(name || '').trim();
  if (!label || !title) return '';
  return `${title.slice(0, MAX_NAME_CHARS)} · ${label}`;
}

// ターンの結果・タスクの実行結果を、通知の種類へ写す。
function turnKind({ error = '', stopped = false } = {}) {
  if (stopped) return '';                 // 利用者が止めたのだから、知らせる相手はもう見ている
  return error ? 'failed' : 'done';
}

function taskRunKind({ ok = false, escalate = false } = {}) {
  return ok ? 'taskDone' : escalate ? 'taskAttention' : 'taskFailed';
}

// getWindow … 主ウィンドウ（無ければ null）
// enabled  … 設定「前面に無いときに通知する」を読む
// open     … 通知を押したときに呼ぶ（会話を開く）
// electron … 差し替え用（試験）。既定は electron の Notification
function createNotifier({ getWindow, enabled = () => true, open = () => {}, electron = null } = {}) {
  function show(event) {
    if (!enabled()) return null;
    const win = typeof getWindow === 'function' ? getWindow() : null;
    if (!win || win.isDestroyed() || win.isFocused()) return null;
    const text = notificationText(event);
    if (!text) return null;
    let Notification;
    try { ({ Notification } = electron || require('electron')); } catch { return null; }
    if (!Notification || (Notification.isSupported && !Notification.isSupported())) return null;
    const notification = new Notification({ title: text });
    notification.on('click', () => {
      if (win.isDestroyed()) return;
      if (typeof win.restore === 'function' && win.isMinimized && win.isMinimized()) win.restore();
      win.focus();
      open(event);
    });
    notification.show();
    if (process.platform === 'win32' && typeof win.flashFrame === 'function') win.flashFrame(true);
    return notification;
  }
  return { show };
}

module.exports = { LABEL, notificationText, turnKind, taskRunKind, createNotifier };
