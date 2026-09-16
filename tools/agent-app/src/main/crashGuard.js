'use strict';

// 落ち方を拾う。これまで main の unhandledRejection は握りつぶされ、renderer が落ちると白い画面のまま、
// 固まると Electron 既定の何も無い状態だった。ここでは 3 つだけを決める:
//   - main の uncaughtException / unhandledRejection … userData/logs/crash.log に残し、例外はダイアログでも 1 回知らせる
//   - renderer が落ちた（render-process-gone） … 記録して画面を読み直す（tmux の CLI は main 側で生きている）。
//     短時間に続けて落ちるなら読み直さず、ダイアログで知らせる
//   - renderer が固まった（unresponsive） … 「待つ」か「読み直す」を聞く
// 復旧の判断はここで閉じ、ログの書き方も 1 か所にする。

const fs = require('fs');
const path = require('path');

const MAX_LOG_BYTES = 1024 * 1024;
const RELOAD_WINDOW_MS = 60 * 1000;
const MAX_RELOADS = 3;

function describe(err) {
  if (typeof err === 'string') return err;
  if (err && err.stack) return String(err.stack);
  if (err && err.message) return String(err.message);
  try { return JSON.stringify(err); } catch { return String(err); }
}

// ログは 1 ファイル。上限を超えたら .1 へ回して書き直す（増え続けない）
function createLogger(file) {
  return (kind, detail) => {
    const line = `${new Date().toISOString()} [${kind}] ${describe(detail)}\n`;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      let size = 0;
      try { size = fs.statSync(file).size; } catch { /* 無ければ 0 */ }
      if (size + line.length > MAX_LOG_BYTES) {
        try { fs.renameSync(file, `${file}.1`); } catch { /* 回せなくても書く */ }
      }
      fs.appendFileSync(file, line, 'utf8');
    } catch { /* ログが書けないときに、さらに落とさない */ }
    return line;
  };
}

// process / window へ付ける前に、判断だけを取り出しておく（テストはここを叩く）
function createCrashGuard({ log, dialog, now = () => Date.now() }) {
  const reloads = [];
  let shownException = false;
  return {
    onUncaughtException(err) {
      log('uncaughtException', err);
      if (shownException) return;
      shownException = true;
      dialog.showErrorBox('Agent App で予期しないエラーが起きました', `${err && err.message ? err.message : String(err)}\n\n詳しい内容は crash.log に残しました。`);
    },
    onUnhandledRejection(reason) {
      log('unhandledRejection', reason);
    },
    // 戻り値: 'reload' | 'stop' | 'ignore'
    onRenderProcessGone(details) {
      const reason = details && details.reason ? String(details.reason) : 'unknown';
      log('render-process-gone', `${reason} (exitCode=${details && details.exitCode})`);
      if (reason === 'clean-exit' || reason === 'killed') return 'ignore';
      const t = now();
      while (reloads.length && t - reloads[0] > RELOAD_WINDOW_MS) reloads.shift();
      if (reloads.length >= MAX_RELOADS) {
        dialog.showErrorBox('画面が続けて落ちました', '画面を読み直しても直らないため、アプリを開き直してください。詳しい内容は crash.log に残しました。');
        return 'stop';
      }
      reloads.push(t);
      return 'reload';
    },
    // 戻り値は showMessageBox の応答。0 = 待つ、1 = 読み直す
    async onUnresponsive() {
      log('unresponsive', '画面が応答しません');
      const { response } = await dialog.showMessageBox({
        type: 'warning', buttons: ['待つ', '読み直す'], defaultId: 0, cancelId: 0,
        message: '画面が応答していません', detail: '待つと戻ることがあります。読み直すと画面だけを開き直します（動いている会話は残ります）。',
      });
      return response === 1 ? 'reload' : 'wait';
    },
  };
}

function install({ app, dialog, userData, processObject = process }) {
  const logFile = path.join(userData, 'logs', 'crash.log');
  const guard = createCrashGuard({ log: createLogger(logFile), dialog });
  processObject.on('uncaughtException', (err) => guard.onUncaughtException(err));
  processObject.on('unhandledRejection', (reason) => guard.onUnhandledRejection(reason));
  const attach = (win) => {
    const contents = win.webContents;
    contents.on('render-process-gone', (event, details) => {
      const action = guard.onRenderProcessGone(details);
      if (action === 'reload' && !win.isDestroyed()) contents.reload();
    });
    contents.on('unresponsive', () => {
      guard.onUnresponsive().then((action) => {
        if (action === 'reload' && !win.isDestroyed()) contents.forcefullyCrashRenderer();
      });
    });
  };
  return { logFile, guard, attach, app };
}

module.exports = { createCrashGuard, createLogger, install, MAX_RELOADS, RELOAD_WINDOW_MS };
