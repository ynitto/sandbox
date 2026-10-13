// Figma のコンポーネントとコード側の部品の対応表を読む。
//
// Code Connect の対応は REST API では取れないため、次の 2 つから作る。
// - 手動マッピング（JSON）: { "components": { "<node ID | component key | 名前>": { "component": "Button", "import": "..." } } }
// - リポジトリ内の Code Connect ファイル（*.figma.tsx など）の figma.connect(部品, "URL") 呼び出し
// 対応付けの正しさ（Props の対応など）は判断しない。見つけた事実だけを渡す。

import fs from 'node:fs';
import path from 'node:path';
import { parseFigmaInput } from './url.mjs';

const CONNECT_RE = /figma\.connect\(\s*([A-Za-z_$][\w$.]*)\s*,\s*(['"`])(https?:\/\/[^'"`]+)\2/g;
const CODE_CONNECT_FILE_RE = /\.figma\.(t|j)sx?$/;
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', '.next', 'coverage']);
const MAX_FILES = 5000;

export function loadManualMapping(file) {
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  const components = data.components || data;
  const out = {};
  for (const [key, value] of Object.entries(components)) {
    if (!value || typeof value !== 'object') continue;
    out[key] = { ...value, source: 'manual' };
    // node ID はハイフン表記でも書けるようにする。
    if (/^\d+-\d+$/.test(key)) out[key.replace('-', ':')] = out[key];
  }
  return out;
}

export function scanCodeConnect(rootDir) {
  const out = {};
  const found = [];
  let count = 0;
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (count >= MAX_FILES) return;
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name)) walk(path.join(dir, e.name));
      } else if (CODE_CONNECT_FILE_RE.test(e.name)) {
        count += 1;
        const file = path.join(dir, e.name);
        const text = fs.readFileSync(file, 'utf8');
        for (const m of text.matchAll(CONNECT_RE)) {
          try {
            const parsed = parseFigmaInput(m[3]);
            const nodeId = parsed.nodeIds[0];
            if (!nodeId) continue;
            const entry = {
              component: m[1],
              source: 'code-connect',
              file: path.relative(rootDir, file).split(path.sep).join('/'),
              fileKey: parsed.mainFileKey,
            };
            out[nodeId] = entry;
            found.push({ nodeId, ...entry });
          } catch {
            // 解釈できない URL は対象外。
          }
        }
      }
    }
  };
  walk(rootDir);
  return { mapping: out, found };
}

export function buildMapping({ mappingFile, codeConnectDir }) {
  let mapping = null;
  const notes = [];
  if (codeConnectDir) {
    const { mapping: cc, found } = scanCodeConnect(codeConnectDir);
    mapping = { ...cc };
    notes.push(`Code Connect: ${found.length} 件の figma.connect を読み取り（${codeConnectDir}）`);
  }
  if (mappingFile) {
    mapping = { ...(mapping || {}), ...loadManualMapping(mappingFile) };
    notes.push(`手動マッピング: ${mappingFile}`);
  }
  return { mapping, notes };
}
