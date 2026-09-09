'use strict';

// タスクを AI と作る tmux 会話の「約束事」。main（依頼文を組む側）と renderer（AI の返答から
// 見本の依頼を拾い、ボタンで固定文を送る側）の両方が同じ 1 か所を読む。Node からも読める純粋なモジュール。
//
//   見本の依頼 … AI が画面操作の見本を要るとき、返答の中に次の 1 行を単独で書く。
//                 @record browser <開始 URL>
//                 @record windows <アプリ名>
//   ブラウザの見本 … 利用者が「記録を始める」を押すと、このアプリが Edge をリモートデバッグ付きで
//                 起こし、固定文（recordingStartMessage）を tmux 経由で AI へ渡す。AI はその接続先に
//                 playwright-cli で接続して記録を始める。操作が終わって「終了してAIへ渡す」を押すと
//                 固定文（recordingStopMessage）が渡り、AI が記録を止めて工程に起こす。
//   Windows アプリの見本 … 記録は利用者の端末（このアプリ）の winauto で取る。AI（Windows では WSL
//                 の tmux）は winauto の記録を起こさない。
(function exposeTeachingProtocol() {
  const MARKER = '@record';
  const RECORDING_MARKER = '@recording';
  const SOURCES = ['browser', 'windows'];
  // ブラウザの見本で AI が接続する CDP の接続先（main/automation/browser.js の PORT と揃える）。
  const DEFAULT_ENDPOINT = 'http://localhost:9222';
  const LINE_RE = /^\s*(?:[>*\-•]\s*)?@record\s+(browser|windows)(?:\s*[:：]?\s*(.*?))?\s*$/i;

  // 返答本文から見本の依頼を拾う（最後の 1 件）。無ければ null。
  function parseRecordRequest(text) {
    const lines = String(text || '').split(/\r?\n/);
    let found = null;
    for (const line of lines) {
      const m = LINE_RE.exec(line);
      if (!m) continue;
      const source = m[1].toLowerCase();
      const target = String(m[2] || '').trim().replace(/^[`"'「]+|[`"'」]+$/g, '');
      found = { source, target };
    }
    return found;
  }

  function recordLine(source, target = '') {
    const kind = SOURCES.includes(source) ? source : 'browser';
    return `${MARKER} ${kind}${target ? ` ${target}` : ''}`;
  }

  function recordingsDir(machine) {
    return `.statemachine/${String(machine || '').trim()}/recordings/`;
  }

  // 「記録を始める」で AI へ渡す固定文。1 行目の印（@recording start）で AI が見分ける。
  function recordingStartMessage({ endpoint = DEFAULT_ENDPOINT, url = '', browser = 'Edge' } = {}) {
    const cdp = String(endpoint || '').trim() || DEFAULT_ENDPOINT;
    return [
      `${RECORDING_MARKER} start`,
      `ブラウザ（${browser}）をリモートデバッグ付きで起動しました。接続先: ${cdp}`,
      url ? `開始 URL: ${url}` : '開始 URL: （未指定。利用者がブラウザで開きます）',
      `\`playwright-cli attach --cdp=${cdp}\` で接続し、\`playwright-cli recording-start\` で操作の記録を始めてください。`,
      '始めたら 1 行で知らせて、利用者が操作を終えて「終了してAIへ渡す」を押すのを待ってください。利用者が操作している間は、あなたはブラウザを操作しないでください。',
    ].join('\n');
  }

  // 「終了してAIへ渡す」で AI へ渡す固定文。
  function recordingStopMessage({ machine } = {}) {
    return [
      `${RECORDING_MARKER} stop`,
      '操作が終わりました。`playwright-cli recording-stop` で記録を止めてください。',
      `記録の行（Playwright のコード行）はそのまま \`${recordingsDir(machine)}<時刻>-browser.md\` に保存し、\`playwright-cli detach\` でブラウザから切り離してください（ブラウザは閉じません）。`,
      'その見本を根拠に工程を組み（または直し）、固定値か毎回変わる値かが曖昧な点だけ質問してください。パスワードらしい値は定義に残さないでください。',
    ].join('\n');
  }

  const protocol = { MARKER, RECORDING_MARKER, SOURCES, DEFAULT_ENDPOINT, parseRecordRequest, recordLine, recordingsDir, recordingStartMessage, recordingStopMessage };
  if (typeof window === 'undefined') module.exports = protocol;
  else window.TeachingProtocol = protocol;
}());
