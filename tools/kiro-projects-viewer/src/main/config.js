'use strict';

// 設定の読み書き。ユーザーデータディレクトリの config.json に保存する。
// 欠けているキーは DEFAULT_CONFIG で補完するため、バージョンアップで
// 新しい設定項目が増えても既存の設定ファイルはそのまま使える
// （gitlab-review-viewer と同じ流儀）。

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

const DEFAULT_CONFIG = {
  kiro: {
    // 監視する kiro-projects コンテナ（--root に渡す値）の一覧。
    // 例: ["C:\\work\\repo\\.kiro-projects", "/home/me/proj/.kiro-projects"]
    roots: [],
    // ~/.kiro-projects/instances/*.json（稼働発見レコード）から
    // 稼働中コンテナを自動発見して roots に加える。
    autoDiscover: true,
    // 自動リロードの間隔（秒）。0 で無効（手動リロードのみ）。
    refreshSec: 5,
    // 選択中プロジェクトのリポジトリを git pull で最新化する間隔（秒）。
    // 0 で自動 pull なし（サイドバーの ⇣ ボタンで手動 pull は常にできる）。
    // ポーリング（refreshSec）よりずっと長い間隔にしてリモートへの負荷を抑える。
    // 60 秒未満は 60 秒に切り上げる。
    gitPullSec: 300,
    // 状態共有 git 同期の push 側: ユーザー操作（指示ドロップ・inbox 投入・
    // needs 記入・削除）のたびに、操作したディレクトリだけをコミットして push する。
    // コンテナが独立した状態共有リポジトリ（state_git の clone 等）であることが
    // 前提のため既定は無効（ソースリポジトリ内の .kiro-projects へ意図しない
    // コミットを作らない）。有効時は pull も --rebase で取り込む。
    gitAutoPush: false,
    // approve / hold / reprioritize（決定記録を残す人の操作）に使う
    // kiro-projects CLI。PATH に無い場合はフルパスや
    // "python3 /path/to/kiro-projects.py" 形式でも指定できる。
    command: 'kiro-projects',
    // 人の指示（approve / hold / pin / defer）の届け方。
    //   auto … 本体が稼働中なら commands/<name>.json のファイルドロップ
    //          （WSL 内の本体にも届く）、稼働していなければ CLI、
    //          CLI も使えなければファイルドロップにフォールバック
    //   file … 常にファイルドロップ（次回の watch/起動が取り込む）
    //   cli  … 常に CLI（従来の挙動）
    actionMode: 'auto',
    // kiro-flow の共有バス（kiro-projects を --bus / 設定 bus: 付きで運用している
    // 場合）の明示パス。空なら <project>/bus → <container>/bus →
    // kiro-projects 設定ファイル（.kiro/）の bus: の順にファイルから自動発見する。
    flowBus: '',
    // kiro-flow daemon ロック（daemon-<sha1>.lock）の置き場。空なら ~/.kiro の
    // 設定ファイル lock_dir → 両ツール既定の $TMPDIR/kiro-flow-locks を使う。
    flowLockDir: '',
  },
  gitlab: {
    // gitlab-review-viewer と同じ形。タスクに紐づく GitLab イシューの
    // 最新状態（ラベル・state）を API で補完するのに使う。空なら
    // bus 上の結果ファイルにある情報だけで表示する。
    baseUrl: 'https://gitlab.com',
    token: '',
  },
  reviewViewer: {
    // gitlab-review-viewer へのレビュー引き継ぎ方法。
    //   protocol … カスタム URL スキームで起動（gitlab-review-viewer 側の
    //              ディープリンク対応が OS に登録されている必要がある。既定はこれ）
    //   exe      … gitlab-review-viewer の実行ファイルを直接起動し、ディープリンク
    //              URL（gitlab-review-viewer://open?url=...）を引数として渡す。
    //              portable exe はカスタム URL スキームを OS に恒久登録できない
    //              （インストーラ無し・起動ごとに一時ディレクトリへ展開される）ため、
    //              protocol では連携起動できない。この exe モードなら exePath で
    //              指定した実行ファイルへ直接ディープリンクを渡すので portable でも動く
    //              （gitlab-review-viewer は argv / second-instance でこれを解釈する）。
    //   command  … 任意コマンドで起動。{url} {projectPath} {type} {iid}
    //              {protocolUrl}（組み立て済みディープリンク）を置換
    mode: 'protocol',
    protocol: 'gitlab-review-viewer://open',
    // exe モードで直接起動する gitlab-review-viewer 実行ファイルのパス
    // （例: C:\\Apps\\GitLab Review Viewer.exe や portable exe のパス）
    exePath: '',
    command: '',
  },
};

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function deepMerge(base, over) {
  if (!isPlainObject(base) || !isPlainObject(over)) {
    return over === undefined ? base : over;
  }
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) {
    out[k] = isPlainObject(base[k]) && isPlainObject(v) ? deepMerge(base[k], v) : v;
  }
  return out;
}

function configPath() {
  return path.join(app.getPath('userData'), 'config.json');
}

function loadConfig() {
  try {
    const raw = fs.readFileSync(configPath(), 'utf8');
    return deepMerge(DEFAULT_CONFIG, JSON.parse(raw));
  } catch {
    return deepMerge(DEFAULT_CONFIG, {});
  }
}

function saveConfig(cfg) {
  const merged = deepMerge(DEFAULT_CONFIG, cfg || {});
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(configPath(), JSON.stringify(merged, null, 2), 'utf8');
  return merged;
}

module.exports = { DEFAULT_CONFIG, loadConfig, saveConfig, configPath };
