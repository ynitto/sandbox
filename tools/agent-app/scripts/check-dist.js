'use strict';

// `npm run dist` の前に、パッケージへ入るはずのものが手元に揃っているかを見る。
// 無いまま electron-builder を走らせると、依存の収集や asar の作成で黙って落ちるか、
// パッケージ版だけが起動直後に「Cannot find module」で止まる。

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const missing = Object.keys(pkg.dependencies || {}).filter((dep) => {
  try { require.resolve(dep, { paths: [ROOT] }); return false; } catch { return true; }
});
if (missing.length) {
  console.error(`check-dist: 本番依存が入っていない: ${missing.join(', ')}\n  npm install を先に実行してください`);
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
