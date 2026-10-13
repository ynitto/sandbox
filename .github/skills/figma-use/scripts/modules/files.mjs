// 成果物のファイル名と置き場。Windows で使えない文字・予約名・大文字小文字だけの衝突・
// パストラバーサルを防ぐ。規則は実行中の OS に関係なく同じにする（WSL で作った成果物を
// Windows 側で開くことがあるため）。

import fs from 'node:fs';
import path from 'node:path';

const WINDOWS_RESERVED = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i;
const MAX_NAME = 100;

/** 1 つのファイル名部品を安全にする（拡張子は含めない）。 */
export function safeName(raw, fallback = 'item') {
  let name = String(raw ?? '')
    .normalize('NFC')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/g, '')
    .replace(/^\.+/g, '');
  if (!name || /^_+$/.test(name)) name = fallback;
  if (WINDOWS_RESERVED.test(name.split('.')[0])) name = `_${name}`;
  if (name.length > MAX_NAME) name = name.slice(0, MAX_NAME).replace(/[. ]+$/g, '');
  return name;
}

/** node ID をファイル名にする。1:2 -> 1-2、I1:2;3:4 -> I1-2_3-4。 */
export function nodeIdToFileName(id) {
  return safeName(String(id).replace(/:/g, '-').replace(/;/g, '_'), 'node');
}

/** 同じフォルダ内でのファイル名の衝突（大文字小文字の違いだけも含む）を避ける。 */
export class NameRegistry {
  constructor() {
    this.used = new Set();
  }

  take(base, ext = '') {
    let candidate = `${base}${ext}`;
    let n = 2;
    while (this.used.has(candidate.toLowerCase())) {
      candidate = `${base}-${n}${ext}`;
      n += 1;
    }
    this.used.add(candidate.toLowerCase());
    return candidate;
  }
}

/** target が base の内側にあるか。pathImpl に path.win32 / path.posix を渡して両方を検査できる。 */
export function isInside(base, target, pathImpl = path) {
  const rel = pathImpl.relative(pathImpl.resolve(base), pathImpl.resolve(base, target));
  return rel === '' || (!rel.startsWith('..') && !pathImpl.isAbsolute(rel));
}

/** base の内側にだけ書く。外へ出る相対パスは例外にする。 */
export function resolveInside(base, relative, pathImpl = path) {
  if (!isInside(base, relative, pathImpl)) {
    throw new Error(`出力先の外へ書こうとしました: ${relative}`);
  }
  return pathImpl.resolve(base, relative);
}

export function writeFileInside(base, relative, data) {
  const target = resolveInside(base, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, data);
  return target;
}

export function writeJsonInside(base, relative, value) {
  return writeFileInside(base, relative, `${JSON.stringify(value, null, 2)}\n`);
}

/** 成果物の記録用に、出力先からの相対パスを / 区切りで返す。 */
export function portableRelative(base, target) {
  return path.relative(base, target).split(path.sep).join('/');
}

/** 中身の先頭バイトから拡張子を決める（Content-Type が当てにならないことがあるため）。 */
export function sniffExtension(bytes, contentType = '') {
  const b = bytes;
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return '.png';
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return '.jpg';
  if (b.length >= 6 && b.subarray(0, 6).toString('ascii').startsWith('GIF8')) return '.gif';
  if (b.length >= 12 && b.subarray(0, 4).toString('ascii') === 'RIFF' && b.subarray(8, 12).toString('ascii') === 'WEBP') return '.webp';
  if (b.length >= 4 && b.subarray(0, 4).toString('ascii') === '%PDF') return '.pdf';
  const head = b.subarray(0, 256).toString('utf8').trimStart();
  if (head.startsWith('<svg') || (head.startsWith('<?xml') && head.includes('<svg'))) return '.svg';
  if (/png/i.test(contentType)) return '.png';
  if (/jpe?g/i.test(contentType)) return '.jpg';
  if (/svg/i.test(contentType)) return '.svg';
  return '.bin';
}
