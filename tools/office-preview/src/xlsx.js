'use strict';

// xlsx の 1 シートを、Excel で開いたときの左上の見え方に近い HTML にする。
// 列見出し（A, B, …）と行番号、列幅と行の高さ、セルの書式（フォント・塗り・罫線・配置）、
// 表示形式（桁区切り・小数・%・日付）、セルの結合、文字のはみ出し、シート上の画像を描く。
// グラフ・条件付き書式・図形は描かない。

const { parseXml, kid, kids, attr, path: xpath, textOf, findAll } = require('./xml');
const { escapeHtml, fontStack, applyTint, emuToPx } = require('./ooxml');

// Excel の古い色番号（indexed）
const INDEXED = [
  '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF', '000000', 'FFFFFF', 'FF0000', '00FF00',
  '0000FF', 'FFFF00', 'FF00FF', '00FFFF', '800000', '008000', '000080', '808000', '800080', '008080', 'C0C0C0', '808080',
  '9999FF', '993366', 'FFFFCC', 'CCFFFF', '660066', 'FF8080', '0066CC', 'CCCCFF', '000080', 'FF00FF', 'FFFF00', '00FFFF',
  '800080', '800000', '008080', '0000FF', '00CCFF', 'CCFFFF', 'CCFFCC', 'FFFF99', '99CCFF', 'FF99CC', 'CC99FF', 'FFCC99',
  '3366FF', '33CCCC', '99CC00', 'FFCC00', 'FF9900', 'FF6600', '666699', '969696', '003366', '339966', '003300', '333300',
  '993300', '993366', '333399', '333333',
];
const THEME_ORDER = ['lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink'];

const BUILTIN_FORMATS = {
  0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 5: '"¥"#,##0;"¥"\\-#,##0', 6: '"¥"#,##0;[Red]"¥"\\-#,##0',
  7: '"¥"#,##0.00;"¥"\\-#,##0.00', 8: '"¥"#,##0.00;[Red]"¥"\\-#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00',
  12: '# ?/?', 13: '# ??/??', 14: 'yyyy/m/d', 15: 'd-mmm-yy', 16: 'd-mmm', 17: 'mmm-yy', 18: 'h:mm AM/PM',
  19: 'h:mm:ss AM/PM', 20: 'h:mm', 21: 'h:mm:ss', 22: 'yyyy/m/d h:mm', 37: '#,##0 ;(#,##0)', 38: '#,##0 ;[Red](#,##0)',
  39: '#,##0.00;(#,##0.00)', 40: '#,##0.00;[Red](#,##0.00)', 45: 'mm:ss', 46: '[h]:mm:ss', 47: 'mm:ss.0', 48: '##0.0E+0', 49: '@',
  // 日本語版の組み込み
  27: 'yyyy"年"m"月"', 28: 'm"月"d"日"', 29: 'm"月"d"日"', 30: 'm/d/yy', 31: 'yyyy"年"m"月"d"日"', 32: 'h"時"mm"分"',
  33: 'h"時"mm"分"ss"秒"', 34: 'yyyy"年"m"月"', 35: 'm"月"d"日"', 36: 'yyyy"年"m"月"', 50: 'yyyy"年"m"月"', 51: 'm"月"d"日"',
  52: 'yyyy"年"m"月"', 53: 'm"月"d"日"', 54: 'm"月"d"日"', 55: 'yyyy"年"m"月"', 56: 'm"月"d"日"', 57: 'yyyy"年"m"月"', 58: 'm"月"d"日"',
};

const COLOR_NAMES = { red: '#FF0000', blue: '#0000FF', green: '#008000', black: '#000000', white: '#FFFFFF', yellow: '#FFFF00', magenta: '#FF00FF', cyan: '#00FFFF' };

// ---- 番地 -------------------------------------------------------------------

function colIndex(letters) {
  let n = 0;
  for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n; // 1 始まり
}
function colName(n) {
  let s = '';
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}
function parseRef(ref) {
  const m = /^\$?([A-Za-z]+)\$?(\d+)$/.exec(ref || '');
  return m ? { col: colIndex(m[1]), row: Number(m[2]) } : null;
}

// ---- 表示形式 -----------------------------------------------------------------

function splitSections(code) {
  const out = [];
  let cur = '', q = false;
  for (let i = 0; i < code.length; i++) {
    const ch = code[i];
    if (ch === '"') q = !q;
    if (ch === '\\' && !q) { cur += ch + (code[i + 1] ?? ''); i++; continue; }
    if (ch === ';' && !q) { out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}

// [Red] や [$-411] を取り除き、色と通貨記号を拾う
function stripBrackets(section) {
  let color = null;
  const body = section.replace(/\[([^\]]*)\]/g, (m, inner) => {
    if (COLOR_NAMES[inner.toLowerCase()]) { color = COLOR_NAMES[inner.toLowerCase()]; return ''; }
    if (/^color\d+$/i.test(inner)) return '';
    if (inner.startsWith('$')) { const sym = inner.slice(1).split('-')[0]; return sym ? `"${sym}"` : ''; }
    if (/^[hms]+$/i.test(inner)) return m; // 経過時間 [h]
    return ''; // 条件 [>100] など
  });
  return { body, color };
}

function isDateFormat(body) {
  const plain = body.replace(/"[^"]*"/g, '').replace(/\\./g, '').replace(/_.|\*./g, '');
  return /[ymdhsegaYMDHSEGA]/.test(plain.replace(/AM\/PM|A\/P/gi, 'h')) && !/[0#?]/.test(plain.replace(/\.0+/, ''));
}

function serialToDate(serial, date1904) {
  if (date1904) return new Date(Date.UTC(1904, 0, 1) + Math.round(serial * 86400000));
  // 1900 年をうるう年とみなす Excel の誤りに合わせる
  const base = serial < 60 ? Date.UTC(1899, 11, 31) : Date.UTC(1899, 11, 30);
  return new Date(base + Math.round(serial * 86400000));
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAYS_JA = ['日', '月', '火', '水', '木', '金', '土'];

function formatDate(serial, body, date1904) {
  const d = serialToDate(serial, date1904);
  let Y = d.getUTCFullYear(), M = d.getUTCMonth(), D = d.getUTCDate(), W = d.getUTCDay();
  if (!date1904 && Math.floor(serial) === 60) { Y = 1900; M = 1; D = 29; W = 3; } // Excel にだけある 1900/2/29
  let h = d.getUTCHours();
  const mi = d.getUTCMinutes(), s = d.getUTCSeconds();
  const ampm = /AM\/PM|A\/P/i.test(body);
  const tokens = body.match(/"[^"]*"|\\.|\[h+\]|\[m+\]|\[s+\]|AM\/PM|A\/P|yyyy|yy|e+|g+|m{1,5}|d{1,4}|a{3,4}|h{1,2}|s{1,2}|\.0+|_.|\*.|./gi) || [];
  // m は直前に h があるか直後に s があれば「分」
  let out = '';
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    const lower = t.toLowerCase();
    const pad = (n, w = 2) => String(n).padStart(w, '0');
    if (t.startsWith('"')) out += t.slice(1, -1);
    else if (t.startsWith('\\')) out += t[1];
    else if (t.startsWith('_')) out += ' ';
    else if (t.startsWith('*')) continue;
    else if (lower === 'yyyy' || /^e+$/.test(lower)) out += Y;
    else if (lower === 'yy') out += pad(Y % 100);
    else if (/^g+$/.test(lower)) continue;
    else if (/^m+$/.test(lower)) {
      const prev = tokens.slice(0, i).reverse().find((x) => /^\[?[a-z]/i.test(x) && !/^(am\/pm|a\/p)$/i.test(x));
      const next = tokens.slice(i + 1).find((x) => /^\[?[a-z]/i.test(x));
      const isMinute = lower.length <= 2 && ((prev && /^\[?h/i.test(prev)) || (next && /^\[?s/i.test(next)));
      if (isMinute) out += lower.length === 2 ? pad(mi) : mi;
      else if (lower.length === 1) out += M + 1;
      else if (lower.length === 2) out += pad(M + 1);
      else if (lower.length === 3) out += MONTHS[M].slice(0, 3);
      else if (lower.length === 5) out += MONTHS[M][0];
      else out += MONTHS[M];
    } else if (/^d+$/.test(lower)) {
      if (lower.length === 1) out += D;
      else if (lower.length === 2) out += pad(D);
      else if (lower.length === 3) out += DAYS[W].slice(0, 3);
      else out += DAYS[W];
    } else if (lower === 'aaa') out += DAYS_JA[W];
    else if (lower === 'aaaa') out += `${DAYS_JA[W]}曜日`;
    else if (/^h+$/.test(lower)) { const hh = ampm ? ((h % 12) || 12) : h; out += lower.length === 2 ? pad(hh) : hh; }
    // [h] は最小桁数 1、[hh] は最小桁数 2。分・秒の経過時間も同様。
    else if (/^\[h+\]$/.test(lower)) out += pad(Math.floor(serial * 24), lower.length - 2);
    else if (/^\[m+\]$/.test(lower)) out += pad(Math.floor(serial * 1440), lower.length - 2);
    else if (/^\[s+\]$/.test(lower)) out += pad(Math.floor(serial * 86400), lower.length - 2);
    else if (/^s+$/.test(lower)) out += lower.length === 2 ? pad(s) : s;
    else if (/^\.0+$/.test(t)) out += (d.getUTCMilliseconds() / 1000).toFixed(t.length - 1).slice(1);
    else if (lower === 'am/pm') out += h < 12 ? 'AM' : 'PM';
    else if (lower === 'a/p') out += h < 12 ? 'A' : 'P';
    else out += t;
  }
  return out;
}

function formatGeneral(v) {
  if (!Number.isFinite(v)) return String(v);
  if (Number.isInteger(v) && Math.abs(v) < 1e11) return String(v);
  const abs = Math.abs(v);
  if (abs !== 0 && (abs >= 1e11 || abs < 1e-9)) {
    const [m, e] = v.toExponential(5).split('e');
    const exp = Number(e);
    return `${m.replace(/\.?0+$/, '')}E${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`;
  }
  return String(Number(v.toPrecision(10)));
}

function formatNumberBody(v, body) {
  // 文字列リテラルを退避してから、数値の部分を探す
  const lits = [];
  let s = body.replace(/"([^"]*)"|\\(.)|_(.)|\*(.)/g, (m, q, esc, under) => {
    lits.push(q ?? esc ?? (under != null ? ' ' : ''));
    // 数字を含まない目印（私用領域の 1 文字）で退避する。数字だと書式の 0 と見分けられない
    return String.fromCharCode(0xe000 + lits.length - 1);
  });
  const m = /[0#?][0#?,]*(\.[0#?]*)?([eE][+-][0#]+)?/.exec(s);
  if (!m) return restore(s, lits).replace(/@/g, '');
  const numPart = m[0];
  let val = Math.abs(v);
  const percent = (s.match(/%/g) || []).length;
  val *= 100 ** percent;
  const trailingCommas = /,+$/.exec(numPart.split('.')[0]);
  if (trailingCommas) val /= 1000 ** trailingCommas[0].length;
  const decimals = m[1] ? m[1].length - 1 : 0;
  let text;
  if (m[2]) {
    text = val.toExponential(decimals).toUpperCase().replace(/E([+-])(\d)$/, 'E$10$2');
  } else {
    text = val.toFixed(decimals);
    if (/[0#?],[0#?]/.test(numPart)) {
      const [i, f] = text.split('.');
      text = i.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (f != null ? `.${f}` : '');
    }
    // 整数部が # だけなら 0 を出さない（#.## の 0.5 → .5）
    const intPat = numPart.split('.')[0].replace(/,/g, '');
    if (!/0/.test(intPat) && text.startsWith('0')) text = text.slice(1);
    const minInt = (intPat.match(/0/g) || []).length;
    const [ip, fp] = text.split('.');
    if (ip.replace(/,/g, '').length < minInt) text = ip.padStart(minInt, '0') + (fp != null ? `.${fp}` : '');
  }
  return restore(s.slice(0, m.index) + text + s.slice(m.index + numPart.length), lits);
}
function restore(s, lits) { return s.replace(/[\ue000-\uf8ff]/g, (ch) => lits[ch.charCodeAt(0) - 0xe000] ?? ''); }

// 数値 v を書式 code で文字列にする。{ text, color }
function formatValue(v, code, date1904 = false) {
  if (!code || /^general$/i.test(code)) return { text: formatGeneral(v), color: null };
  const sections = splitSections(code);
  let idx = 0;
  if (v < 0 && sections.length > 1) idx = 1;
  else if (v === 0 && sections.length > 2) idx = 2;
  const { body, color } = stripBrackets(sections[idx]);
  if (/^general$/i.test(body.trim())) return { text: (idx === 1 ? '' : (v < 0 ? '-' : '')) + formatGeneral(Math.abs(v)), color };
  if (isDateFormat(body)) return { text: formatDate(v, body, date1904), color };
  let text = formatNumberBody(v, body);
  // 負の値で、負の書式が無いときだけ符号を付ける
  if (v < 0 && idx === 0 && /[1-9]/.test(text)) text = `-${text}`;
  return { text, color };
}

// ---- スタイル -----------------------------------------------------------------

class XlsxRenderer {
  constructor(pkg, opts = {}) {
    this.pkg = pkg;
    this.opts = opts;
    this.main = pkg.mainPart();
    this.wb = pkg.xml(this.main);
    this.theme = pkg.theme(this.main);
    this.date1904 = /^(1|true)$/i.test(attr(kid(this.wb, 'workbookPr'), 'date1904') || '');
    this.loadStrings();
    this.loadStyles();
  }

  color(node) {
    if (!node) return null;
    if (attr(node, 'auto') === '1') return null;
    let hex = null;
    const rgb = attr(node, 'rgb');
    if (rgb) hex = rgb.slice(-6);
    else if (attr(node, 'theme') != null) hex = this.theme.colors[THEME_ORDER[Number(attr(node, 'theme'))]];
    else if (attr(node, 'indexed') != null) {
      const i = Number(attr(node, 'indexed'));
      if (i === 64) return null; // システムの前景色
      hex = INDEXED[i];
    }
    if (!hex) return null;
    const tint = Number(attr(node, 'tint') || 0);
    return `#${applyTint(hex, tint)}`;
  }

  loadStrings() {
    this.strings = [];
    const tree = this.pkg.xml(this.pkg.relByType(this.main, 'sharedStrings'));
    for (const si of kids(tree, 'si')) {
      // ふりがな（rPh）は表示しない
      const t = kid(si, 't');
      this.strings.push(t ? textOf(t) : kids(si, 'r').map((r) => textOf(kid(r, 't'))).join(''));
    }
  }

  loadStyles() {
    const tree = this.pkg.xml(this.pkg.relByType(this.main, 'styles'));
    this.numFmts = new Map();
    for (const f of kids(kid(tree, 'numFmts'), 'numFmt')) this.numFmts.set(Number(attr(f, 'numFmtId')), attr(f, 'formatCode'));
    this.fonts = kids(kid(tree, 'fonts'), 'font').map((f) => ({
      name: attr(kid(f, 'name'), 'val'),
      sz: Number(attr(kid(f, 'sz'), 'val')) || 11,
      bold: !!kid(f, 'b') && attr(kid(f, 'b'), 'val') !== '0',
      italic: !!kid(f, 'i') && attr(kid(f, 'i'), 'val') !== '0',
      underline: !!kid(f, 'u') && attr(kid(f, 'u'), 'val') !== 'none',
      strike: !!kid(f, 'strike') && attr(kid(f, 'strike'), 'val') !== '0',
      color: this.color(kid(f, 'color')),
    }));
    this.fills = kids(kid(tree, 'fills'), 'fill').map((f) => {
      const p = kid(f, 'patternFill');
      if (p && attr(p, 'patternType') && attr(p, 'patternType') !== 'none') return this.color(kid(p, 'fgColor')) || this.color(kid(p, 'bgColor'));
      const g = kid(f, 'gradientFill');
      if (g) return this.color(kid(kid(g, 'stop'), 'color'));
      return null;
    });
    this.borders = kids(kid(tree, 'borders'), 'border').map((b) => {
      const o = {};
      for (const side of ['left', 'right', 'top', 'bottom']) {
        const s = kid(b, side) || kid(b, { left: 'start', right: 'end' }[side] || side);
        const style = attr(s, 'style');
        if (style && style !== 'none') o[side] = borderCss(style, this.color(kid(s, 'color')) || '#000');
      }
      return o;
    });
    this.xfs = kids(kid(tree, 'cellXfs'), 'xf').map((x) => {
      const al = kid(x, 'alignment');
      return {
        numFmtId: Number(attr(x, 'numFmtId') || 0),
        font: this.fonts[Number(attr(x, 'fontId') || 0)] || this.fonts[0],
        fill: this.fills[Number(attr(x, 'fillId') || 0)] || null,
        border: this.borders[Number(attr(x, 'borderId') || 0)] || {},
        h: attr(al, 'horizontal'),
        v: attr(al, 'vertical'),
        wrap: attr(al, 'wrapText') === '1' || attr(al, 'wrapText') === 'true',
        indent: Number(attr(al, 'indent') || 0),
      };
    });
    this.defaultFont = this.fonts[0] || { name: 'Calibri', sz: 11 };
  }

  formatCode(id) { return this.numFmts.get(id) ?? BUILTIN_FORMATS[id] ?? 'General'; }

  // ---- シート ----

  pickSheet() {
    const sheets = kids(kid(this.wb, 'sheets'), 'sheet').map((s) => ({ name: attr(s, 'name'), state: attr(s, 'state') || 'visible', rid: attr(s, 'r:id') }));
    if (!sheets.length) return null;
    const want = this.opts.sheet;
    if (typeof want === 'string') { const s = sheets.find((x) => x.name === want); if (s) return s; }
    if (Number.isInteger(want) && sheets[want]) return sheets[want];
    // Excel が開いたときに出るシート（activeTab）。隠しシートなら最初の見えるシート
    const active = Number(attr(xpath(this.wb, 'bookViews', 'workbookView'), 'activeTab') || 0);
    if (sheets[active] && sheets[active].state === 'visible') return sheets[active];
    return sheets.find((s) => s.state === 'visible') || sheets[0];
  }

  render() {
    const vw = this.opts.viewport?.width ?? 1024;
    const vh = this.opts.viewport?.height ?? 640;
    const sheet = this.pickSheet();
    const part = sheet && this.pkg.relTarget(this.main, sheet.rid);
    const ws = part && this.sheetXml(part);
    if (!ws) return { html: `<div class="page" style="width:${vw}px;height:${vh}px;background:#fff"></div>`, width: vw, height: vh, sheet: sheet?.name };

    const df = this.defaultFont;
    // 既定のフォントの数字 1 文字の幅（px）。列幅の単位
    const cjk = /游|Yu |ＭＳ|MS |Meiryo|メイリオ|Hiragino|ヒラギノ|Noto Sans CJK/i.test(df.name || '');
    const mdw = Math.max(5, Math.round((cjk ? 8 : 7) * df.sz / 11));
    const fmtPr = kid(ws, 'sheetFormatPr');
    const defaultRowPx = (Number(attr(fmtPr, 'defaultRowHeight')) || (cjk ? 18.75 : 15)) * 96 / 72;
    const defaultColPx = attr(fmtPr, 'defaultColWidth') != null
      ? Math.round(Number(attr(fmtPr, 'defaultColWidth')) * mdw)
      : Math.ceil(((Number(attr(fmtPr, 'baseColWidth')) || 8) * mdw + 5) / 8) * 8;
    const colDefs = new Map();
    for (const c of kids(kid(ws, 'cols'), 'col')) {
      const min = Number(attr(c, 'min')), max = Math.min(Number(attr(c, 'max')), 16384);
      const hidden = attr(c, 'hidden') === '1' || attr(c, 'hidden') === 'true';
      const w = attr(c, 'width') != null ? Math.round(Number(attr(c, 'width')) * mdw) : defaultColPx;
      for (let i = min; i <= max && i <= min + 400; i++) colDefs.set(i, { width: hidden ? 0 : w, style: attr(c, 's') });
    }
    const colWidth = (i) => (colDefs.has(i) ? colDefs.get(i).width : defaultColPx);

    const view = xpath(ws, 'sheetViews', 'sheetView');
    const showGrid = attr(view, 'showGridLines') !== '0' && attr(view, 'showGridLines') !== 'false';
    const showHeaders = this.opts.headers !== false && attr(view, 'showRowColHeaders') !== '0';
    const pane = kid(view, 'pane');
    // 固定枠の外側でスクロールされていても、見えるのは左上から（ファイルを開いた直後に近づける）
    const topLeft = parseRef(attr(view, 'topLeftCell')) || { col: 1, row: 1 };
    if (pane && attr(pane, 'state') === 'frozen') { topLeft.col = 1; topLeft.row = 1; }

    // 行
    const rows = new Map();
    for (const r of kids(kid(ws, 'sheetData'), 'row')) {
      const rn = Number(attr(r, 'r'));
      const cells = new Map();
      let next = 1;
      for (const c of kids(r, 'c')) {
        const ref = parseRef(attr(c, 'r'));
        const col = ref ? ref.col : next;
        next = col + 1;
        cells.set(col, c);
      }
      rows.set(rn, {
        height: attr(r, 'hidden') === '1' ? 0 : (attr(r, 'ht') != null ? Number(attr(r, 'ht')) * 96 / 72 : defaultRowPx),
        style: attr(r, 's') != null && attr(r, 'customFormat') === '1' ? Number(attr(r, 's')) : null,
        cells,
      });
    }
    const rowHeight = (i) => (rows.has(i) ? rows.get(i).height : defaultRowPx);

    const headerW = showHeaders ? Math.max(32, 9 * String(topLeft.row + 60).length + 12) : 0;
    const headerH = showHeaders ? 20 : 0;
    const cols = [];
    for (let i = topLeft.col, x = headerW; x < vw && cols.length < 200 && i <= 16384; i++) { const w = colWidth(i); if (w > 0) { cols.push(i); x += w; } }
    const rowList = [];
    for (let i = topLeft.row, y = headerH; y < vh && rowList.length < 500 && i <= 1048576; i++) { const h = rowHeight(i); if (h > 0) { rowList.push(i); y += h; } }
    const colSet = new Set(cols);
    const rowSet = new Set(rowList);

    // 結合セル
    const merges = new Map(); // "r,c" → { rowspan, colspan } / 'covered'
    for (const mc of kids(kid(ws, 'mergeCells'), 'mergeCell')) {
      const [a, b] = String(attr(mc, 'ref') || '').split(':').map(parseRef);
      if (!a || !b) continue;
      const colspan = cols.filter((c) => c >= a.col && c <= b.col).length;
      const rowspan = rowList.filter((r) => r >= a.row && r <= b.row).length;
      if (!colspan || !rowspan) continue;
      // 結合の左上が画面の外なら、見えている範囲の左上を代表にする
      const r0 = rowList.find((r) => r >= a.row && r <= b.row);
      const c0 = cols.find((c) => c >= a.col && c <= b.col);
      for (let r = a.row; r <= b.row; r++) {
        if (!rowSet.has(r)) continue;
        for (let c = a.col; c <= b.col; c++) if (colSet.has(c)) merges.set(`${r},${c}`, 'covered');
      }
      merges.set(`${r0},${c0}`, { rowspan, colspan, src: `${a.row},${a.col}` });
    }

    const valueOf = (rn, cn) => {
      const c = rows.get(rn)?.cells.get(cn);
      return c ? this.cellValue(c) : null;
    };
    const grid = showGrid ? '1px solid #E1E1E1' : 'none';
    let html = `<table style="border-collapse:collapse;table-layout:fixed;width:${(headerW + cols.reduce((a, c) => a + colWidth(c), 0)).toFixed(0)}px">`;
    html += `<colgroup>${showHeaders ? `<col style="width:${headerW}px">` : ''}${cols.map((c) => `<col style="width:${colWidth(c)}px">`).join('')}</colgroup>`;
    const hdrCss = 'background:#F2F2F2;color:#444;border:1px solid #D0D0D0;font:11px/1 sans-serif;text-align:center;overflow:hidden';
    if (showHeaders) {
      html += `<tr style="height:${headerH}px"><td style="${hdrCss}"></td>${cols.map((c) => `<td style="${hdrCss}">${colName(c)}</td>`).join('')}</tr>`;
    }
    for (const rn of rowList) {
      const row = rows.get(rn);
      html += `<tr style="height:${rowHeight(rn).toFixed(1)}px">`;
      if (showHeaders) html += `<td style="${hdrCss}">${rn}</td>`;
      for (let k = 0; k < cols.length; k++) {
        const cn = cols[k];
        const merge = merges.get(`${rn},${cn}`);
        if (merge === 'covered') continue;
        let src = row?.cells.get(cn);
        if (merge?.src) { const [r, c] = merge.src.split(',').map(Number); src = rows.get(r)?.cells.get(c); }
        const styleIdx = src ? Number(attr(src, 's') || 0) : (row?.style ?? (colDefs.get(cn)?.style != null ? Number(colDefs.get(cn).style) : 0));
        const xf = this.xfs[styleIdx] || this.xfs[0] || { font: df, border: {} };
        const val = src ? this.cellValue(src, xf) : null;
        const css = [`border:${grid}`];
        for (const side of ['left', 'right', 'top', 'bottom']) if (xf.border?.[side]) css.push(`border-${side}:${xf.border[side]}`);
        if (xf.fill) css.push(`background:${xf.fill}`);
        const f = xf.font || df;
        css.push(`font-family:${fontStack(f.name)}`, `font-size:${f.sz}pt`);
        if (f.bold) css.push('font-weight:bold');
        if (f.italic) css.push('font-style:italic');
        const deco = [f.underline && 'underline', f.strike && 'line-through'].filter(Boolean);
        if (deco.length) css.push(`text-decoration:${deco.join(' ')}`);
        const color = val?.color || f.color;
        if (color) css.push(`color:${color}`);
        const h = xf.h && xf.h !== 'general' ? xf.h : (val?.kind === 'number' ? 'right' : (val?.kind === 'bool' || val?.kind === 'error' ? 'center' : 'left'));
        const align = { centerContinuous: 'center', distributed: 'justify', fill: 'left' }[h] || h;
        css.push(`text-align:${align}`, `vertical-align:${{ top: 'top', center: 'middle' }[xf.v] || 'bottom'}`, 'padding:0 2px', 'line-height:1.15');
        if (xf.indent) css.push(`padding-left:${xf.indent * mdw + 2}px`);
        const span = merge ? `${merge.colspan > 1 ? ` colspan="${merge.colspan}"` : ''}${merge.rowspan > 1 ? ` rowspan="${merge.rowspan}"` : ''}` : '';
        let inner = '';
        if (val && val.text !== '') {
          // 文字列は右隣が空なら隣へはみ出す（Excel と同じ）。数値ははみ出さない
          const spill = !xf.wrap && !merge && val.kind === 'text' && align === 'left' && cols[k + 1] && valueOf(rn, cols[k + 1]) == null;
          const ws2 = xf.wrap ? 'white-space:pre-wrap;word-break:break-all' : 'white-space:pre';
          inner = `<div style="${ws2};overflow:${spill ? 'visible' : 'hidden'};max-height:${(rowHeight(rn) * (merge?.rowspan || 1)).toFixed(0)}px">${escapeHtml(val.text)}</div>`;
        }
        html += `<td${span} style="${css.join(';')}">${inner}</td>`;
      }
      html += '</tr>';
    }
    html += '</table>';

    const images = this.drawings(part, ws, { colWidth, rowHeight, topLeft, headerW, headerH });
    return {
      html: `<div class="page" style="position:relative;width:${vw}px;height:${vh}px;overflow:hidden;background:#fff">${html}${images}</div>`,
      width: vw,
      height: vh,
      sheet: sheet.name,
    };
  }

  // 大きなシートは、見える範囲より十分多い行（2000 行）で切ってから読む（数十万行を木にしない）
  sheetXml(part) {
    const text = this.pkg.zip.readText(part);
    if (text == null) return null;
    if (text.length < 4 * 1024 * 1024) return this.pkg.xml(part);
    const endMatch = /<\/(?:\w+:)?sheetData>/.exec(text);
    if (!endMatch) return this.pkg.xml(part);
    const rowEnd = /<\/(?:\w+:)?row>/g;
    let cut = -1;
    for (let k = 0; k < 2000; k++) {
      const m = rowEnd.exec(text);
      if (!m || m.index > endMatch.index) return this.pkg.xml(part);
      cut = rowEnd.lastIndex;
    }
    return parseXml(text.slice(0, cut) + endMatch[0] + text.slice(endMatch.index + endMatch[0].length));
  }

  cellValue(c, xf) {
    const t = attr(c, 't') || 'n';
    const v = kid(c, 'v');
    if (t === 'inlineStr') {
      const is = kid(c, 'is');
      return { kind: 'text', text: kid(is, 't') ? textOf(kid(is, 't')) : kids(is, 'r').map((r) => textOf(kid(r, 't'))).join('') };
    }
    if (!v) return null;
    const raw = textOf(v);
    if (t === 's') return { kind: 'text', text: this.strings[Number(raw)] ?? '' };
    if (t === 'str') return { kind: 'text', text: raw };
    if (t === 'b') return { kind: 'bool', text: raw === '1' ? 'TRUE' : 'FALSE' };
    if (t === 'e') return { kind: 'error', text: raw };
    if (raw === '') return null;
    let n = Number(raw);
    if (t === 'd') {
      const d = Date.parse(raw);
      n = Number.isFinite(d) ? (d - Date.UTC(1899, 11, 30)) / 86400000 : NaN;
    }
    if (!Number.isFinite(n)) return { kind: 'text', text: raw };
    if (!xf) return { kind: 'number', text: raw };
    const code = this.formatCode(xf.numFmtId);
    if (code === '@') return { kind: 'text', text: raw };
    const { text, color } = formatValue(n, code, this.date1904);
    return { kind: 'number', text, color };
  }

  // シートに貼られた画像（xdr:pic）。セル基準の位置を px に直して重ねる
  drawings(sheetPart, ws, g) {
    const out = [];
    for (const d of kids(ws, 'drawing')) {
      const part = this.pkg.relTarget(sheetPart, attr(d, 'r:id'));
      const tree = part && this.pkg.xml(part);
      if (!tree) continue;
      const colLeft = (col0) => { let x = g.headerW; for (let i = g.topLeft.col; i < col0 + 1; i++) x += g.colWidth(i); return x; };
      const rowTop = (row0) => { let y = g.headerH; for (let i = g.topLeft.row; i < row0 + 1; i++) y += g.rowHeight(i); return y; };
      for (const a of kids(tree)) {
        if (!/Anchor$/.test(a.local)) continue;
        const blip = findAll(a, 'blip')[0];
        if (!blip || !findAll(a, 'pic').length) continue;
        const src = this.pkg.blipDataUri(part, blip);
        if (!src) continue;
        const pt = (node) => ({
          x: colLeft(Number(textOf(kid(node, 'col')))) + emuToPx(textOf(kid(node, 'colOff'))),
          y: rowTop(Number(textOf(kid(node, 'row')))) + emuToPx(textOf(kid(node, 'rowOff'))),
        });
        let x, y, w, h;
        if (a.local === 'absoluteAnchor') {
          x = g.headerW + emuToPx(attr(kid(a, 'pos'), 'x')); y = g.headerH + emuToPx(attr(kid(a, 'pos'), 'y'));
          w = emuToPx(attr(kid(a, 'ext'), 'cx')); h = emuToPx(attr(kid(a, 'ext'), 'cy'));
        } else {
          ({ x, y } = pt(kid(a, 'from')));
          if (a.local === 'twoCellAnchor') { const to = pt(kid(a, 'to')); w = to.x - x; h = to.y - y; } else { w = emuToPx(attr(kid(a, 'ext'), 'cx')); h = emuToPx(attr(kid(a, 'ext'), 'cy')); }
        }
        if (!(w > 0 && h > 0)) continue;
        out.push(`<img src="${src}" style="position:absolute;left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px">`);
      }
    }
    return out.join('');
  }
}

function borderCss(style, color) {
  const map = {
    thin: '1px solid', hair: '1px dotted', dotted: '1px dotted', dashed: '1px dashed', dashDot: '1px dashed', dashDotDot: '1px dashed',
    medium: '2px solid', mediumDashed: '2px dashed', mediumDashDot: '2px dashed', mediumDashDotDot: '2px dashed', slantDashDot: '2px dashed',
    thick: '3px solid', double: '3px double',
  };
  return `${map[style] || '1px solid'} ${color}`;
}

function xlsxToHtml(pkg, opts) {
  return new XlsxRenderer(pkg, opts).render();
}

module.exports = { xlsxToHtml, formatValue, colName, colIndex };
