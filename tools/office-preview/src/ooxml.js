'use strict';

// 3 形式に共通する道具: 部品の読み出し、関係（.rels）の解決、テーマの色とフォント、色の加工、単位。

const path = require('path').posix;
const { ZipArchive } = require('./zip');
const { parseXml, kid, kids, attr } = require('./xml');
const { OfficePreviewError } = require('./errors');

const REL_OFFICE_DOCUMENT = /\/officeDocument$/;

// 画像は Chromium がそのまま描ける形式だけ data: URI にする（EMF / WMF / TIFF は描けない）
const IMAGE_MIME = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jpe': 'image/jpeg', '.gif': 'image/gif',
  '.bmp': 'image/bmp', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.avif': 'image/avif',
};
const MAX_IMAGE_BYTES = 16 * 1024 * 1024;

class OoxmlPackage {
  constructor(buf, limits) {
    this.zip = new ZipArchive(buf, limits);
    this.xmlCache = new Map();
    this.relsCache = new Map();
    this.imageCache = new Map();
  }

  // 部品を XML の木として返す。無ければ null
  xml(part) {
    if (!part) return null;
    const key = part.replace(/^\/+/, '').toLowerCase();
    if (this.xmlCache.has(key)) return this.xmlCache.get(key);
    const text = this.zip.readText(part);
    const tree = text == null ? null : parseXml(text);
    this.xmlCache.set(key, tree);
    return tree;
  }

  // part の関係を id → { target, type, external } で返す。target はパッケージ内の絶対パス（先頭の / なし）
  rels(part) {
    const relsPart = part ? path.join(path.dirname(part), '_rels', `${path.basename(part)}.rels`) : '_rels/.rels';
    if (this.relsCache.has(relsPart)) return this.relsCache.get(relsPart);
    const map = new Map();
    const tree = this.xml(relsPart);
    for (const r of kids(tree, 'Relationship')) {
      const external = attr(r, 'TargetMode') === 'External';
      const target = attr(r, 'Target') || '';
      map.set(attr(r, 'Id'), {
        type: attr(r, 'Type') || '',
        external,
        target: external ? target : resolvePart(part ? path.dirname(part) : '', target),
      });
    }
    this.relsCache.set(relsPart, map);
    return map;
  }

  relTarget(part, id) {
    const r = id && this.rels(part).get(id);
    return r && !r.external ? r.target : null;
  }

  // 種類（/relationships/xxx の xxx）で最初の関係先を探す
  relByType(part, typeSuffix) {
    for (const r of this.rels(part).values()) {
      if (!r.external && r.type.endsWith(`/${typeSuffix}`)) return r.target;
    }
    return null;
  }

  mainPart() {
    for (const r of this.rels('').values()) {
      if (REL_OFFICE_DOCUMENT.test(r.type) && !r.external) return r.target;
    }
    // .rels が欠けていても、よくある場所を当たる
    for (const p of ['word/document.xml', 'xl/workbook.xml', 'ppt/presentation.xml']) if (this.zip.has(p)) return p;
    throw new OfficePreviewError('UNSUPPORTED', 'Office のファイルの本体が見つからない');
  }

  // 画像部品を data: URI にする。描けない形式や大きすぎるものは null
  imageDataUri(part) {
    if (!part) return null;
    if (this.imageCache.has(part)) return this.imageCache.get(part);
    let uri = null;
    const mime = IMAGE_MIME[path.extname(part).toLowerCase()];
    const entry = this.zip.entries.get(part.toLowerCase());
    if (mime && entry && entry.size <= MAX_IMAGE_BYTES) {
      const data = this.zip.read(part);
      if (data) uri = `data:${mime};base64,${data.toString('base64')}`;
    }
    this.imageCache.set(part, uri);
    return uri;
  }

  // a:blip が指す画像。SVG の図は拡張（svgBlip）に本体があり、r:embed は PNG の代わり（無いこともある）
  blipDataUri(part, blip) {
    if (!blip) return null;
    const svg = findSvgBlip(blip);
    return (svg && this.imageDataUri(this.relTarget(part, attr(svg, 'r:embed'))))
      || this.imageDataUri(this.relTarget(part, attr(blip, 'r:embed')));
  }

  // 保存時に「プレビューの画像を保存する」を選ぶと docProps/thumbnail.* が入る
  embeddedThumbnail() {
    const target = this.relByType('', 'thumbnail') || ['docProps/thumbnail.jpeg', 'docProps/thumbnail.png'].find((p) => this.zip.has(p));
    if (!target) return null;
    const mime = IMAGE_MIME[path.extname(target).toLowerCase()];
    if (!mime || mime === 'image/svg+xml') return null; // EMF / WMF のことも多い
    const data = this.zip.read(target);
    return data ? { mime, data } : null;
  }

  theme(part) {
    const target = part && this.relByType(part, 'theme');
    return readTheme(target ? this.xml(target) : null);
  }
}

function findSvgBlip(blip) {
  for (const ext of kids(kid(blip, 'extLst'), 'ext')) {
    const svg = kid(ext, 'svgBlip');
    if (svg) return svg;
  }
  return null;
}

function resolvePart(baseDir, target) {
  if (target.startsWith('/')) return path.normalize(target).replace(/^\/+/, '');
  return path.normalize(path.join(baseDir || '', target)).replace(/^\/+/, '').replace(/^(\.\.\/)+/, '');
}

// ---- テーマ ------------------------------------------------------------------

const DEFAULT_SCHEME = {
  dk1: '000000', lt1: 'FFFFFF', dk2: '44546A', lt2: 'E7E6E6', accent1: '4472C4', accent2: 'ED7D31', accent3: 'A5A5A5',
  accent4: 'FFC000', accent5: '5B9BD5', accent6: '70AD47', hlink: '0563C1', folHlink: '954F72',
};

function readTheme(tree) {
  const colors = { ...DEFAULT_SCHEME };
  const fonts = { major: { latin: '', ea: '' }, minor: { latin: '', ea: '' } };
  const elements = kid(tree, 'themeElements');
  const scheme = kid(elements, 'clrScheme');
  for (const c of kids(scheme)) {
    const v = kid(c, 'srgbClr') ? attr(kid(c, 'srgbClr'), 'val') : attr(kid(c, 'sysClr'), 'lastClr');
    if (v) colors[c.local] = v.toUpperCase();
  }
  const fontScheme = kid(elements, 'fontScheme');
  for (const which of ['major', 'minor']) {
    const f = kid(fontScheme, `${which}Font`);
    if (!f) continue;
    fonts[which].latin = attr(kid(f, 'latin'), 'typeface') || '';
    fonts[which].ea = attr(kid(f, 'ea'), 'typeface') || '';
    // ea が空のテーマは多い。日本語のスクリプト指定があれば使う
    const jpan = kids(f, 'font').find((x) => attr(x, 'script') === 'Jpan');
    if (!fonts[which].ea && jpan) fonts[which].ea = attr(jpan, 'typeface') || '';
  }
  return { colors, fonts };
}

// ---- 色 ---------------------------------------------------------------------

const PRESET_COLORS = {
  black: '000000', white: 'FFFFFF', red: 'FF0000', green: '008000', blue: '0000FF', yellow: 'FFFF00', cyan: '00FFFF',
  magenta: 'FF00FF', gray: '808080', grey: '808080', darkGray: 'A9A9A9', lightGray: 'D3D3D3', darkBlue: '00008B',
  darkRed: '8B0000', darkGreen: '006400', orange: 'FFA500', purple: '800080', navy: '000080',
};

function hexToRgb(hex) {
  const h = String(hex || '').replace(/^#/, '');
  const v = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h.slice(-6), 16);
  if (!Number.isFinite(v)) return [0, 0, 0];
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function rgbToHex([r, g, b]) {
  const c = (x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0');
  return (c(r) + c(g) + c(b)).toUpperCase();
}

function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h / 6, s, l];
}

function hslToRgb([h, s, l]) {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const f = (t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
}

const clamp01 = (x) => Math.max(0, Math.min(1, x));

// DrawingML の色の加工（lumMod / lumOff / tint / shade / satMod / alpha）
function applyColorMods(hex, mods) {
  let rgb = hexToRgb(hex);
  let alpha = 1;
  for (const m of mods) {
    const v = Number(attr(m, 'val')) / 100000;
    if (!Number.isFinite(v)) continue;
    if (m.local === 'lumMod' || m.local === 'lumOff' || m.local === 'satMod') {
      const hsl = rgbToHsl(rgb);
      if (m.local === 'lumMod') hsl[2] = clamp01(hsl[2] * v);
      else if (m.local === 'lumOff') hsl[2] = clamp01(hsl[2] + v);
      else hsl[1] = clamp01(hsl[1] * v);
      rgb = hslToRgb(hsl);
    } else if (m.local === 'tint') rgb = rgb.map((c) => c + (255 - c) * (1 - v));
    else if (m.local === 'shade') rgb = rgb.map((c) => c * v);
    else if (m.local === 'alpha') alpha = clamp01(v);
  }
  return { hex: rgbToHex(rgb), alpha };
}

// 文書の中の 16 進の色を CSS に入れてよい形にする。6 桁の 16 進でなければ null
function safeHex(v) {
  return typeof v === 'string' && /^[0-9a-f]{6}$/i.test(v) ? `#${v.toUpperCase()}` : null;
}

// Excel の tint（-1〜1）。戻り値はいつも 6 桁の 16 進（文書の値をそのまま返さない）
function applyTint(hex, tint) {
  if (!tint) return rgbToHex(hexToRgb(hex));
  const hsl = rgbToHsl(hexToRgb(hex));
  hsl[2] = tint < 0 ? hsl[2] * (1 + tint) : hsl[2] * (1 - tint) + tint;
  return rgbToHex(hslToRgb(hsl));
}

// a:solidFill などの中身（srgbClr / schemeClr / sysClr / prstClr / scrgbClr）を CSS の色にする。
// clrMap は bg1→lt1 などの読み替え。placeholderColor は phClr の差し替え先。
function drawingColor(node, theme, clrMap = {}, placeholderColor = null) {
  if (!node) return null;
  const c = kids(node).find((x) => /Clr$/.test(x.local)) || (/Clr$/.test(node.local) ? node : null);
  if (!c) return null;
  let hex = null;
  if (c.local === 'srgbClr') hex = attr(c, 'val');
  else if (c.local === 'sysClr') hex = attr(c, 'lastClr') || (attr(c, 'val') === 'window' ? 'FFFFFF' : '000000');
  else if (c.local === 'prstClr') hex = PRESET_COLORS[attr(c, 'val')] || '000000';
  else if (c.local === 'scrgbClr') {
    hex = rgbToHex(['r', 'g', 'b'].map((k) => 255 * Math.pow(clamp01(Number(attr(c, k)) / 100000), 1 / 2.2)));
  } else if (c.local === 'schemeClr') {
    const val = attr(c, 'val');
    if (val === 'phClr') hex = placeholderColor;
    else {
      const mapped = clrMap[val] || { bg1: 'lt1', tx1: 'dk1', bg2: 'lt2', tx2: 'dk2' }[val] || val;
      hex = theme.colors[mapped];
    }
  }
  if (!hex) return null;
  const { hex: out, alpha } = applyColorMods(hex, kids(c));
  return alpha < 1 ? `rgba(${hexToRgb(out).join(',')},${alpha.toFixed(3)})` : `#${out}`;
}

// ---- 単位 -------------------------------------------------------------------

const emuToPx = (emu) => Number(emu || 0) / 9525;      // 914400 EMU = 1 inch = 96 px
const twipToPx = (tw) => Number(tw || 0) / 15;         // 1440 twip = 1 inch

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// CSS の font-family。指定のフォントが無い PC でも日本語が豆腐にならないよう後ろに足す
function fontStack(...names) {
  const out = [];
  for (const n of names) if (n && !out.includes(n)) out.push(n);
  // style="…" の中に入るので、引用符は ' を使う
  const list = out.map((n) => `'${n.replace(/['"\\<>&;]/g, '')}'`);
  list.push("'Yu Gothic'", "'Meiryo'", "'Hiragino Sans'", "'Noto Sans CJK JP'", "'Noto Sans JP'", 'sans-serif');
  return list.join(',');
}

module.exports = {
  OoxmlPackage, resolvePart, readTheme, drawingColor, applyTint, safeHex, applyColorMods, hexToRgb,
  emuToPx, twipToPx, escapeHtml, fontStack,
};
