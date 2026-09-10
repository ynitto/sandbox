'use strict';

// タスクを AI と作る tmux 会話の「約束事」。main（依頼文を組む側）と renderer（AI の返答から
// 見本の依頼を拾い、ボタンで固定文を送る側）の両方が同じ 1 か所を読む。Node からも読める純粋なモジュール。
//
//   見本の依頼 … AI が画面操作の見本を要るとき、返答の中に次の 1 行を単独で書く。
//                 @record browser <開始 URL>
//                 @record windows <アプリ名>
//   ブラウザの見本 … 利用者は 1 つのボタンを 3 回押す。押すたびに、いま会話がどこにいるかを言う
//                 固定文が tmux 経由で AI へ渡る。
//                   1.「ブラウザを開く」… このアプリが Edge をリモートデバッグ付きで起こす
//                        → @recording open  AI は接続だけして待つ（利用者がログイン・画面の移動をする）
//                   2.「記録を始める」  … 準備が終わった合図
//                        → @recording start AI が recording-start で記録を始める
//                   3.「終了してAIへ渡す」… 操作の終わり
//                        → @recording stop  AI が recording-stop で止め、保存して工程に起こす
//                 途中で「やり直す」を押すと @recording cancel が渡り、AI は記録を捨てて 1. を待ち直す。
//   Windows アプリの見本 … 記録は利用者の端末（このアプリ）の winauto で取る。AI（Windows では WSL
//                 の tmux）は winauto の記録を起こさない。ボタンは「記録を始める」「終了してAIへ渡す」の 2 段。
(function exposeTeachingProtocol() {
  const MARKER = '@record';
  const RECORDING_MARKER = '@recording';
  const SOURCES = ['browser', 'windows'];
  // ブラウザの見本で AI が接続する CDP の接続先（main/automation/browser.js の PORT と揃える）。
  const DEFAULT_ENDPOINT = 'http://localhost:9222';
  // 画面の種類ごとの段。ボタン 1 つがこの順に進む（renderer が現在地を持つ）。
  const RECORDING_STEPS = { browser: ['open', 'start', 'stop'], windows: ['start', 'stop'] };
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

  function recordingSteps(source) {
    return RECORDING_STEPS[source === 'windows' ? 'windows' : 'browser'];
  }

  function recordingsDir(machine) {
    return `.statemachine/${String(machine || '').trim()}/recordings/`;
  }

  function endpointOf(endpoint) {
    return String(endpoint || '').trim() || DEFAULT_ENDPOINT;
  }

  // 固定文を会話に接ぐ枕。AI が自分で頼んだ見本なら、その依頼を指して続きだと分かるようにする。
  // 依頼が無い（利用者が自分から見せに来た）ときは fallback を使う。
  function opening(request, fallback = '') {
    const target = request && String(request.target || '').trim();
    if (!request) return fallback;
    return target ? `先ほど依頼のあった見本（${target}）を取ります。` : '依頼のあった操作の見本を取ります。';
  }

  // 1.「ブラウザを開く」で AI へ渡す固定文。接続だけさせて、記録はまだ始めさせない。
  function recordingOpenMessage({ endpoint = DEFAULT_ENDPOINT, url = '', browser = 'Edge', request = null } = {}) {
    const cdp = endpointOf(endpoint);
    return [
      `${RECORDING_MARKER} open`,
      `${opening(request, '操作の見本を取ります。')}ブラウザ（${browser}）をリモートデバッグ付きで起動しました。接続先: ${cdp}`,
      url ? `開いたページ: ${url}` : '開いたページ: （未指定。利用者がこれから開きます）',
      `\`playwright-cli attach --cdp=${cdp}\` で接続だけしておき、つながったかどうかを 1 行で知らせてください。`,
      'いまは利用者がログインや画面の移動などの準備をしています。まだ記録を始めないでください（`playwright-cli recording-start` は次の `@recording start` が届いてからです）。準備の操作を見本に混ぜないため、ここではブラウザを操作しないでください。',
    ].join('\n');
  }

  // 2.「記録を始める」で AI へ渡す固定文。準備が終わり、ここからが見本になる。
  function recordingStartMessage({ endpoint = DEFAULT_ENDPOINT, page = '', request = null } = {}) {
    const cdp = endpointOf(endpoint);
    return [
      `${RECORDING_MARKER} start`,
      `${opening(request)}利用者の準備が終わりました。ここからの操作が見本です。`,
      page ? `記録の起点になるページ: ${page}` : '記録の起点になるページ: （読み取れませんでした。いまブラウザに出ている画面が起点です）',
      `まだ接続していなければ \`playwright-cli attach --cdp=${cdp}\` で接続し、\`playwright-cli recording-start\` で操作の記録を始めてください。`,
      '始めたら 1 行で知らせて、利用者が操作を終えて「終了してAIへ渡す」を押すのを待ってください。利用者が操作している間は、あなたはブラウザを操作しないでください。',
    ].join('\n');
  }

  // 3.「終了してAIへ渡す」で AI へ渡す固定文。
  function recordingStopMessage({ machine } = {}) {
    return [
      `${RECORDING_MARKER} stop`,
      '操作が終わりました。`playwright-cli recording-stop` で記録を止めてください。',
      `記録の行（Playwright のコード行）はそのまま \`${recordingsDir(machine)}<時刻>-browser.md\` に保存し、\`playwright-cli detach\` でブラウザから切り離してください（ブラウザは閉じません）。`,
      'その見本を根拠に工程を組み（または直し）、固定値か毎回変わる値かが曖昧な点だけ質問してください。パスワードらしい値は定義に残さないでください。',
    ].join('\n');
  }

  // 「やり直す」で AI へ渡す固定文。取りかけの記録は残さない。
  function recordingCancelMessage({ machine } = {}) {
    return [
      `${RECORDING_MARKER} cancel`,
      '利用者が見本を取り直します。',
      `記録を始めていれば \`playwright-cli recording-stop\` で止め、その記録は使わずに破棄してください（\`${recordingsDir(machine)}\` には保存しないでください）。`,
      '`playwright-cli detach` で切り離し、次の `@recording open` が届くまで待ってください。ブラウザは開いたままでかまいません。',
    ].join('\n');
  }

  const protocol = {
    MARKER, RECORDING_MARKER, SOURCES, DEFAULT_ENDPOINT, RECORDING_STEPS,
    parseRecordRequest, recordLine, recordingSteps, recordingsDir,
    recordingOpenMessage, recordingStartMessage, recordingStopMessage, recordingCancelMessage,
  };
  if (typeof window === 'undefined') module.exports = protocol;
  else window.TeachingProtocol = protocol;
}());
