'use strict';

// 成果物プレビューの共通リーダー。
//
// 同じ読み方を 2 か所で使う:
//   - 受入待ち（バスの `deliverable/`）        … missions.js
//   - 受け取り済み（納品棚の `deliveries/<mid>/`）… deliveries.js
//
// 成果物はプログラムに限らず調査結果・ドキュメント・画像に及ぶので、
// 「開かなくても中身が分かる」ことを優先しつつ有界に読む。文書はテキスト、
// 小さい画像は data URI、それ以外はメタ情報だけを返し、renderer が kind で描き分ける。
// （画像を data URI で載せるため index.html の CSP は img-src に data: を許可している）

const fs = require('fs');
const path = require('path');

const MAX_FILES = 30;
const MAX_TEXT = 20000;
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

const IMAGE_MIME = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
};
const TEXT_EXT = new Set([
  '.md', '.markdown', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv', '.py',
  '.js', '.ts', '.tsx', '.jsx', '.sh', '.sql', '.html', '.css', '.toml', '.ini',
]);

function walkFiles(root) {
  const out = [];
  const walk = (dir, rel) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(dir, e.name);
      const relPath = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) walk(full, relPath);
      else if (e.isFile() && !e.name.includes('.tmp.')) out.push({ full, rel: relPath });
    }
  };
  walk(root, '');
  return out;
}

// root 配下を有界に読む。skip は除外するファイル名（MANIFEST.json / delivery.json）。
function readPreview(root, skip) {
  const skipNames = new Set(skip || []);
  const all = walkFiles(root).filter((f) => !skipNames.has(path.basename(f.rel)));
  const files = [];
  for (const f of all.slice(0, MAX_FILES)) {
    const ext = path.extname(f.rel).toLowerCase();
    let bytes = 0;
    try {
      bytes = fs.statSync(f.full).size;
    } catch {
      continue;
    }
    const row = { path: f.rel, role: f.rel.split('/')[0] || '', bytes, kind: 'binary' };
    if (IMAGE_MIME[ext] && bytes <= MAX_IMAGE_BYTES) {
      try {
        row.kind = 'image';
        row.dataUri = `data:${IMAGE_MIME[ext]};base64,${fs.readFileSync(f.full).toString('base64')}`;
      } catch {
        row.kind = 'binary';
      }
    } else if (TEXT_EXT.has(ext) || ext === '') {
      try {
        const text = fs.readFileSync(f.full, 'utf8');
        row.kind = ext === '.md' || ext === '.markdown' ? 'markdown' : 'text';
        row.text = text.slice(0, MAX_TEXT);
        row.truncated = text.length > MAX_TEXT;
      } catch {
        row.kind = 'binary';   // UTF-8 で読めないものはバイナリ扱い
      }
    }
    files.push(row);
  }
  return { files, total: all.length, truncated: all.length > files.length };
}

module.exports = { readPreview, MAX_FILES, MAX_TEXT, MAX_IMAGE_BYTES };
