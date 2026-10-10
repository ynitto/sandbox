'use strict';

// docx / xlsx / pptx / pdf のプレビュー画像を作る。
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
const { isPdf, hasEncryption, guessPageSize, capturePdfPage, pdfViewerUrl } = require('./pdf');

const MAX_FILE_BYTES = 200 * 1024 * 1024;

const EXT_TYPE = {
  '.docx': 'docx', '.docm': 'docx', '.dotx': 'docx', '.dotm': 'docx',
  '.xlsx': 'xlsx', '.xlsm': 'xlsx', '.xltx': 'xlsx', '.xltm': 'xlsx',
  '.pptx': 'pptx', '.pptm': 'pptx', '.potx': 'pptx', '.potm': 'pptx', '.ppsx': 'pptx', '.ppsm': 'pptx',
  '.pdf': 'pdf',
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

// ファイルを HTML にする。{ type, html, width, height, ... }（width / height は CSS px）。PDF は HTML にしない
async function toHtml(input, opts = {}) {
  const { buf } = await loadInput(input);
  if (isPdf(buf)) throw new OfficePreviewError('UNSUPPORTED', 'PDF は HTML にしない（renderPreview で画像にする）');
  return toHtmlFromPackage(openPackage(buf), opts);
}

function toHtmlFromPackage(pkg, opts = {}) {
  const type = detectType(pkg);
  const out = type === 'docx' ? docxToHtml(pkg, opts) : type === 'xlsx' ? xlsxToHtml(pkg, opts) : pptxToHtml(pkg, opts);
  return { type, ...out, html: wrapDocument(out.html, opts.zoom ?? 1), body: out.html };
}

// 保存時に埋め込まれたサムネイル（docProps/thumbnail.jpeg）。無ければ null（PDF も null）
async function readEmbeddedThumbnail(input) {
  const { buf } = await loadInput(input);
  if (isPdf(buf)) return null;
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
  // PDF ビューア（Chromium に組み込みの拡張）の部品
  if (url.startsWith('chrome-extension://') || url.startsWith('chrome://')) return true;
  if (!url.startsWith('file:')) return false;
  try { return allowedFiles.has(samePathKey(fileURLToPath(url))); } catch { return false; }
}

// 描画専用のセッション。自分で書いた一時ファイル・data:・PDF ビューア以外は読ませない
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

// 一時フォルダに 1 つファイルを書き、それだけを読める隠しウィンドウで work(win, url) を動かす。
// 時間切れ・失敗のどちらでも、ウィンドウと一時フォルダを片付ける
async function withWindow(e, { fileName, content, width, height, pdf, timeoutMs }, work) {
  await slot();
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'office-preview-'));
  const file = path.join(dir, fileName);
  let win = null;
  let timer = null;
  try {
    await fsp.writeFile(file, content);
    allowedFiles.add(samePathKey(file));
    win = new e.BrowserWindow({
      show: false,
      width,
      height,
      useContentSize: true,
      frame: false,
      enableLargerThanScreen: true,
      webPreferences: {
        offscreen: true,
        // Office の文書は HTML に組み直したものなので、スクリプトは要らない。
        // PDF ビューアはスクリプトで動くので、PDF のときだけ許す（Node の機能はどちらも渡さない）
        javascript: Boolean(pdf),
        plugins: Boolean(pdf),
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
      timer = setTimeout(() => reject(new OfficePreviewError('TIMEOUT', '描画が時間内に終わらなかった')), timeoutMs ?? 20000);
    });
    const running = work(win, pathToFileURL(file).href);
    running.catch(() => {}); // 時間切れのあとに失敗しても、未処理の reject にしない
    return await Promise.race([running, timeout]);
  } finally {
    clearTimeout(timer);
    allowedFiles.delete(samePathKey(file));
    if (win && !win.isDestroyed()) win.destroy();
    fs.rm(dir, { recursive: true, force: true }, () => {});
    release();
  }
}

function encode(image, opts) {
  const jpeg = opts.format === 'jpeg';
  return { data: jpeg ? image.toJPEG(opts.quality ?? 85) : image.toPNG(), mime: jpeg ? 'image/jpeg' : 'image/png' };
}

// 画像を作る。
//   opts.width   出力の幅（px）。省略すると原寸（1 CSS px = 1 px、PDF は 1 pt = 96/72 px）
//   opts.scale   width の代わりに倍率で指定する
//   opts.format  'png'（既定）か 'jpeg'。opts.quality は jpeg の品質（既定 85）
//   opts.slide   pptx の何枚目か（0 始まり）。opts.sheet は xlsx のシート（番号か名前）。opts.page は PDF のページ（0 始まり）
//   opts.viewport xlsx で切り取る大きさ（CSS px、既定 1024×640）
//   opts.timeoutMs 既定 20000
// 返り値: { data: Buffer, mime, width, height, type }
async function renderPreview(input, opts = {}) {
  const e = electron();
  for (const k of ['width', 'scale']) {
    if (opts[k] != null && !(Number.isFinite(opts[k]) && opts[k] > 0)) throw new TypeError(`${k} は正の数で指定する`);
  }
  if (!e.app.isReady()) await e.app.whenReady();
  const { buf } = await loadInput(input);
  return isPdf(buf) ? renderPdf(e, buf, opts) : renderOffice(e, buf, opts);
}

async function renderOffice(e, buf, opts) {
  const page = toHtmlFromPackage(openPackage(buf), { ...opts, zoom: 1 });
  const zoom = opts.width ? opts.width / page.width : (opts.scale || 1);
  const outW = Math.max(1, Math.round(page.width * zoom));
  const outH = Math.max(1, Math.round(page.height * zoom));
  if (outW * outH > 64e6) throw new OfficePreviewError('TOO_LARGE', '出力する画像が大きすぎる');
  const image = await withWindow(e, {
    fileName: 'page.html', content: wrapDocument(page.body, zoom), width: outW, height: outH, timeoutMs: opts.timeoutMs,
  }, async (win, url) => {
    await win.loadURL(url);
    // 画像の展開とフォントの読み込みを待つ（スクリプトを止めているので、描画が落ち着くまで少し待つ）
    await delay(opts.settleMs ?? 120);
    let shot = await win.webContents.capturePage({ x: 0, y: 0, width: outW, height: outH });
    const size = shot.getSize();
    if (size.width !== outW || size.height !== outH) shot = shot.resize({ width: outW, height: outH, quality: 'best' });
    if (shot.isEmpty()) throw new OfficePreviewError('BROKEN', '描画した画像が空だった');
    return shot;
  });
  return {
    ...encode(image, opts),
    width: outW,
    height: outH,
    type: page.type,
    ...(page.slideCount != null ? { slide: page.slide, slideCount: page.slideCount } : {}),
    ...(page.sheet != null ? { sheet: page.sheet } : {}),
  };
}

async function renderPdf(e, buf, opts) {
  // 原寸は 1 pt = 96/72 px。大きさが読めない PDF は A4 とみなす。
  // 読めるのは最初に見つかった MediaBox なので、2 ページ目以降は大きさが分からないものとして扱う
  const known = (opts.page || 0) === 0 ? guessPageSize(buf) : null;
  const pt = known || { width: 595, height: 842 };
  const natural = { width: (pt.width * 96) / 72, height: (pt.height * 96) / 72 };
  const zoom = opts.width ? opts.width / natural.width : (opts.scale || 1);
  const outW = Math.max(1, Math.round(natural.width * zoom));
  // ページは出力より 1 割大きく描かせてから縮める。窓はページより十分横長にして、ビューアにページを
  // 高さで合わせて中央へ置かせる（左右に背景が残り、ページを確実に切り出せる）。
  // 大きさが読めないときは A4 縦の高さで、横長のページも高さで収まるよう幅を 3 倍取る
  const rw = outW * 1.1;
  const winH = Math.min(8000, Math.round(rw * (pt.height / pt.width)) + 60);
  const winW = Math.min(8000, Math.round(rw * (known ? 1.5 : 3)) + 60);
  if (winW * winH > 64e6) throw new OfficePreviewError('TOO_LARGE', '出力する画像が大きすぎる');
  const image = await withWindow(e, {
    fileName: 'page.pdf', content: buf, width: winW, height: winH, pdf: true, timeoutMs: opts.timeoutMs,
  }, async (win, url) => {
    await win.loadURL(pdfViewerUrl(url, opts));
    return capturePdfPage(win, { minWidth: Math.min(outW, winW) * 0.5, encrypted: hasEncryption(buf) });
  });
  const size = image.getSize();
  const outH = Math.max(1, Math.round((size.height * outW) / size.width));
  const fitted = size.width === outW ? image : image.resize({ width: outW, height: outH, quality: 'best' });
  return { ...encode(fitted, opts), width: outW, height: outH, type: 'pdf', page: Number.isInteger(opts.page) ? opts.page : 0 };
}

module.exports = {
  renderPreview,
  toHtml,
  readEmbeddedThumbnail,
  supports,
  setConcurrency,
  OfficePreviewError,
};
