'use strict';

// preload が window へ置く窓口（contextBridge）と renderer の宣言がぶつからないこと。
//
// なぜこの検査が要るか（実際に起きた事故）:
//   contextBridge.exposeInMainWorld('api', …) が定義する `window.api` は **再定義できない**
//   （non-configurable）。renderer の先頭で `const api = window.api;` と受けると、
//   スクリプトの**実行前**に "Identifier 'api' has already been declared" で落ち、
//   renderer が 1 行も動かない——画面には静的な HTML と CSS だけが残り、**真っ白**に見える。
//   コンソールにも自分のログは出ないので、原因が非常に見えにくい。
//
//   ブラウザに `window.api = {…}` を代入して描画を確かめる方法ではこの事故を再現できない
//   （代入で作ったプロパティは configurable なので衝突しない）。だから**実機の起動**
//   （test/electron-smoke.test.js）と、この静的検査の 2 つで押さえる。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

// preload が公開するグローバル名（`contextBridge.exposeInMainWorld('<name>'`）。
function exposedGlobals(preload) {
  return [...preload.matchAll(/exposeInMainWorld\(\s*'([^']+)'/g)].map((m) => m[1]);
}

// renderer の**トップレベル**の宣言（行頭から始まるものだけ見る。関数の中の同名は衝突しない）。
function topLevelDeclarations(source) {
  return new Set([...source.matchAll(/^(?:const|let|var|class|function)\s+([A-Za-z_$][\w$]*)/gm)].map((m) => m[1]));
}

test('renderer は preload が window へ置いた名前を宣言し直さない（真っ白画面の防止）', () => {
  const exposed = exposedGlobals(read('preload.js'));
  assert.ok(exposed.length, 'preload が何も公開していない');
  const declared = topLevelDeclarations(read('renderer/renderer.js'));
  for (const name of exposed) {
    assert.ok(!declared.has(name),
      `renderer/renderer.js がトップレベルで '${name}' を宣言しています。`
      + `contextBridge が置いた window.${name} は再定義できないため、`
      + '"Identifier has already been declared" でスクリプト全体が実行されず画面が真っ白になります。'
      + `宣言せずにグローバルの ${name} をそのまま使ってください。`);
  }
});

test('renderer が使う api.* は preload にあり、その IPC チャネルを main が受ける', () => {
  const preload = read('preload.js');
  const renderer = read('renderer/renderer.js');
  const ipc = read('main/ipc.js');
  const exposedKeys = new Set([...preload.matchAll(/^\s{2}(\w+): /gm)].map((m) => m[1]));
  for (const m of renderer.matchAll(/\bapi\.(\w+)\(/g)) {
    assert.ok(exposedKeys.has(m[1]), `preload に無い api: ${m[1]}`);
  }
  for (const m of preload.matchAll(/invoke\('([\w:]+)'/g)) {
    assert.ok(ipc.includes(`handle('${m[1]}'`), `ipc.js が受けないチャネル: ${m[1]}`);
  }
});
