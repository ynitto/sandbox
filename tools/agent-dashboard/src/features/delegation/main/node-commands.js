'use strict';

// ノード宛て指示ドロップ（`schemas/agent-node-command.schema.json`）の書き手。
//
// **なぜ板へ直接書かないか**（S8-2/8-3）:
//
// 1. board-adapter は板の作業ディレクトリへファイルを置くだけで push しない。ローカル dir の
//    板なら成立するが、`git+<url>` の板では**書いたものが誰にも届かない**——押しても効かない
//    キャンセルボタンになっていた。板への書き込みと push は常駐体だけが行う。
// 2. 入札は lease と `(ts, who)` タイブレークを持つ claim プロトコル。UI 側に 2 つ目の実装を
//    作れば必ずずれる（二重落札を防ぐ判断を複製しない — S8-3）。
//
// 置き場は `$AGENT_COMMANDS_DIR`（既定 = 実行エンジンのホーム配下 `.agents/commands/`。
// Windows の画面 + WSL の実行エンジンでは WSL 側のホームを指す）。`control/`（望ましい状態）や
// `budget/` と同じ**ノードスコープ**の並びで、プロジェクト配下の `commands/` とは別物——
// 板はプロジェクトに属さないので、プロジェクト経由の口しか無いとプロジェクトを 1 つも
// 持たない PC（ワーカーノード）から板を操作できない。
//
// 取り込み側は常駐体の board tick（`agent_project/resident_cli.py`）。受理は
// `processed/<name>.json`、失敗は `<name>.json.err` に残り、画面はそれを読んで
// 「送信済み → 受理済み / 失敗」を出す（プロジェクト配下の commands/ と同じ表示規約）。

const fs = require('fs');
const os = require('os');
const path = require('path');

// 投函先は「実行エンジンが読む場所」でなければ意味がない。ホームの解決は engine.js が
// 1 つ持っている（Windows なら wslpath で WSL 側 $HOME/.agents を引く・60 秒キャッシュ）
// ので、そこを通す。ここで os.homedir() を使うと、正典構成（Windows の画面 + WSL の
// 実行エンジン）で投函先と取り込み先が別ファイルシステムになり、押しても何も起きない。
const engine = require('../../agent-project/main/engine');

const ACTIONS = new Set(['board-bid', 'board-cancel', 'board-award']);

function expandHome(p) {
  return String(p || '').replace(/^~(?=$|\/|\\)/, os.homedir());
}

function resolveCommandsDir(cfg) {
  const d = (cfg && cfg.delegation) || {};
  const declared = String(d.nodeCommandsDir || process.env.AGENT_COMMANDS_DIR || '').trim();
  if (declared) return expandHome(declared);
  // 旧ホーム（~/.agent）へは落とさない。実行エンジン側にそのフォールバックが無いので、
  // 落とすと「書けるのに誰も読まない場所」が増えるだけになる。旧ホームしか無い環境は
  // delegation.nodeCommandsDir で明示する（設定画面から辿れる）。
  return path.join(engine.agentsHome(cfg), 'commands');
}

function slugify(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
    .slice(0, 40) || 'x';
}

// ファイル名は時刻順に並ぶ形にする——常駐体は名前順に処理するので、同じ公示への
// 「入札 → 中止」が入れ替わると、中止済みの板へ入札を書くことになる。
function commandFileName(action, id) {
  const ts = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  return `viewer-${ts}-${slugify(action)}-${slugify(id)}.json`;
}

// 指示を 1 件投函する。書きかけを常駐体に読ませないよう .tmp に書いてから rename する。
function dropNodeCommand(cfg, { action, id, board, node, reason }) {
  if (!ACTIONS.has(String(action))) throw new Error(`未知のノード指示です: ${action}`);
  const did = String(id || '').trim();
  if (!did) throw new Error('委譲 id が必要です');
  if (action === 'board-award' && !String(node || '').trim()) {
    throw new Error('落札には対象ノードが必要です');
  }
  const dir = resolveCommandsDir(cfg);
  fs.mkdirSync(dir, { recursive: true });
  const rec = {
    command: String(action),
    id: did,
    ...(board ? { board: String(board) } : {}),
    ...(node ? { node: String(node) } : {}),
    reason: String(reason || ''),
    issued_by: 'agent-dashboard',
    issued_at: new Date().toISOString(),
  };
  const file = path.join(dir, commandFileName(action, did));
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, rec };
}

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

// 投函済み指示の状況（委譲 id ごとの最新 1 件）。画面が「送信済み → 受理済み / 失敗」を
// 出すための読み取り。未取り込み（pending）・受理（done）・失敗（error）の 3 状態。
function nodeCommandStatus(cfg) {
  const dir = resolveCommandsDir(cfg);
  const out = {};
  const put = (id, entry) => {
    if (!id) return;
    const prev = out[id];
    if (!prev || String(prev.at || '') <= String(entry.at || '')) out[id] = entry;
  };
  for (const name of safeList(dir)) {
    if (!name.endsWith('.json')) continue;
    const rec = readJson(path.join(dir, name));
    if (!rec) continue;
    put(String(rec.id || ''), { state: 'pending', action: String(rec.command || ''),
                                at: String(rec.issued_at || ''), source: name });
  }
  for (const name of safeList(dir)) {
    if (!name.endsWith('.json.err')) continue;
    const rec = readJson(path.join(dir, name));
    const cmd = (rec && rec.command) || {};
    put(String(cmd.id || ''), { state: 'error', action: String(cmd.command || ''),
                                at: String((rec && rec.failed_at) || ''),
                                error: String((rec && rec.error) || ''), source: name });
  }
  const pdir = path.join(dir, 'processed');
  for (const name of safeList(pdir)) {
    if (!name.endsWith('.json')) continue;
    const rec = readJson(path.join(pdir, name));
    if (!rec || !rec.ok) continue;
    put(String(rec.id || ''), { state: 'done', action: String(rec.action || ''),
                                at: String(rec.processed_at || ''),
                                detail: String(rec.detail || ''), source: String(rec.source || name) });
  }
  return out;
}

module.exports = { ACTIONS, resolveCommandsDir, dropNodeCommand, nodeCommandStatus,
                   commandFileName };
