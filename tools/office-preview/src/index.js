'use strict';

// docx / xlsx / pptx のプレビュー画像を作る。
//
//   const preview = require('office-preview');
//   const { data, width, height } = await preview.renderPreview('/path/to/file.pptx', { width: 640 });
//
// renderPreview は Electron の main プロセスで、app の ready 後に呼ぶ。
// toHtml は Electron なしで動く（HTML を確かめたいとき・テスト用）。

const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');
const { pathToFileURL, fileURLToPath } = require('url');
const { OoxmlPackage } = require('./ooxml');
const { OfficePreviewError } = require('./errors');
const { docxToHtml } = require('./docx');
const { xlsxToHtml } = require('./xlsx');
const { pptxToHtml } = require('./pptx');

const MAX_FILE_BYTES = 200 * 1024 * 1024;

const EXT_TYPE = {
  '.docx': 'docx', '.docm': 'docx', '.dotx': 'docx', '.dotm': 'docx',
  '.xlsx': 'xlsx', '.xlsm': 'xlsx', '.xltx': 'xlsx', '.xltm': 'xlsx',
  '.pptx': 'pptx', '.pptm': 'pptx', '.potx': 'pptx', '.potm': 'pptx', '.ppsx': 'pptx', '.ppsm': 'pptx',
};
const ROOT_TYPE = { document: 'docx', workbook: 'xlsx', presentation: 'pptx' };

function supports(file) {
  return Boolean(EXT_TYPE[path.extname(String(file || '')).toLowerCase()]);
}

async function loadInput(input) {
  if (Buffer.isBuffer(input)) return { buf: input, name: '' };
  if (typeof input !== 'string' || !input) throw new TypeError('ファイルのパスか Buffer を渡す');
  const st = await fsp.stat(input);
  if (!st.isFile()) throw new OfficePreviewError('UNSUPPORTED', 'ファイルではない');
  if (st.size > MAX_FILE_BYTES) throw new OfficePreviewError('TOO_LARGE', 'ファイルが大きすぎる');
  return { buf: await fsp.readFile(input), name: input };
}

function openPackage(buf) {
  return new OoxmlPackage(buf);
}

// 種類は中身（本体の部品のルート要素）で決める。拡張子が違っていても正しく描く
function detectType(pkg) {
  const root = pkg.xml(pkg.mainPart());
  const type = root && ROOT_TYPE[root.local];
  if (!type) throw new OfficePreviewError('UNSUPPORTED', '対応していない Office のファイル（docx / xlsx / pptx のみ）');
  return type;
}

function wrapDocument(body, zoom = 1) {
  return '<!doctype html><html><head><meta charset="utf-8">'
    // 外への通信とスクリプトを止める。画像は data: だけ
    + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'
    + '<style>html,body{margin:0;padding:0;overflow:hidden;background:#fff}'
    + `body{zoom:${zoom}}p{margin:0}table{border-spacing:0}td{overflow-wrap:break-word}.tab{display:inline-block;width:48px}`
    + 'body{-webkit-font-smoothing:antialiased;font-kerning:normal}</style></head>'
    + `<body>${body}</body></html>`;
}

// ファイルを HTML にする。{ type, html, width, height, ... }（width / height は CSS px）
async function toHtml(input, opts = {}) {
  const { buf } = await loadInput(input);
  return toHtmlFromPackage(openPackage(buf), opts);
}

function toHtmlFromPackage(pkg, opts = {}) {
  const type = detectType(pkg);
  const out = type === 'docx' ? docxToHtml(pkg, opts) : type === 'xlsx' ? xlsxToHtml(pkg, opts) : pptxToHtml(pkg, opts);
  return { type, ...out, html: wrapDocument(out.html, opts.zoom ?? 1), body: out.html };
}

// 保存時に埋め込まれたサムネイル（docProps/thumbnail.jpeg）。無ければ null
async function readEmbeddedThumbnail(input) {
  const { buf } = await loadInput(input);
  return openPackage(buf).embeddedThumbnail();
}

// ---- Electron で描いて撮る ------------------------------------------------------

let electronSession = null;
const allowedFiles = new Set();

function electron() {
  let mod;
  try { mod = require('electron'); } catch { mod = null; }
  if (!mod || typeof mod !== 'object' || !mod.BrowserWindow) {
    throw new OfficePreviewError('NO_ELECTRON', 'renderPreview は Electron の main プロセスで呼ぶ');
  }
  return mod;
}

// パスを比べられる形にする（Windows は大文字小文字と区切りの違いを無視する）
function samePathKey(p) {
  const r = path.resolve(p);
  return process.platform === 'win32' ? r.toLowerCase() : r;
}

function isAllowed(url) {
  if (url.startsWith('data:')) return true;
  if (!url.startsWith('file:')) return false;
  try { return allowedFiles.has(samePathKey(fileURLToPath(url))); } catch { return false; }
}

// 描画専用のセッション。自分で書いた一時ファイルと data: 以外は読ませない
function previewSession(e) {
  if (electronSession) return electronSession;
  const ses = e.session.fromPartition('office-preview', { cache: false });
  ses.webRequest.onBeforeRequest((details, cb) => cb({ cancel: !isAllowed(details.url) }));
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  electronSession = ses;
  return ses;
}

// 同時に開く隠しウィンドウの数を絞る
const queue = { running: 0, waiting: [], limit: 2 };
function slot() {
  if (queue.running < queue.limit) { queue.running++; return Promise.resolve(); }
  return new Promise((resolve) => queue.waiting.push(resolve));
}
function release() {
  const next = queue.waiting.shift();
  if (next) next(); else queue.running--;
}

function setConcurrency(n) { queue.limit = Math.max(1, Math.floor(n) || 1); }

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// 画像を作る。
//   opts.width   出力の幅（px）。省略すると原寸（1 CSS px = 1 px）
//   opts.scale   width の代わりに倍率で指定する
//   opts.format  'png'（既定）か 'jpeg'。opts.quality は jpeg の品質（既定 85）
//   opts.slide   pptx の何枚目か（0 始まり）。opts.sheet は xlsx のシート（番号か名前）
//   opts.viewport xlsx で切り取る大きさ（CSS px、既定 1024×640）
//   opts.timeoutMs 既定 20000
// 返り値: { data: Buffer, mime, width, height, type }
async function renderPreview(input, opts = {}) {
  const e = electron();
  if (!e.app.isReady()) await e.app.whenReady();
  const { buf } = await loadInput(input);
  const pkg = openPackage(buf);
  const page = toHtmlFromPackage(pkg, { ...opts, zoom: 1 });
  for (const k of ['width', 'scale']) {
    if (opts[k] != null && !(Number.isFinite(opts[k]) && opts[k] > 0)) throw new TypeError(`${k} は正の数で指定する`);
  }
  const zoom = opts.width ? opts.width / page.width : (opts.scale || 1);
  const outW = Math.max(1, Math.round(page.width * zoom));
  const outH = Math.max(1, Math.round(page.height * zoom));
  if (outW * outH > 64e6) throw new OfficePreviewError('TOO_LARGE', '出力する画像が大きすぎる');

  await slot();
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'office-preview-'));
  const file = path.join(dir, 'page.html');
  const url = pathToFileURL(file).href;
  let win = null;
  let timer = null;
  try {
    await fsp.writeFile(file, wrapDocument(page.body, zoom));
    allowedFiles.add(samePathKey(file));
    win = new e.BrowserWindow({
      show: false,
      width: outW,
      height: outH,
      useContentSize: true,
      frame: false,
      enableLargerThanScreen: true,
      webPreferences: {
        offscreen: true,
        javascript: false,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true,
        spellcheck: false,
        backgroundThrottling: false,
        session: previewSession(e),
      },
    });
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (ev) => ev.preventDefault());
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new OfficePreviewError('TIMEOUT', '描画が時間内に終わらなかった')), opts.timeoutMs ?? 20000);
    });
    const work = (async () => {
      await win.loadURL(url);
      // 画像の展開とフォントの読み込みを待つ（スクリプトを止めているので、描画が落ち着くまで少し待つ）
      await delay(opts.settleMs ?? 120);
      let image = await win.webContents.capturePage({ x: 0, y: 0, width: outW, height: outH });
      const size = image.getSize();
      if (size.width !== outW || size.height !== outH) image = image.resize({ width: outW, height: outH, quality: 'best' });
      if (image.isEmpty()) throw new OfficePreviewError('BROKEN', '描画した画像が空だった');
      return image;
    })();
    work.catch(() => {}); // 時間切れのあとに失敗しても、未処理の reject にしない
    const image = await Promise.race([work, timeout]);
    const jpeg = opts.format === 'jpeg';
    return {
      data: jpeg ? image.toJPEG(opts.quality ?? 85) : image.toPNG(),
      mime: jpeg ? 'image/jpeg' : 'image/png',
      width: outW,
      height: outH,
      type: page.type,
      ...(page.slideCount != null ? { slide: page.slide, slideCount: page.slideCount } : {}),
      ...(page.sheet != null ? { sheet: page.sheet } : {}),
    };
  } finally {
    clearTimeout(timer);
    allowedFiles.delete(samePathKey(file));
    if (win && !win.isDestroyed()) win.destroy();
    fs.rm(dir, { recursive: true, force: true }, () => {});
    release();
  }
}

module.exports = {
  renderPreview,
  toHtml,
  readEmbeddedThumbnail,
  supports,
  setConcurrency,
  OfficePreviewError,
};
