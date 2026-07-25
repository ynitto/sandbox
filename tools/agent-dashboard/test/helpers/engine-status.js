'use strict';

// テスト用: 実行エンジンの状況ファイル（engine/status.json）を一時ディレクトリへ書き、
// それを指す設定を返す。プロジェクト発見はこのファイルが唯一の入口（実装計画 W2-4）。

const fs = require('fs');
const os = require('os');
const path = require('path');

// roots: プロジェクトの置き場（実行側が `run --watch --root` に渡す値）の配列
function engineConfig(roots, extra = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agents-'));
  const dir = path.join(home, '.agents', 'engine');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'status.json'),
    JSON.stringify({
      node: 'pc-test',
      contract_version: 1,
      heartbeat: new Date().toISOString(),
      children: roots.map((root, i) => ({
        name: `p${i}`, alive: true, quarantined: false, deaths: 0, root,
      })),
    }),
    'utf8'
  );
  return { engine: { home: path.join(home, '.agents'), distro: '' }, projects: {}, ...extra };
}

module.exports = { engineConfig };
