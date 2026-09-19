'use strict';

const YAML = require('yaml');

function readVersion(content) {
  const front = String(content || '').match(/^\uFEFF?---[^\S\r\n]*\r?\n([\s\S]*?)\r?\n---(?:\s|$)/);
  if (!front) return '';
  try {
    const doc = YAML.parseDocument(front[1]);
    if (doc.errors.length) return '';
    const version = doc.getIn(['metadata', 'version'], true) ?? doc.get('version', true);
    if (!YAML.isScalar(version)) return '';
    // YAML の数値変換で 1.10 を 1.1 に丸めない。
    if (typeof version.value === 'number') return String(version.source || version.value).trim();
    return typeof version.value === 'string' ? version.value.trim() : '';
  } catch { return ''; }
}

function parseVersion(value) {
  const match = String(value || '').trim().match(/^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/);
  if (!match) return null;
  return { numbers: match.slice(1, 4).map((part) => BigInt(part || '0')), pre: match[4]?.split('.') || [] };
}

// 省略された minor/patch は 0。版不明は大小を推測せず null を返す。
function compareVersions(a, b) {
  const left = parseVersion(a); const right = parseVersion(b);
  if (!left || !right) return null;
  for (let i = 0; i < 3; i++) {
    if (left.numbers[i] !== right.numbers[i]) return left.numbers[i] > right.numbers[i] ? 1 : -1;
  }
  if (!left.pre.length && !right.pre.length) return 0;
  if (!left.pre.length) return 1;
  if (!right.pre.length) return -1;
  for (let i = 0; i < Math.max(left.pre.length, right.pre.length); i++) {
    const x = left.pre[i]; const y = right.pre[i];
    if (x === y) continue;
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x); const ny = /^\d+$/.test(y);
    if (nx && ny) {
      if (BigInt(x) === BigInt(y)) continue;
      return BigInt(x) > BigInt(y) ? 1 : -1;
    }
    if (nx !== ny) return nx ? -1 : 1;
    return x > y ? 1 : -1;
  }
  return 0;
}

module.exports = { readVersion, compareVersions };
