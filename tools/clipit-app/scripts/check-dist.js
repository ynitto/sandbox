"use strict";

// npm run dist の前に、extraResources で同梱する clipit-helper.exe が
// 用意できているかを確認する。無いと electron-builder の失敗が分かりにくいため、
// 先に手順を案内して止める。

const fs = require("node:fs");
const path = require("node:path");

const exe = path.join(__dirname, "..", "helper", "clipit-helper.exe");
if (!fs.existsSync(exe)) {
  console.error(
    "helper/clipit-helper.exe がありません。先に Windows で helper\\build_helper.bat を実行し、" +
      "生成された helper\\dist\\clipit-helper.exe を helper\\clipit-helper.exe にコピーしてください（README の「Windows 向けビルド」参照）。"
  );
  process.exit(1);
}
