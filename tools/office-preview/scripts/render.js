'use strict';

// 手元で見た目を確かめるための入口。Electron で動かす。
//   npx electron scripts/render.js <入力ファイル> <出力.png> [幅]
// 複数を一度に撮るときは、入力と出力の組を続けて並べる。

const { app } = require('electron');
const fs = require('fs');

let renderPreview;
try {
  ({ renderPreview } = require('../src'));
} catch (err) {
  console.error(err);
  app.exit(2);
}

app.disableHardwareAcceleration();
app.on('window-all-closed', () => {}); // 隠しウィンドウを閉じても終わらせない

app.whenReady().then(async () => {
  const args = process.argv.slice(process.argv.findIndex((a) => /render\.js$/.test(a)) + 1).filter((a) => !a.startsWith('--'));
  let width;
  if (args.length % 2 === 1) width = Number(args.pop());
  let failed = false;
  for (let i = 0; i < args.length; i += 2) {
    try {
      const r = await renderPreview(args[i], width ? { width } : {});
      fs.writeFileSync(args[i + 1], r.data);
      console.log(`${args[i]} -> ${args[i + 1]} (${r.width}x${r.height})`);
    } catch (err) {
      failed = true;
      console.error(`${args[i]}: ${err.code || ''} ${err.message}`);
    }
  }
  app.exit(failed ? 1 : 0);
});
