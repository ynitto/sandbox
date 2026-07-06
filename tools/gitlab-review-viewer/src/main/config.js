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
  { label: 'status:elaborated', exclusivePrefix: 'status:', shortcut: 'Ctrl+2' },
  { label: 'status:blocked', exclusivePrefix: 'status:', shortcut: 'Ctrl+3' },
  { label: 'status:in-progress', exclusivePrefix: 'status:', shortcut: 'Ctrl+4' },
  { label: 'status:review-ready', exclusivePrefix: 'status:', shortcut: 'Ctrl+5' },
  { label: 'status:approved', exclusivePrefix: 'status:', shortcut: 'Ctrl+6' },
  { label: 'status:needs-rework', exclusivePrefix: 'status:', shortcut: 'Ctrl+7' },
  { label: 'status:needs-clarification', exclusivePrefix: 'status:', shortcut: 'Ctrl+8' },
  { label: 'status:done', exclusivePrefix: 'status:', shortcut: 'Ctrl+9' },
  { label: 'priority:high', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+1' },
  { label: 'priority:normal', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+2' },
  { label: 'priority:low', exclusivePrefix: 'priority:', shortcut: 'Ctrl+Shift+3' },
  { label: 'assignee:any', toggle: true, shortcut: 'Ctrl+Shift+0' },
];

// 高速化のため、余計な出力・ツール実行・質問を禁止し、分量の上限を明示する。
// 出力はマーカー（===SUMMARY_START=== / ===SUMMARY_END===）で挟ませ、
// agent.js が要約本文のみを抽出する。
const DEFAULT_PROMPT_TEMPLATE = [
  '以下の GitLab {typeLabel} を日本語で簡潔に要約してください。',
  '',
  '制約（厳守）:',
  '- 出力は ===SUMMARY_START=== と ===SUMMARY_END=== で挟んだ要約本文のみ。',
  '  前置き・後書き・思考過程・確認の質問は一切書かない',
  '- 追加のツール実行や検索はしない（ここに書かれた内容だけで要約する）',
  '- 全体で 400 字以内を目安に、次の構成の Markdown で書く:',
  '  - 概要（2 行以内）',
  '  - 論点・レビュー観点（最大 5 項目の箇条書き）',
  '  - 未解決・TODO（あれば・最大 3 項目）',
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

// 既定値の型を保つマージ。config.json が手編集や書き込み失敗で
// 想定外の形（セクションが null・オブジェクトが文字列 等）になっていても、
// 既定値の構造を壊さない — 壊れた値で起動不能になるのを防ぐ。
function deepMerge(base, over) {
  if (isPlainObject(base)) {
    if (!isPlainObject(over)) return base;
    const out = { ...base };
    for (const [k, v] of Object.entries(over)) {
      out[k] = deepMerge(base[k], v);
    }
    return out;
  }
  if (Array.isArray(base)) {
    return Array.isArray(over) ? over : base;
  }
  return over === undefined || over === null ? base : over;
}

// 既定プリセットのうち、保存済み設定に無いラベルを追加する（純粋に不足分の注入）。
// アプリを一度でも起動すると config.json に labelPresets 配列が丸ごと保存され、
// deepMerge は配列を保存値で丸ごと置換するため、バージョンアップで増えた既定
// ラベル（例: status:elaborated）が既存環境に一切反映されない。これを補う。
// - ユーザーの既存項目・並び・カスタム項目・変更したショートカットは一切変更しない
// - 挿入位置は既定の並びで直前にある既定ラベルの後ろ（無ければ末尾）
// - 注入するショートカットが既存と衝突する場合は付けない（既存の割当を壊さない）
function withMissingDefaultPresets(saved) {
  if (!Array.isArray(saved)) return DEFAULT_LABEL_PRESETS.map((p) => ({ ...p }));
  const result = saved.slice();
  const hasLabel = (label) => result.some((p) => p && p.label === label);
  const usedShortcut = (sc) => !!sc && result.some((p) => p && p.shortcut === sc);
  DEFAULT_LABEL_PRESETS.forEach((def, i) => {
    if (hasLabel(def.label)) return;
    let insertAt = result.length;
    for (let j = i - 1; j >= 0; j--) {
      const idx = result.findIndex((p) => p && p.label === DEFAULT_LABEL_PRESETS[j].label);
      if (idx >= 0) {
        insertAt = idx + 1;
        break;
      }
    }
    const inject = { ...def };
    if (usedShortcut(inject.shortcut)) delete inject.shortcut;
    result.splice(insertAt, 0, inject);
  });
  return result;
}

function configPath() {
  return path.join(app.getPath('userData'), 'config.json');
}

function loadConfig() {
  let cfg;
  try {
    const raw = fs.readFileSync(configPath(), 'utf8');
    cfg = deepMerge(DEFAULT_CONFIG, JSON.parse(raw));
  } catch {
    cfg = deepMerge(DEFAULT_CONFIG, {});
  }
  cfg.labelPresets = withMissingDefaultPresets(cfg.labelPresets);
  return cfg;
}

function saveConfig(cfg) {
  const merged = deepMerge(DEFAULT_CONFIG, cfg || {});
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(configPath(), JSON.stringify(merged, null, 2), 'utf8');
  return merged;
}

module.exports = { DEFAULT_CONFIG, loadConfig, saveConfig, configPath };
