'use strict';

// テスト用: 実行エンジンの状況ファイル（engine/status.json）を一時ディレクトリへ書き、
// それを指す設定を返す。プロジェクト発見はこのファイルが唯一の入口（実装計画 W2-4）。

const fs = require('fs');
const os = require('os');
const path = require('path');

// roots: プロジェクトの置き場（実行側が `run --watch --root` に渡す値）の配列
// extra: 返す設定へ足すキー
// statusExtra: status.json 自体へ足すブロック（例 board: {...}）
function engineConfig(roots, extra = {}, statusExtra = {}) {
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
      ...statusExtra,
    }),
    'utf8'
  );
  return { engine: { home: path.join(home, '.agents'), distro: '' }, projects: {}, ...extra };
}

// 板に参加している実行エンジンの状況。`location`（板の所在＝host.yaml の board）と
// `workdir`（この PC の作業フォルダ）の対が、画面の設定にある板フォルダを所在へ翻訳する
// 唯一の根拠になる（P0-2）。
function engineConfigWithBoard(boardWorkdir, { location = 'git+ssh://example/board.git',
                                               node = 'pc-test', extra = {} } = {}) {
  return engineConfig([], extra, {
    board: {
      configured: true, location, workdir: boardWorkdir, node_id: node,
      contract_version: 1, intake_projects: ['p0'], open_delegations: 0, my_bids: [],
      last_tick: new Date().toISOString(), last_error: null,
    },
  });
}

module.exports = { engineConfig, engineConfigWithBoard };
