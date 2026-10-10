'use strict';

// docx の 1 ページ目を HTML にする。
// 段落・文字の書式（スタイルの継承を含む）、箇条書きと段落番号、表、画像、テキストボックス、
// ヘッダーとフッターを描く。改ページ（明示の改ページ・次ページからのセクション区切り）で打ち切る。
// 自動の改ページは計算しない。1 ページの高さで切り取るので、溢れた分は描かれない。

const { kid, kids, attr, path: xpath, find, textOf, isEl } = require('./xml');
const { escapeHtml, fontStack, twipToPx, emuToPx, hexToRgb, safeHex } = require('./ooxml');

const MAX_BLOCKS = 600; // 1 ページに収まる量を十分に超える段落数で打ち切る

const HIGHLIGHT = {
  yellow: '#FFFF00', green: '#00FF00', cyan: '#00FFFF', magenta: '#FF00FF', blue: '#0000FF', red: '#FF0000',
  darkBlue: '#00008B', darkCyan: '#008B8B', darkGreen: '#006400', darkMagenta: '#8B008B', darkRed: '#8B0000',
  darkYellow: '#808000', darkGray: '#A9A9A9', lightGray: '#D3D3D3', black: '#000000', white: '#FFFFFF',
};

const THEME_COLOR_NAME = { text1: 'dk1', background1: 'lt1', text2: 'dk2', background2: 'lt2', dark1: 'dk1', light1: 'lt1', dark2: 'dk2', light2: 'lt2', hyperlink: 'hlink', followedHyperlink: 'folHlink' };

// Symbol / Wingdings の私用領域の記号を、どの PC にもある記号に置き換える
const BULLET_MAP = { '': '•', '': '▪', '': '➢', '': '✓', '': '❖', '': '■', '': '□', '': '◆', '': '•', o: '○' };

function on(node) {
  if (!node) return undefined;
  const v = attr(node, 'val');
  return v === undefined || !/^(0|false|off|none)$/i.test(v);
}

function num(v, fallback = undefined) {
  const n = Number(v);
  return v == null || v === '' || !Number.isFinite(n) ? fallback : n;
}

function assignDefined(target, src) {
  if (!src) return target;
  for (const [k, v] of Object.entries(src)) if (v !== undefined) target[k] = v;
  return target;
}

// ---- 書式を読む --------------------------------------------------------------

function readRPr(rPr) {
  if (!rPr) return null;
  const o = {};
  const b = kid(rPr, 'b'); if (b) o.bold = on(b);
  const i = kid(rPr, 'i'); if (i) o.italic = on(i);
  const u = kid(rPr, 'u'); if (u) o.underline = on(u) && attr(u, 'val') !== 'none';
  const s = kid(rPr, 'strike'); if (s) o.strike = on(s);
  const ds = kid(rPr, 'dstrike'); if (ds && on(ds)) o.strike = true;
  const caps = kid(rPr, 'caps'); if (caps) o.caps = on(caps);
  const sc = kid(rPr, 'smallCaps'); if (sc) o.smallCaps = on(sc);
  const v = kid(rPr, 'vanish'); if (v) o.hidden = on(v);
  const sz = kid(rPr, 'sz'); if (sz) o.sz = num(attr(sz, 'val'));
  const color = kid(rPr, 'color');
  if (color) o.color = { val: attr(color, 'val'), theme: attr(color, 'themeColor'), tint: attr(color, 'themeTint'), shade: attr(color, 'themeShade') };
  const hl = kid(rPr, 'highlight'); if (hl) o.highlight = attr(hl, 'val');
  const shd = kid(rPr, 'shd'); if (shd) o.shd = attr(shd, 'fill');
  const va = kid(rPr, 'vertAlign'); if (va) o.vertAlign = attr(va, 'val');
  const sp = kid(rPr, 'spacing'); if (sp) o.spacing = num(attr(sp, 'val'));
  const f = kid(rPr, 'rFonts');
  if (f) {
    for (const k of ['ascii', 'hAnsi', 'eastAsia', 'asciiTheme', 'hAnsiTheme', 'eastAsiaTheme']) {
      const val = attr(f, k);
      if (val) o[`font_${k}`] = val;
    }
  }
  return o;
}

function readPPr(pPr) {
  if (!pPr) return null;
  const o = {};
  const st = kid(pPr, 'pStyle'); if (st) o.style = attr(st, 'val');
  const jc = kid(pPr, 'jc'); if (jc) o.jc = attr(jc, 'val');
  const sp = kid(pPr, 'spacing');
  if (sp) {
    o.before = num(attr(sp, 'before'), o.before);
    o.after = num(attr(sp, 'after'), o.after);
    if (/^(1|true|on)$/i.test(attr(sp, 'beforeAutospacing') || '')) o.before = 280;
    if (/^(1|true|on)$/i.test(attr(sp, 'afterAutospacing') || '')) o.after = 280;
    if (attr(sp, 'line') != null) { o.line = num(attr(sp, 'line')); o.lineRule = attr(sp, 'lineRule') || 'auto'; }
  }
  const ind = kid(pPr, 'ind');
  if (ind) {
    o.indLeft = num(attr(ind, 'left') ?? attr(ind, 'start'), o.indLeft);
    o.indRight = num(attr(ind, 'right') ?? attr(ind, 'end'), o.indRight);
    if (attr(ind, 'firstLine') != null) { o.firstLine = num(attr(ind, 'firstLine')); o.hanging = 0; }
    if (attr(ind, 'hanging') != null) { o.hanging = num(attr(ind, 'hanging')); o.firstLine = 0; }
  }
  const numPr = kid(pPr, 'numPr');
  if (numPr) {
    const id = attr(kid(numPr, 'numId'), 'val');
    if (id != null) o.numId = id;
    const lvl = attr(kid(numPr, 'ilvl'), 'val');
    if (lvl != null) o.ilvl = num(lvl, 0);
  }
  const shd = kid(pPr, 'shd'); if (shd) o.shd = attr(shd, 'fill');
  const pb = kid(pPr, 'pageBreakBefore'); if (pb) o.pageBreakBefore = on(pb);
  const bdr = kid(pPr, 'pBdr');
  if (bdr) o.borders = { top: kid(bdr, 'top'), bottom: kid(bdr, 'bottom'), left: kid(bdr, 'left'), right: kid(bdr, 'right') };
  const cs = kid(pPr, 'contextualSpacing'); if (cs) o.contextualSpacing = on(cs);
  const sg = kid(pPr, 'snapToGrid'); if (sg) o.snapToGrid = on(sg);
  return o;
}

// ---- 本体 -------------------------------------------------------------------

class DocxRenderer {
  constructor(pkg) {
    this.pkg = pkg;
    this.main = pkg.mainPart();
    this.doc = pkg.xml(this.main);
    this.theme = pkg.theme(this.main);
    this.loadStyles();
    this.loadNumbering();
    this.counters = new Map();
    this.blocks = 0;
    this.stopped = false;
    this.floats = [];
  }

  loadStyles() {
    this.styles = new Map();
    this.defaultParaStyle = null;
    this.defaultTableStyle = null;
    this.docDefaults = { pPr: {}, rPr: {} };
    const tree = this.pkg.xml(this.pkg.relByType(this.main, 'styles'));
    if (!tree) return;
    const dd = kid(tree, 'docDefaults');
    this.docDefaults.rPr = readRPr(xpath(dd, 'rPrDefault', 'rPr')) || {};
    this.docDefaults.pPr = readPPr(xpath(dd, 'pPrDefault', 'pPr')) || {};
    for (const s of kids(tree, 'style')) {
      const id = attr(s, 'styleId');
      const type = attr(s, 'type');
      const entry = {
        type,
        basedOn: attr(kid(s, 'basedOn'), 'val'),
        pPr: readPPr(kid(s, 'pPr')),
        rPr: readRPr(kid(s, 'rPr')),
        tblBorders: xpath(s, 'tblPr', 'tblBorders'),
        tcFill: attr(xpath(s, 'tcPr', 'shd'), 'fill'),
      };
      this.styles.set(id, entry);
      if (attr(s, 'default') === '1' || attr(s, 'default') === 'true') {
        if (type === 'paragraph') this.defaultParaStyle = id;
        if (type === 'table') this.defaultTableStyle = id;
      }
    }
  }

  // basedOn をたどって、根から順に並べる
  styleChain(id) {
    const chain = [];
    const seen = new Set();
    while (id && this.styles.has(id) && !seen.has(id)) {
      seen.add(id);
      const s = this.styles.get(id);
      chain.unshift(s);
      id = s.basedOn;
    }
    return chain;
  }

  loadNumbering() {
    this.nums = new Map();
    const tree = this.pkg.xml(this.pkg.relByType(this.main, 'numbering'));
    if (!tree) return;
    const abstracts = new Map();
    for (const a of kids(tree, 'abstractNum')) {
      const levels = new Map();
      for (const l of kids(a, 'lvl')) {
        levels.set(num(attr(l, 'ilvl'), 0), {
          start: num(attr(kid(l, 'start'), 'val'), 1),
          fmt: attr(kid(l, 'numFmt'), 'val') || 'decimal',
          text: attr(kid(l, 'lvlText'), 'val') ?? '',
          pPr: readPPr(kid(l, 'pPr')),
          rPr: readRPr(kid(l, 'rPr')),
        });
      }
      abstracts.set(attr(a, 'abstractNumId'), levels);
    }
    for (const n of kids(tree, 'num')) {
      const levels = abstracts.get(attr(kid(n, 'abstractNumId'), 'val'));
      if (!levels) continue;
      const starts = new Map();
      for (const o of kids(n, 'lvlOverride')) {
        const so = attr(kid(o, 'startOverride'), 'val');
        if (so != null) starts.set(num(attr(o, 'ilvl'), 0), num(so, 1));
      }
      this.nums.set(attr(n, 'numId'), { levels, starts });
    }
  }

  paraProps(pPr, ctx = {}) {
    const direct = readPPr(pPr) || {};
    const styleId = direct.style || this.defaultParaStyle;
    const out = assignDefined({}, this.docDefaults.pPr);
    const rPr = assignDefined({}, this.docDefaults.rPr);
    // 表の中の段落は、表のスタイルの段落書式が文書の既定より優先される
    if (ctx.tableStyle) { assignDefined(out, ctx.tableStyle.pPr); assignDefined(rPr, ctx.tableStyle.rPr); }
    for (const s of this.styleChain(styleId)) { assignDefined(out, s.pPr); assignDefined(rPr, s.rPr); }
    const numId = direct.numId ?? out.numId;
    const ilvl = direct.ilvl ?? out.ilvl ?? 0;
    const lvl = numId && numId !== '0' ? this.nums.get(numId)?.levels.get(ilvl) : null;
    if (lvl) assignDefined(out, lvl.pPr);
    assignDefined(out, direct);
    out.styleId = styleId;
    return { p: out, rPr, list: lvl ? { numId, ilvl, lvl } : null };
  }

  runProps(baseRPr, rPr) {
    const direct = readRPr(rPr) || {};
    const out = assignDefined({}, baseRPr);
    if (rPr) {
      const rs = attr(kid(rPr, 'rStyle'), 'val');
      for (const s of this.styleChain(rs)) assignDefined(out, s.rPr);
    }
    return assignDefined(out, direct);
  }

  colorOf(c) {
    if (!c) return null;
    if (c.theme) {
      let hex = this.theme.colors[THEME_COLOR_NAME[c.theme] || c.theme];
      if (hex) {
        let rgb = hexToRgb(hex);
        if (c.tint) { const t = parseInt(c.tint, 16) / 255; rgb = rgb.map((x) => x * t + 255 * (1 - t)); }
        if (c.shade) { const s = parseInt(c.shade, 16) / 255; rgb = rgb.map((x) => x * s); }
        return `rgb(${rgb.map(Math.round).join(',')})`;
      }
    }
    return safeHex(c.val);
  }

  fontName(name, themeRef) {
    if (themeRef) {
      const t = this.theme.fonts[/^major/.test(themeRef) ? 'major' : 'minor'];
      return /EastAsia$/.test(themeRef) ? (t.ea || t.latin) : t.latin;
    }
    return name || '';
  }

  runCss(r) {
    const css = [];
    const latin = this.fontName(r.font_ascii, r.font_asciiTheme) || this.fontName(r.font_hAnsi, r.font_hAnsiTheme);
    const ea = this.fontName(r.font_eastAsia, r.font_eastAsiaTheme);
    css.push(`font-family:${fontStack(latin, ea)}`);
    let size = (r.sz ?? 20) / 2;
    if (r.vertAlign === 'superscript' || r.vertAlign === 'subscript') {
      css.push(`vertical-align:${r.vertAlign === 'superscript' ? 'super' : 'sub'}`);
      size *= 0.65;
    }
    css.push(`font-size:${size}pt`);
    if (r.bold) css.push('font-weight:bold');
    if (r.italic) css.push('font-style:italic');
    const deco = [r.underline && 'underline', r.strike && 'line-through'].filter(Boolean);
    if (deco.length) css.push(`text-decoration:${deco.join(' ')}`);
    if (r.caps) css.push('text-transform:uppercase');
    if (r.smallCaps) css.push('font-variant:small-caps');
    const color = this.colorOf(r.color);
    if (color) css.push(`color:${color}`);
    const bg = r.highlight && r.highlight !== 'none' ? HIGHLIGHT[r.highlight] : safeHex(r.shd);
    if (bg) css.push(`background:${bg}`);
    if (r.spacing) css.push(`letter-spacing:${twipToPx(r.spacing).toFixed(2)}px`);
    return css.join(';');
  }

  // ---- 段落番号 ----

  listMarker(list, rPr, hanging) {
    const { numId, ilvl, lvl } = list;
    const def = this.nums.get(numId);
    let counts = this.counters.get(numId);
    if (!counts) { counts = []; this.counters.set(numId, counts); }
    for (let k = 0; k <= ilvl; k++) {
      if (counts[k] == null) counts[k] = (def.starts.get(k) ?? def.levels.get(k)?.start ?? 1) - (k === ilvl ? 1 : 0);
    }
    counts[ilvl] += 1;
    counts.length = ilvl + 1; // 下の階層は数え直す
    let text;
    if (lvl.fmt === 'bullet') text = [...lvl.text].map((ch) => BULLET_MAP[ch] || ch).join('') || '•';
    else if (lvl.fmt === 'none') text = lvl.text.replace(/%\d/g, '');
    else {
      text = lvl.text.replace(/%(\d)/g, (_, d) => {
        const k = Number(d) - 1;
        const level = def.levels.get(k);
        return formatNumber(counts[k] ?? level?.start ?? 1, k === ilvl ? lvl.fmt : (level?.fmt || 'decimal'));
      });
    }
    const markerRPr = assignDefined(assignDefined({}, rPr), lvl.rPr);
    delete markerRPr.font_ascii; delete markerRPr.font_hAnsi; // 記号用フォント（Symbol など）は使わない
    // 記号のあとはぶら下げの位置まで空ける（Word では次のタブ位置）
    const pad = hanging > 0 ? `display:inline-block;min-width:${hanging.toFixed(1)}px;text-indent:0;` : 'padding-right:0.5em;';
    return `<span style="${pad}${this.runCss(markerRPr)}">${escapeHtml(text)}</span>`;
  }

  // ---- 段落 ----

  paragraph(p, ctx = {}) {
    const { p: pp, rPr: paraRPr, list } = this.paraProps(kid(p, 'pPr'), ctx);
    // 同じスタイルの段落が続くときは、間隔を空けない（「段落の間隔を追加しない」）
    if (pp.contextualSpacing) {
      if (ctx.prevStyle === pp.styleId) pp.before = 0;
      if (ctx.nextStyle === pp.styleId) pp.after = 0;
    }
    if (ctx.body && pp.pageBreakBefore && this.blocks > 1) { this.stopped = true; return ''; }
    const markRPr = this.runProps(paraRPr, xpath(p, 'pPr', 'rPr'));
    const css = [];
    const jc = { center: 'center', right: 'right', end: 'right', both: 'justify', distribute: 'justify', left: 'left', start: 'left' }[pp.jc];
    if (jc) css.push(`text-align:${jc}`);
    if (pp.jc === 'distribute') css.push('text-align-last:justify');
    if (pp.before) css.push(`padding-top:${twipToPx(pp.before).toFixed(1)}px`);
    if (pp.after) css.push(`padding-bottom:${twipToPx(pp.after).toFixed(1)}px`);
    if (pp.indLeft) css.push(`margin-left:${twipToPx(pp.indLeft).toFixed(1)}px`);
    if (pp.indRight) css.push(`margin-right:${twipToPx(pp.indRight).toFixed(1)}px`);
    if (pp.firstLine) css.push(`text-indent:${twipToPx(pp.firstLine).toFixed(1)}px`);
    if (pp.hanging) css.push(`text-indent:${(-twipToPx(pp.hanging)).toFixed(1)}px`);
    // 行の高さは、その段落でいちばん大きい文字で決まる
    let maxSz = markRPr.sz ?? 20;
    for (const r of kids(p, 'r')) maxSz = Math.max(maxSz, this.runProps(paraRPr, kid(r, 'rPr')).sz ?? 20);
    const fontPx = (maxSz / 2) * (96 / 72);
    if (pp.line && pp.lineRule !== 'auto') css.push(`line-height:${twipToPx(pp.line).toFixed(1)}px`);
    else if (this.grid && pp.snapToGrid !== false) {
      // 行グリッド（日本語版 Word の既定）。1 行は行送りの整数倍になる
      const pitch = this.grid;
      const lines = Math.max(1, Math.ceil((fontPx * 1.2) / pitch - 0.05));
      css.push(`line-height:${(pitch * lines * ((pp.line || 240) / 240)).toFixed(1)}px`);
    } else css.push(`line-height:${(((pp.line || 240) / 240) * 1.2).toFixed(3)}`);
    if (safeHex(pp.shd)) css.push(`background:${safeHex(pp.shd)}`);
    for (const [side, b] of Object.entries(pp.borders || {})) {
      const v = this.borderCss(b);
      if (!v || v === 'none') continue;
      css.push(`border-${side}:${v}`);
      const space = num(attr(b, 'space'), 0) * (96 / 72);
      if (space) css.push(`${side === 'top' || side === 'bottom' ? `margin-${side}` : `padding-${side}`}:${space.toFixed(1)}px`);
    }
    // 空の段落でも段落記号の大きさの高さを持たせる
    css.push(`font-size:${(markRPr.sz ?? 20) / 2}pt`);

    let inner = list ? this.listMarker(list, markRPr, twipToPx(pp.hanging || 0)) : '';
    const state = { field: 0, inResult: [], breakAfter: false };
    inner += this.inline(p, paraRPr, state, ctx);
    if (!inner.replace(/<[^>]*>/g, '').trim() && !/<img|class="(?:box|tab)"/.test(inner)) inner += '​';
    const sectPr = xpath(p, 'pPr', 'sectPr');
    if (ctx.body && sectPr) {
      const type = attr(kid(sectPr, 'type'), 'val') || 'nextPage';
      if (type !== 'continuous') this.stopped = true;
    }
    if (state.breakAfter && ctx.body) this.stopped = true;
    return `<p style="${css.join(';')}">${inner}</p>`;
  }

  // 段落の中身（文字列・ハイパーリンク・フィールド・図）
  inline(node, paraRPr, state, ctx) {
    let out = '';
    for (const c of node.children) {
      if (!isEl(c)) continue;
      if (state.breakAfter) break; // 改ページより後ろは次のページ
      switch (c.local) {
        case 'r': out += this.run(c, paraRPr, state, ctx); break;
        case 'hyperlink': case 'smartTag': case 'ins': case 'fldSimple': case 'customXml': case 'bdo': case 'dir':
          out += this.inline(c, paraRPr, state, ctx); break;
        case 'sdt': out += this.inline(kid(c, 'sdtContent') || c, paraRPr, state, ctx); break;
        case 'oMath': case 'oMathPara': out += `<span>${escapeHtml(textOf(c))}</span>`; break;
        case 'AlternateContent': out += this.inline(kid(c, 'Choice') || kid(c, 'Fallback') || c, paraRPr, state, ctx); break;
        default: break; // del・moveFrom・bookmark・proofErr などは描かない
      }
    }
    return out;
  }

  run(r, paraRPr, state, ctx) {
    const props = this.runProps(paraRPr, kid(r, 'rPr'));
    if (props.hidden) return '';
    const style = this.runCss(props);
    let text = '';
    const flush = () => { const t = text; text = ''; return t ? `<span style="${style}">${escapeHtml(t)}</span>` : ''; };
    let out = '';
    for (const c of r.children) {
      if (!isEl(c)) continue;
      if (c.local === 'fldChar') {
        const t = attr(c, 'fldCharType');
        if (t === 'begin') state.inResult.push(false);
        else if (t === 'separate' && state.inResult.length) state.inResult[state.inResult.length - 1] = true;
        else if (t === 'end') state.inResult.pop();
        continue;
      }
      // フィールドの命令部分（begin〜separate）は描かず、結果だけを描く
      if (state.inResult.length && !state.inResult[state.inResult.length - 1]) continue;
      switch (c.local) {
        case 't': text += textOf(c); break;
        case 'delText': case 'instrText': break;
        case 'tab': out += flush() + '<span class="tab"></span>'; break;
        case 'noBreakHyphen': text += '‑'; break;
        case 'softHyphen': break;
        case 'sym': {
          const ch = String.fromCharCode(parseInt(attr(c, 'char') || '0', 16));
          text += BULLET_MAP[ch] || ch;
          break;
        }
        case 'br': case 'cr': {
          const type = attr(c, 'type');
          if (type === 'page' && ctx.body) { state.breakAfter = true; out += flush(); return out; }
          out += flush() + '<br>';
          break;
        }
        case 'drawing': out += flush() + this.drawing(c, ctx); break;
        case 'pict': case 'object': out += flush() + this.vml(c, ctx); break;
        case 'AlternateContent': {
          const choice = kid(c, 'Choice') || kid(c, 'Fallback');
          const d = find(choice, 'drawing');
          if (d) out += flush() + this.drawing(d, ctx);
          else { const pict = find(choice, 'pict'); if (pict) out += flush() + this.vml(pict, ctx); }
          break;
        }
        default: break;
      }
    }
    return out + flush();
  }

  // ---- 図 ----

  drawing(d, ctx) {
    const holder = kid(d, 'inline') || kid(d, 'anchor');
    if (!holder) return '';
    const ext = kid(holder, 'extent');
    const w = emuToPx(attr(ext, 'cx'));
    const h = emuToPx(attr(ext, 'cy'));
    const blip = find(holder, 'blip');
    const txbx = find(holder, 'txbxContent');
    let content = '';
    if (blip) {
      const src = this.pkg.blipDataUri(ctx.part || this.main, blip);
      content = src ? `<img src="${src}" style="width:${w.toFixed(1)}px;height:${h.toFixed(1)}px;display:block">` : '';
    } else if (txbx) {
      const fill = find(find(holder, 'spPr'), 'srgbClr');
      const bg = safeHex(attr(fill, 'val')) ? `background:${safeHex(attr(fill, 'val'))};` : '';
      content = `<div class="box" style="${bg}width:${w.toFixed(1)}px;min-height:${h.toFixed(1)}px;padding:4px 7px;box-sizing:border-box;text-indent:0">${this.blocksOf(txbx, { ...ctx, body: false })}</div>`;
    }
    if (!content) return `<span class="box" style="display:inline-block;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px"></span>`;
    if (holder.local === 'anchor') {
      const pos = this.anchorPosition(holder);
      if (pos && ctx.body) {
        // ページ基準で置ける浮動の図は、本文の流れから外してページの上に重ねる
        this.floats.push(`<div style="position:absolute;left:${pos.left.toFixed(1)}px;top:${pos.top.toFixed(1)}px;z-index:${attr(holder, 'behindDoc') === '1' ? 0 : 2}">${content}</div>`);
        return '';
      }
      const align = textOf(xpath(holder, 'positionH', 'align'));
      return `<span style="float:${align === 'right' ? 'right' : 'left'};margin:0 8px 4px 0">${content}</span>`;
    }
    return `<span style="display:inline-block;vertical-align:bottom;text-indent:0">${content}</span>`;
  }

  anchorPosition(anchor) {
    const ph = kid(anchor, 'positionH');
    const pv = kid(anchor, 'positionV');
    const offH = kid(ph, 'posOffset');
    const offV = kid(pv, 'posOffset');
    if (!offH || !offV) return null;
    const relH = attr(ph, 'relativeFrom');
    const relV = attr(pv, 'relativeFrom');
    const base = (rel, axis) => {
      if (rel === 'page') return 0;
      if (rel === 'margin' || rel === 'column') return axis === 'h' ? this.page.marginLeft : this.page.marginTop;
      return null;
    };
    const bx = base(relH, 'h');
    const by = base(relV, 'v');
    if (bx == null || by == null) return null;
    return { left: bx + emuToPx(textOf(offH)), top: by + emuToPx(textOf(offV)) };
  }

  // 古い形式の画像（v:imagedata）
  vml(node, ctx) {
    const img = find(node, 'imagedata');
    if (!img) return '';
    const src = this.pkg.imageDataUri(this.pkg.relTarget(ctx.part || this.main, attr(img, 'r:id')));
    if (!src) return '';
    const shape = find(node, 'shape');
    const style = attr(shape, 'style') || '';
    const dim = (k) => { const m = new RegExp(`${k}:([\\d.]+)pt`).exec(style); return m ? Number(m[1]) * (96 / 72) : null; };
    const w = dim('width'), h = dim('height');
    return `<img src="${src}" style="${w ? `width:${w.toFixed(1)}px;` : ''}${h ? `height:${h.toFixed(1)}px;` : ''}">`;
  }

  // ---- 表 ----

  borderCss(b) {
    if (!b) return null;
    const val = attr(b, 'val');
    if (!val || val === 'nil' || val === 'none') return 'none';
    const width = Math.max(1, (num(attr(b, 'sz'), 4) / 8) * (96 / 72));
    const theme = attr(b, 'themeColor');
    const color = theme ? null : attr(b, 'color');
    const themed = theme ? this.colorOf({ theme, tint: attr(b, 'themeTint'), shade: attr(b, 'themeShade') }) : null;
    const style = val === 'double' ? 'double' : (/dash/i.test(val) ? 'dashed' : (/dot/i.test(val) ? 'dotted' : 'solid'));
    return `${(style === 'double' ? Math.max(3, width) : width).toFixed(1)}px ${style} ${themed || safeHex(color) || '#000'}`;
  }

  table(tbl, ctx) {
    const tblPr = kid(tbl, 'tblPr');
    const styleId = attr(kid(tblPr, 'tblStyle'), 'val') || this.defaultTableStyle;
    let borders = null;
    let styleFill = null;
    const tableStyle = { pPr: {}, rPr: {} };
    for (const s of this.styleChain(styleId)) {
      if (s.tblBorders) borders = s.tblBorders;
      if (s.tcFill) styleFill = s.tcFill;
      assignDefined(tableStyle.pPr, s.pPr);
      assignDefined(tableStyle.rPr, s.rPr);
    }
    if (kid(tblPr, 'tblBorders')) borders = kid(tblPr, 'tblBorders');
    const tb = {};
    for (const side of ['top', 'left', 'bottom', 'right', 'insideH', 'insideV', 'start', 'end']) tb[side] = this.borderCss(kid(borders, side));
    tb.left ??= tb.start; tb.right ??= tb.end;
    const grid = kids(kid(tbl, 'tblGrid'), 'gridCol').map((g) => twipToPx(attr(g, 'w')));
    const rows = kids(tbl, 'tr');
    // 縦の結合（vMerge）を rowspan に直す
    const layout = rows.map((tr) => {
      let col = 0;
      return kids(tr, 'tc').map((tc) => {
        const tcPr = kid(tc, 'tcPr');
        const span = num(attr(kid(tcPr, 'gridSpan'), 'val'), 1);
        const vm = kid(tcPr, 'vMerge');
        const cell = { tc, tcPr, col, span, merge: vm ? (attr(vm, 'val') === 'restart' ? 'restart' : 'continue') : null, rowspan: 1 };
        col += span;
        return cell;
      });
    });
    for (let r = 0; r < layout.length; r++) {
      for (const cell of layout[r]) {
        if (cell.merge !== 'restart') continue;
        for (let k = r + 1; k < layout.length; k++) {
          const below = layout[k].find((c) => c.col === cell.col);
          if (!below || below.merge !== 'continue') break;
          cell.rowspan++;
        }
      }
    }
    const jc = attr(kid(tblPr, 'jc'), 'val');
    const ind = twipToPx(attr(kid(tblPr, 'tblInd'), 'w'));
    const margin = jc === 'center' ? 'margin:0 auto' : (jc === 'right' || jc === 'end' ? 'margin-left:auto' : (ind ? `margin-left:${ind.toFixed(1)}px` : ''));
    const cellMar = kid(tblPr, 'tblCellMar');
    const padL = twipToPx(attr(kid(cellMar, 'left') || kid(cellMar, 'start'), 'w') ?? 108);
    const padR = twipToPx(attr(kid(cellMar, 'right') || kid(cellMar, 'end'), 'w') ?? 108);
    const total = grid.reduce((a, b) => a + b, 0);
    let html = `<table style="border-collapse:collapse;table-layout:fixed;${total ? `width:${total.toFixed(1)}px;` : ''}${margin}">`;
    if (grid.length) html += `<colgroup>${grid.map((w) => `<col style="width:${w.toFixed(1)}px">`).join('')}</colgroup>`;
    const ncols = grid.length || Math.max(...layout.map((r) => r.reduce((a, c) => a + c.span, 0)), 1);
    layout.forEach((cells, r) => {
      const trPr = kid(rows[r], 'trPr');
      const hEl = kid(trPr, 'trHeight');
      const rowH = hEl ? `height:${twipToPx(attr(hEl, 'val')).toFixed(1)}px` : '';
      html += `<tr style="${rowH}">`;
      for (const cell of cells) {
        if (cell.merge === 'continue') continue;
        const css = [`padding:0 ${padR.toFixed(1)}px 0 ${padL.toFixed(1)}px`, 'vertical-align:top'];
        const sides = {
          top: r === 0 ? tb.top : tb.insideH,
          bottom: r + cell.rowspan >= rows.length ? tb.bottom : tb.insideH,
          left: cell.col === 0 ? tb.left : tb.insideV,
          right: cell.col + cell.span >= ncols ? tb.right : tb.insideV,
        };
        const tcB = kid(cell.tcPr, 'tcBorders');
        for (const side of ['top', 'bottom', 'left', 'right']) {
          const own = this.borderCss(kid(tcB, side) || kid(tcB, { left: 'start', right: 'end' }[side] || side));
          const v = own ?? sides[side];
          if (v) css.push(`border-${side}:${v}`);
        }
        const fill = attr(kid(cell.tcPr, 'shd'), 'fill') || styleFill;
        if (safeHex(fill)) css.push(`background:${safeHex(fill)}`);
        const va = attr(kid(cell.tcPr, 'vAlign'), 'val');
        if (va === 'center') css[1] = 'vertical-align:middle';
        else if (va === 'bottom') css[1] = 'vertical-align:bottom';
        const span = cell.span > 1 ? ` colspan="${cell.span}"` : '';
        const rs = cell.rowspan > 1 ? ` rowspan="${cell.rowspan}"` : '';
        html += `<td${span}${rs} style="${css.join(';')}">${this.blocksOf(cell.tc, { ...ctx, body: false, tableStyle })}</td>`;
      }
      html += '</tr>';
    });
    return `${html}</table>`;
  }

  // 段落と表の並び
  blocksOf(container, ctx) {
    let out = '';
    const items = kids(container);
    let prevStyle = null;
    const styleOf = (node) => (node && node.local === 'p' ? (attr(xpath(node, 'pPr', 'pStyle'), 'val') || this.defaultParaStyle) : undefined);
    for (let i = 0; i < items.length; i++) {
      const c = items[i];
      if (this.stopped) break;
      if (ctx.body && ++this.blocks > MAX_BLOCKS) { this.stopped = true; break; }
      if (c.local === 'p') {
        out += this.paragraph(c, { ...ctx, prevStyle, nextStyle: styleOf(items[i + 1]) });
        prevStyle = styleOf(c);
        continue;
      }
      prevStyle = null;
      if (c.local === 'tbl') out += this.table(c, ctx);
      else if (c.local === 'sdt') out += this.blocksOf(kid(c, 'sdtContent') || c, ctx);
      else if (c.local === 'customXml') out += this.blocksOf(c, ctx);
      else if (c.local === 'AlternateContent') out += this.blocksOf(kid(c, 'Choice') || c, ctx);
    }
    return out;
  }

  // 最初のセクションの用紙と余白
  firstSection(body) {
    for (const c of kids(body)) {
      const s = c.local === 'p' ? xpath(c, 'pPr', 'sectPr') : null;
      if (s) return s;
    }
    return kid(body, 'sectPr');
  }

  headerFooter(sectPr, kind) {
    const refs = kids(sectPr, `${kind}Reference`);
    const titlePg = kid(sectPr, 'titlePg') && on(kid(sectPr, 'titlePg'));
    const ref = (titlePg && refs.find((r) => attr(r, 'type') === 'first')) || refs.find((r) => attr(r, 'type') === 'default');
    const part = ref && this.pkg.relTarget(this.main, attr(ref, 'r:id'));
    const tree = part && this.pkg.xml(part);
    if (!tree) return '';
    return this.blocksOf(tree, { body: false, part });
  }

  render() {
    const body = kid(this.doc, 'body');
    if (!body) return { html: '', width: 794, height: 1123 };
    const sectPr = this.firstSection(body);
    const pgSz = kid(sectPr, 'pgSz');
    const pgMar = kid(sectPr, 'pgMar');
    let width = twipToPx(num(attr(pgSz, 'w'), 11906));
    let height = twipToPx(num(attr(pgSz, 'h'), 16838));
    if (attr(pgSz, 'orient') === 'landscape' && width < height) [width, height] = [height, width];
    const m = (k, d) => twipToPx(Math.abs(num(attr(pgMar, k), d)));
    this.page = {
      marginTop: m('top', 1440), marginBottom: m('bottom', 1440), marginLeft: m('left', 1440), marginRight: m('right', 1440),
      header: m('header', 720), footer: m('footer', 720),
    };
    const pg = this.page;
    const docGrid = kid(sectPr, 'docGrid');
    const gridType = attr(docGrid, 'type');
    this.grid = (gridType === 'lines' || gridType === 'linesAndChars' || gridType === 'snapToChars') && num(attr(docGrid, 'linePitch'))
      ? twipToPx(num(attr(docGrid, 'linePitch'))) : 0;
    const contentW = width - pg.marginLeft - pg.marginRight;
    const header = this.headerFooter(sectPr, 'header');
    const footer = this.headerFooter(sectPr, 'footer');
    const main = this.blocksOf(body, { body: true, part: this.main });
    const bgColor = attr(kid(this.doc, 'background'), 'color');
    const bg = safeHex(bgColor) || '#fff';
    // 和文と英数字の間を少し空ける（Word の既定）
    const html = `<div class="page" style="text-autospace:ideograph-alpha ideograph-numeric;position:relative;width:${width.toFixed(1)}px;height:${height.toFixed(1)}px;overflow:hidden;background:${bg}">`
      + (header ? `<div style="position:absolute;left:${pg.marginLeft.toFixed(1)}px;top:${pg.header.toFixed(1)}px;width:${contentW.toFixed(1)}px">${header}</div>` : '')
      + `<div style="position:absolute;left:${pg.marginLeft.toFixed(1)}px;top:${pg.marginTop.toFixed(1)}px;width:${contentW.toFixed(1)}px;z-index:1">${main}</div>`
      + (footer ? `<div style="position:absolute;left:${pg.marginLeft.toFixed(1)}px;bottom:${pg.footer.toFixed(1)}px;width:${contentW.toFixed(1)}px">${footer}</div>` : '')
      + this.floats.join('')
      + '</div>';
    return { html, width, height };
  }
}

// 段落番号の書式
function formatNumber(n, fmt) {
  switch (fmt) {
    case 'lowerLetter': return alpha(n).toLowerCase();
    case 'upperLetter': return alpha(n);
    case 'lowerRoman': return roman(n).toLowerCase();
    case 'upperRoman': return roman(n);
    case 'decimalZero': return String(n).padStart(2, '0');
    case 'decimalFullWidth': case 'decimalFullWidth2':
      return String(n).replace(/\d/g, (d) => String.fromCharCode(0xff10 + Number(d)));
    case 'decimalEnclosedCircle': case 'decimalEnclosedCircleChinese':
      return n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : String(n);
    case 'aiueoFullWidth': case 'aiueo': return kana('アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン', n);
    case 'iroha': case 'irohaFullWidth': return kana('イロハニホヘトチリヌルヲワカヨタレソツネナラムウヰノオクヤマケフコエテアサキユメミシヱヒモセス', n);
    case 'ideographTraditional': return kana('甲乙丙丁戊己庚辛壬癸', n);
    case 'japaneseCounting': case 'ideographDigital': case 'chineseCounting': return kanji(n);
    default: return String(n);
  }
}
function alpha(n) { let s = ''; while (n > 0) { n--; s = String.fromCharCode(65 + (n % 26)) + s; n = Math.floor(n / 26); } return s || 'A'; }
function roman(n) {
  const t = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
  let s = '';
  for (const [v, r] of t) while (n >= v) { s += r; n -= v; }
  return s;
}
function kana(list, n) { const a = [...list]; return a[(n - 1) % a.length] || String(n); }
function kanji(n) {
  const d = '〇一二三四五六七八九';
  if (n < 10) return d[n];
  if (n < 100) return `${n >= 20 ? d[Math.floor(n / 10)] : ''}十${n % 10 ? d[n % 10] : ''}`;
  return String(n);
}

function docxToHtml(pkg) {
  return new DocxRenderer(pkg).render();
}

module.exports = { docxToHtml, formatNumber };
