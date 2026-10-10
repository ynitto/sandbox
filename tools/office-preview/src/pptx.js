'use strict';

// pptx の 1 枚のスライドを HTML にする。
// スライド → レイアウト → マスターの順に、位置・文字の書式・背景を引き継ぐ。
// 図形（四角・角丸・楕円・よく使う多角形・自由形状）、文字、画像（トリミングを含む）、表、
// グループ、コネクタ、SmartArt（保存されている描画結果）を描く。グラフは枠だけを描く。

const { parseXml, kid, kids, attr, path: xpath, find, findAll, textOf } = require('./xml');
const { escapeHtml, fontStack, emuToPx, drawingColor, readTheme } = require('./ooxml');
const { formatNumber } = require('./docx');

const TITLE_TYPES = new Set(['title', 'ctrTitle']);

function n(v, d = 0) { const x = Number(v); return v == null || v === '' || !Number.isFinite(x) ? d : x; }
function bool(v) { return v === '1' || v === 'true' || v === 'on'; }
const f1 = (x) => (Math.round(x * 10) / 10).toString();

// プリセット図形の輪郭（0〜1 の座標）
const PRESET_POLYGONS = {
  triangle: [[0.5, 0], [1, 1], [0, 1]],
  rtTriangle: [[0, 0], [1, 1], [0, 1]],
  diamond: [[0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]],
  parallelogram: [[0.25, 0], [1, 0], [0.75, 1], [0, 1]],
  trapezoid: [[0.25, 0], [0.75, 0], [1, 1], [0, 1]],
  pentagon: [[0.5, 0], [1, 0.38], [0.81, 1], [0.19, 1], [0, 0.38]],
  hexagon: [[0.25, 0], [0.75, 0], [1, 0.5], [0.75, 1], [0.25, 1], [0, 0.5]],
  octagon: [[0.29, 0], [0.71, 0], [1, 0.29], [1, 0.71], [0.71, 1], [0.29, 1], [0, 0.71], [0, 0.29]],
  homePlate: [[0, 0], [0.8, 0], [1, 0.5], [0.8, 1], [0, 1]],
  chevron: [[0, 0], [0.8, 0], [1, 0.5], [0.8, 1], [0, 1], [0.2, 0.5]],
  rightArrow: [[0, 0.25], [0.7, 0.25], [0.7, 0], [1, 0.5], [0.7, 1], [0.7, 0.75], [0, 0.75]],
  leftArrow: [[1, 0.25], [0.3, 0.25], [0.3, 0], [0, 0.5], [0.3, 1], [0.3, 0.75], [1, 0.75]],
  upArrow: [[0.25, 1], [0.25, 0.3], [0, 0.3], [0.5, 0], [1, 0.3], [0.75, 0.3], [0.75, 1]],
  downArrow: [[0.25, 0], [0.25, 0.7], [0, 0.7], [0.5, 1], [1, 0.7], [0.75, 0.7], [0.75, 0]],
  leftRightArrow: [[0, 0.5], [0.2, 0], [0.2, 0.25], [0.8, 0.25], [0.8, 0], [1, 0.5], [0.8, 1], [0.8, 0.75], [0.2, 0.75], [0.2, 1]],
  star5: [[0.5, 0], [0.62, 0.38], [1, 0.38], [0.69, 0.62], [0.81, 1], [0.5, 0.76], [0.19, 1], [0.31, 0.62], [0, 0.38], [0.38, 0.38]],
  plus: [[0.33, 0], [0.67, 0], [0.67, 0.33], [1, 0.33], [1, 0.67], [0.67, 0.67], [0.67, 1], [0.33, 1], [0.33, 0.67], [0, 0.67], [0, 0.33], [0.33, 0.33]],
  flowChartDecision: [[0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]],
  flowChartInputOutput: [[0.2, 0], [1, 0], [0.8, 1], [0, 1]],
  snip1Rect: [[0, 0], [0.84, 0], [1, 0.16], [1, 1], [0, 1]],
};
// 破線の種類 → 線幅を単位にした間隔
const DASHES = {
  dot: [1, 1], sysDot: [1, 1], sysDash: [3, 1], dash: [4, 3], lgDash: [8, 3], dashDot: [4, 3, 1, 3],
  lgDashDot: [8, 3, 1, 3], lgDashDotDot: [8, 3, 1, 3, 1, 3], sysDashDot: [3, 1, 1, 1], sysDashDotDot: [3, 1, 1, 1, 1, 1],
};
function dashArray(line) {
  const pat = DASHES[line.dash] || DASHES.dash;
  const w = Math.max(1, line.width);
  return pat.map((x) => f1(x * w)).join(' ');
}
const dashCss = (line) => (line.dash ? (/[dD]ot$/.test(line.dash) && !/Dash/.test(line.dash) ? 'dotted' : 'dashed') : 'solid');

// 線だけの図形（中かっこ・かっこ）。w, h から SVG の path を作る
const PRESET_PATHS = {
  rightBrace: (w, h) => `M0,0Q${w / 2},0 ${w / 2},${h * 0.08}L${w / 2},${h * 0.42}Q${w / 2},${h / 2} ${w},${h / 2}Q${w / 2},${h / 2} ${w / 2},${h * 0.58}L${w / 2},${h * 0.92}Q${w / 2},${h} 0,${h}`,
  leftBrace: (w, h) => `M${w},0Q${w / 2},0 ${w / 2},${h * 0.08}L${w / 2},${h * 0.42}Q${w / 2},${h / 2} 0,${h / 2}Q${w / 2},${h / 2} ${w / 2},${h * 0.58}L${w / 2},${h * 0.92}Q${w / 2},${h} ${w},${h}`,
  rightBracket: (w, h) => `M0,0Q${w},0 ${w},${h * 0.1}L${w},${h * 0.9}Q${w},${h} 0,${h}`,
  leftBracket: (w, h) => `M${w},0Q0,0 0,${h * 0.1}L0,${h * 0.9}Q0,${h} ${w},${h}`,
  bracePair: (w, h) => `M${w * 0.1},0Q0,0 0,${h * 0.1}L0,${h * 0.9}Q0,${h} ${w * 0.1},${h}M${w * 0.9},0Q${w},0 ${w},${h * 0.1}L${w},${h * 0.9}Q${w},${h} ${w * 0.9},${h}`,
  bracketPair: (w, h) => `M${w * 0.1},0L0,0L0,${h}L${w * 0.1},${h}M${w * 0.9},0L${w},0L${w},${h}L${w * 0.9},${h}`,
  arc: (w, h) => `M${w / 2},0A${w / 2},${h / 2} 0 0 1 ${w},${h / 2}`,
};
// 調整値（adj）で形が決まる塗りの図形
function arcPoint(cx, cy, rx, ry, deg) {
  const a = (deg * Math.PI) / 180;
  return `${f1(cx + rx * Math.cos(a))},${f1(cy + ry * Math.sin(a))}`;
}
const FILLED_PATHS = {
  donut: (w, h, adj) => {
    const t = Math.min(w, h) * Math.min(0.5, (adj.adj ?? 25000) / 100000);
    const ring = (rx, ry) => `M${f1(w / 2 - rx)},${f1(h / 2)}a${f1(rx)},${f1(ry)} 0 1 0 ${f1(rx * 2)},0a${f1(rx)},${f1(ry)} 0 1 0 ${f1(-rx * 2)},0Z`;
    return ring(w / 2, h / 2) + ring(Math.max(0, w / 2 - t), Math.max(0, h / 2 - t));
  },
  blockArc: (w, h, adj) => {
    // adj1 から adj2 まで時計回り。adj3 は太さ（短い辺に対する割合）
    const st = (adj.adj1 ?? 10800000) / 60000;
    const en = (adj.adj2 ?? 0) / 60000;
    const t = Math.min(w, h) * Math.min(0.5, (adj.adj3 ?? 25000) / 100000);
    let sweep = (en - st) % 360;
    if (sweep <= 0) sweep += 360;
    const large = sweep > 180 ? 1 : 0;
    const cx = w / 2, cy = h / 2, rx = w / 2, ry = h / 2, ix = Math.max(0, rx - t), iy = Math.max(0, ry - t);
    return `M${arcPoint(cx, cy, rx, ry, st)}A${f1(rx)},${f1(ry)} 0 ${large} 1 ${arcPoint(cx, cy, rx, ry, en)}`
      + `L${arcPoint(cx, cy, ix, iy, en)}A${f1(ix)},${f1(iy)} 0 ${large} 0 ${arcPoint(cx, cy, ix, iy, st)}Z`;
  },
};

function adjValues(geom) {
  const out = {};
  for (const gd of kids(kid(geom, 'avLst'), 'gd')) {
    const m = /^val\s+(-?\d+)/.exec(attr(gd, 'fmla') || '');
    if (m) out[attr(gd, 'name')] = Number(m[1]);
  }
  return out;
}

const ROUND_PRESETS = new Set(['roundRect', 'flowChartAlternateProcess', 'round2SameRect', 'flowChartTerminator']);
const ELLIPSE_PRESETS = new Set(['ellipse', 'flowChartConnector', 'pie', 'chord']);

const AUTONUM = {
  arabicPeriod: [(i) => `${i}.`], arabicParenR: [(i) => `${i})`], arabicParenBoth: [(i) => `(${i})`], arabicPlain: [(i) => `${i}`],
  alphaLcPeriod: [(i) => `${formatNumber(i, 'lowerLetter')}.`], alphaUcPeriod: [(i) => `${formatNumber(i, 'upperLetter')}.`],
  alphaLcParenR: [(i) => `${formatNumber(i, 'lowerLetter')})`], alphaUcParenR: [(i) => `${formatNumber(i, 'upperLetter')})`],
  alphaLcParenBoth: [(i) => `(${formatNumber(i, 'lowerLetter')})`], alphaUcParenBoth: [(i) => `(${formatNumber(i, 'upperLetter')})`],
  romanLcPeriod: [(i) => `${formatNumber(i, 'lowerRoman')}.`], romanUcPeriod: [(i) => `${formatNumber(i, 'upperRoman')}.`],
  romanLcParenR: [(i) => `${formatNumber(i, 'lowerRoman')})`], romanUcParenR: [(i) => `${formatNumber(i, 'upperRoman')})`],
  circleNumDbPlain: [(i) => formatNumber(i, 'decimalEnclosedCircle')], circleNumWdBlackPlain: [(i) => formatNumber(i, 'decimalEnclosedCircle')],
  ea1JpnChsDbPeriod: [(i) => `${formatNumber(i, 'decimalFullWidth')}．`], arabicDbPeriod: [(i) => `${formatNumber(i, 'decimalFullWidth')}．`],
  arabicDbPlain: [(i) => formatNumber(i, 'decimalFullWidth')],
};
const BULLET_MAP = { '': '•', '': '▪', '': '➢', '': '✓', '': '❖', '': '■', '': '□', '': '◆', '§': '▪', 'Ø': '➢', 'ü': '✓', 'q': '❑', 'n': '■', 'l': '●', 'v': '❖', 'Ä': '►' };

// PowerPoint に組み込みの表のスタイル（tableStyles.xml に書かれないことが多い）。
// 表を挿入したときの既定「中間スタイル 2 - アクセント 1」とその色違いだけを持つ。
const BUILTIN_TABLE_STYLES = {
  '{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}': 'dk1',
  '{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}': 'accent1',
  '{21E4AEA4-8DFA-4A89-87EB-49C32662AFE8}': 'accent2',
  '{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}': 'accent3',
  '{00A15C55-8517-42AA-B614-E9B94910E393}': 'accent4',
  '{7DF18680-E054-41AD-8BC1-D1AEF772440D}': 'accent5',
  '{93296810-A885-4BE3-A3E7-6D5BEEA58F35}': 'accent6',
};

function mediumStyle2(accent) {
  const ln = (w = 12700) => `<a:ln w="${w}"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln>`;
  const fill = (tint) => `<a:fill><a:solidFill><a:schemeClr val="${accent}">${tint ? `<a:tint val="${tint}"/>` : ''}</a:schemeClr></a:solidFill></a:fill>`;
  const strong = `<a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="lt1"/></a:tcTxStyle>`;
  return parseXml(`<a:tblStyle xmlns:a="a">
    <a:wholeTbl><a:tcTxStyle><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="dk1"/></a:tcTxStyle>
      <a:tcStyle><a:tcBdr><a:left>${ln()}</a:left><a:right>${ln()}</a:right><a:top>${ln()}</a:top><a:bottom>${ln()}</a:bottom><a:insideH>${ln()}</a:insideH><a:insideV>${ln()}</a:insideV></a:tcBdr>${fill(20000)}</a:tcStyle></a:wholeTbl>
    <a:band1H><a:tcStyle>${fill(40000)}</a:tcStyle></a:band1H>
    <a:band1V><a:tcStyle>${fill(40000)}</a:tcStyle></a:band1V>
    <a:firstCol>${strong}<a:tcStyle>${fill(0)}</a:tcStyle></a:firstCol>
    <a:lastCol>${strong}<a:tcStyle>${fill(0)}</a:tcStyle></a:lastCol>
    <a:lastRow>${strong}<a:tcStyle><a:tcBdr><a:top>${ln(38100)}</a:top></a:tcBdr>${fill(0)}</a:tcStyle></a:lastRow>
    <a:firstRow>${strong}<a:tcStyle><a:tcBdr><a:bottom>${ln(38100)}</a:bottom></a:tcBdr>${fill(0)}</a:tcStyle></a:firstRow>
  </a:tblStyle>`);
}

class PptxRenderer {
  constructor(pkg, opts = {}) {
    this.pkg = pkg;
    this.opts = opts;
    this.main = pkg.mainPart();
    this.pres = pkg.xml(this.main);
    this.uid = 0; // SVG の id（グラデーション・矢印）を 1 枚の中で重ねない
    this.loadTableStyles();
  }

  loadTableStyles() {
    this.tableStyles = new Map();
    const tree = this.pkg.xml(this.pkg.relByType(this.main, 'tableStyles'));
    this.defaultTableStyle = attr(tree, 'def') || '{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}';
    for (const s of kids(tree, 'tblStyle')) this.tableStyles.set(attr(s, 'styleId'), s);
  }

  tableStyle(id) {
    const key = id || this.defaultTableStyle;
    if (this.tableStyles.has(key)) return this.tableStyles.get(key);
    const accent = BUILTIN_TABLE_STYLES[String(key).toUpperCase()];
    if (!accent) return null;
    const node = mediumStyle2(accent);
    this.tableStyles.set(key, node);
    return node;
  }

  // ---- 色・塗り ----

  color(node, phClr = null) {
    return drawingColor(node, this.theme, this.clrMap, phClr);
  }

  // 塗りの要素（solidFill / gradFill / blipFill / noFill / pattFill）を CSS の background にする
  fillCss(fill, part, phClr = null) {
    if (!fill) return null;
    switch (fill.local) {
      case 'noFill': return 'transparent';
      case 'solidFill': return this.color(fill, phClr);
      case 'pattFill': return this.color(kid(fill, 'fgClr'), phClr);
      case 'gradFill': {
        const stops = kids(kid(fill, 'gsLst'), 'gs').map((gs) => ({ pos: n(attr(gs, 'pos')) / 1000, color: this.color(gs, phClr) })).filter((s) => s.color).sort((a, b) => a.pos - b.pos);
        if (!stops.length) return null;
        if (stops.length === 1) return stops[0].color;
        const list = stops.map((s) => `${s.color} ${f1(s.pos)}%`).join(',');
        if (kid(fill, 'path')) return `radial-gradient(circle,${list})`;
        const ang = n(attr(kid(fill, 'lin'), 'ang')) / 60000;
        return `linear-gradient(${f1(ang + 90)}deg,${list})`;
      }
      case 'blipFill': {
        const src = this.pkg.blipDataUri(part, kid(fill, 'blip'));
        return src ? `center/100% 100% no-repeat url("${src}")` : null;
      }
      case 'grpFill': return null;
      default: return null;
    }
  }

  firstFill(node) {
    return node ? kids(node).find((c) => /^(noFill|solidFill|gradFill|blipFill|pattFill|grpFill)$/.test(c.local)) || null : null;
  }

  // テーマの書式（塗り・線）を idx で引く。fillRef / lnRef / bgRef 用
  themeStyle(list, idx) {
    const fmt = xpath(this.themeTree, 'themeElements', 'fmtScheme');
    if (list === 'bg') return kids(kid(fmt, 'bgFillStyleLst'))[idx - 1001] || null;
    if (list === 'fill') return kids(kid(fmt, 'fillStyleLst'))[idx - 1] || null;
    if (list === 'ln') return kids(kid(fmt, 'lnStyleLst'))[idx - 1] || null;
    return null;
  }

  // ---- 継承 ----

  // スライドの部品と、そのレイアウト・マスター・テーマを読む
  loadSlide(index) {
    const ids = kids(kid(this.pres, 'sldIdLst'), 'sldId');
    if (!ids.length) return null;
    const i = Math.max(0, Math.min(ids.length - 1, Number.isInteger(index) ? index : 0));
    const slidePart = this.pkg.relTarget(this.main, attr(ids[i], 'r:id'));
    const slide = this.pkg.xml(slidePart);
    if (!slide) return null;
    const layoutPart = this.pkg.relByType(slidePart, 'slideLayout');
    const layout = this.pkg.xml(layoutPart);
    const masterPart = layoutPart && this.pkg.relByType(layoutPart, 'slideMaster');
    const master = this.pkg.xml(masterPart);
    const themePart = masterPart && this.pkg.relByType(masterPart, 'theme');
    this.themeTree = this.pkg.xml(themePart);
    this.theme = readTheme(this.themeTree);
    this.clrMap = { ...(kid(master, 'clrMap')?.attrs || {}) };
    for (const tree of [layout, slide]) {
      const ovr = xpath(tree, 'clrMapOvr', 'overrideClrMapping');
      if (ovr) this.clrMap = { ...ovr.attrs };
    }
    return { index: i, count: ids.length, slide, slidePart, layout, layoutPart, master, masterPart };
  }

  phInfo(sp) {
    const nv = kids(sp).find((c) => /^nv/.test(c.local));
    const ph = find(kid(nv, 'nvPr'), 'ph') || xpath(nv, 'nvPr', 'ph');
    if (!ph) return null;
    return { type: attr(ph, 'type') || 'obj', idx: attr(ph, 'idx') };
  }

  findPlaceholder(tree, info, byIdx) {
    if (!tree || !info) return null;
    const all = findAll(xpath(tree, 'cSld', 'spTree'), 'sp').concat(findAll(xpath(tree, 'cSld', 'spTree'), 'pic'));
    const withPh = all.map((s) => ({ s, p: this.phInfo(s) })).filter((x) => x.p);
    if (byIdx && info.idx != null) {
      const hit = withPh.find((x) => x.p.idx === info.idx);
      if (hit) return hit.s;
    }
    const same = (a, b) => a === b || (TITLE_TYPES.has(a) && TITLE_TYPES.has(b));
    let hit = withPh.find((x) => same(x.p.type, info.type));
    if (!hit && !byIdx && ['obj', 'body', 'subTitle', 'tbl', 'chart', 'pic', 'media', 'clipArt', 'dgm'].includes(info.type)) hit = withPh.find((x) => x.p.type === 'body');
    return hit ? hit.s : null;
  }

  // 図形の継承元（レイアウトのプレースホルダー、マスターのプレースホルダー）
  inheritance(sp, ctx) {
    const info = this.phInfo(sp);
    if (!info || ctx.level === 'master') return { info, chain: [] };
    const chain = [];
    if (ctx.level === 'slide') {
      const l = this.findPlaceholder(this.s.layout, info, true);
      if (l) chain.push({ node: l, part: this.s.layoutPart });
    }
    const m = this.findPlaceholder(this.s.master, info, false);
    if (m) chain.push({ node: m, part: this.s.masterPart });
    return { info, chain };
  }

  // ---- 図形 ----

  xfrmOf(node) {
    const spPr = kid(node, 'spPr') || kid(node, 'grpSpPr');
    return kid(spPr, 'xfrm') || kid(node, 'xfrm');
  }

  // EMU の矩形を、グループの変換を通してスライドの px にする
  box(xfrm, tf) {
    const off = kid(xfrm, 'off'), ext = kid(xfrm, 'ext');
    const x = n(attr(off, 'x')), y = n(attr(off, 'y')), w = n(attr(ext, 'cx')), h = n(attr(ext, 'cy'));
    return {
      x: emuToPx(tf.ox + (x - tf.cx) * tf.sx), y: emuToPx(tf.oy + (y - tf.cy) * tf.sy),
      w: emuToPx(w * tf.sx), h: emuToPx(h * tf.sy),
      rot: n(attr(xfrm, 'rot')) / 60000, flipH: bool(attr(xfrm, 'flipH')), flipV: bool(attr(xfrm, 'flipV')),
    };
  }

  shapes(spTree, ctx) {
    let out = '';
    for (const c of kids(spTree)) {
      if (ctx.level !== 'slide' && this.phInfo(c)) continue; // レイアウトとマスターのプレースホルダーは枠だけなので描かない
      switch (c.local) {
        case 'sp': out += this.shape(c, ctx); break;
        case 'pic': out += this.picture(c, ctx); break;
        case 'grpSp': out += this.group(c, ctx); break;
        case 'cxnSp': out += this.connector(c, ctx); break;
        case 'graphicFrame': out += this.graphicFrame(c, ctx); break;
        case 'AlternateContent': {
          const choice = kid(c, 'Choice') || kid(c, 'Fallback');
          if (choice) out += this.shapes(choice, ctx);
          break;
        }
        default: break;
      }
    }
    return out;
  }

  group(g, ctx) {
    const xfrm = kid(kid(g, 'grpSpPr'), 'xfrm');
    if (!xfrm) return this.shapes(g, ctx);
    const off = kid(xfrm, 'off'), ext = kid(xfrm, 'ext'), chOff = kid(xfrm, 'chOff'), chExt = kid(xfrm, 'chExt');
    const t = ctx.tf;
    const sx = n(attr(chExt, 'cx')) ? n(attr(ext, 'cx')) / n(attr(chExt, 'cx')) : 1;
    const sy = n(attr(chExt, 'cy')) ? n(attr(ext, 'cy')) / n(attr(chExt, 'cy')) : 1;
    const tf = {
      ox: t.ox + (n(attr(off, 'x')) - t.cx) * t.sx, oy: t.oy + (n(attr(off, 'y')) - t.cy) * t.sy,
      cx: n(attr(chOff, 'x')), cy: n(attr(chOff, 'y')), sx: t.sx * sx, sy: t.sy * sy,
    };
    const rot = n(attr(xfrm, 'rot')) / 60000;
    const inner = this.shapes(g, { ...ctx, tf });
    if (!rot) return inner;
    const b = this.box(xfrm, t);
    return `<div style="position:absolute;left:0;top:0;width:100%;height:100%;transform-origin:${f1(b.x + b.w / 2)}px ${f1(b.y + b.h / 2)}px;transform:rotate(${rot}deg)">${inner}</div>`;
  }

  lineStyle(ln, styleRef) {
    let color = null, width = 0, dash = null, gradient = null;
    if (ln && kid(ln, 'noFill')) return null;
    if (ln) {
      color = this.color(kid(ln, 'solidFill'));
      const grad = kid(ln, 'gradFill');
      if (!color && grad) {
        const stops = kids(kid(grad, 'gsLst'), 'gs').map((gs) => ({ pos: n(attr(gs, 'pos')) / 100000, color: this.color(gs) })).filter((x) => x.color).sort((a, b) => a.pos - b.pos);
        if (stops.length) {
          gradient = { stops, ang: n(attr(kid(grad, 'lin'), 'ang')) / 60000 };
          // 枠線（CSS）で描くときの代わりの色は、透けていない最初の色
          color = (stops.find((x) => !/^rgba/.test(x.color)) || stops[0]).color;
        }
      }
      if (attr(ln, 'w') != null) width = emuToPx(attr(ln, 'w'));
      dash = attr(kid(ln, 'prstDash'), 'val');
    }
    if (!color && styleRef && n(attr(styleRef, 'idx')) > 0) {
      const themeLn = this.themeStyle('ln', n(attr(styleRef, 'idx')));
      const phClr = this.color(styleRef);
      color = this.color(kid(themeLn, 'solidFill'), phClr) || phClr;
      if (!width && themeLn) width = emuToPx(attr(themeLn, 'w') || 9525);
    }
    if (!color) return null;
    return { color, gradient, width: Math.max(width || 0.75, 0.75), dash: dash && dash !== 'solid' ? dash : null };
  }

  // SVG の線の属性。グラデーションの線は、長さ 0 の辺がある（水平・垂直の線）ので userSpaceOnUse で引く
  strokeSvg(line, w, h) {
    if (!line) return { defs: '', attrs: '' };
    let paint = line.color;
    let defs = '';
    if (line.gradient) {
      const id = `g${++this.uid}`;
      const a = (line.gradient.ang * Math.PI) / 180;
      const dx = Math.cos(a) * w / 2, dy = Math.sin(a) * h / 2;
      defs = `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${f1(w / 2 - dx)}" y1="${f1(h / 2 - dy)}" x2="${f1(w / 2 + dx)}" y2="${f1(h / 2 + dy)}">`
        + line.gradient.stops.map((st) => `<stop offset="${st.pos}" stop-color="${st.color}"/>`).join('') + '</linearGradient>';
      paint = `url(#${id})`;
    }
    return { defs, attrs: ` stroke="${paint}" stroke-width="${f1(line.width)}"${line.dash ? ` stroke-dasharray="${dashArray(line)}"` : ''}` };
  }

  shape(sp, ctx) {
    const { info, chain } = this.inheritance(sp, ctx);
    const spPr = kid(sp, 'spPr');
    const xfrm = kid(spPr, 'xfrm') || chain.map((c) => kid(kid(c.node, 'spPr'), 'xfrm')).find(Boolean);
    if (!xfrm) return '';
    const b = this.box(xfrm, ctx.tf);
    const style = kid(sp, 'style');
    let fill = this.firstFill(spPr);
    for (const c of chain) if (!fill) fill = this.firstFill(kid(c.node, 'spPr'));
    let bg = fill ? this.fillCss(fill, ctx.part) : null;
    if (!fill && style) {
      const ref = kid(style, 'fillRef');
      const idx = n(attr(ref, 'idx'));
      if (idx > 0) { const phClr = this.color(ref); bg = this.fillCss(this.firstFill({ children: [this.themeStyle('fill', idx)].filter(Boolean) }), ctx.part, phClr) || phClr; }
    }
    let ln = kid(spPr, 'ln');
    for (const c of chain) if (!ln) ln = kid(kid(c.node, 'spPr'), 'ln');
    const line = this.lineStyle(ln, kid(style, 'lnRef'));
    const geom = kid(spPr, 'prstGeom') || chain.map((c) => kid(kid(c.node, 'spPr'), 'prstGeom')).find(Boolean);
    const prst = attr(geom, 'prst') || 'rect';
    const cust = kid(spPr, 'custGeom');
    const text = this.textBody(sp, info, chain, ctx, style);
    return this.place(b, this.geometry(b, prst, geom, cust, bg, line), text);
  }

  // 位置と回転を持つ箱。中身（輪郭・文字）は箱の中に置く
  place(b, geometryHtml, textHtml = '') {
    const tr = [];
    if (b.rot) tr.push(`rotate(${f1(b.rot)}deg)`);
    const flip = [];
    if (b.flipH) flip.push('scaleX(-1)');
    if (b.flipV) flip.push('scaleY(-1)');
    const geo = flip.length && geometryHtml ? `<div style="position:absolute;inset:0;transform:${flip.join(' ')}">${geometryHtml}</div>` : geometryHtml;
    return `<div style="position:absolute;left:${f1(b.x)}px;top:${f1(b.y)}px;width:${f1(b.w)}px;height:${f1(b.h)}px${tr.length ? `;transform:${tr.join(' ')}` : ''}">${geo}${textHtml}</div>`;
  }

  geometry(b, prst, geom, cust, bg, line) {
    if (!bg && !line) return '';
    const fillCss = bg && bg !== 'transparent' ? bg : null;
    const isSolid = (c) => c && !/gradient|url\(/.test(c);
    if (PRESET_PATHS[prst]) {
      return line ? this.svgStroke(PRESET_PATHS[prst](Math.max(b.w, 0.1), Math.max(b.h, 0.1)), b.w, b.h, line) : '';
    }
    if (cust || PRESET_POLYGONS[prst] || FILLED_PATHS[prst] || (prst === 'line' || prst === 'straightConnector1')) {
      const w = Math.max(b.w, 0.1), h = Math.max(b.h, 0.1);
      let d = '';
      if (cust) d = this.customPath(cust, w, h);
      else if (FILLED_PATHS[prst]) d = FILLED_PATHS[prst](w, h, adjValues(geom));
      else if (PRESET_POLYGONS[prst]) d = `M${PRESET_POLYGONS[prst].map(([x, y]) => `${f1(x * w)},${f1(y * h)}`).join('L')}Z`;
      else d = `M0,0L${f1(w)},${f1(h)}`;
      let paint = 'none';
      if (fillCss && isSolid(fillCss)) paint = fillCss;
      else if (fillCss) {
        // グラデーションや画像の塗りは、輪郭で切り抜いた div で描く
        return `<div style="position:absolute;inset:0;background:${fillCss};clip-path:path('${d}')"></div>${line ? this.svgStroke(d, w, h, line) : ''}`;
      }
      const stroke = this.strokeSvg(line, w, h);
      return `<svg width="${f1(w)}" height="${f1(h)}" style="position:absolute;left:0;top:0;overflow:visible">${stroke.defs}<path d="${d}" fill="${paint}" fill-rule="evenodd"${stroke.attrs}/></svg>`;
    }
    const css = ['position:absolute', 'inset:0', 'box-sizing:border-box'];
    if (fillCss) css.push(`background:${fillCss}`);
    if (line) css.push(`border:${f1(line.width)}px ${dashCss(line)} ${line.color}`);
    if (ELLIPSE_PRESETS.has(prst)) css.push('border-radius:50%');
    else if (ROUND_PRESETS.has(prst)) {
      const gd = find(geom, 'gd');
      const adj = gd ? n(/val\s+(\d+)/.exec(attr(gd, 'fmla') || '')?.[1], 16667) : 16667;
      css.push(`border-radius:${f1(Math.min(b.w, b.h) * (prst === 'flowChartTerminator' ? 0.5 : adj / 100000))}px`);
    }
    return `<div style="${css.join(';')}"></div>`;
  }

  svgStroke(d, w, h, line) {
    const stroke = this.strokeSvg(line, w, h);
    return `<svg width="${f1(Math.max(w, 1))}" height="${f1(Math.max(h, 1))}" style="position:absolute;left:0;top:0;overflow:visible">${stroke.defs}<path d="${d}" fill="none"${stroke.attrs}/></svg>`;
  }

  // 自由形状（a:custGeom）を SVG の path にする
  customPath(cust, w, h) {
    let d = '';
    for (const p of kids(kid(cust, 'pathLst'), 'path')) {
      const pw = n(attr(p, 'w')) || 1, ph = n(attr(p, 'h')) || 1;
      const pt = (node) => `${f1((n(attr(node, 'x')) / pw) * w)},${f1((n(attr(node, 'y')) / ph) * h)}`;
      for (const c of kids(p)) {
        const pts = kids(c, 'pt');
        if (c.local === 'moveTo') d += `M${pt(pts[0])}`;
        else if (c.local === 'lnTo') d += `L${pt(pts[0])}`;
        else if (c.local === 'cubicBezTo') d += `C${pts.map(pt).join(' ')}`;
        else if (c.local === 'quadBezTo') d += `Q${pts.map(pt).join(' ')}`;
        else if (c.local === 'close') d += 'Z';
      }
    }
    return d || `M0,0H${f1(w)}V${f1(h)}H0Z`;
  }

  picture(pic, ctx) {
    const { chain } = this.inheritance(pic, ctx);
    const xfrm = kid(kid(pic, 'spPr'), 'xfrm') || chain.map((c) => kid(kid(c.node, 'spPr'), 'xfrm')).find(Boolean);
    if (!xfrm) return '';
    const b = this.box(xfrm, ctx.tf);
    const blipFill = kid(pic, 'blipFill');
    const src = this.pkg.blipDataUri(ctx.part, kid(blipFill, 'blip'));
    const line = this.lineStyle(kid(kid(pic, 'spPr'), 'ln'), kid(kid(pic, 'style'), 'lnRef'));
    const prst = attr(kid(kid(pic, 'spPr'), 'prstGeom'), 'prst') || 'rect';
    const radius = ELLIPSE_PRESETS.has(prst) ? 'border-radius:50%;' : (ROUND_PRESETS.has(prst) ? `border-radius:${f1(Math.min(b.w, b.h) * 0.16667)}px;` : '');
    let inner = '';
    if (src) {
      // トリミング（srcRect）は、画像を拡大して外側を切り落とす
      const r = kid(blipFill, 'srcRect');
      const l = n(attr(r, 'l')) / 100000, t = n(attr(r, 't')) / 100000, rr = n(attr(r, 'r')) / 100000, bb = n(attr(r, 'b')) / 100000;
      const iw = b.w / Math.max(0.01, 1 - l - rr), ih = b.h / Math.max(0.01, 1 - t - bb);
      const flip = [b.flipH && 'scaleX(-1)', b.flipV && 'scaleY(-1)'].filter(Boolean).join(' ');
      inner = `<div style="position:absolute;inset:0;overflow:hidden;${radius}${flip ? `transform:${flip}` : ''}"><img src="${src}" style="position:absolute;left:${f1(-l * iw)}px;top:${f1(-t * ih)}px;width:${f1(iw)}px;height:${f1(ih)}px"></div>`;
    }
    if (line) inner += `<div style="position:absolute;inset:0;${radius}border:${f1(line.width)}px solid ${line.color}"></div>`;
    return this.place({ ...b, flipH: false, flipV: false }, inner);
  }

  connector(c, ctx) {
    const xfrm = kid(kid(c, 'spPr'), 'xfrm');
    if (!xfrm) return '';
    const b = this.box(xfrm, ctx.tf);
    const line = this.lineStyle(kid(kid(c, 'spPr'), 'ln'), kid(kid(c, 'style'), 'lnRef'));
    if (!line) return '';
    const prst = attr(kid(kid(c, 'spPr'), 'prstGeom'), 'prst') || 'line';
    const w = b.w, h = b.h;
    const d = /bentConnector/.test(prst) ? `M0,0H${f1(w / 2)}V${f1(h)}H${f1(w)}` : `M0,0L${f1(w)},${f1(h)}`;
    const tail = kid(kid(kid(c, 'spPr'), 'ln'), 'tailEnd');
    const head = kid(kid(kid(c, 'spPr'), 'ln'), 'headEnd');
    const arrow = (e) => e && attr(e, 'type') && attr(e, 'type') !== 'none';
    const id = `m${++this.uid}`;
    const stroke = this.strokeSvg(line, w, h);
    const marker = `<defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0,0L10,5L0,10Z" fill="${line.color}"/></marker>${stroke.defs}</defs>`;
    const svg = `<svg width="${f1(Math.max(w, 1))}" height="${f1(Math.max(h, 1))}" style="position:absolute;left:0;top:0;overflow:visible">${marker}<path d="${d}" fill="none"${stroke.attrs}${arrow(tail) ? ` marker-end="url(#${id})"` : ''}${arrow(head) ? ` marker-start="url(#${id})"` : ''}/></svg>`;
    return this.place(b, svg);
  }

  graphicFrame(g, ctx) {
    const xfrm = kid(g, 'xfrm');
    if (!xfrm) return '';
    const b = this.box(xfrm, ctx.tf);
    const data = xpath(g, 'graphic', 'graphicData');
    const uri = attr(data, 'uri') || '';
    const tbl = kid(data, 'tbl');
    if (tbl) return this.place(b, '', this.table(tbl, b, ctx));
    if (/\/diagram$/.test(uri)) {
      // SmartArt は PowerPoint が保存した描画結果（diagramDrawing）を描く
      const drawing = this.diagramDrawing(ctx);
      if (drawing) {
        const tf = { ox: n(attr(kid(xfrm, 'off'), 'x')), oy: n(attr(kid(xfrm, 'off'), 'y')), cx: 0, cy: 0, sx: 1, sy: 1 };
        return this.shapes(drawing.tree, { ...ctx, part: drawing.part, tf, level: 'diagram' });
      }
    }
    if (/\/chart$/.test(uri)) {
      return this.place(b, '<div style="position:absolute;inset:0;border:1px solid #D9D9D9;background:repeating-linear-gradient(0deg,transparent 0 23px,#EEE 23px 24px)"></div>');
    }
    // OLE などは代わりの画像があれば描く
    const pic = find(data, 'pic');
    if (pic) {
      const src = this.pkg.blipDataUri(ctx.part, find(pic, 'blip'));
      if (src) return this.place(b, `<img src="${src}" style="position:absolute;inset:0;width:100%;height:100%">`);
    }
    return '';
  }

  diagramDrawing(ctx) {
    this.diagramSeen = (this.diagramSeen || 0) + 1;
    const rels = [...this.pkg.rels(ctx.part).values()].filter((r) => !r.external && /\/diagramDrawing$/.test(r.type));
    const target = rels[this.diagramSeen - 1]?.target;
    const tree = target && this.pkg.xml(target);
    const spTree = find(tree, 'spTree');
    return spTree ? { tree: spTree, part: target } : null;
  }

  // ---- 文字 ----

  // 段落の書式を探す順（高い順）: 図形の lstStyle → レイアウト → マスター → マスターの txStyles → 既定
  listStyles(sp, info, chain) {
    const own = kid(kid(sp, 'txBody'), 'lstStyle');
    const lists = [own];
    for (const c of chain) lists.push(kid(kid(c.node, 'txBody'), 'lstStyle'));
    const tx = kid(this.s.master, 'txStyles');
    if (info) lists.push(TITLE_TYPES.has(info.type) ? kid(tx, 'titleStyle') : (['dt', 'ftr', 'sldNum', 'hdr'].includes(info.type) ? kid(tx, 'otherStyle') : kid(tx, 'bodyStyle')));
    else lists.push(kid(tx, 'otherStyle'));
    lists.push(kid(this.pres, 'defaultTextStyle'));
    const out = lists.filter(Boolean);
    out.own = own;
    return out;
  }

  levelNodes(lists, lvl) {
    const out = [];
    for (const l of lists) {
      const node = kid(l, `lvl${lvl + 1}pPr`);
      if (node) out.push(node);
    }
    for (const l of lists) { const d = kid(l, 'defPPr'); if (d) out.push(d); }
    return out;
  }

  bodyPr(sp, chain) {
    const nodes = [kid(kid(sp, 'txBody'), 'bodyPr'), ...chain.map((c) => kid(kid(c.node, 'txBody'), 'bodyPr'))].filter(Boolean);
    const get = (k) => { for (const nd of nodes) if (attr(nd, k) != null) return attr(nd, k); return undefined; };
    const autofit = nodes.map((nd) => kid(nd, 'normAutofit')).find(Boolean);
    return {
      anchor: get('anchor') || 't',
      l: emuToPx(get('lIns') ?? 91440), t: emuToPx(get('tIns') ?? 45720), r: emuToPx(get('rIns') ?? 91440), b: emuToPx(get('bIns') ?? 45720),
      wrap: get('wrap') !== 'none',
      vert: get('vert'),
      fontScale: autofit ? n(attr(autofit, 'fontScale'), 100000) / 100000 : 1,
      lnSpcReduction: autofit ? n(attr(autofit, 'lnSpcReduction'), 0) / 100000 : 0,
    };
  }

  textBody(sp, info, chain, ctx, style) {
    const txBody = kid(sp, 'txBody');
    if (!txBody) return '';
    const paras = kids(txBody, 'p');
    if (!paras.some((p) => textOf(p).trim())) return '';
    const bp = this.bodyPr(sp, chain);
    const lists = this.listStyles(sp, info, chain);
    const defaults = { color: style ? this.color(kid(style, 'fontRef')) : null };
    const counters = [];
    let html = '';
    for (const p of paras) html += this.paragraph(p, lists, bp, counters, defaults, ctx);
    const justify = { t: 'flex-start', ctr: 'center', b: 'flex-end', just: 'center', dist: 'center' }[bp.anchor] || 'flex-start';
    const css = [
      'position:absolute', 'inset:0', 'display:flex', 'flex-direction:column', `justify-content:${justify}`,
      `padding:${f1(bp.t)}px ${f1(bp.r)}px ${f1(bp.b)}px ${f1(bp.l)}px`, 'box-sizing:border-box',
      bp.wrap ? 'overflow-wrap:break-word' : 'white-space:nowrap',
    ];
    if (bp.vert && /vert|eaVert/.test(bp.vert) && bp.vert !== 'horz') css.push('writing-mode:vertical-rl');
    return `<div style="${css.join(';')}">${html}</div>`;
  }

  paragraph(p, lists, bp, counters, defaults, ctx) {
    const pPr = kid(p, 'pPr');
    const lvl = n(attr(pPr, 'lvl'));
    const levels = [pPr, ...this.levelNodes(lists, lvl)].filter(Boolean);
    // 図形自身の書式（段落と図形の lstStyle）。図形のスタイルの文字色（fontRef）はこれより弱く、マスターより強い
    const own = new Set([pPr, kid(lists.own, `lvl${lvl + 1}pPr`)].filter(Boolean));
    defaults = { ...defaults, own };
    const pget = (k) => { for (const nd of levels) if (attr(nd, k) != null) return attr(nd, k); return undefined; };
    const pkid = (k) => { for (const nd of levels) { const c = kid(nd, k); if (c) return c; } return null; };
    const css = ['margin:0'];
    const algn = { ctr: 'center', r: 'right', just: 'justify', dist: 'justify', l: 'left' }[pget('algn')];
    if (algn) css.push(`text-align:${algn}`);
    const marL = emuToPx(pget('marL') ?? 0);
    const indent = emuToPx(pget('indent') ?? 0);
    if (marL) css.push(`padding-left:${f1(marL)}px`);
    if (indent) css.push(`text-indent:${f1(indent)}px`);
    const lnSpc = pkid('lnSpc');
    const runSize = (rPr) => this.runProp(rPr, levels, 'sz');
    const baseSz = n(runSize(kid(p, 'endParaRPr')) ?? runSize(kids(p, 'r').map((r) => kid(r, 'rPr'))[0]), 1800) / 100 * bp.fontScale;
    if (lnSpc && kid(lnSpc, 'spcPct')) css.push(`line-height:${(n(attr(kid(lnSpc, 'spcPct'), 'val'), 100000) / 100000 * 1.2 * (1 - bp.lnSpcReduction)).toFixed(3)}`);
    else if (lnSpc && kid(lnSpc, 'spcPts')) css.push(`line-height:${f1(n(attr(kid(lnSpc, 'spcPts'), 'val')) / 100)}pt`);
    else css.push(`line-height:${(1.2 * (1 - bp.lnSpcReduction)).toFixed(3)}`);
    const spc = (k) => {
      const node = pkid(k);
      if (!node) return 0;
      if (kid(node, 'spcPts')) return n(attr(kid(node, 'spcPts'), 'val')) / 100;
      if (kid(node, 'spcPct')) return (n(attr(kid(node, 'spcPct'), 'val')) / 100000) * baseSz * 1.2;
      return 0;
    };
    const before = spc('spcBef'), after = spc('spcAft');
    if (before) css.push(`padding-top:${f1(before * (1 - bp.lnSpcReduction))}pt`);
    if (after) css.push(`padding-bottom:${f1(after)}pt`);
    css.push(`font-size:${f1(baseSz)}pt`);

    let inner = '';
    const hasText = textOf(p).length > 0;
    // 箇条書きの記号
    const bu = levels.map((nd) => kids(nd).find((c) => /^bu(None|Char|AutoNum|Blip)$/.test(c.local))).find(Boolean);
    if (bu && bu.local !== 'buNone' && hasText) {
      let mark = '';
      if (bu.local === 'buChar') {
        const ch = attr(bu, 'char') || '•';
        mark = [...ch].map((x) => BULLET_MAP[x] || x).join('');
      } else if (bu.local === 'buAutoNum') {
        const type = attr(bu, 'type') || 'arabicPeriod';
        const key = `${lvl}:${type}`;
        if (counters[lvl]?.key !== key) counters[lvl] = { key, n: n(attr(bu, 'startAt'), 1) - 1 };
        counters.length = lvl + 1;
        counters[lvl].n += 1;
        mark = (AUTONUM[type]?.[0] || AUTONUM.arabicPeriod[0])(counters[lvl].n);
      } else mark = '•';
      const buClr = pkid('buClr');
      const color = buClr ? this.color(buClr) : null;
      const pct = pkid('buSzPct') ? n(attr(pkid('buSzPct'), 'val'), 100000) / 100000 : 1;
      const firstRun = kids(p, 'r')[0];
      const runCss = this.runCss(kid(firstRun, 'rPr'), levels, bp, defaults);
      const width = Math.max(0, -indent);
      inner += `<span style="${runCss};${color ? `color:${color};` : ''}font-size:${f1(this.sizeOf(kid(firstRun, 'rPr'), levels, bp) * pct)}pt;display:inline-block;min-width:${f1(width)}px;text-indent:0;font-family:${fontStack()}">${escapeHtml(mark)}</span>`;
    } else if (!hasText) counters.length = Math.min(counters.length, lvl);
    for (const c of kids(p)) {
      if (c.local === 'r' || c.local === 'fld') {
        const rPr = kid(c, 'rPr');
        let t = textOf(kid(c, 't'));
        if (c.local === 'fld' && attr(c, 'type') === 'slidenum') t = String((this.s.index || 0) + 1);
        if (!t) continue;
        const cap = this.runProp(rPr, levels, 'cap');
        inner += `<span style="${this.runCss(rPr, levels, bp, defaults)}${cap === 'all' ? ';text-transform:uppercase' : ''}">${escapeHtml(t)}</span>`;
      } else if (c.local === 'br') inner += '<br>';
    }
    if (!hasText) inner = '​';
    return `<p style="${css.join(';')}">${inner}</p>`;
  }

  runProp(rPr, levels, key) {
    if (rPr && attr(rPr, key) != null) return attr(rPr, key);
    for (const nd of levels) { const d = kid(nd, 'defRPr'); if (d && attr(d, key) != null) return attr(d, key); }
    return undefined;
  }

  runKid(rPr, levels, key) {
    if (rPr && kid(rPr, key)) return kid(rPr, key);
    for (const nd of levels) { const d = kid(nd, 'defRPr'); if (d && kid(d, key)) return kid(d, key); }
    return null;
  }

  sizeOf(rPr, levels, bp) { return n(this.runProp(rPr, levels, 'sz'), 1800) / 100 * bp.fontScale; }

  runCss(rPr, levels, bp, defaults = {}) {
    const css = [];
    css.push(`font-size:${f1(this.sizeOf(rPr, levels, bp))}pt`);
    const font = (k) => {
      const node = this.runKid(rPr, levels, k);
      let face = attr(node, 'typeface') || '';
      if (face.startsWith('+')) {
        const t = this.theme.fonts[face.startsWith('+mj') ? 'major' : 'minor'];
        face = face.endsWith('-ea') ? (t.ea || t.latin) : t.latin;
      }
      return face;
    };
    const latin = font('latin') || this.theme.fonts.minor.latin;
    const ea = font('ea') || this.theme.fonts.minor.ea;
    css.push(`font-family:${fontStack(latin, ea)}`);
    const b = this.runProp(rPr, levels, 'b');
    if (b != null ? bool(b) : defaults.bold) css.push('font-weight:bold');
    if (bool(this.runProp(rPr, levels, 'i'))) css.push('font-style:italic');
    const u = this.runProp(rPr, levels, 'u');
    const strike = this.runProp(rPr, levels, 'strike');
    const deco = [u && u !== 'none' && 'underline', strike && strike !== 'noStrike' && 'line-through'].filter(Boolean);
    if (deco.length) css.push(`text-decoration:${deco.join(' ')}`);
    const fill = this.runKid(rPr, levels, 'solidFill') || this.runKid(rPr, levels, 'gradFill');
    let color = fill ? (fill.local === 'gradFill' ? this.color(find(fill, 'gs')) : this.color(fill)) : null;
    // 直接の指定が無ければ、図形のスタイル（fontRef）の色をマスターより先に使う
    if (defaults.color && !(rPr && (kid(rPr, 'solidFill') || kid(rPr, 'gradFill')))
      && ![...(defaults.own || [])].some((nd) => kid(kid(nd, 'defRPr'), 'solidFill'))) color = defaults.color;
    if (color) css.push(`color:${color}`);
    const hl = this.runKid(rPr, levels, 'highlight');
    if (hl) css.push(`background:${this.color(hl)}`);
    const base = n(this.runProp(rPr, levels, 'baseline'));
    if (base) css.push(`vertical-align:${base > 0 ? 'super' : 'sub'};font-size:${f1(this.sizeOf(rPr, levels, bp) * 0.65)}pt`);
    const spc = n(this.runProp(rPr, levels, 'spc'));
    if (spc) css.push(`letter-spacing:${f1(spc / 100)}pt`);
    return css.join(';');
  }

  // ---- 表 ----

  table(tbl, b, ctx) {
    const tblPr = kid(tbl, 'tblPr');
    // 表のスタイル ID が無い表は「スタイルなし」。ID があれば tableStyles.xml か組み込みから引く
    const styleId = textOf(kid(tblPr, 'tableStyleId')).trim();
    const styleNode = kid(tblPr, 'tableStyleId') ? this.tableStyle(styleId) : null;
    const flags = { firstRow: bool(attr(tblPr, 'firstRow')), bandRow: bool(attr(tblPr, 'bandRow')), firstCol: bool(attr(tblPr, 'firstCol')), lastRow: bool(attr(tblPr, 'lastRow')) };
    const cols = kids(kid(tbl, 'tblGrid'), 'gridCol').map((g) => emuToPx(attr(g, 'w')));
    const rows = kids(tbl, 'tr');
    const part = (name) => kid(styleNode, name);
    const partFill = (name) => { const pn = part(name); return pn ? this.fillCss(this.firstFill(xpath(pn, 'tcStyle', 'fill')), ctx.part) : null; };
    const partText = (name) => {
      const tx = kid(part(name), 'tcTxStyle');
      if (!tx) return {};
      return { bold: bool(attr(tx, 'b')), color: this.color(tx) || this.color(kid(tx, 'fontRef')) };
    };
    const sides = (name) => {
      const bdr = xpath(part(name), 'tcStyle', 'tcBdr');
      const o = {};
      for (const [k, css] of [['left', 'left'], ['right', 'right'], ['top', 'top'], ['bottom', 'bottom'], ['insideH', 'insideH'], ['insideV', 'insideV']]) {
        const ln = xpath(bdr, k, 'ln');
        if (ln && !kid(ln, 'noFill')) { const c = this.color(kid(ln, 'solidFill')); if (c) o[css] = `${f1(Math.max(1, emuToPx(attr(ln, 'w') || 12700)))}px solid ${c}`; }
      }
      return o;
    };
    const whole = sides('wholeTbl');
    let html = `<table style="position:absolute;left:0;top:0;border-collapse:collapse;table-layout:fixed;width:${f1(cols.reduce((a, c) => a + c, 0))}px"><colgroup>${cols.map((w) => `<col style="width:${f1(w)}px">`).join('')}</colgroup>`;
    rows.forEach((tr, ri) => {
      html += `<tr style="height:${f1(emuToPx(attr(tr, 'h')))}px">`;
      kids(tr, 'tc').forEach((tc, ci) => {
        if (bool(attr(tc, 'hMerge')) || bool(attr(tc, 'vMerge'))) return;
        const tcPr = kid(tc, 'tcPr');
        const regions = ['wholeTbl'];
        if (flags.bandRow && !(flags.firstRow && ri === 0)) regions.push((flags.firstRow ? ri - 1 : ri) % 2 === 0 ? 'band1H' : 'band2H');
        if (flags.firstCol && ci === 0) regions.push('firstCol');
        if (flags.lastRow && ri === rows.length - 1) regions.push('lastRow');
        if (flags.firstRow && ri === 0) regions.push('firstRow');
        let bg = null, txt = {};
        for (const r of regions) { bg = partFill(r) || bg; txt = { ...txt, ...Object.fromEntries(Object.entries(partText(r)).filter(([, v]) => v)) }; }
        const own = this.firstFill(tcPr);
        if (own) bg = this.fillCss(own, ctx.part);
        const css = ['vertical-align:' + ({ ctr: 'middle', b: 'bottom' }[attr(tcPr, 'anchor')] || 'top'), 'overflow:hidden'];
        css.push(`padding:${f1(emuToPx(attr(tcPr, 'marT') ?? 45720))}px ${f1(emuToPx(attr(tcPr, 'marR') ?? 91440))}px ${f1(emuToPx(attr(tcPr, 'marB') ?? 45720))}px ${f1(emuToPx(attr(tcPr, 'marL') ?? 91440))}px`);
        if (bg && bg !== 'transparent') css.push(`background:${bg}`);
        const borderOf = (k, fallback) => {
          const ln = kid(tcPr, k);
          if (ln) { if (kid(ln, 'noFill')) return 'none'; const c = this.color(kid(ln, 'solidFill')); if (c) return `${f1(Math.max(1, emuToPx(attr(ln, 'w') || 12700)))}px solid ${c}`; }
          return fallback;
        };
        css.push(`border-left:${borderOf('lnL', ci === 0 ? whole.left : whole.insideV) || 'none'}`);
        css.push(`border-right:${borderOf('lnR', whole.right || whole.insideV) || 'none'}`);
        css.push(`border-top:${borderOf('lnT', ri === 0 ? whole.top : whole.insideH) || 'none'}`);
        css.push(`border-bottom:${borderOf('lnB', ri === rows.length - 1 ? whole.bottom : whole.insideH) || 'none'}`);
        const gs = Math.floor(n(attr(tc, 'gridSpan'), 1)), rs = Math.floor(n(attr(tc, 'rowSpan'), 1));
        const span = `${gs > 1 ? ` colspan="${gs}"` : ''}${rs > 1 ? ` rowspan="${rs}"` : ''}`;
        const cellList = kid(kid(tc, 'txBody'), 'lstStyle');
        const lists = [cellList, kid(kid(this.s.master, 'txStyles'), 'otherStyle'), kid(this.pres, 'defaultTextStyle')].filter(Boolean);
        lists.own = cellList;
        const bp = { fontScale: 1, lnSpcReduction: 0 };
        const counters = [];
        let inner = '';
        for (const p of kids(kid(tc, 'txBody'), 'p')) inner += this.paragraph(p, lists, bp, counters, { color: txt.color || null, bold: txt.bold }, ctx);
        html += `<td${span} style="${css.join(';')}">${inner}</td>`;
      });
      html += '</tr>';
    });
    return `${html}</table>`;
  }

  // ---- 背景 ----

  background() {
    for (const [tree, part] of [[this.s.slide, this.s.slidePart], [this.s.layout, this.s.layoutPart], [this.s.master, this.s.masterPart]]) {
      const bg = xpath(tree, 'cSld', 'bg');
      if (!bg) continue;
      const bgPr = kid(bg, 'bgPr');
      if (bgPr) return this.fillCss(this.firstFill(bgPr), part) || '#fff';
      const ref = kid(bg, 'bgRef');
      if (ref) {
        const idx = n(attr(ref, 'idx'));
        const phClr = this.color(ref);
        const style = idx >= 1001 ? this.themeStyle('bg', idx) : this.themeStyle('fill', idx);
        return (style && this.fillCss(style, this.s.masterPart, phClr)) || phClr || '#fff';
      }
    }
    return '#fff';
  }

  render() {
    const sz = kid(this.pres, 'sldSz');
    const width = emuToPx(n(attr(sz, 'cx'), 12192000));
    const height = emuToPx(n(attr(sz, 'cy'), 6858000));
    this.s = this.loadSlide(this.opts.slide);
    if (!this.s) return { html: `<div class="page" style="width:${f1(width)}px;height:${f1(height)}px;background:#fff"></div>`, width, height };
    const tf = { ox: 0, oy: 0, cx: 0, cy: 0, sx: 1, sy: 1 };
    let body = '';
    const showMasterOnLayout = attr(this.s.layout, 'showMasterSp') !== '0';
    const showOnSlide = attr(this.s.slide, 'showMasterSp') !== '0';
    if (showOnSlide && showMasterOnLayout) body += this.shapes(xpath(this.s.master, 'cSld', 'spTree'), { level: 'master', part: this.s.masterPart, tf });
    if (showOnSlide) body += this.shapes(xpath(this.s.layout, 'cSld', 'spTree'), { level: 'layout', part: this.s.layoutPart, tf });
    body += this.shapes(xpath(this.s.slide, 'cSld', 'spTree'), { level: 'slide', part: this.s.slidePart, tf });
    const html = `<div class="page" style="position:relative;width:${f1(width)}px;height:${f1(height)}px;overflow:hidden;background:${this.background()}">${body}</div>`;
    return { html, width, height, slide: this.s.index, slideCount: this.s.count };
  }
}

function pptxToHtml(pkg, opts) {
  return new PptxRenderer(pkg, opts).render();
}

module.exports = { pptxToHtml };
