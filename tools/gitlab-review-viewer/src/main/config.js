'use strict';

// 設定の読み書き。ユーザーデータディレクトリの config.json に保存する。
// 欠けているキーは DEFAULT_CONFIG で補完するため、バージョンアップで
// 新しい設定項目が増えても既存の設定ファイルはそのまま使える。

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

// gitlab-idd スキルのラベル規約をデフォルトのプリセットにする。
// exclusivePrefix: 同じ接頭辞のラベルを外してから付ける（status:* は排他）
// toggle: 付いていれば外す・無ければ付ける
const DEFAULT_LABEL_PRESETS = [
  { label: 'status:open', exclusivePrefix: 'status:', shortcut: 'Ctrl+1' },
  { label: 'status:blocked', exclusivePrefix: 'status:', shortcut: 'Ctrl+2' },
  { label: 'status:in-progress', exclusivePrefix: 'status:', shortcut: 'Ctrl+3' },
  { label: 'status:review-ready', exclusivePrefix: 'status:', shortcut: 'Ctrl+4' },
  { label: 'status:approved', exclusivePrefix: 'status:', shortcut: 'Ctrl+5' },
  { label: 'status:needs-rework', exclusivePrefix: 'status:', shortcut: 'Ctrl+6' },
  { label: 'status:needs-clarification', exclusivePrefix: 'status:', shortcut: 'Ctrl+7' },
  { label: 'status:done', exclusivePrefix: 'status:', shortcut: 'Ctrl+8' },
  { label: 'priority:high', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+1' },
  { label: 'priority:normal', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+2' },
  { label: 'priority:low', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+3' },
  { label: 'assignee:any', toggle: true, shortcut: 'Ctrl+Shift+0' },
];

const DEFAULT_PROMPT_TEMPLATE = [
  'あなたはコードレビューを補佐する要約アシスタントです。',
  '以下の GitLab {typeLabel} の内容を読み、日本語で要約してください。',
  '出力は Markdown で、次の構成にしてください:',
  '- 概要（3 行以内）',
  '- 論点・レビュー観点（箇条書き）',
  '- 未解決の議論・TODO（あれば）',
  '',
  '# 対象',
  'タイトル: {title}',
  'URL: {url}',
  '状態: {state}',
  'ラベル: {labels}',
  '',
  '# 本文',
  '{description}',
  '',
  '# コメント',
  '{notes}',
  '',
  '{changes}',
].join('\n');

const DEFAULT_CONFIG = {
  gitlab: {
    baseUrl: 'https://gitlab.com',
    token: '',
  },
  // 前回の検索条件（グループ / プロジェクトの候補リストと選択値・ラベル・
  // 種別・状態・キーワード・作成者）。renderer が保存し、起動時に復元する。
  searchCache: {},
  agent: {
    // {promptFile} はプロンプト全文を書き出した一時ファイルのパスに置換される。
    // {prompt} を使うとプロンプト全文を argv でそのまま渡す（長文は自動でファイル退避）。
    // どちらも無い場合は標準入力にプロンプトを流し込む。
    command:
      'kiro-cli chat --no-interactive --trust-all-tools ' +
      '"{promptFile} に要約タスクの指示があります。このファイルを読み込み、指示に従って要約だけを出力してください。"',
    timeoutSec: 300,
    promptTemplate: DEFAULT_PROMPT_TEMPLATE,
  },
  obsidian: {
    vaultDir: '',
    subDir: 'GitLab Reviews',
    openAfterExport: false,
  },
  labelPresets: DEFAULT_LABEL_PRESETS,
  actionShortcuts: {
    postComment: 'Ctrl+Enter',
    summarize: 'Ctrl+Shift+S',
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
