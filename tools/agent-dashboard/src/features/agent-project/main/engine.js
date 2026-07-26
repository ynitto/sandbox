'use strict';

// 実行エンジン（常駐体 `agent-project serve`）の状況を読む層（実装計画 W2-4・W2-5）。
//
// `<agents home>/engine/status.json` は常駐体だけが書く 1 枚で、dashboard にとっては
//   ・プロジェクトの発見（children[].root）
//   ・稼働しているかどうか（heartbeat の鮮度）
//   ・共有の状況（sync_health の ahead/behind/エラー）と直近のエラー
//   ・子の隔離（quarantined）
// の唯一の根拠になる。以前あった「フォルダを列挙する設定」「ロックファイルを直接覗く」
// 「本体を CLI で起こす」経路はすべてこの 1 枚に置き換わった（設計 §4.6）。
//
// 読むだけ。ここから書くのは commands/heal の投函だけで、それも actions.dropCommand を通す。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

// project.js とは相互参照になる（あちらの discover がこのモジュールを入口にする）。
// トップレベルで require すると読み込み順によって空の module.exports を掴むため、
// 使う場所で都度引く。
const proj = () => require('./project');

// 心拍がこれより古ければ「停止中か不明」。常駐体の supervise tick は 5 秒周期なので、
// 一時的な詰まり（gc tick 中の長い git 待ち等）で赤にしない余裕を持たせる。
const FRESH_SEC = 120;

const AGENTS_DIRNAME = '.agents';

// この画面が読める状況ファイルの版（実行側 resident/status.py の CONTRACT_VERSION と対）。
// 食い違うノードは「更新漏れ」として表示する——黙って一部の情報を欠いたまま「正常」に
// 見せると、更新し忘れた PC がいつまでも気づかれない（設計 §6・実装計画 W2-5）。
const EXPECTED_CONTRACT_VERSION = 1;

let _homeCache = { at: 0, key: '', dir: '' };

// WSL 側の `$HOME/.agents` を Windows パス（UNC）で引く。ディストロ指定が無ければ既定。
// ホームの場所はディストロのユーザー設定次第なので、推測せず wslpath に聞く。
function wslAgentsHome(distro) {
  const args = distro ? ['-d', distro] : [];
  try {
    const r = spawnSync(
      'wsl.exe',
      [...args, '-e', 'sh', '-lc', 'command -v wslpath >/dev/null && wslpath -w "$HOME/.agents"'],
      { encoding: 'utf8', timeout: 8000, windowsHide: true }
    );
    const out = String(r.stdout || '').trim().split(/\r?\n/)[0] || '';
    if (r.status === 0 && out && /^[A-Za-z]:\\|^\\\\/.test(out)) return out;
  } catch {
    /* WSL 無し */
  }
  return '';
}

// engine/status.json を置いている `.agents` ディレクトリ。
//   1. 設定のベースパス（明示指定。この画面から開ける形で書く＝UNC かドライブパス）
//   2. Windows なら WSL（設定のディストロ・無ければ既定）の $HOME/.agents
//   3. このマシンの ~/.agents
function agentsHome(cfg) {
  const eng = (cfg && cfg.engine) || {};
  const distro = String(eng.distro || '').trim();
  const declared = String(eng.home || '').trim();
  if (declared) {
    // 明示指定はそのまま使う（勝手に UNC へ寄せない——WSL のホームを自動で引きたいなら
    // 空欄にする、が設定の意味。指定したパスを別のパスへ書き換えるのは驚きでしかない）。
    const raw = declared.replace(/^~(?=$|\/|\\)/, os.homedir());
    return path.isAbsolute(raw) || raw.startsWith('\\\\') ? raw : path.resolve(raw);
  }
  if (process.platform === 'win32') {
    const key = `d:${distro}`;
    const now = Date.now();
    if (_homeCache.key === key && now - _homeCache.at < 60000) return _homeCache.dir;
    const dir = wslAgentsHome(distro) || path.join(os.homedir(), AGENTS_DIRNAME);
    _homeCache = { at: now, key, dir };
    return dir;
  }
  return path.join(os.homedir(), AGENTS_DIRNAME);
}

function statusPath(cfg) {
  return path.join(agentsHome(cfg), 'engine', 'status.json');
}

function _readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

// 常駐体の状況スナップショット。ファイルが無い＝まだ一度も動いていない（案内表示へ）。
//   running: true=稼働中 / false=心拍が古い（停止中か不明） / null=状況ファイルが無い
function readStatus(cfg) {
  const file = statusPath(cfg);
  const data = _readJson(file);
  if (!data || typeof data !== 'object') {
    return { file, exists: false, running: null, children: [], syncHealth: [], recentErrors: [] };
  }
  const beatMs = Date.parse(data.heartbeat || '');
  const ageSec = isNaN(beatMs) ? null : Math.max(0, Math.round((Date.now() - beatMs) / 1000));
  const distro = String(((cfg && cfg.engine) || {}).distro || '').trim();
  const children = (Array.isArray(data.children) ? data.children : []).map((c) => {
    const root = String((c && c.root) || '');
    return {
      name: String((c && c.name) || ''),
      alive: !!(c && c.alive),
      quarantined: !!(c && c.quarantined),
      // 計画停止（稼働時間帯の外）。隔離と混ぜない——こちらは時間が来れば自動で戻る。
      paused: !!(c && c.paused),
      deaths: Number((c && c.deaths) || 0),
      root,
      // 実行側（WSL）が書いた POSIX パスを、この画面から開ける形へ寄せる
      viewerRoot: root && proj()._isPosixAbs(root) ? proj().toViewerPath(root, distro) : root,
    };
  });
  return {
    file,
    exists: true,
    node: String(data.node || ''),
    // 契約バージョンを載せる前の常駐体は 0 として扱う（未宣言＝古い、で fail-close）
    contractVersion: Number(data.contract_version || 0),
    heartbeat: data.heartbeat || null,
    ageSec,
    running: ageSec !== null && ageSec <= FRESH_SEC,
    tickCounts: data.tick_counts || {},
    syncHealth: (Array.isArray(data.sync_health) ? data.sync_health : []).map((s) => ({
      name: String((s && s.name) || ''),
      ahead: Number((s && s.ahead) || 0),
      behind: Number((s && s.behind) || 0),
      lastError: (s && s.last_error) || null,
    })),
    recentErrors: (Array.isArray(data.recent_errors) ? data.recent_errors : []).slice(-20),
    children,
    runningRuns: Array.isArray(data.running_runs) ? data.running_runs : [],
    // 委譲公示板への参加状況（R2a）。**この端末が板に参加しているか・手動入札できるかの
    // 唯一の根拠**——dashboard が host.yaml と agent-flow の設定を自前で読み解くと、
    // 宣言の解釈が 2 実装になる（S1 で畳んだ二重宣言が別の場所に戻る）。
    board: normalizeBoardStatus(data.board, data.heartbeat, distro),
  };
}

// engine/status.json の board ブロック → 画面が読む形。板を宣言していない常駐体
// （board 無し）と、board ブロックを載せる前の古い常駐体は、どちらも configured=false。
function normalizeBoardStatus(raw, heartbeat, distro) {
  if (!raw || typeof raw !== 'object' || !raw.configured) {
    return { configured: false, intakeProjects: [], myBids: [], openDelegations: 0 };
  }
  // 実行側が使っている板の作業フォルダ。画面が設定で持っているフォルダと突き合わせて
  // 「この端末の実行エンジンが参加している板か」を判定するために使う（指示の投函先の同定）。
  // 実行側は POSIX パスを書くので、この画面から開ける形にも寄せておく。
  const workdir = String(raw.workdir || '');
  return {
    configured: true,
    location: String(raw.location || ''),
    workdir,
    viewerWorkdir: workdir && proj()._isPosixAbs(workdir)
      ? proj().toViewerPath(workdir, distro) : workdir,
    selfName: String(raw.node_id || ''),
    contractVersion: Number(raw.contract_version || 0),
    // 板 tick が 1 度も回っていなければ常駐体は動いていない（手動入札を届けられない）。
    lastTick: String(raw.last_tick || ''),
    lastError: raw.last_error || null,
    intakeProjects: (Array.isArray(raw.intake_projects) ? raw.intake_projects : []).map(String),
    openDelegations: Number(raw.open_delegations || 0),
    myBids: (Array.isArray(raw.my_bids) ? raw.my_bids : []).map(String),
    heartbeat: heartbeat || null,
  };
}

// 発見済みプロジェクトの置き場（この画面から開ける形）。discover の入口。
function projectRoots(cfg) {
  return readStatus(cfg)
    .children.map((c) => c.viewerRoot)
    .filter(Boolean);
}

// 状況を人の言葉 1 文にまとめる（level: ok | warn | error）。内部名（node / sync / resident）は出さない。
function summarize(status) {
  if (!status || !status.exists) {
    return {
      level: 'error',
      summary: '実行エンジンが動いていません（起動コマンド: agent-project serve）',
    };
  }
  if (!status.running) {
    const ago = status.ageSec === null ? '' : `（最終確認 ${status.ageSec} 秒前）`;
    return {
      level: 'error',
      summary: `実行エンジンの応答が止まっています${ago}。起動コマンド: agent-project serve`,
    };
  }
  if (status.contractVersion !== EXPECTED_CONTRACT_VERSION) {
    return {
      level: 'warn',
      summary: '実行エンジンの更新が必要です（この画面と組み合わせが合っていません）。' +
        '実行する PC で git pull と install.sh を実行してください',
    };
  }
  const failing = status.syncHealth.filter((s) => s.lastError);
  if (failing.length) {
    return { level: 'error', summary: '共有先とやり取りできていません（ネットワークか認証を確認してください）' };
  }
  const behind = status.syncHealth.reduce((n, s) => n + (s.behind || 0), 0);
  const ahead = status.syncHealth.reduce((n, s) => n + (s.ahead || 0), 0);
  if (behind > 0 || ahead > 0) {
    const bits = [];
    if (behind > 0) bits.push(`未取得 ${behind} 件`);
    if (ahead > 0) bits.push(`未送信 ${ahead} 件`);
    return { level: 'warn', summary: `共有の途中です（${bits.join('・')}）。まもなく自動で揃います` };
  }
  const quarantined = status.children.filter((c) => c.quarantined).map((c) => c.name);
  if (quarantined.length) {
    return {
      level: 'warn',
      summary: `繰り返し失敗したため一時的に切り離したプロジェクトがあります: ${quarantined.join('・')}`,
    };
  }
  // 計画停止は異常ではない（時間が来れば自動で戻る）。異常として色を付けず、
  // 「なぜ進んでいないのか」だけが分かるように書く。
  const paused = status.children.filter((c) => c.paused).map((c) => c.name);
  if (paused.length) {
    return {
      level: 'ok',
      summary: `稼働時間外のため休止中です（時間になると自動で再開します）: ${paused.join('・')}`,
    };
  }
  return { level: 'ok', summary: '共有先と揃っています' };
}

module.exports = {
  agentsHome, statusPath, readStatus, projectRoots, summarize,
  FRESH_SEC, EXPECTED_CONTRACT_VERSION,
};
