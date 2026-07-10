'use strict';

// プロジェクトのリセット（charter 以外の全データを削除して最初からやり直す）。
// charter.md だけを残すので、稼働中の kiro-projects は次パスで「消化可能タスクなし」
// を検知して charter から再分解し、プロジェクトはゼロから再出発する。
// 削除対象の決定（plan）と実行（execute）を分け、実削除の手段（ゴミ箱 or rm）は
// 呼び出し側（ipc = Electron shell）が注入する（このモジュールは electron に依存しない）。

const fs = require('fs');
const path = require('path');

// 残すファイル（人が書く上位入力のうち、プロジェクトの目的そのもの）
const KEEP = new Set(['charter.md']);

// ドット始まりで唯一の削除対象（本体が立てる再分解要求マーカー。データの一部）。
// それ以外のドット始まり（.state-git 等）は同期機構の内部なので決して触らない —
// 管理クローンの manifest を残すことで、削除が次の同期で「ローカルの削除」として
// リモートへ伝播する（消したデータがリモートから復活しない）。
const DOT_TARGETS = new Set(['.replan.request']);

// 削除対象を列挙して検証する（実削除はしない）。charter.md が無いプロジェクトは
// 「残すものが無い」＝プロジェクト削除になってしまうため拒否する。
function planReset(projectDir) {
  const dir = path.resolve(String(projectDir || ''));
  if (!dir || !fs.existsSync(dir)) throw new Error(`プロジェクトディレクトリがありません: ${projectDir}`);
  if (!fs.existsSync(path.join(dir, 'charter.md'))) {
    throw new Error('charter.md が無いプロジェクトはリセットできません（残すものが無く、プロジェクト削除になってしまいます）');
  }
  const targets = [];
  for (const name of fs.readdirSync(dir)) {
    if (KEEP.has(name)) continue;
    if (name.startsWith('.') && !DOT_TARGETS.has(name)) continue; // 同期クローン等の内部は温存
    targets.push({ name, path: path.join(dir, name) });
  }
  targets.sort((a, b) => a.name.localeCompare(b.name));
  return { dir, keep: [...KEEP], targets };
}

// plan の対象を順に削除する。remover は (path) => Promise<via 文字列>（ipc は
// ゴミ箱移動、テストは fs 削除を注入する）。1 件の失敗で止めず、失敗は errors に集める
// （途中まで消えた状態で放置しない: 残りも消しに行く方が「リセット」の意図に合う）。
async function executeReset(plan, remover) {
  const removed = [];
  const errors = [];
  for (const t of plan.targets) {
    try {
      const via = await remover(t.path);
      removed.push({ name: t.name, via });
    } catch (err) {
      errors.push({ name: t.name, error: err && err.message ? err.message : String(err) });
    }
  }
  return { dir: plan.dir, keep: plan.keep, removed, errors };
}

module.exports = { planReset, executeReset };
