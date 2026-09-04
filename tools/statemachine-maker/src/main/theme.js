'use strict';

// 見た目のカスタマイズ。画面の色・余白・文字サイズはすべて CSS 変数で、ここが 2 段で上書きする:
//   1. theme.json … 設定画面で選ぶ値（アクセント色・密度・文字サイズ・種類ごとの色）
//   2. custom.css … 人が自由に書く CSS（userData に置く。存在すれば末尾に読み込む）
// どちらも userData に置き、定義（.statemachine/）には混ぜない。

const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  accent: '#3b5bdb',
  density: 'comfortable',   // compact | comfortable
  fontSize: 14,
  kindColors: { browser: '#2f6fed', windows: '#0f8b8d', skill: '#7c3aed', command: '#b45309', agent: '#db2777' },
};

const DENSITIES = ['compact', 'comfortable'];
const COLOR_RE = /^#[0-9a-fA-F]{6}$/;

function themePath(userData) {
  return path.join(userData, 'theme.json');
}

function cssPath(userData) {
  return path.join(userData, 'custom.css');
}

function normalize(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const out = { ...DEFAULTS, kindColors: { ...DEFAULTS.kindColors } };
  if (COLOR_RE.test(String(src.accent || ''))) out.accent = src.accent;
  if (DENSITIES.includes(src.density)) out.density = src.density;
  const size = Number(src.fontSize);
  if (size >= 11 && size <= 20) out.fontSize = size;
  for (const [k, v] of Object.entries(src.kindColors || {})) {
    if (k in out.kindColors && COLOR_RE.test(String(v))) out.kindColors[k] = v;
  }
  return out;
}

function load(userData) {
  let theme;
  try { theme = normalize(JSON.parse(fs.readFileSync(themePath(userData), 'utf8'))); } catch { theme = normalize(null); }
  let customCss;
  try { customCss = fs.readFileSync(cssPath(userData), 'utf8').slice(0, 200000); } catch { customCss = ''; }
  return { theme, customCss, customCssPath: cssPath(userData), dir: userData };
}

function save(userData, raw) {
  const theme = normalize(raw);
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(themePath(userData), `${JSON.stringify(theme, null, 2)}\n`, 'utf8');
  return theme;
}

// custom.css の雛形。無いときに設定画面から作れるようにする（変数の名前をここで教える）。
const CSS_TEMPLATE = `/* statemachine-maker のユーザー CSS。保存して画面の「見た目」→「再読み込み」で反映します。
   色・余白・文字はすべて :root の変数です。例: */
:root {
  /* --accent: #3b5bdb;         操作の色（ボタン・選択枠） */
  /* --bg: #f7f7f8;             画面の地 */
  /* --card: #ffffff;           カードの地 */
  /* --radius: 12px;            角の丸み */
  /* --column-width: 720px;     工程の列の幅 */
  /* --kind-browser: #2f6fed;   種類ごとの色（browser / windows / skill / command / agent） */
}
/* .step-card { box-shadow: none; } */
`;

function ensureCustomCss(userData) {
  const file = cssPath(userData);
  if (!fs.existsSync(file)) {
    fs.mkdirSync(userData, { recursive: true });
    fs.writeFileSync(file, CSS_TEMPLATE, 'utf8');
  }
  return file;
}

// theme.json → :root に当てる CSS 変数。
function cssVariables(theme) {
  const t = normalize(theme);
  const vars = {
    '--accent': t.accent,
    '--font-size': `${t.fontSize}px`,
    '--space': t.density === 'compact' ? '8px' : '12px',
    '--card-pad': t.density === 'compact' ? '10px 14px' : '14px 18px',
  };
  for (const [k, v] of Object.entries(t.kindColors)) vars[`--kind-${k}`] = v;
  return vars;
}

module.exports = { DEFAULTS, DENSITIES, normalize, load, save, ensureCustomCss, cssVariables, CSS_TEMPLATE };
