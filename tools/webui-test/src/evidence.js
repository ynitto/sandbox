'use strict';
// テストで得たもの（合否のほかに、確かめた振る舞い・測った時間・前回と比べた画面）を evidence.json に書き出す。
// 仕様書には依存しない。codd-statemachine などがこれを読み、文書への影響を測って差し替える。
//
//   { "version": 1, "source": "webui-test", "generatedAt": "…", "items": [
//     { "id": "login/S-01", "kind": "behavior", "title": "ログイン画面 正しい ID でログインできる", "status": "passed",
//       "file": "tests/login.yaml" },
//     { "id": "login/S-01/load", "kind": "metric", "title": "…（ページの読み込み）", "value": 812, "unit": "ms", … },
//     { "id": "login/S-01/ログイン画面", "kind": "image", "status": "changed", "path": "webui-test-results/screens/…png",
//       "sha256": "…", "history": ["今の sha256", "前の sha256", …], "previous": "…png", … } ] }
//
// id は「ケースファイルの名前（拡張子なし）/ケースの ID[組]/名前」。パスは設定ファイルのフォルダ（root）からの相対。

const fs = require('fs');
const path = require('path');
const { caseKey } = require('./screens');

const posix = (p) => p.split(path.sep).join('/');

function buildEvidence(report, screens, { root }) {
  const rel = (p) => posix(path.relative(root, p));
  const items = [];
  for (const s of report.suites) {
    if (!s.file) continue;
    const file = rel(s.file);
    for (const c of s.cases) {
      const id = caseKey(s.file, c);
      const title = `${s.suite} ${c.title}${c.variant ? ` [${c.variant}]` : ''}`;
      items.push({ id, kind: 'behavior', title, status: c.status, requirement: c.requirement || undefined, file });
      if (c.status === 'skipped') continue;
      for (const m of c.metrics || []) {
        items.push({ id: `${id}/${m.name}`, kind: 'metric', title: `${title}（${m.label || m.name}）`, value: m.value, unit: m.unit, max: m.max, file });
      }
    }
  }
  for (const sc of screens) {
    items.push({
      id: sc.id, kind: 'image', title: sc.title || sc.id, status: sc.status, file: rel(sc.file),
      path: sc.path ? rel(sc.path) : undefined, sha256: sc.sha256, history: sc.history,
      previous: sc.previous ? rel(sc.previous) : undefined, diff: sc.diff ? rel(sc.diff) : undefined,
    });
  }
  return items;
}

// 実行ごとの evidence.json と、最新をまとめた <置き場>/evidence.json を書く。最新は今回動かしたケースファイルの分だけ入れ替える。
function writeEvidence(report, screens, { outDir, latestDir, root }) {
  const items = buildEvidence(report, screens, { root });
  const doc = (list) => JSON.stringify({ version: 1, source: 'webui-test', generatedAt: report.finishedAt || new Date().toISOString(), items: list }, null, 2) + '\n';
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'evidence.json'), doc(items));
  const latest = path.join(latestDir, 'evidence.json');
  let prev = [];
  try { prev = JSON.parse(fs.readFileSync(latest, 'utf8')).items || []; } catch (_) { /* 初めて */ }
  const ran = new Set(items.map((i) => i.file));
  const merged = [...prev.filter((i) => !ran.has(i.file)), ...items];
  fs.mkdirSync(latestDir, { recursive: true });
  fs.writeFileSync(latest, doc(merged));
  return latest;
}

module.exports = { buildEvidence, writeEvidence };
