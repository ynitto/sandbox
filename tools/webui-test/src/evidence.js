'use strict';
// テストで得たもの（合否のほかに、確かめた振る舞い・測った時間・撮った画像）を evidence.json に書き出す。
// codd-statemachine などがこれを読み、仕様書に写した値が今と同じか・目安を超えていないかを確かめる。
//
//   { "version": 1, "source": "webui-test", "generatedAt": "…", "items": [
//     { "id": "login/S-01", "kind": "behavior", "title": "ログイン画面 正しい ID でログインできる", "status": "passed",
//       "file": "tests/login.yaml", "doc": ["docs/login.md"], "code": ["src/login.tsx"] },
//     { "id": "login/S-01/load", "kind": "metric", "title": "…（ページの読み込み）", "value": 812, "unit": "ms", … },
//     { "id": "login/S-01/login", "kind": "image", "title": "…", "path": "docs/images/login.png", … } ] }
//
// id は「ケースファイルの名前（拡張子なし）/ケースの ID[組]/名前」。パスは設定ファイルのフォルダ（root）からの相対。

const fs = require('fs');
const path = require('path');

const ANNOT = /coherence:\s*(doc|code)\s*=\s*([^\s`"'<>]+)/g;

function links(file) {
  const out = { doc: [], code: [] };
  let text = '';
  try { text = fs.readFileSync(file, 'utf8'); } catch (_) { return out; }
  for (const line of text.split(/\r?\n/)) {
    if (!/^\s*#/.test(line)) continue;
    for (const m of line.matchAll(ANNOT)) if (!out[m[1]].includes(m[2])) out[m[1]].push(m[2]);
  }
  return out;
}

const posix = (p) => p.split(path.sep).join('/');

function buildEvidence(report, { root, outDir }) {
  const items = [];
  const rel = (p) => posix(path.relative(root, p));
  for (const s of report.suites) {
    const file = s.file ? rel(s.file) : null;
    const stem = s.file ? path.basename(s.file).replace(/\.(ya?ml|json)$/i, '') : s.suite;
    const link = s.file ? links(s.file) : { doc: [], code: [] };
    for (const c of s.cases) {
      const caseId = `${stem}/${c.variant ? `${c.id}[${c.variant}]` : c.id}`;
      const title = `${s.suite} ${c.title}${c.variant ? ` [${c.variant}]` : ''}`;
      const base = { file, ...link };
      items.push({ id: caseId, kind: 'behavior', title, status: c.status, requirement: c.requirement || undefined, ...base });
      if (c.status === 'skipped') continue;
      for (const m of c.metrics || []) {
        items.push({ id: `${caseId}/${m.name}`, kind: 'metric', title: `${title}（${m.label || m.name}）`, value: m.value, unit: m.unit, max: m.max, ...base });
      }
      const docs = new Map((c.docImages || []).map((d) => [d.actual, d.path]));
      for (const sh of c.screenshots || []) {
        if (sh.name === 'failure') continue;
        const doc = docs.get(sh.file);
        items.push({ id: `${caseId}/${sh.name}`, kind: 'image', title: `${title}（${sh.name}）`,
          path: doc ? rel(doc) : rel(path.join(outDir, sh.file)), ...base });
      }
    }
  }
  return items;
}

// 実行ごとの evidence.json と、最新をまとめた <置き場>/evidence.json を書く。最新は今回動かしたケースファイルの分だけ入れ替える。
function writeEvidence(report, { outDir, latestDir, root }) {
  const items = buildEvidence(report, { root, outDir });
  const doc = (list) => JSON.stringify({ version: 1, source: 'webui-test', generatedAt: report.finishedAt || new Date().toISOString(), items: list }, null, 2) + '\n';
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'evidence.json'), doc(items));
  if (!latestDir) return path.join(outDir, 'evidence.json');
  const latest = path.join(latestDir, 'evidence.json');
  let prev = [];
  try { prev = JSON.parse(fs.readFileSync(latest, 'utf8')).items || []; } catch (_) { /* 初めて */ }
  const ran = new Set(items.map((i) => i.file));
  const ids = new Set(items.map((i) => i.id));
  const merged = [...prev.filter((i) => !ran.has(i.file) && !ids.has(i.id)), ...items];
  fs.mkdirSync(latestDir, { recursive: true });
  fs.writeFileSync(latest, doc(merged));
  return latest;
}

module.exports = { buildEvidence, writeEvidence };
