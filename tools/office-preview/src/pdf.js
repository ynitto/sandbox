'use strict';

// PDF は Electron に入っている PDF ビューア（Chromium の PDFium）に描かせる。
// ツールバーを消して 1 ページを窓に合わせて表示し、周りの灰色の余白を切り落として、
// 描き終わる（続けて撮った 2 枚が同じになる）まで待つ。
// ビューア自身がスクリプトで動くので、この窓だけはスクリプトを止められない（Node の機能は渡さない）。

function isPdf(buf) {
  // 先頭 1 KB のどこかに %PDF- があればよい（前にゴミが付いた PDF も読める）
  return Buffer.isBuffer(buf) && buf.subarray(0, 1024).includes('%PDF-');
}

// 最初に見つかった MediaBox から、ページの大きさ（pt）を推す。
// 圧縮されたオブジェクトの中にしかないときは見つからない（null）。そのときは切り抜きで決める
function guessPageSize(buf) {
  const head = buf.subarray(0, Math.min(buf.length, 4 * 1024 * 1024)).toString('latin1');
  const m = /\/MediaBox\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]/.exec(head);
  if (!m) return null;
  const w = Math.abs(Number(m[3]) - Number(m[1]));
  const h = Math.abs(Number(m[4]) - Number(m[2]));
  if (!(w > 0 && h > 0)) return null;
  const rot = /\/Rotate\s+(-?\d+)/.exec(head);
  return rot && Math.abs(Number(rot[1])) % 180 === 90 ? { width: h, height: w } : { width: w, height: h };
}

// 最初のページの矩形を、ビューアの画面（toBitmap の BGRA）から探す。
// 窓はページより十分横長に取るので、ビューアはページを高さに合わせて中央に置き、左右に背景が残る。
// - 背景は左端の列でいちばん多い色。ページの下と横には背景より少し暗い無彩色の影が付くので、それは背景とみなす
// - 右端の 24 px はスクロールバーなので見ない
// - 背景と違う画素が幅の 15% 以上並ぶ行をページの行とし、上から見て最初のかたまりを 1 ページ目とする
//   （下に次のページが見えていても切り離す。影の縁のような数画素だけの違いは数えない）
function findPageBox(bitmap, width, height) {
  const at = (x, y) => (y * width + x) * 4;
  const counts = new Map();
  for (let y = 0; y < height; y++) {
    const i = at(1, y);
    const key = (bitmap[i + 2] << 16) | (bitmap[i + 1] << 8) | bitmap[i];
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const bgKey = [...counts.entries()].sort((p, q) => q[1] - p[1])[0][0];
  const bg = [(bgKey >> 16) & 255, (bgKey >> 8) & 255, bgKey & 255];
  const differs = (i) => {
    const r = bitmap[i + 2], g = bitmap[i + 1], bl = bitmap[i];
    const neutral = Math.max(r, g, bl) - Math.min(r, g, bl) <= 3;
    if (neutral && r <= bg[0] && bg[0] - r <= 22) return false; // 影
    return Math.abs(r - bg[0]) > 6 || Math.abs(g - bg[1]) > 6 || Math.abs(bl - bg[2]) > 6;
  };
  const usable = Math.max(1, width - 24);
  const minRun = Math.max(4, Math.floor(usable * 0.15));
  let top = -1, bottom = -1, left = width, right = -1;
  for (let y = 0; y < height; y++) {
    let rowLeft = -1, rowRight = -1, count = 0;
    for (let x = 0; x < usable; x++) {
      if (!differs(at(x, y))) continue;
      if (rowLeft < 0) rowLeft = x;
      rowRight = x;
      count++;
    }
    if (count < minRun) {
      if (top >= 0) break; // 1 ページ目の下端
      continue;
    }
    if (top < 0) top = y;
    bottom = y;
    left = Math.min(left, rowLeft);
    right = Math.max(right, rowRight);
  }
  if (top < 0) return null;
  // ページの四隅は角ばっている。ビューアの案内（パスワードの入力・読み込みの失敗）は角が丸いので、隅が背景になる
  const corners = [at(left, top), at(right, top), at(left, bottom), at(right, bottom)];
  return { x: left, y: top, width: right - left + 1, height: bottom - top + 1, square: corners.every(differs) };
}

const { OfficePreviewError } = require('./errors');

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// win にはもう PDF が読み込まれている。描き終わったページだけの NativeImage を返す
// giveUpMs のあいだページが出なければ、壊れているかパスワード付きとみなす（ビューアは入力を待ったまま止まる）
async function capturePdfPage(win, { minWidth, giveUpMs = 8000, encrypted = false }) {
  let previous = null;
  let dialogs = 0;
  const started = Date.now();
  const failure = () => (encrypted
    ? new OfficePreviewError('ENCRYPTED_OR_LEGACY', 'パスワード付きの PDF は表示できない')
    : new OfficePreviewError('BROKEN', 'PDF を表示できない（壊れている）'));
  for (;;) {
    if (!previous && Date.now() - started > giveUpMs) throw failure();
    await delay(previous ? 150 : 300);
    const shot = await win.webContents.capturePage();
    const { width, height } = shot.getSize();
    const box = findPageBox(shot.toBitmap(), width, height);
    if (box && !box.square && box.width >= minWidth && ++dialogs >= 3) throw failure();
    // ページが窓の小さな一部にしか出ていないなら、まだ描いている途中（読み込み中の表示など）
    if (!box || !box.square || box.width < minWidth) { previous = null; continue; }
    dialogs = 0;
    const { x, y, width: w, height: h } = box;
    const page = shot.crop({ x, y, width: w, height: h });
    const bytes = page.toBitmap();
    if (previous && previous.equals(bytes)) return page; // 続けて撮った 2 枚が同じなら描き終わっている
    previous = bytes;
  }
}

function pdfViewerUrl(fileUrl, opts) {
  const page = Number.isInteger(opts.page) && opts.page >= 0 ? opts.page + 1 : 1;
  return `${fileUrl}#page=${page}&toolbar=0&navpanes=0&scrollbar=0&view=Fit`;
}

// 暗号化の辞書を持つか（所有者パスワードだけのものは開けるので、表示に失敗したときの説明にだけ使う）
function hasEncryption(buf) {
  return buf.includes('/Encrypt');
}

module.exports = { isPdf, hasEncryption, guessPageSize, findPageBox, capturePdfPage, pdfViewerUrl };
