'use strict';

// 要約済みのイシュー / MR を Markdown 化して Obsidian Vault のフォルダへ書き出す。

const fs = require('fs');
const path = require('path');

function sanitizeFileName(name) {
  return String(name)
    .replace(/[\\/:*?"<>|#^[\]]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
}

function yamlEscape(s) {
  return String(s).replaceAll('\\', '\\\\').replaceAll('"', '\\"');
}

function buildMarkdown({ detail, summary, exportedAt }) {
  const it = detail.item;
  const typeLabel = it.type === 'issue' ? 'issue' : 'merge_request';
  const lines = [
    '---',
    `title: "${yamlEscape(it.title)}"`,
    `url: ${it.url}`,
    `type: ${typeLabel}`,
    `ref: "${yamlEscape(it.ref)}"`,
    `state: ${it.state}`,
    `labels: [${it.labels.map((l) => `"${yamlEscape(l)}"`).join(', ')}]`,
    `author: ${it.author}`,
    `created: ${it.createdAt}`,
    `exported: ${exportedAt}`,
    '---',
    '',
    `# ${it.title}`,
    '',
    `- URL: ${it.url}`,
    `- 状態: ${it.state}`,
    `- ラベル: ${it.labels.join(', ') || '(なし)'}`,
    '',
  ];
  if (summary && summary.trim()) {
    lines.push('## 要約', '', summary.trim(), '');
  }
  if (detail.description && detail.description.trim()) {
    lines.push('## 説明', '', detail.description.trim(), '');
  }
  if (detail.changedFiles && detail.changedFiles.length) {
    lines.push('## 変更ファイル', '');
    for (const f of detail.changedFiles) lines.push(`- ${f}`);
    lines.push('');
  }
  return lines.join('\n');
}

function exportToObsidian({ vaultDir, subDir }, { detail, summary, exportedAt }) {
  if (!vaultDir) {
    throw new Error('Obsidian Vault のフォルダが設定されていません（設定画面から指定してください）');
  }
  const it = detail.item;
  const dir = subDir ? path.join(vaultDir, subDir) : vaultDir;
  fs.mkdirSync(dir, { recursive: true });
  const name = sanitizeFileName(`${it.ref} ${it.title}`) || `${it.type}-${it.iid}`;
  const file = path.join(dir, `${name}.md`);
  fs.writeFileSync(file, buildMarkdown({ detail, summary, exportedAt }), 'utf8');
  return file;
}

module.exports = { exportToObsidian, buildMarkdown, sanitizeFileName };
