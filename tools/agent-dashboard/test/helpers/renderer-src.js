'use strict';

// 分割された renderer のソースを結合して 1 本の文字列で返す。
// renderer.js は保守性のため機能ごとに分割された（core=renderer.js + sections/*.js +
// bootstrap.js）。テストの文字列アサーションがコードのファイル位置に依存しないよう、
// 元の renderer.js 相当の全文をここで組み立てる。読み込み順（index.html）と同じ順で結合する。

const fs = require('fs');
const path = require('path');

const RENDERER_DIR = path.join(__dirname, '..', '..', 'src', 'renderer');

// index.html の読み込み順に合わせたセクション順（結合順は表示に影響しないが、意味的に揃える）。
const SECTION_ORDER = [
  'overview', 'backlog', 'authoring', 'form-edit', 'needs', 'flow',
  'node-detail', 'gitlab', 'history', 'amigos', 'orchestration', 'cowork', 'kiro-loop',
];

function read() {
  const files = [path.join(RENDERER_DIR, 'renderer.js')];
  for (const s of SECTION_ORDER) files.push(path.join(RENDERER_DIR, 'sections', `${s}.js`));
  files.push(path.join(RENDERER_DIR, 'bootstrap.js'));
  return files.map((f) => fs.readFileSync(f, 'utf8')).join('\n');
}

module.exports = { read, RENDERER_DIR, SECTION_ORDER };
