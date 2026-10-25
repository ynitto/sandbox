'use strict';
// 暫定テストの終了条件を、ケース形式・言語・テストランナーから独立して点検する。
const fs = require('fs');
const path = require('path');
const { createHash } = require('node:crypto');
const Ajv = require('ajv');
const { Findings } = require('./findings');

const SIGNAL = /暫定|仮実装|一時(?:実装|対応)|\b(?:provisional|interim|workaround)\b|\btemporary(?:[_ -]+(?:implementation|behavio[u]?r|fallback|test|compatibility))\b|\[temporary\]|@temporary\b/iu;
const TAG = /^(?:@?temporary|@?provisional|暫定)$/iu;
const DIRECTIVE = /^\s*testcase-lint-temporary\s*:\s*(.*)$/u;
const validate = new Ajv({ allErrors: true }).compile(require('../schema/testcase.schema.json').definitions.temporary);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const hasSignal = (text) => SIGNAL.test(String(text).replace(/_/g, ' '));

// パーサーが返した本物のコメントだけを読む。文字列やPythonのdocstringは指示として扱わない。
function annotateTemporary(tests, comments, source) {
  const lines = source.split('\n');
  const preceding = new Map(comments.filter((c) => c.standalone).map((c) => [c.endLine, c]));
  const sorted = [...comments].sort((a, b) => a.line - b.line);
  for (const t of tests) {
    const selected = new Set();
    for (const start of new Set([t.annotationLine || t.line, t.line])) {
      let cursor = start - 1;
      while (cursor >= 1) {
        if (!lines[cursor - 1].trim()) { cursor -= 1; continue; }
        const comment = preceding.get(cursor);
        if (!comment) break;
        selected.add(comment);
        cursor = comment.line - 1;
      }
    }
    const leading = [...selected].sort((a, b) => a.line - b.line);
    let lo = 0; let hi = sorted.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (sorted[mid].line < t.line) lo = mid + 1; else hi = mid; }
    const inBody = [];
    for (let i = lo; i < sorted.length && sorted[i].line <= (t.endLine || t.line); i += 1) {
      if (sorted[i].endLine <= (t.endLine || t.line)) inBody.push(sorted[i]);
    }
    t.temporarySignals = [...leading, ...inBody].filter((c) => hasSignal(c.text) && !DIRECTIVE.test(c.text)).map((c) => c.text.trim());
    const directives = leading.filter((c) => DIRECTIVE.test(c.text));
    if (!directives.length) continue;
    if (directives.length > 1) t.temporaryError = '1つのテストに暫定テストの管理コメントが複数あります';
    else {
      try { t.temporary = JSON.parse(directives[0].text.match(DIRECTIVE)[1]); }
      catch { t.temporaryError = 'testcase-lint-temporary の値は1行のJSONオブジェクトで指定してください'; }
    }
  }
}

function temporaryFindings(tests, { now = new Date() } = {}) {
  const f = new Findings();
  const today = now.toISOString().slice(0, 10);
  const cache = new Map();
  function read(file) {
    if (!cache.has(file)) {
      try {
        const stat = fs.statSync(file);
        if (!stat.isFile() || stat.size > 2 * 1024 * 1024) throw new Error('実装の参照先は2MiB以下の通常ファイルにしてください');
        cache.set(file, { buffer: fs.readFileSync(file) });
      } catch (error) { cache.set(file, { error }); }
    }
    return cache.get(file);
  }
  for (const t of tests) {
    function add(code, message, hint) {
      const finding = f.add(code, message, { case: t, hint });
      finding.cases = [t.id || t.fullTitle || t.title || `(${t.line} 行目)`];
      finding.verdict = code === 'T02' ? 'review-retirement' : code === 'T03' ? 'review-implementation-change' : 'track-temporary';
      return finding;
    }
    const meta = t.temporary;
    if (t.temporaryError) {
      add('T04', t.temporaryError, '暫定テストの管理情報を修正する。壊れた情報を有効な終了条件として扱わない');
      continue;
    }
    if (!own(t, 'temporary')) {
      const text = [t.title, t.fullTitle, ...(t.temporarySignals || []),
        ...[...(t.preconditions || []), ...(t.steps || []), ...(t.expected || [])].map((item) => item.text)].join('\n');
      if (hasSignal(text) || (Array.isArray(t.tags) && t.tags.some((tag) => TAG.test(tag)))) {
        add('T01', '暫定仕様・暫定実装を前提にした可能性がありますが、終了条件が管理されていません',
          'temporary に理由と期限または実装への参照を記録する。恒久的な回帰テストなら status: permanent と残す理由を記録する');
      }
      continue;
    }
    if (!validate(meta) || !meta.reason.trim() || (meta.status === 'permanent' && (meta.expires_on || meta.implementation))) {
      add('T04', '暫定テストの管理情報が書式と違います', 'reason と expires_on / implementation を確認する。permanent には恒久的に残す理由だけを書く');
      continue;
    }
    if (meta.status === 'permanent') continue;
    if (meta.expires_on) {
      const date = new Date(`${meta.expires_on}T00:00:00Z`);
      if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== meta.expires_on) {
        add('T04', 'expires_on は実在する日付ではありません', 'UTCの日付を YYYY-MM-DD で指定する');
        continue;
      }
      if (today > meta.expires_on) add('T02', `暫定テストの期限 ${meta.expires_on} を過ぎています`,
        '正式仕様のテストへ置き換えるか、恒久的な回帰テストとして残す根拠を確認する。期限だけを機械的に延長しない');
    }
    if (!meta.implementation) continue;
    const impl = meta.implementation;
    if (path.isAbsolute(impl.file) || /^[A-Za-z]:|\\/.test(impl.file) || !impl.file.trim() || (impl.marker != null && !impl.marker.trim())) {
      add('T04', 'implementation はテストファイルからの相対パスと空でないマーカーを使います', 'file と marker / sha256 を確認する');
      continue;
    }
    const file = path.resolve(path.dirname(t.file), impl.file);
    const entry = read(file);
    if (entry.error) {
      if (entry.error.code === 'ENOENT') add('T02', `暫定実装の参照先がなくなっています: ${impl.file}`, '実装の削除・移動後にテストだけが残っていないかを確認し、削除または正式仕様へ更新する');
      else add('T04', `暫定実装の参照先を読めません: ${impl.file} (${entry.error.message})`, '参照先のパス・権限・ファイルサイズを確認する');
      continue;
    }
    const body = entry.buffer.toString('utf8');
    if (impl.marker && !body.includes(impl.marker)) {
      add('T02', `暫定実装のマーカーがなくなっています: ${impl.marker}`, '正式実装への置き換え後に、暫定仕様だけを保証するテストが残っていないかを確認する');
    } else if (impl.sha256 && createHash('sha256').update(entry.buffer).digest('hex') !== impl.sha256) {
      add('T03', `暫定実装の内容が変わっています: ${impl.file}`, '暫定仕様の期待値が取り残されていないかを確認する。実装変更だけで不要と断定したり、ハッシュだけを自動更新したりしない');
    }
  }
  return f.list;
}

function temporaryInputs(tests) {
  return tests.filter((t) => typeof t.temporary?.implementation?.file === 'string')
    .map((t) => path.resolve(path.dirname(t.file), t.temporary.implementation.file));
}

module.exports = { annotateTemporary, temporaryFindings, temporaryInputs };
