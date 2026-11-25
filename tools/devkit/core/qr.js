"use strict";

const ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";
const FORMAT_LEVEL = { L: 1, M: 0, Q: 3, H: 2 };
const TOTAL = [26, 44, 70, 100, 134, 172, 196, 242, 292, 346];
// Each pair is [number of blocks, error correction codewords per block].
const BLOCKS = {
  L: [[1, 7], [1, 10], [1, 15], [1, 20], [1, 26], [2, 18], [2, 20], [2, 24], [2, 30], [4, 18]],
  M: [[1, 10], [1, 16], [1, 26], [2, 18], [2, 24], [4, 16], [4, 18], [4, 22], [5, 22], [5, 26]],
  Q: [[1, 13], [1, 22], [2, 18], [2, 26], [4, 18], [4, 24], [6, 18], [6, 22], [8, 20], [8, 24]],
  H: [[1, 17], [1, 28], [2, 22], [4, 16], [4, 22], [4, 28], [5, 26], [6, 26], [8, 24], [8, 28]],
};
const ALIGNMENT = [
  [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
  [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
];

function append(bits, value, length) {
  for (let shift = length - 1; shift >= 0; shift--) bits.push((value >>> shift) & 1);
}

function segment(text) {
  const bits = [];
  if (text.length && !/[^0-9]/.test(text)) {
    for (const group of text.match(/.{1,3}/g)) append(bits, Number(group), group.length * 3 + 1);
    return { mode: 1, count: text.length, countBits: [10, 12], bits };
  }
  if (text.length && [...text].every((char) => ALPHANUMERIC.includes(char))) {
    for (const pair of text.match(/.{1,2}/g)) {
      const first = ALPHANUMERIC.indexOf(pair[0]);
      append(bits, pair.length === 2 ? first * 45 + ALPHANUMERIC.indexOf(pair[1]) : first, pair.length === 2 ? 11 : 6);
    }
    return { mode: 2, count: text.length, countBits: [9, 11], bits };
  }
  const bytes = Buffer.from(text, "utf8");
  for (const byte of bytes) append(bits, byte, 8);
  return { mode: 4, count: bytes.length, countBits: [8, 16], bits, utf8: bytes.some((byte) => byte >= 128) };
}

function dataWords(input, capacity, countBits) {
  const bits = [];
  if (input.utf8) {
    append(bits, 7, 4); // ECI
    append(bits, 26, 8); // UTF-8
  }
  append(bits, input.mode, 4);
  append(bits, input.count, countBits);
  bits.push(...input.bits);
  append(bits, 0, Math.min(4, capacity * 8 - bits.length));
  append(bits, 0, (8 - bits.length % 8) % 8);
  const words = [];
  for (let offset = 0; offset < bits.length; offset += 8) {
    words.push(bits.slice(offset, offset + 8).reduce((byte, bit) => (byte << 1) | bit, 0));
  }
  for (let pad = 0; words.length < capacity; pad++) words.push(pad % 2 ? 0x11 : 0xec);
  return words;
}

function multiply(a, b) {
  let result = 0;
  while (b) {
    if (b & 1) result ^= a;
    a <<= 1;
    if (a & 256) a ^= 0x11d;
    b >>>= 1;
  }
  return result;
}

function divisor(degree) {
  let polynomial = [1];
  for (let root = 1; degree > 0; degree--, root = multiply(root, 2)) {
    const next = [0, ...polynomial.map((coefficient) => multiply(coefficient, root))];
    for (let index = 0; index < polynomial.length; index++) next[index] ^= polynomial[index];
    polynomial = next;
  }
  return polynomial;
}

function remainder(data, polynomial) {
  const work = [...data, ...Array(polynomial.length - 1).fill(0)];
  for (let index = 0; index < data.length; index++) {
    const factor = work[index];
    for (let j = 0; j < polynomial.length; j++) work[index + j] ^= multiply(polynomial[j], factor);
  }
  return work.slice(data.length);
}

function interleave(blocks) {
  const result = [];
  const length = Math.max(...blocks.map((block) => block.length));
  for (let i = 0; i < length; i++) {
    for (const block of blocks) if (i < block.length) result.push(block[i]);
  }
  return result;
}

function splitBlocks(data, count) {
  const short = Math.floor(data.length / count);
  const shortCount = count - data.length % count;
  const blocks = [];
  for (let i = 0, offset = 0; i < count; i++) {
    const length = short + Number(i >= shortCount);
    blocks.push(data.slice(offset, offset + length));
    offset += length;
  }
  return blocks;
}

function bch(value, polynomial, degree) {
  let remainder = value;
  for (let i = 0; i < degree; i++) {
    remainder <<= 1;
    if (remainder & (1 << degree)) remainder ^= polynomial;
  }
  return (value << degree) | remainder;
}

function format(set, size, level, mask) {
  const value = bch((FORMAT_LEVEL[level] << 3) | mask, 0x537, 10) ^ 0x5412;
  const bit = (index) => Boolean((value >>> index) & 1);
  for (let i = 0; i < 6; i++) set(8, i, bit(i));
  set(8, 7, bit(6));
  set(8, 8, bit(7));
  set(7, 8, bit(8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
  set(8, size - 8, true);
}

function functionPatterns(version, size, level) {
  const grid = Array.from({ length: size }, () => Array(size).fill(null));
  const set = (x, y, dark) => { grid[y][x] = dark; };
  function square(cx, cy, radius, dark) {
    for (let y = Math.max(0, cy - radius); y <= Math.min(size - 1, cy + radius); y++) {
      for (let x = Math.max(0, cx - radius); x <= Math.min(size - 1, cx + radius); x++) {
        set(x, y, dark(Math.max(Math.abs(x - cx), Math.abs(y - cy))));
      }
    }
  }

  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  for (const [x, y] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    square(x, y, 4, (distance) => distance !== 2 && distance !== 4);
  }
  const centers = ALIGNMENT[version - 1];
  const last = centers.at(-1);
  for (const y of centers) {
    for (const x of centers) {
      if ((x === 6 && (y === 6 || y === last)) || (x === last && y === 6)) continue;
      square(x, y, 2, (distance) => distance !== 1);
    }
  }
  format(set, size, level, 0);
  if (version >= 7) {
    const value = bch(version, 0x1f25, 12);
    for (let i = 0; i < 18; i++) {
      const x = size - 11 + i % 3;
      const y = Math.floor(i / 3);
      const dark = Boolean((value >>> i) & 1);
      set(x, y, dark);
      set(y, x, dark);
    }
  }
  return grid;
}

function place(grid, codewords) {
  const size = grid.length;
  const cells = [];
  let index = 0;
  let upwards = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let row = 0; row < size; row++) {
      const y = upwards ? size - 1 - row : row;
      for (const x of [right, right - 1]) {
        if (grid[y][x] !== null) continue;
        const bit = (codewords[index >>> 3] ?? 0) >>> (7 - index % 8);
        cells.push({ x, y, dark: Boolean(bit & 1) });
        index++;
      }
    }
    upwards = !upwards;
  }
  return cells;
}

const MASKS = [
  (x, y) => (x + y) % 2,
  (_x, y) => y % 2,
  (x) => x % 3,
  (x, y) => (x + y) % 3,
  (x, y) => (Math.floor(y / 2) + Math.floor(x / 3)) % 2,
  (x, y) => x * y % 2 + x * y % 3,
  (x, y) => (x * y % 2 + x * y % 3) % 2,
  (x, y) => ((x + y) % 2 + x * y % 3) % 2,
];

function linePenalty(line) {
  const runs = [];
  let dark = false;
  let length = 0;
  let penalty = 0;
  for (const bit of line) {
    if (bit !== dark) {
      runs.push(length);
      dark = bit;
      length = 0;
    }
    length++;
    if (length === 5) penalty += 3;
    else if (length > 5) penalty++;
  }
  runs.push(length);
  if (dark) runs.push(0);
  // Light space outside the symbol counts when checking finder-like patterns.
  runs[0] += line.length;
  runs[runs.length - 1] += line.length;
  for (let i = 1; i + 5 < runs.length; i += 2) {
    const unit = runs[i];
    if (runs[i + 1] !== unit || runs[i + 2] !== 3 * unit ||
        runs[i + 3] !== unit || runs[i + 4] !== unit) continue;
    if (runs[i - 1] >= 4 * unit && runs[i + 5] >= unit) penalty += 40;
    if (runs[i + 5] >= 4 * unit && runs[i - 1] >= unit) penalty += 40;
  }
  return penalty;
}

function penalty(grid) {
  const size = grid.length;
  let score = 0;
  let dark = 0;
  for (let y = 0; y < size; y++) {
    score += linePenalty(grid[y]) + linePenalty(grid.map((row) => row[y]));
    for (let x = 0; x < size; x++) {
      const bit = grid[y][x];
      if (bit) dark++;
      if (x && y && bit === grid[y][x - 1] && bit === grid[y - 1][x] && bit === grid[y - 1][x - 1]) score += 3;
    }
  }
  return score + 10 * Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size));
}

function encodeQr(text, level = "M") {
  if (!Object.hasOwn(BLOCKS, level)) throw new Error("QR の誤り訂正レベルは L/M/Q/H を指定してください");
  if (text.length > 652) throw new Error("テキストが QR バージョン 10 の容量を超えています");
  const input = segment(text);
  for (const [index, [blockCount, eccLength]] of BLOCKS[level].entries()) {
    const version = index + 1;
    const capacity = TOTAL[index] - blockCount * eccLength;
    const countBits = input.countBits[version === 10 ? 1 : 0];
    if (input.count >= 2 ** countBits ||
        (input.utf8 ? 12 : 0) + 4 + countBits + input.bits.length > capacity * 8) continue;
    const dataCodewords = dataWords(input, capacity, countBits);
    const blocks = splitBlocks(dataCodewords, blockCount);
    const polynomial = divisor(eccLength);
    const errorCorrectionCodewords = interleave(blocks.map((block) => remainder(block, polynomial)));
    const interleavedCodewords = [...interleave(blocks), ...errorCorrectionCodewords];
    const size = version * 4 + 17;
    const grid = functionPatterns(version, size, level);
    const cells = place(grid, interleavedCodewords);
    let minimum = Infinity;
    let modules, mask;
    for (const [candidate, predicate] of MASKS.entries()) {
      for (const { x, y, dark } of cells) grid[y][x] = dark !== (predicate(x, y) === 0);
      format((x, y, dark) => { grid[y][x] = dark; }, size, level, candidate);
      const score = penalty(grid);
      if (score < minimum) {
        minimum = score;
        mask = candidate;
        modules = grid.map((row) => row.slice());
      }
    }
    return { version, size, modules, dataCodewords, errorCorrectionCodewords, interleavedCodewords, mask };
  }
  throw new Error("テキストが QR バージョン 10 の容量を超えています");
}

module.exports = { encodeQr };
