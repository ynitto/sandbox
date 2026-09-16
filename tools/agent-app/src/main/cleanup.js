'use strict';

// アプリが自分で作ったファイルを数え、選ばれた分だけ消す。
//
// 対象はアプリの保存先（userData）と、その外に置いた作業用のファイルだけにする。
// リポジトリの中（作業フォルダ・タスクとワークフローの定義）は利用者の成果物と
// 隣り合うので、ここでは触らない（README「保存データの整理」）。
//
//   scan(options)   … 種類ごとに { files, bytes } を数える（消さない）
//   remove(options) … 選ばれた種類の files を消し、空けた大きさを返す
//
// 種類の定義は KINDS の 1 か所に持つ。collect は「消してよいものの絶対パス」を並べ、
// 数えるのも消すのも同じ並びを使う（画面に出した大きさと、実際に消す対象がずれない）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const LEDGER_KEEP_DAYS = 30;
const UUID_RE = /^[0-9a-f-]{36}$/;

function statOf(file) {
  try { return fs.lstatSync(file); } catch { return null; }
}

function listDir(dir) {
  try { return fs.readdirSync(dir); } catch { return []; }
}

// 1 つのパス（ファイルでもフォルダでも）の大きさ。シンボリックリンクは辿らない
// （kiro の記録は本物の設定へのリンクを持つ。辿ると他人の大きさを数えてしまう）。
function sizeOf(target) {
  const st = statOf(target);
  if (!st) return 0;
  if (st.isSymbolicLink()) return 0;
  if (st.isFile()) return st.size;
  if (!st.isDirectory()) return 0;
  let total = 0;
  for (const name of listDir(target)) total += sizeOf(path.join(target, name));
  return total;
}

function removePath(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

// 会話が参照している添付の ID。
function usedAttachments(sessions) {
  const used = new Set();
  for (const sess of sessions || []) {
    for (const message of (sess && sess.messages) || []) {
      for (const a of message.attachments || []) if (a && a.id) used.add(String(a.id));
    }
  }
  return used;
}

function sessionIds(sessions) {
  return new Set((sessions || []).map((s) => String((s && s.id) || '')).filter(Boolean));
}

// 端末の画面記録は会話ファイルの中にあるので、パスではなく「その会話の控えの文字数」を数える。
// 動いている会話（live）の控えは画面の続きに使うので残す。
function snapshotsOf(sessions) {
  const out = [];
  for (const sess of sessions || []) {
    if (!sess || sess.live) continue;
    const shots = Array.isArray(sess.terminalSnapshots) ? sess.terminalSnapshots : [];
    if (!shots.length) continue;
    out.push({ id: String(sess.id), bytes: shots.reduce((n, s) => n + Buffer.byteLength(String((s && s.screenText) || ''), 'utf8'), 0) });
  }
  return out;
}

function dayNumber(name) {
  const m = /^(\d{4})(\d{2})(\d{2})\.jsonl$/.exec(name);
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

// 一時フォルダに置く自分のファイルの形。返すのは書いた起動の番号（分からなければ null）。
//   agent-app-<番号>-<時刻>-<乱数>.txt … CLI の出力（agentCli）
//   agent-app-run|assist-<番号>-<時刻>.txt … タスクの実行・AI 支援の出力
//   agent-app-update-<番号>.cmd / agent-app-update.log … 入れ替えの手順とその記録
const TEMP_SHAPES = [
  /^agent-app-(?:run-|assist-)?(\d+)-[0-9a-z]+(?:-[0-9a-z]+)?\.txt$/,
  /^agent-app-update-(\d+)\.cmd$/,
];

function tempOwner(name) {
  if (name === 'agent-app-update.log') return 0;
  for (const shape of TEMP_SHAPES) {
    const m = shape.exec(name);
    if (m) return Number(m[1]);
  }
  return null;
}

// 種類ごとの定義。collect は消してよい絶対パスの並びを返す。
const KINDS = [
  {
    key: 'temp',
    title: '一時ファイル',
    detail: '使い終わった作業用ファイルを削除します。',
    defaultOn: true,
    collect({ tmpdir, pid }) {
      const out = [];
      for (const name of listDir(tmpdir)) {
        const owner = tempOwner(name);
        // 名前の形が分かっているものだけを消す（agent-app- で始まるだけのフォルダには
        // 試験の作業場などが混ざる）。いま動いている自分の出力は取りに行く前かもしれないので残す。
        if (owner == null || owner === pid) continue;
        const file = path.join(tmpdir, name);
        if (statOf(file)?.isFile()) out.push(file);
      }
      const maker = path.join(tmpdir, 'statemachine-maker');
      for (const name of listDir(maker)) {
        if (/^record-[0-9a-z]+\.(?:jsonl|stop)$/.test(name)) out.push(path.join(maker, name));
      }
      return out;
    },
  },
  {
    key: 'attachments',
    title: '添付ファイル',
    detail: '会話で使われていない添付ファイルを削除します。',
    defaultOn: true,
    collect({ userData, sessions }) {
      const used = usedAttachments(sessions);
      const base = path.join(userData, 'attachments');
      return listDir(base).filter((name) => UUID_RE.test(name) && !used.has(name)).map((name) => path.join(base, name));
    },
  },
  {
    key: 'cliSessions',
    title: '会話の再開情報',
    detail: '削除済みの会話の再開情報を削除します。',
    defaultOn: true,
    collect({ home, sessions }) {
      const ids = sessionIds(sessions);
      const base = path.join(home, '.local', 'state', 'agent-app', 'cli-sessions');
      return listDir(base).filter((name) => !ids.has(name)).map((name) => path.join(base, name));
    },
  },
  {
    key: 'runHistory',
    title: '実行履歴',
    detail: '登録を解除したリポジトリの実行履歴を削除します。',
    defaultOn: true,
    collect({ userData, repos }) {
      const keep = new Set((repos || []).map((repo) => `${crypto.createHash('sha256').update(String(repo)).digest('hex')}.json`));
      const base = path.join(userData, 'run-history');
      return listDir(base).filter((name) => name.endsWith('.json') && !keep.has(name)).map((name) => path.join(base, name));
    },
  },
  {
    key: 'updates',
    title: '更新ファイル',
    detail: 'ダウンロードした更新ファイルを削除します。',
    defaultOn: true,
    collect({ userData }) {
      const base = path.join(userData, 'updates');
      return listDir(base).map((name) => path.join(base, name));
    },
  },
  {
    key: 'exports',
    title: '書き出したテキスト',
    detail: '会話から書き出したテキストファイルを削除します。会話の本文は残ります。',
    defaultOn: true,
    collect({ userData }) {
      const base = path.join(userData, 'exports');
      return listDir(base).map((name) => path.join(base, name));
    },
  },
  {
    key: 'share',
    title: '共有の履歴',
    detail: `${LEDGER_KEEP_DAYS}日より前の受付記録と一時作業フォルダを削除します。`,
    defaultOn: true,
    collect({ userData, now }) {
      const out = [];
      const ledger = path.join(userData, 'share', 'ledger');
      const limit = now - LEDGER_KEEP_DAYS * 86400000;
      for (const name of listDir(ledger)) {
        const day = dayNumber(name);
        if (day != null && day < limit) out.push(path.join(ledger, name));
      }
      const scratch = path.join(userData, 'share', 'scratch');
      for (const name of listDir(scratch)) out.push(path.join(scratch, name));
      return out;
    },
  },
  {
    key: 'auditFeed',
    title: '利用状況の集計に使った記録',
    detail: `${LEDGER_KEEP_DAYS}日より前の申告を削除します。利用状況の集計結果は残ります。`,
    defaultOn: true,
    collect({ userData, now }) {
      const out = [];
      const feed = path.join(userData, 'audit-feed');
      const limit = now - LEDGER_KEEP_DAYS * 86400000;
      for (const name of listDir(feed)) {
        const day = dayNumber(name);
        if (day != null && day < limit) out.push(path.join(feed, name));
      }
      return out;
    },
  },
  {
    key: 'browserProfile',
    title: 'ブラウザデータ',
    detail: '記録用ブラウザのデータを削除します。再ログインが必要です。',
    defaultOn: false,
    collect({ userData }) {
      const base = path.join(userData, 'recording-browser-profile');
      return statOf(base) ? [base] : [];
    },
  },
];

// 会話ファイルの中にある控えは、パスを消すのではなく会話を書き直して外す。
const SNAPSHOTS = {
  key: 'snapshots',
  title: '端末の画面記録',
  detail: '端末の画面記録を削除し、会話の本文は残します。',
  defaultOn: true,
};

function options({ userData, sessions = [], repos = [], home = os.homedir(), tmpdir = os.tmpdir(), pid = process.pid, now = Date.now() }) {
  return { userData, sessions, repos, home, tmpdir, pid, now };
}

// 種類ごとに数える。消す対象が無い種類も、0 として並べる（画面の行は毎回同じ並び）。
function scan(input) {
  const opts = options(input);
  const items = KINDS.map((kind) => {
    const files = kind.collect(opts);
    return {
      key: kind.key, title: kind.title, detail: kind.detail, defaultOn: kind.defaultOn,
      count: files.length, bytes: files.reduce((n, file) => n + sizeOf(file), 0),
    };
  });
  const shots = snapshotsOf(opts.sessions);
  items.push({
    key: SNAPSHOTS.key, title: SNAPSHOTS.title, detail: SNAPSHOTS.detail, defaultOn: SNAPSHOTS.defaultOn,
    count: shots.length, bytes: shots.reduce((n, s) => n + s.bytes, 0),
  });
  return { scannedAt: new Date(opts.now).toISOString(), items };
}

// 選ばれた種類を消す。1 つ消せなくても残りは進める（消せない理由は数に出す）。
//   clearSnapshots … 会話 ID を受け取って控えを外す（呼ぶ側が store を渡す）
function remove(input, keys = [], { clearSnapshots = () => {} } = {}) {
  const opts = options(input);
  const wanted = new Set((keys || []).map(String));
  let freed = 0;
  let removed = 0;
  let failed = 0;
  for (const kind of KINDS) {
    if (!wanted.has(kind.key)) continue;
    for (const file of kind.collect(opts)) {
      const bytes = sizeOf(file);
      try { removePath(file); freed += bytes; removed += 1; } catch { failed += 1; }
    }
  }
  if (wanted.has(SNAPSHOTS.key)) {
    for (const shot of snapshotsOf(opts.sessions)) {
      try { clearSnapshots(shot.id); freed += shot.bytes; removed += 1; } catch { failed += 1; }
    }
  }
  return { freed, removed, failed };
}

module.exports = { KINDS, SNAPSHOTS, LEDGER_KEEP_DAYS, scan, remove, sizeOf };
