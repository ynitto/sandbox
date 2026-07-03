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
    // approve / hold / reprioritize（決定記録を残す人の操作）に使う
    // kiro-projects CLI。PATH に無い場合はフルパスや
    // "python3 /path/to/kiro-projects.py" 形式でも指定できる。
    command: 'kiro-projects',
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
    //              ディープリンク対応が必要。既定はこれ）
    //   command  … 任意コマンドで起動。{url} {projectId} {type} {iid} を置換
    mode: 'protocol',
    protocol: 'gitlab-review-viewer://open',
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
