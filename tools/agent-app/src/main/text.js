'use strict';

// 端末出力のテキスト処理。ipc（ヘッドレス）と tmux（対話）の両方が使う。

// 端末向けの装飾を剥がす。kiro は --no-interactive でも色と入力欄の `> ` を出す。
function stripAnsi(text) {
  return String(text).replace(/\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]/g, '');
}

function cleanAnswer(text) {
  return stripAnsi(text).trim().replace(/^>\s+/, '');
}

function lineEmitter(onLine) {
  let buf = '';
  return (chunk) => {
    buf += chunk.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      onLine(stripAnsi(buf.slice(0, i)).replace(/\r$/, ''));
      buf = buf.slice(i + 1);
    }
  };
}

// grep -E 向けの ERE（定義ファイルの ready_pattern 等）を JS の RegExp に写す。
// POSIX のブラケットクラスだけ書き換えれば、残りは JS と同じに読める。
const POSIX_CLASSES = {
  space: '\\s', blank: ' \\t', alnum: 'A-Za-z0-9', alpha: 'A-Za-z', digit: '0-9',
  upper: 'A-Z', lower: 'a-z', punct: '!-\\/:-@\\[-`{-~', xdigit: '0-9A-Fa-f', print: ' -~', graph: '!-~', cntrl: '\\x00-\\x1f',
};
function ereToRegExp(ere, flags = 'im') {
  const src = String(ere || '')
    .replace(/\[\[:(\w+):\]\]/g, (m, name) => (name === 'space' ? '\\s' : `[${POSIX_CLASSES[name] || ''}]`))
    .replace(/\[:(\w+):\]/g, (m, name) => (name === 'space' ? '\\s' : POSIX_CLASSES[name] || ''));
  try { return new RegExp(src, flags); } catch { return null; }
}

module.exports = { stripAnsi, cleanAnswer, lineEmitter, ereToRegExp };
