'use strict';

// `npm run dist` の前に、パッケージへ入るはずの依存が手元に揃っているかを見る。
// statemachine-maker は file: リンクなので、その依存（yaml）は agent-app の npm install では
// 入らない（../statemachine-maker で npm install が要る）。無いまま electron-builder を走らせると
// 依存の収集で黙って落ち、パッケージ版がタスク画面を開いた瞬間に「Cannot find module 'yaml'」で止まる。

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const maker = path.join(ROOT, 'node_modules', 'statemachine-maker');
const makerPkg = JSON.parse(fs.readFileSync(path.join(maker, 'package.json'), 'utf8'));
const missing = Object.keys(makerPkg.dependencies || {}).filter((dep) => {
  try { require.resolve(`${dep}/package.json`, { paths: [fs.realpathSync(maker)] }); return false; } catch { return true; }
});
if (missing.length) {
  console.error(`check-dist: statemachine-maker の依存が入っていない: ${missing.join(', ')}\n` +
    '  cd ../statemachine-maker && npm install を先に実行してください');
  process.exit(1);
}
if (!fs.existsSync(path.join(ROOT, 'src', 'renderer', 'vendor', 'xterm.js'))) {
  console.error('check-dist: src/renderer/vendor/ が無い（npm install または npm run vendor）');
  process.exit(1);
}
if (!fs.existsSync(path.join(ROOT, 'assets', 'icon.ico'))) {
  console.error('check-dist: assets/icon.ico が無い（npm run icon）');
  process.exit(1);
}
console.log('check-dist: ok');
