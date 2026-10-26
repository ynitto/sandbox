'use strict';
// 言語・ランナーを知らない比較層。アダプターの本文/構造/実行条件のキーだけを読む。
const { Findings } = require('../findings');
const { RULES } = require('../rules');
const { severityOf } = require('../config');

function groups(items, key) {
  const out = new Map();
  for (const item of items) {
    const k = JSON.stringify(key(item));
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(item);
  }
  return [...out.values()].filter((members) => members.length > 1);
}

function analyze(tests, diagnostics, config) {
  const f = new Findings();
  const emit = (code, members, message, hint, verdict) => {
    const first = members[0];
    const finding = f.add(code, message, {
      file: first.file, line: first.line, hint,
    });
    finding.cases = members.map((m) => m.title || `(${m.line} 行目)`);
    finding.related = members.slice(1).map((m) => ({ file: m.file, line: m.line }));
    finding.verdict = verdict;
  };
  for (const d of diagnostics) f.add('K00', d.reason, { file: d.file, line: d.line || 0, hint: '対象・構文・静的に読めない部分を確認する。未解析の部分を重複なしとは扱わない' });
  const comparable = tests.filter((t) => t.comparable && t.body && t.body.exact && !t.trivial);
  const suppressed = (t, code) => typeof t.suppressions?.[code] === 'string' && Boolean(t.suppressions[code].trim());
  for (const members of groups(tests.filter((t) => t.staticTitle && !t.parameterized && !suppressed(t, 'K03')), (t) => [t.file, t.framework, t.fullTitle])) {
    emit('K03', members, `${members.length} 件のテストが同じ完全名を持ちます`, 'ケースを区別できる名前にする。本文の重複とは別に判断する', 'rename');
  }
  for (const members of groups(comparable.filter((t) => !suppressed(t, 'K01')), (t) => [t.file, t.framework, t.context, t.body.exact])) {
    emit('K01', members, `${members.length} 件のテストで、本文と静的に読み取った実行条件が一致します`,
      '目的・観測する状態を確認し、同じ検証なら1つにまとめる。静的な一致だけで自動削除しない', 'review-duplicate');
  }
  for (const members of groups(comparable.filter((t) => !t.parameterized && !suppressed(t, 'K02')), (t) => [t.file, t.framework, t.context, t.body.structural])) {
    if (new Set(members.map((t) => t.body.exact)).size < 2) continue;
    emit('K02', members, `${members.length} 件のテストは構造が同じでリテラル値が違います`,
      'test.each・pytest.mark.parametrize・subTestなどで、境界値を含む各ケースを独立した行として残す', 'consolidate-keep-cases');
  }
  for (const members of groups(comparable.filter((t) => !suppressed(t, 'K04')), (t) => [t.framework, t.context, t.body.exact])) {
    if (new Set(members.map((t) => t.file)).size < 2) continue;
    // 同一ファイルの重複はK01に任せ、ファイルごとの代表だけで報告する。
    const representatives = [...new Map(members.map((t) => [t.file, t])).values()];
    emit('K04', representatives, `${representatives.length} ファイルに静的に一致する本文があります`,
      '単体・結合・E2Eなど検証する層を確認する。別の層で必要なケースは残し、共通化する範囲を判断する', 'review-cross-file');
  }
  for (const t of tests) {
    if (!Array.isArray(t.rows) || suppressed(t, 'K05')) continue;
    for (const rows of groups(t.rows.filter((r) => !suppressed(r, 'K05')), (r) => r.key)) {
      emit('K05', rows.map((row) => ({ file: t.file, line: row.line, title: t.title })),
        `${rows.length} 行のパラメーター値が一致します`, '意図的な繰り返しでなければ表の行をまとめる。異なる値の行は残す', 'review-duplicate-row');
    }
  }
  return f.list.flatMap((finding) => {
    const severity = severityOf(config, finding.code);
    return severity ? [{ ...finding, severity, category: RULES[finding.code][0], name: RULES[finding.code][2] }] : [];
  });
}

module.exports = { analyze };
