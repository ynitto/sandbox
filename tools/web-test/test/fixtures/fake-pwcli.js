#!/usr/bin/env node
'use strict';
// テスト用の playwright-cli の代役（ブラウザなし）。web-test browse の判断だけを確かめるのに使う。
// run-code の中身で probe / 確かめ直し / 操作を見分け、決まった応答を返す。
// FAKE_PWCLI_MOVED=1 なら確かめ直しで位置が変わった要素を返す。呼ばれたコマンドを FAKE_PWCLI_LOG に追記する。
const fs = require('fs');
const args = process.argv.slice(2).filter((a) => !a.startsWith('-s=') && a !== '--raw');
if (process.env.FAKE_PWCLI_LOG) fs.appendFileSync(process.env.FAKE_PWCLI_LOG, args[0] + '\n');
const desc = (x) => ({ tag: 'button', type: 'button', role: null, id: null, name: null, aria: null, labelledby: null, expanded: null, text: '保存', disabled: false, bbox: { x, y: 10, width: 60, height: 30 } });
if (args[0] === 'run-code') {
  const code = args[1];
  let out;
  if (code.includes('visibleMatches')) out = { matches: 1, visibleMatches: 1, url: 'http://app.test/edit?token=abc#x', visible: true, enabled: true, desc: desc(100) };
  else if (code.includes("rejected: matches ?")) out = { desc: desc(process.env.FAKE_PWCLI_MOVED ? 140 : 100) };
  else out = { url: 'http://app.test/edit?token=abc' };
  process.stdout.write(JSON.stringify(out));
} else {
  process.stdout.write(`ran ${args[0]}\n`);
}
