'use strict';

// 設定の読み書き。ユーザーデータディレクトリの config.json に保存する。
// 欠けているキーは DEFAULT_CONFIG（base + 各 feature の configDefaults）で補完するため、
// バージョンアップで新しい設定項目が増えても既存の設定ファイルはそのまま使える
// （gitlab-review-viewer と同じ流儀）。
//
// feature 固有の既定は src/features/*/config.js に置き、ここには
// Electron シェル共通（GitLab API・レビュー引き継ぎ）だけを残す。

const fs = require('fs');
const path = require('path');
const { app } = require('electron');
const { loadFeatures } = require('../../features');

const BASE_DEFAULT_CONFIG = {
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

function buildDefaultConfig() {
  let defaults = { ...BASE_DEFAULT_CONFIG };
  for (const feature of loadFeatures()) {
    if (feature && feature.configDefaults && Object.keys(feature.configDefaults).length) {
      defaults = deepMerge(defaults, feature.configDefaults);
    }
  }
  return defaults;
}

// 遅延評価: feature 読み込み時の循環参照を避けるため、DEFAULT_CONFIG は
// 初回アクセスで組み立てる（Node の module.exports getter 相当を関数で提供）。
let _cachedDefaults = null;
function DEFAULT_CONFIG() {
  if (!_cachedDefaults) _cachedDefaults = buildDefaultConfig();
  return _cachedDefaults;
}

function configPath() {
  return path.join(app.getPath('userData'), 'config.json');
}

function loadConfig() {
  try {
    const raw = fs.readFileSync(configPath(), 'utf8');
    return deepMerge(DEFAULT_CONFIG(), JSON.parse(raw));
  } catch {
    return deepMerge(DEFAULT_CONFIG(), {});
  }
}

function saveConfig(cfg) {
  const merged = deepMerge(DEFAULT_CONFIG(), cfg || {});
  const dst = configPath();
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  // temp → rename のアトミック書き込み。直接 write すると、書き込み途中のクラッシュ・
  // 電源断で config.json が途切れ、次回起動時に JSON.parse が失敗して設定
  // （プロジェクト登録・CLI コマンド等）が丸ごと既定値へ戻る。
  const tmp = `${dst}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(merged, null, 2), 'utf8');
  fs.renameSync(tmp, dst);
  return merged;
}

module.exports = {
  BASE_DEFAULT_CONFIG,
  // 後方互換: 旧コードは DEFAULT_CONFIG をオブジェクトとして読む。
  // getter で毎回同じ組み立て結果を返す。
  get DEFAULT_CONFIG() {
    return DEFAULT_CONFIG();
  },
  loadConfig,
  saveConfig,
  configPath,
  deepMerge,
};
