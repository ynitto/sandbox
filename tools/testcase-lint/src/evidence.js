'use strict';
// 点検の結果を、ほかの道具が読める結果ファイル（version 1 の items 形式）に書く。
// どの道具が読むかは知らない。読む側は id で項目を引き、文書に件数を写したり、落ちた理由を示したりする。
//
//   { id: "<prefix>/errors",            kind: "metric", value: 3, max: 0 }      エラーの件数（max を超えたら目安を外れた）
//   { id: "<prefix>/warnings",          kind: "metric", value: 5 }
//   { id: "<prefix>/<分類>",             kind: "metric", value: n }              分類ごとの件数
//   { id: "<prefix>/<ファイル>",          kind: "behavior", status: passed|failed, error, path }  ケースのファイルごとの合否

const fs = require('fs');
const path = require('path');
const { CATEGORY } = require('./rules');

const CATEGORY_IDS = { [CATEGORY.defect]: 'defect', [CATEGORY.ambiguity]: 'ambiguity', [CATEGORY.duplicate]: 'duplicate', [CATEGORY.conflict]: 'conflict', [CATEGORY.gap]: 'gap', [CATEGORY.oracle]: 'oracle', [CATEGORY.exec]: 'exec' };

function writeEvidence(file, result, { prefix = 'testcase-lint', failOn = 'error' } = {}) {
  const rank = { info: 0, warning: 1, error: 2 };
  const limit = failOn === 'never' ? Infinity : rank[failOn];
  const count = (sev) => result.findings.filter((f) => f.severity === sev).length;
  const items = [
    { id: `${prefix}/errors`, kind: 'metric', value: count('error'), max: 0 },
    { id: `${prefix}/warnings`, kind: 'metric', value: count('warning') },
    ...Object.entries(CATEGORY_IDS).map(([cat, id]) => ({ id: `${prefix}/${id}`, kind: 'metric', value: result.findings.filter((f) => f.category === cat).length })),
  ];
  // 項目のパスは、今のフォルダ（root）からの相対で書く
  const dir = path.dirname(path.resolve(file));
  for (const caseFile of result.files) {
    const bad = result.findings.filter((f) => f.file === caseFile && rank[f.severity] >= limit);
    const rel = caseFile;
    items.push({
      id: `${prefix}/${caseFile.replace(/\.(json|ya?ml)$/i, '')}`, kind: 'behavior', title: `${caseFile} の点検`,
      status: bad.length ? 'failed' : 'passed', file: rel,
      ...(bad.length ? { error: `${bad[0].code} ${bad[0].message}${bad.length > 1 ? `（ほか ${bad.length - 1} 件）` : ''}`, path: rel } : {}),
    });
  }
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify({ version: 1, root: path.relative(dir, process.cwd()).split(path.sep).join('/') || '.', items }, null, 2)}\n`);
}

module.exports = { writeEvidence };
