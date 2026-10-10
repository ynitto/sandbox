'use strict';

// Office Open XML の入れ物（ZIP）を読む。書かない。
// 中央ディレクトリから一覧を作り、本体は求められたときにだけ展開する。

const zlib = require('zlib');
const { OfficePreviewError } = require('./errors');

const SIG_EOCD = 0x06054b50;
const SIG_ZIP64_LOCATOR = 0x07064b50;
const SIG_ZIP64_EOCD = 0x06064b50;
const SIG_CENTRAL = 0x02014b50;
const SIG_LOCAL = 0x04034b50;

// 展開後の上限。プレビューに要るのは本文と画像の一部なので、これを超えるものは壊れているか爆弾とみなす。
const DEFAULT_LIMITS = { maxEntryBytes: 64 * 1024 * 1024, maxTotalBytes: 256 * 1024 * 1024 };

function findEocd(buf) {
  const stop = Math.max(0, buf.length - 22 - 0xffff);
  for (let i = buf.length - 22; i >= stop; i--) {
    if (buf.readUInt32LE(i) === SIG_EOCD) return i;
  }
  return -1;
}

function readZip64Extra(extra, entry) {
  let p = 0;
  while (p + 4 <= extra.length) {
    const id = extra.readUInt16LE(p);
    const size = extra.readUInt16LE(p + 2);
    if (id === 0x0001) {
      let q = p + 4;
      const big = (field) => {
        if (entry[field] !== 0xffffffff || q + 8 > p + 4 + size) return;
        entry[field] = Number(extra.readBigUInt64LE(q));
        q += 8;
      };
      big('size'); big('compressedSize'); big('offset');
      return;
    }
    p += 4 + size;
  }
}

class ZipArchive {
  constructor(buf, limits = {}) {
    if (!Buffer.isBuffer(buf)) throw new TypeError('ZipArchive には Buffer を渡す');
    if (buf.length >= 8 && buf.readUInt32BE(0) === 0xd0cf11e0) {
      // 古い形式（.doc / .xls / .ppt）と、パスワード付きの OOXML はどちらも複合ファイル形式で包まれている
      throw new OfficePreviewError('ENCRYPTED_OR_LEGACY', 'パスワード付き、または古い形式（.doc/.xls/.ppt）のファイルは読めない');
    }
    this.buf = buf;
    this.limits = { ...DEFAULT_LIMITS, ...limits };
    this.totalBytes = 0;
    this.entries = new Map();
    this.readCentralDirectory();
  }

  readCentralDirectory() {
    const buf = this.buf;
    const eocd = findEocd(buf);
    if (eocd < 0) throw new OfficePreviewError('NOT_ZIP', 'Office のファイルとして読めない（ZIP ではない）');
    let count = buf.readUInt16LE(eocd + 10);
    let offset = buf.readUInt32LE(eocd + 16);
    if ((count === 0xffff || offset === 0xffffffff) && eocd >= 20 && buf.readUInt32LE(eocd - 20) === SIG_ZIP64_LOCATOR) {
      const z = Number(buf.readBigUInt64LE(eocd - 12));
      if (z + 56 > buf.length || buf.readUInt32LE(z) !== SIG_ZIP64_EOCD) throw new OfficePreviewError('NOT_ZIP', 'ZIP64 の終端が壊れている');
      count = Number(buf.readBigUInt64LE(z + 32));
      offset = Number(buf.readBigUInt64LE(z + 48));
    }
    let p = offset;
    for (let i = 0; i < count; i++) {
      if (p + 46 > buf.length || buf.readUInt32LE(p) !== SIG_CENTRAL) throw new OfficePreviewError('NOT_ZIP', 'ZIP の目次が壊れている');
      const flags = buf.readUInt16LE(p + 8);
      const nameLen = buf.readUInt16LE(p + 28);
      const extraLen = buf.readUInt16LE(p + 30);
      const commentLen = buf.readUInt16LE(p + 32);
      const name = buf.toString(flags & 0x800 ? 'utf8' : 'latin1', p + 46, p + 46 + nameLen);
      const entry = {
        name,
        flags,
        method: buf.readUInt16LE(p + 10),
        compressedSize: buf.readUInt32LE(p + 20),
        size: buf.readUInt32LE(p + 24),
        offset: buf.readUInt32LE(p + 42),
      };
      readZip64Extra(buf.subarray(p + 46 + nameLen, p + 46 + nameLen + extraLen), entry);
      // OOXML の部品名は大文字小文字を区別しない
      this.entries.set(name.replace(/^\/+/, '').toLowerCase(), entry);
      p += 46 + nameLen + extraLen + commentLen;
    }
  }

  has(name) { return this.entries.has(String(name).replace(/^\/+/, '').toLowerCase()); }

  names() { return [...this.entries.values()].map((e) => e.name); }

  // 部品の中身を Buffer で返す。無ければ null。
  read(name) {
    const entry = this.entries.get(String(name).replace(/^\/+/, '').toLowerCase());
    if (!entry) return null;
    if (entry.cache) return entry.cache;
    if (entry.flags & 0x1) throw new OfficePreviewError('ENCRYPTED_OR_LEGACY', 'ZIP が暗号化されている');
    if (entry.size > this.limits.maxEntryBytes) throw new OfficePreviewError('TOO_LARGE', `${entry.name} が大きすぎる`);
    if (this.totalBytes + entry.size > this.limits.maxTotalBytes) throw new OfficePreviewError('TOO_LARGE', '展開後の大きさが上限を超える');
    const buf = this.buf;
    const p = entry.offset;
    if (p + 30 > buf.length || buf.readUInt32LE(p) !== SIG_LOCAL) throw new OfficePreviewError('NOT_ZIP', `${entry.name} の位置が壊れている`);
    const start = p + 30 + buf.readUInt16LE(p + 26) + buf.readUInt16LE(p + 28);
    const raw = buf.subarray(start, start + entry.compressedSize);
    let data;
    if (entry.method === 0) data = Buffer.from(raw);
    else if (entry.method === 8) {
      try {
        data = zlib.inflateRawSync(raw, { maxOutputLength: Math.max(1, entry.size) });
      } catch (err) {
        throw new OfficePreviewError('NOT_ZIP', `${entry.name} を展開できない: ${err.message}`);
      }
    } else throw new OfficePreviewError('UNSUPPORTED', `${entry.name} の圧縮方式（${entry.method}）に対応していない`);
    this.totalBytes += data.length;
    entry.cache = data;
    return data;
  }

  readText(name) {
    const data = this.read(name);
    if (!data) return null;
    // UTF-16 の BOM が付いた部品もまれにある
    if (data[0] === 0xff && data[1] === 0xfe) return data.subarray(2).toString('utf16le');
    return data.toString('utf8').replace(/^﻿/, '');
  }
}

module.exports = { ZipArchive };
