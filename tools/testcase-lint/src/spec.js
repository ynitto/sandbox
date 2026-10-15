'use strict';
// 仕様のフォルダを読み、テストケースと突き合わせられる形（仕様のモデル）にする。LLM は使わない。
//
// 読むもの（フォルダの下を再帰で。node_modules・.git などは飛ばす）
//   .md / .markdown … 見出し・箇条書き・表・Mermaid の状態遷移図・「」で囲んだ文言
//   .yaml / .yml / .json … 要件の一覧・OpenAPI・JSON Schema・状態遷移（states / transitions）
//   .csv … 表として読む（1 行目が見出し）
//
// 取り出すもの
//   requirements … { id, text }            要件 ID（既定の形は REQ-1・FR-01 など。設定で変えられる）
//   fields       … { name, required, min, max, unit, enum, format }  項目定義の表・JSON Schema・OpenAPI の引数
//   machines     … { name, transitions: [{ from, event, to }] }       状態遷移の表・Mermaid stateDiagram
//   permissions  … { name, roles, cells: [{ op, role, allowed }] }    ○× の権限表
//   endpoints    … { method, path, codes }                            OpenAPI・本文の「GET /path」と応答コード
//   messages     … { text }                                           メッセージ列の値と「」で囲んだ文言
// どれも file と line（見つけた場所）を持つ。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const tx = require('./text');

const SKIP_DIRS = new Set(['node_modules', '.git', '.svn', 'dist', 'build', 'out', 'coverage', '.venv', 'venv', '__pycache__', '.next', '.cache']);
const DEFAULT_REQ_ID = '\\b(?:REQ|FR|NFR|UR|SR|US|BR|UC|RQ)[-_]?\\d+(?:[-_.]\\d+)*\\b';

function walkFiles(dir, exts, limit = 20000) {
  const out = [];
  const stack = [dir];
  while (stack.length && out.length < limit) {
    const d = stack.pop();
    let entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { continue; }
    for (const e of entries.sort((a, b) => (a.name < b.name ? 1 : -1))) {
      if (e.name.startsWith('.') && e.name !== '.') continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) stack.push(p); } else if (exts.test(e.name)) out.push(p);
    }
  }
  return out.sort();
}

// ---- 表の見分け ----

const COL = {
  name: /^(項目名?|名前|名称|フィールド名?|属性名?|パラメータ名?|引数名?|入力項目|論理名|物理名|name|field|parameter|param|property|attribute|key)$/,
  required: /必須|required|mandatory/,
  type: /^(型|データ型|種別|type|data ?type)$/,
  length: /桁数?|文字数|長さ|length|size|バイト/,
  min: /^(最小(値|文字数|桁数)?|下限(値)?|min(imum)?|min ?length)$/,
  max: /^(最大(値|文字数|桁数)?|上限(値)?|max(imum)?|max ?length)$/,
  range: /範囲|値域|range|制約|入力制限|入力規則|チェック/,
  format: /形式|書式|フォーマット|format|pattern|正規表現|入力形式/,
  enum: /選択肢|許容値|区分値|コード値|enum|候補|取りうる値/,
  from: /^(現在の?状態|現状態|遷移元(の状態)?|状態|前の?状態|from|source|current ?state|state)$/,
  event: /^(イベント|操作|トリガー?|契機|アクション|event|trigger|action|transition)$/,
  to: /^(次の?状態|遷移先(の状態)?|変更後(の状態)?|後の?状態|to|target|next ?state)$/,
  message: /メッセージ|文言|表示内容|表示文言|エラー文言|message|text|label/,
  op: /^(操作|機能|画面|処理|権限|action|operation|feature|function|permission)$/,
};
const MARK_YES = /^(○|◯|〇|✓|✔|o|y|yes|可|許可|true|allow(ed)?|◎)$/i;
const MARK_NO = /^(×|✕|✗|✖|x|n|no|不可|禁止|false|deny|denied|-|―|ー)$/i;

const cleanCell = (s) => String(s == null ? '' : s).replace(/`/g, '').replace(/\*\*/g, '').replace(/<br\s*\/?>/gi, ' ').trim();
const findCol = (header, re) => header.findIndex((h) => re.test(tx.normalize(h)));

function parseNumber(s) {
  const m = /-?\d+(?:\.\d+)?/.exec(String(s).normalize('NFKC'));
  return m ? Number(m[0]) : null;
}

// 「1〜20」「8-20文字」「最大20文字」「20桁」「0以上100以下」などから [min, max, unit]
function parseRange(s) {
  const t = String(s).normalize('NFKC');
  const unit = tx.unitOf((new RegExp(`(${tx.UNITS.map((u) => u.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).sort((a, b) => b.length - a.length).join('|')})`).exec(t.toLowerCase()) || [])[1]);
  const span = /(-?\d+(?:\.\d+)?)\s*[^\d\s]{0,4}\s*[〜~～\-–—ー]\s*(-?\d+(?:\.\d+)?)/.exec(t);
  if (span) return [Number(span[1]), Number(span[2]), unit];
  let min = null; let max = null;
  for (const k of tx.constraints(t)) { if (k.kind === 'max') max = k.value; else min = k.value; }
  if (min != null || max != null) return [min, max, unit];
  const n = parseNumber(t);
  return n != null ? [null, n, unit] : [null, null, unit];
}

// 表 1 つを見分けて、モデルに足す
function addTable(model, table, ctx) {
  const header = table.header.map(cleanCell);
  const rows = table.rows.map((r) => ({ cells: r.cells.map(cleanCell), line: r.line }));
  const at = (re) => findCol(header, re);
  const where = (r) => ({ file: ctx.file, line: r.line, section: ctx.section });

  // 状態遷移の表
  const iFrom = at(COL.from); const iEvent = at(COL.event); const iTo = at(COL.to);
  if (iFrom >= 0 && iEvent >= 0 && iTo >= 0 && iFrom !== iTo) {
    const transitions = rows.filter((r) => r.cells[iFrom] && r.cells[iTo]).map((r) => ({ from: r.cells[iFrom], event: r.cells[iEvent], to: r.cells[iTo], ...where(r) }));
    if (transitions.length) model.machines.push({ name: ctx.section || path.basename(ctx.file), transitions, file: ctx.file, line: table.line });
    return;
  }
  // 権限表（2 列以上が ○× の印でうまっている）
  const markCols = header.map((h, i) => i).filter((i) => i > 0 && rows.length && rows.filter((r) => MARK_YES.test(r.cells[i] || '') || MARK_NO.test(r.cells[i] || '')).length >= Math.max(1, rows.length * 0.6));
  if (markCols.length >= 2) {
    const cells = [];
    for (const r of rows) {
      for (const i of markCols) {
        const v = r.cells[i] || '';
        if (MARK_YES.test(v) || MARK_NO.test(v)) cells.push({ op: r.cells[0], role: header[i], allowed: MARK_YES.test(v), ...where(r) });
      }
    }
    model.permissions.push({ name: ctx.section || path.basename(ctx.file), roles: markCols.map((i) => header[i]), cells, file: ctx.file, line: table.line });
    return;
  }
  // 項目定義の表
  const iName = at(COL.name);
  const cons = { required: at(COL.required), type: at(COL.type), length: at(COL.length), min: at(COL.min), max: at(COL.max), range: at(COL.range), format: at(COL.format), enum: at(COL.enum) };
  if (iName >= 0 && Object.values(cons).some((i) => i >= 0 && i !== iName)) {
    for (const r of rows) {
      const name = r.cells[iName];
      if (!name) continue;
      const fld = { name, required: false, min: null, max: null, unit: '', enum: null, format: '', type: '', ...where(r), group: ctx.section || path.basename(ctx.file) };
      if (cons.required >= 0) fld.required = MARK_YES.test(r.cells[cons.required]) || /必須|required|must/i.test(r.cells[cons.required]);
      if (cons.type >= 0) fld.type = r.cells[cons.type];
      for (const key of ['length', 'range']) {
        if (cons[key] < 0 || !r.cells[cons[key]]) continue;
        const [mn, mx, unit] = parseRange(r.cells[cons[key]]);
        if (mn != null) fld.min = mn;
        if (mx != null) fld.max = mx;
        if (unit) fld.unit = unit;
        if (key === 'length' && !fld.unit) fld.unit = /桁/.test(header[cons.length]) ? '桁' : '文字';
        if (/必須/.test(r.cells[cons[key]])) fld.required = true;
      }
      if (cons.min >= 0 && r.cells[cons.min]) fld.min = parseNumber(r.cells[cons.min]);
      if (cons.max >= 0 && r.cells[cons.max]) fld.max = parseNumber(r.cells[cons.max]);
      if ((cons.min >= 0 || cons.max >= 0) && !fld.unit && /文字|length/.test(header[cons.max >= 0 ? cons.max : cons.min])) fld.unit = '文字';
      if (cons.format >= 0) fld.format = r.cells[cons.format];
      if (cons.enum >= 0 && r.cells[cons.enum]) {
        const vals = r.cells[cons.enum].split(/[、,，/／|]|\s+/).map((x) => x.trim()).filter(Boolean);
        if (vals.length >= 2) fld.enum = vals;
      }
      model.fields.push(fld);
    }
  }
  // メッセージの表
  const iMsg = at(COL.message);
  if (iMsg >= 0) for (const r of rows) if (r.cells[iMsg]) addMessage(model, r.cells[iMsg], where(r));
  // 表の中の要件 ID
  for (const r of rows) addRequirementsFromLine(model, r.cells.join(' | '), { ...where(r) }, ctx.reqRe, r.cells[0]);
}

function addMessage(model, text, where) {
  const t = String(text).replace(/^[「『"]|[」』"]$/g, '').trim();
  if ([...t].length < 4 || /^[\w./:-]+$/.test(t)) return; // 識別子・パスらしいものは文言に数えない
  model.messages.push({ text: t, ...where });
}

function addRequirementsFromLine(model, line, where, reqRe, firstCell) {
  const re = new RegExp(reqRe, 'gi');
  const ids = [...new Set((line.match(re) || []).map((x) => x.toUpperCase()))];
  for (const id of ids) {
    const existing = model.requirements.find((r) => r.id === id);
    // 見出し・箇条書きの頭・表の 1 列目に書かれた ID を「定義」とみなす。ほかは言及
    const defining = firstCell != null ? new RegExp(`^${reqRe}$`, 'i').test(firstCell.trim()) : new RegExp(`^\\s*(?:#+|[-*+]|\\d+[.)])?\\s*\\[?${id}\\b`, 'i').test(line);
    const text = line.replace(re, '').replace(/^[\s#*+\-|:：.)\]]+/, '').replace(/\s*\|\s*/g, ' ').trim();
    if (!existing) model.requirements.push({ id, text, defined: defining, ...where });
    else if (defining && !existing.defined) Object.assign(existing, { text, defined: true, ...where });
  }
}

// ---- Markdown ----

function readMarkdown(model, file, text, reqRe) {
  const lines = text.split('\n');
  const headings = [];
  let fence = null; let mermaid = [];
  let table = null;
  const section = () => headings.filter(Boolean).slice(-2).join(' / ');
  const flushTable = () => { if (table && table.rows.length) addTable(model, table, { file, section: table.section, reqRe }); table = null; };
  lines.forEach((raw, i) => {
    const ln = i + 1;
    const fm = /^\s*(```|~~~)\s*(\w*)/.exec(raw);
    if (fm) {
      if (!fence) { flushTable(); fence = fm[2] || 'code'; mermaid = []; return; }
      if (fence === 'mermaid') readMermaid(model, file, mermaid, section());
      fence = null; return;
    }
    if (fence) { if (fence === 'mermaid') mermaid.push({ text: raw, line: ln }); return; }
    const h = /^(#{1,6})\s+(.*)$/.exec(raw);
    if (h) { flushTable(); headings.length = h[1].length - 1; headings[h[1].length - 1] = h[2].trim(); addRequirementsFromLine(model, raw, { file, line: ln, section: section() }, reqRe); return; }
    if (/^\s*\|.*\|\s*$/.test(raw)) {
      const cells = raw.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map((c) => c.trim());
      if (!table) { table = { header: cells, rows: [], line: ln, section: section() }; return; }
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) return;
      table.rows.push({ cells, line: ln });
      return;
    }
    flushTable();
    if (!raw.trim()) return;
    addRequirementsFromLine(model, raw, { file, line: ln, section: section() }, reqRe);
    for (const q of raw.matchAll(/「([^」]{4,})」|『([^』]{4,})』/g)) addMessage(model, q[1] || q[2], { file, line: ln, section: section() });
    readEndpointLine(model, raw, { file, line: ln, section: section() });
  });
  flushTable();
}

// Mermaid の stateDiagram（A --> B : イベント）
function readMermaid(model, file, block, section) {
  if (!block.length || !/statediagram/i.test(block[0].text)) return;
  const transitions = [];
  for (const { text, line } of block.slice(1)) {
    const m = /^\s*(\[\*\]|[^\s:]+)\s*-->\s*(\[\*\]|[^\s:]+)\s*(?::\s*(.+))?$/.exec(text);
    if (m && m[1] !== '[*]' && m[2] !== '[*]') transitions.push({ from: m[1], to: m[2], event: (m[3] || '').trim(), file, line, section });
  }
  if (transitions.length) model.machines.push({ name: section || path.basename(file), transitions, file, line: block[0].line });
}

const METHOD_PATH = /\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\/[^\s`'")|]*)/g;
function readEndpointLine(model, raw, where) {
  for (const m of raw.matchAll(METHOD_PATH)) {
    const ep = endpoint(model, m[1], m[2], where);
    for (const c of raw.slice(m.index).match(/\b[1-5]\d\d\b/g) || []) ep.codes.add(c);
    model.lastEndpoint = ep;
  }
  // 見出しの下の本文に応答コードだけが並ぶ書き方（「400: 入力の誤り」）
  if (model.lastEndpoint && model.lastEndpoint.file === where.file && model.lastEndpoint.section === where.section && !METHOD_PATH.test(raw)) {
    for (const c of raw.match(/(?:^|[\s|`(])([1-5]\d\d)(?=[\s|`):：])/g) || []) model.lastEndpoint.codes.add(c.trim().replace(/[|`(]/g, ''));
  }
  METHOD_PATH.lastIndex = 0;
}

function endpoint(model, method, p, where) {
  const key = `${method.toUpperCase()} ${p}`;
  let ep = model.endpoints.find((e) => `${e.method} ${e.path}` === key);
  if (!ep) { ep = { method: method.toUpperCase(), path: p, codes: new Set(), ...where }; model.endpoints.push(ep); }
  return ep;
}

// ---- YAML / JSON ----

function readStructured(model, file, data, reqRe) {
  if (!data || typeof data !== 'object') return;
  const where = { file, line: 0, section: path.basename(file) };
  // OpenAPI / Swagger
  if (data.openapi || data.swagger) {
    for (const [p, ops] of Object.entries(data.paths || {})) {
      for (const [method, op] of Object.entries(ops || {})) {
        if (!/^(get|post|put|patch|delete|head|options)$/i.test(method)) continue;
        const ep = endpoint(model, method, p, where);
        for (const code of Object.keys((op && op.responses) || {})) if (/^\d{3}$/.test(code)) ep.codes.add(code);
        for (const prm of (op && op.parameters) || []) if (prm && prm.name) addSchemaField(model, prm.name, prm.schema || prm, Boolean(prm.required), where, `${method.toUpperCase()} ${p}`);
        const body = op && op.requestBody && op.requestBody.content;
        for (const media of Object.values(body || {})) addSchemaObject(model, resolveRef(data, media.schema), where, `${method.toUpperCase()} ${p}`, data);
      }
    }
    return;
  }
  // JSON Schema
  if (data.$schema || (data.type === 'object' && data.properties)) { addSchemaObject(model, data, where, data.title || path.basename(file), data); return; }
  // 状態遷移（states / transitions）
  if (Array.isArray(data.transitions)) {
    const transitions = data.transitions.filter((t) => t && t.from && t.to).map((t) => ({ from: String(t.from), to: String(t.to), event: String(t.event || t.on || t.trigger || ''), ...where }));
    if (transitions.length) model.machines.push({ name: data.name || path.basename(file), transitions, file, line: 0 });
    return;
  }
  // 要件の一覧（{id, text} の列か、{ID: 本文}）
  const visit = (node) => {
    if (Array.isArray(node)) {
      for (const x of node) {
        if (x && typeof x === 'object' && !Array.isArray(x)) {
          const id = x.id || x.ID || x.key;
          if (id && new RegExp(`^${reqRe}$`, 'i').test(String(id))) {
            const text = ['text', 'title', 'description', 'statement', 'name', '本文', '要件', '内容'].map((k) => x[k]).filter(Boolean).join(' ');
            model.requirements.push({ id: String(id).toUpperCase(), text, defined: true, ...where });
          } else visit(Object.values(x));
        } else if (typeof x === 'string' && new RegExp(`^${reqRe}$`, 'i').test(x)) {
          model.requirements.push({ id: x.toUpperCase(), text: '', defined: true, ...where });
        } else if (Array.isArray(x)) visit(x);
      }
    } else if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) {
        if (new RegExp(`^${reqRe}$`, 'i').test(k) && (typeof v === 'string' || v == null)) model.requirements.push({ id: k.toUpperCase(), text: String(v || ''), defined: true, ...where });
        else if (v && typeof v === 'object') visit(v);
      }
    }
  };
  visit(data);
}

function resolveRef(root, s) {
  let cur = s;
  for (let n = 0; cur && cur.$ref && n < 10; n++) {
    const parts = String(cur.$ref).replace(/^#\//, '').split('/');
    cur = parts.reduce((o, k) => (o ? o[k] : undefined), root);
  }
  return cur;
}

function addSchemaObject(model, schema, where, group, root) {
  const s = resolveRef(root, schema);
  if (!s || !s.properties) return;
  const req = new Set(s.required || []);
  for (const [name, prop] of Object.entries(s.properties)) addSchemaField(model, name, resolveRef(root, prop), req.has(name), where, group);
}

function addSchemaField(model, name, s, required, where, group) {
  s = s || {};
  const fld = { name: s.title || name, required, min: null, max: null, unit: '', enum: null, format: s.format || s.pattern || '', type: s.type || '', ...where, group };
  if (s.minLength != null || s.maxLength != null) { fld.min = s.minLength ?? null; fld.max = s.maxLength ?? null; fld.unit = '文字'; }
  if (s.minimum != null || s.maximum != null) { fld.min = s.minimum ?? null; fld.max = s.maximum ?? null; }
  if (Array.isArray(s.enum) && s.enum.length >= 2) fld.enum = s.enum.map(String);
  model.fields.push(fld);
}

// ---- CSV ----

function readCsv(model, file, text, reqRe) {
  const rows = text.split(/\r?\n/).filter((l) => l.trim()).map((l, i) => ({ cells: splitCsv(l), line: i + 1 }));
  if (rows.length < 2) return;
  addTable(model, { header: rows[0].cells, rows: rows.slice(1), line: 1 }, { file, section: path.basename(file), reqRe });
}

function splitCsv(line) {
  const out = []; let cur = ''; let q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch; } else if (ch === '"') q = true; else if (ch === ',') { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out;
}

// ---- 入口 ----

function loadSpec(dirs, { requirementId, display = (p) => p } = {}) {
  const reqRe = requirementId || DEFAULT_REQ_ID;
  const model = { requirements: [], fields: [], machines: [], permissions: [], endpoints: [], messages: [], files: [], errors: [], corpus: '' };
  const seen = new Set(); // 同じファイルを 2 度読まない（フォルダと glob の両方で渡したときなど）
  for (const dir of dirs) {
    let stat;
    try { stat = fs.statSync(dir); } catch { model.errors.push(`仕様のフォルダがありません: ${dir}`); continue; }
    const files = stat.isDirectory() ? walkFiles(dir, /\.(md|markdown|ya?ml|json|csv)$/i) : [dir];
    for (const abs of files) {
      if (seen.has(path.resolve(abs))) continue;
      seen.add(path.resolve(abs));
      const file = display(abs);
      let text;
      try { text = fs.readFileSync(abs, 'utf8').replace(/^﻿/, ''); } catch { continue; }
      model.files.push(file);
      model.corpus += `\n${tx.normalize(text)}`;
      model.lastEndpoint = null;
      try {
        if (/\.(md|markdown)$/i.test(abs)) readMarkdown(model, file, text, reqRe);
        else if (/\.csv$/i.test(abs)) readCsv(model, file, text, reqRe);
        else readStructured(model, file, /\.json$/i.test(abs) ? JSON.parse(text) : YAML.parse(text), reqRe);
      } catch (e) {
        model.errors.push(`${file}: 読めません（${String(e.message).split('\n')[0]}）`);
      }
    }
  }
  delete model.lastEndpoint;
  // 言及だけで定義の無い ID は、ほかのどこにも定義が無いときだけ要件として残す
  model.messages = dedupeBy(model.messages, (m) => tx.normalize(m.text));
  for (const ep of model.endpoints) ep.codes = [...ep.codes].sort();
  return model;
}

function dedupeBy(list, key) {
  const seen = new Set();
  return list.filter((x) => { const k = key(x); if (seen.has(k)) return false; seen.add(k); return true; });
}

module.exports = { loadSpec, parseRange, DEFAULT_REQ_ID, walkFiles, SKIP_DIRS };
