'use strict';
// JSON / YAML を読み、形の違うテストケースを同じ Case にそろえる。
//
// - JSON も YAML の読み手で読む（行番号と重複キーの検出を共有するため。JSON は YAML 1.2 に含まれる）
// - LLM が出しがちな ``` の囲みは、行番号を保ったまま中身だけを読む
// - 項目名は英語・日本語・Gherkin（given / when / then）のよくある綴りを受ける。設定で足せる
// - 手順の中の expect: などは期待結果として扱う（手順を構造で書くケースファイルをそのまま読める）
// - 知らない項目（mocks・テストデータなど）は前提として扱う。同じ手順でも前提が違えば別のケースとして比べるため
// - conditions / params / data は「条件の組」としても持つ（組み合わせの点検に使う）

const fs = require('fs');
const YAML = require('yaml');

const FIELD_ALIASES = {
  id: ['id', 'case_id', 'caseid', 'test_id', 'testid', 'tc_id', 'no', 'no.', 'number', 'key', '番号', '項番',
    'テストid', 'ケースid', 'テスト番号', 'ケース番号'],
  title: ['title', 'name', 'summary', 'scenario', 'scenario_name', 'test_name', 'testname', 'objective', 'item',
    'check', 'checkpoint', 'タイトル', 'テスト名', 'ケース名', 'テスト項目', '確認項目', '項目', '概要', 'シナリオ',
    '目的', 'チェック項目'],
  description: ['description', 'desc', '説明', '内容', '詳細'],
  preconditions: ['precondition', 'preconditions', 'pre_condition', 'pre_conditions', 'prerequisite',
    'prerequisites', 'given', 'setup', 'background', '前提', '前提条件', '事前条件', '事前準備', '準備'],
  steps: ['steps', 'step', 'procedure', 'procedures', 'actions', 'action', 'when', 'test_steps', '手順', '操作',
    '操作手順', 'テスト手順', '実施手順'],
  expected: ['expected', 'expected_result', 'expected_results', 'expectation', 'expectations', 'then', 'assert',
    'asserts', 'assertion', 'assertions', 'verify', 'verification', 'result', '期待結果', '期待値', '確認内容',
    '確認結果', '合否基準', '判定基準', '期待する結果'],
  requirements: ['requirement', 'requirements', 'req', 'reqs', 'requirement_id', 'requirement_ids', 'refs', 'ref',
    'trace', 'traceability', 'story', '要件', '要件id', '要求', '関連要件'],
  conditions: ['conditions', 'condition', 'params', 'parameters', 'inputs', 'input', 'data', 'test_data',
    'testdata', '条件', '入力', 'パラメータ', 'テストデータ', '入力値'],
};
// 比べない付帯情報（前提にも入れない）
const META_KEYS = new Set(['tags', 'tag', 'priority', 'severity', 'status', 'author', 'owner', 'notes', 'note',
  'comment', 'comments', 'category', 'type', 'level', 'component', 'module', 'automated', 'estimate', 'created',
  'updated', 'version', 'skip', 'envs', 'variants', 'タグ', '優先度', '重要度', '状態', '作成者', '担当', '担当者',
  '備考', 'メモ', 'コメント', '区分', '分類', '種別', 'カテゴリ', '機能', '画面', '自動化']);
// 手順の中で期待結果を表すキー
const STEP_EXPECT_KEYS = new Set(['expect', 'expected', 'expected_result', 'assert', 'verify', 'check', 'then',
  '期待結果', '期待値', '確認', '確認内容']);
const STEP_ACTION_KEYS = new Set(['action', 'step', 'do', 'operation', 'when', '操作', '手順', '内容']);
// ケースの列を置くキー。これがあれば、隣のキー（variants・mocks・setup など）は読まない
const CASE_LIST_KEYS = new Set(['checklist', 'checks', 'items', 'check_items', 'checkpoints', 'cases', 'testcases',
  'test_cases', 'tests', 'scenarios', 'specs', 'チェックリスト', '確認項目', 'チェック項目', '項目', 'テストケース',
  'ケース', 'シナリオ', 'テスト項目']);
const GROUP_NAME_KEYS = new Set(['suite', 'feature', 'name', 'title', 'group', 'section', '機能', '画面', '名前', 'タイトル']);

const FENCE = /^\s*(```|~~~)/;
const NUMBERED = /^\s*(?:\d+[.)．、]|[-*・●]|\(\d+\)|（\d+）|[①-⑳])\s*/;

const normKey = (k) => String(k).trim().toLowerCase().replace(/[\s-]+/g, '_');

class FieldMap {
  constructor(extra = {}) {
    this.alias = new Map();
    for (const [canon, names] of Object.entries(FIELD_ALIASES)) {
      for (const n of [...names, ...((extra && extra[canon]) || [])]) this.alias.set(normKey(n), canon);
    }
  }

  canon(key) { return this.alias.get(normKey(key)) || null; }

  isMeta(key) { return META_KEYS.has(normKey(key)); }
}

// ``` で囲まれた部分があれば、その外側を空行に置き換える（行番号は変えない）
function stripFences(text) {
  const lines = text.split('\n');
  const marks = lines.map((l, i) => (FENCE.test(l) ? i : -1)).filter((i) => i >= 0);
  if (marks.length < 2) return text;
  const keep = new Set();
  for (let k = 0; k + 1 < marks.length; k += 2) for (let i = marks[k] + 1; i < marks[k + 1]; i++) keep.add(i);
  return lines.map((l, i) => (keep.has(i) ? l : '')).join('\n');
}

// YAML の構文木を、行番号付きの小さな木（map / seq / scalar）にする
function toTree(node, doc, lc, dupKeys, seen = new Set()) {
  const line = (n) => (n && n.range ? lc.linePos(n.range[0]).line : 0);
  if (node == null) return { t: 'scalar', value: null, line: 0 };
  if (YAML.isAlias(node)) {
    const target = node.resolve(doc);
    if (!target || seen.has(target)) return { t: 'scalar', value: null, line: line(node) };
    return toTree(target, doc, lc, dupKeys, new Set([...seen, target]));
  }
  if (YAML.isMap(node)) {
    const entries = [];
    const index = new Map();
    for (const pair of node.items) {
      const key = YAML.isScalar(pair.key) ? pair.key.value : String(pair.key);
      const keyLine = line(pair.key);
      const value = toTree(pair.value, doc, lc, dupKeys, seen);
      if (value.line === 0) value.line = keyLine;
      const k = String(key);
      if (index.has(k)) {
        dupKeys.push({ key: k, line: keyLine });
        entries[index.get(k)] = { key: k, keyLine, value };
      } else {
        index.set(k, entries.length);
        entries.push({ key: k, keyLine, value });
      }
    }
    return { t: 'map', entries, line: line(node) };
  }
  if (YAML.isSeq(node)) {
    return { t: 'seq', items: node.items.map((x) => toTree(x, doc, lc, dupKeys, seen)), line: line(node) };
  }
  if (YAML.isScalar(node)) return { t: 'scalar', value: node.value, line: line(node), quoted: node.type === 'QUOTE_DOUBLE' || node.type === 'QUOTE_SINGLE' };
  return { t: 'scalar', value: String(node), line: line(node) };
}

class ParseError extends Error {
  constructor(message, line) { super(message); this.line = line; }
}

function parseText(text) {
  const body = stripFences(text.replace(/^﻿/, ''));
  const lc = new YAML.LineCounter();
  const docs = YAML.parseAllDocuments(body, { lineCounter: lc, uniqueKeys: false });
  const list = Array.isArray(docs) ? docs : [docs];
  const dupKeys = [];
  const trees = [];
  for (const doc of list) {
    if (doc.errors && doc.errors.length) {
      const e = doc.errors[0];
      const line = e.linePos ? e.linePos[0].line : 0;
      throw new ParseError(String(e.message).split('\n')[0], line);
    }
    if (doc.contents == null) continue;
    trees.push(toTree(doc.contents, doc, lc, dupKeys));
  }
  return { trees, dupKeys };
}

// 木を素の値に戻す（書式の検査・平たい文に使う）
function plain(n) {
  if (!n) return null;
  if (n.t === 'map') return Object.fromEntries(n.entries.map((e) => [e.key, plain(e.value)]));
  if (n.t === 'seq') return n.items.map(plain);
  return n.value;
}

const scalarText = (v) => (v == null ? '' : typeof v === 'boolean' ? String(v) : String(v).trim());
const isEmpty = (n) => !n || (n.t === 'scalar' && scalarText(n.value) === '') || (n.t === 'seq' && !n.items.length)
  || (n.t === 'map' && !n.entries.length);

// 構造のある値を、比べられる 1 行の文にする。文字列の値は "" で囲み、値として扱わせる
function flatten(n) {
  if (n.t === 'map') {
    return n.entries.map((e) => (e.value.t === 'scalar' ? `${e.key}="${scalarText(e.value.value)}"` : `${e.key}=${flatten(e.value)}`)).join(' ');
  }
  if (n.t === 'seq') return `[${n.items.map(flatten).join(', ')}]`;
  return `"${scalarText(n.value)}"`;
}

function splitLines(text, line, field) {
  const out = [];
  String(text).split('\n').forEach((p, k) => {
    const t = p.replace(NUMBERED, '').trim();
    if (t) out.push({ text: t, line: line ? line + k : 0, field: `${field}[${out.length}]` });
  });
  return out;
}

// 前提・期待結果の値を Item の列にする
function items(n, field, baseLine) {
  if (!n) return [];
  const line = n.line || baseLine;
  if (n.t === 'seq') {
    const out = [];
    for (const x of n.items) {
      const t = x.t === 'scalar' ? scalarText(x.value).replace(NUMBERED, '').trim() : flatten(x);
      if (t) out.push({ text: t, line: x.line || line, field: `${field}[${out.length}]` });
    }
    return out;
  }
  if (n.t === 'map') {
    return n.entries.map((e) => ({ text: `${e.key}: ${e.value.t === 'scalar' ? scalarText(e.value.value) : flatten(e.value)}`, line: e.keyLine || line, field: `${field}.${e.key}` }));
  }
  const text = scalarText(n.value);
  // ブロック（| や >）の中身は、キーの次の行から始まる
  return splitLines(text, line && text.includes('\n') ? line + 1 : line, field);
}

// 手順を (手順, 手順に書かれた期待結果) に分ける
function steps(n, baseLine, fmap) {
  const out = { steps: [], expected: [] };
  if (!n) return out;
  if (n.t !== 'seq') { out.steps = items(n, 'steps', baseLine); return out; }
  n.items.forEach((s, i) => {
    const ln = s.line || n.line || baseLine;
    if (s.t === 'map') {
      let act = null; let exp = null; const rest = [];
      for (const e of s.entries) {
        const key = normKey(e.key);
        if (STEP_EXPECT_KEYS.has(key) || fmap.canon(e.key) === 'expected') exp = e.value;
        else if (STEP_ACTION_KEYS.has(key) || fmap.canon(e.key) === 'steps') act = e.value;
        else rest.push(e);
      }
      if (act || rest.length) {
        const parts = [];
        if (act) parts.push(act.t === 'scalar' ? scalarText(act.value) : flatten(act));
        if (rest.length) parts.push(rest.map((e) => `${e.key} ${flatten(e.value)}`).join(' '));
        out.steps.push({ text: parts.filter(Boolean).join(' '), line: ln, field: `steps[${i}]` });
      }
      if (exp) {
        const list = exp.t === 'seq' ? exp.items : [exp];
        list.forEach((x, k) => {
          const t = x.t === 'scalar' ? scalarText(x.value) : flatten(x);
          if (t) out.expected.push({ text: t, line: x.line || ln, field: `steps[${i}].expected[${k}]` });
        });
      }
    } else {
      const t = s.t === 'scalar' ? scalarText(s.value).replace(NUMBERED, '').trim() : flatten(s);
      if (t) out.steps.push({ text: t, line: ln, field: `steps[${i}]` });
    }
  });
  return out;
}

function requirementList(n) {
  if (!n) return [];
  const vals = n.t === 'seq' ? n.items.map((x) => scalarText(x.value)) : scalarText(n.value).split(/[,、，\s]+/);
  return vals.map((v) => v.trim()).filter(Boolean);
}

// 条件の組（{名前: 値}）。値は文字列にそろえる
function conditionMap(n) {
  if (!n || n.t !== 'map') return null;
  const out = {};
  for (const e of n.entries) out[e.key] = e.value.t === 'scalar' ? scalarText(e.value.value) : flatten(e.value);
  return out;
}

function looksLikeCase(m, fmap, loose) {
  const keys = new Set(m.entries.map((e) => fmap.canon(e.key)));
  if (keys.has('steps') || keys.has('expected') || keys.has('preconditions')) return true;
  if (!loose) return false;
  return keys.has('title') || (keys.has('id') && m.entries.length > 1);
}

const caseListKeys = (m) => m.entries.filter((e) => CASE_LIST_KEYS.has(normKey(e.key)) && e.value.t === 'seq').map((e) => e.key);

function makeCase(m, file, group, index, kind, fmap) {
  const c = {
    file, line: m.line, index, group: [...group], id: null, title: '', preconditions: [], steps: [], expected: [],
    requirements: [], conditions: null, kind, raw: plain(m),
  };
  const extra = [];
  let desc = '';
  for (const e of m.entries) {
    const canon = fmap.canon(e.key);
    const v = e.value;
    if (canon === 'id') c.id = scalarText(v.value) || null;
    else if (canon === 'title') c.title = v.t === 'scalar' ? scalarText(v.value) : flatten(v);
    else if (canon === 'description') desc = v.t === 'scalar' ? scalarText(v.value) : flatten(v);
    else if (canon === 'preconditions') c.preconditions.push(...items(v, 'preconditions', c.line));
    else if (canon === 'steps') { const s = steps(v, c.line, fmap); c.steps.push(...s.steps); c.expected.push(...s.expected); }
    else if (canon === 'expected') c.expected.push(...items(v, 'expected', c.line));
    else if (canon === 'requirements') c.requirements.push(...requirementList(v));
    else if (canon === 'conditions') {
      const cm = conditionMap(v);
      if (cm) c.conditions = { ...(c.conditions || {}), ...cm };
      if (!isEmpty(v)) extra.push(...items(v, String(e.key), c.line));
    } else if (fmap.isMeta(e.key)) continue;
    else if (!isEmpty(v)) {
      extra.push({ text: `${e.key}: ${v.t === 'scalar' ? scalarText(v.value) : flatten(v)}`, line: v.line || c.line, field: String(e.key) });
    }
  }
  c.preconditions.push(...extra);
  if (!c.title && desc) c.title = desc;
  return c;
}

function extractCases(tree, file, fmap, startIndex = 0) {
  const cases = [];

  function addList(seq, group, known) {
    const maps = seq.items.filter((x) => x.t === 'map');
    // 1 件も手順・期待結果を持たない列はチェックリストとみなす
    const hasBody = maps.some((x) => x.entries.some((e) => ['steps', 'expected', 'preconditions'].includes(fmap.canon(e.key))));
    const kind = hasBody ? 'case' : 'check';
    for (const x of seq.items) {
      if (x.t === 'map') {
        const own = new Set(x.entries.map((e) => fmap.canon(e.key)));
        if (caseListKeys(x).length && !own.has('steps') && !own.has('expected')) walk(x, group);
        else if (looksLikeCase(x, fmap, known)) cases.push(makeCase(x, file, group, startIndex + cases.length, kind, fmap));
        else walk(x, group);
      } else if (known && x.t === 'scalar' && scalarText(x.value)) {
        cases.push({
          file, line: x.line || seq.line, index: startIndex + cases.length, group: [...group], id: null,
          title: scalarText(x.value), preconditions: [], steps: [], expected: [], requirements: [], conditions: null,
          kind: 'check', raw: x.value,
        });
      } else if (x.t === 'seq') addList(x, group, known);
    }
  }

  function walk(n, group, known = false) {
    if (n.t === 'seq') return addList(n, group, known);
    if (n.t !== 'map') return undefined;
    const keys = caseListKeys(n);
    if (keys.length) {
      const nameEntry = n.entries.find((e) => GROUP_NAME_KEYS.has(normKey(e.key)) && e.value.t === 'scalar');
      const name = nameEntry ? scalarText(nameEntry.value.value) : '';
      const g = name ? [...group, name] : group;
      for (const k of keys) addList(n.entries.find((e) => e.key === k).value, g, true);
    } else if (looksLikeCase(n, fmap, false) && !group.length && !cases.length) {
      cases.push(makeCase(n, file, group, startIndex, 'case', fmap));
    } else {
      for (const e of n.entries) {
        if ((e.value.t === 'map' || e.value.t === 'seq') && !fmap.canon(e.key) && !fmap.isMeta(e.key)) walk(e.value, [...group, e.key]);
      }
    }
    return undefined;
  }

  walk(tree, [], tree.t === 'seq');
  return cases;
}

// 1 ファイルを読み、{ cases, findings, docs }（docs は書式の検査に使う素の値）を返す
function loadFile(path, display, fmap, startIndex = 0) {
  let text;
  try {
    text = fs.readFileSync(path, 'utf8');
  } catch (e) {
    return { cases: [], docs: [], findings: [{ code: 'D00', message: `読めません: ${e.message}`, file: display, line: 0 }] };
  }
  let parsed;
  try {
    parsed = parseText(text);
  } catch (e) {
    return {
      cases: [], docs: [],
      findings: [{ code: 'D00', message: `JSON / YAML として読めません: ${e.message}`, file: display, line: e.line || 0, hint: '括弧・引用符・インデントの崩れを直す' }],
    };
  }
  const findings = parsed.dupKeys.map((d) => ({
    code: 'D15', message: `キー「${d.key}」が同じ所に 2 回あり、先の値は読まれずに捨てられます`, file: display, line: d.line,
    hint: 'どちらかを消すか、別のキーにする',
  }));
  const cases = [];
  for (const tree of parsed.trees) cases.push(...extractCases(tree, display, fmap, startIndex + cases.length));
  if (!cases.length) {
    findings.push({
      code: 'D17', message: 'ケースを 1 件も読み取れませんでした（空のファイル、または項目名が読み取れる綴りと違います）', file: display, line: 0,
      hint: 'ケースの列（cases・tests・テストケース など）と、手順・期待結果の項目名を確かめる。`testcase-lint schema` におすすめの書式がある',
    });
  }
  return { cases, findings, docs: parsed.trees.map(plain), trees: parsed.trees };
}

// 木の中の場所（JSON Pointer の分かれ目の列）の行番号。たどれない所は、たどれた所までの行
function lineAt(tree, segments) {
  let n = tree; let line = tree ? tree.line : 0;
  for (const seg of segments) {
    if (!n) break;
    if (n.t === 'map') { const e = n.entries.find((x) => x.key === seg); if (!e) break; line = e.keyLine || line; n = e.value; } else if (n.t === 'seq') { n = n.items[Number(seg)]; if (n && n.line) line = n.line; } else break;
  }
  return line;
}

module.exports = {
  lineAt, FieldMap, parseText, stripFences, extractCases, loadFile, plain, flatten, scalarText, normKey, ParseError,
};
