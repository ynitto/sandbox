'use strict';
// 文の処理 — 正規化・似ている度合い・肯定否定・数値の条件。
//
// 形態素解析は kuromoji.js（純 JS、IPA 辞書同梱、Apache-2.0）。始める前に一度 init() を待つ。
// 似ている度合いは文字の 2〜3 文字組（n-gram）の余弦で測る。分かち書きに頼らないので表記の揺れに強い。

const path = require('path');

let tokenizer = null;

async function init() {
  if (tokenizer) return;
  const kuromoji = require('kuromoji');
  const dicPath = path.join(path.dirname(require.resolve('kuromoji/package.json')), 'dict');
  tokenizer = await new Promise((resolve, reject) => {
    kuromoji.builder({ dicPath }).build((err, t) => (err ? reject(err) : resolve(t)));
  });
}

function normalize(text) {
  return String(text == null ? '' : text).normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase();
}

const tokenCache = new Map();
function tokens(text) {
  const t = normalize(text);
  if (tokenCache.has(t)) return tokenCache.get(t);
  if (!tokenizer) throw new Error('init() を先に呼ぶ');
  const out = tokenizer.tokenize(t).map((x) => ({
    surface: x.surface_form,
    base: x.basic_form && x.basic_form !== '*' ? x.basic_form : x.surface_form,
    pos: x.pos,
    pos2: x.pos_detail_1,
  }));
  if (tokenCache.size > 50000) tokenCache.clear();
  tokenCache.set(t, out);
  return out;
}

// ---- 値（数値・引用）と、それを伏せた文 ----

const QUOTED = /「[^」]*」|『[^』]*』|"[^"]*"|“[^”]*”|'[^'\s][^']*'/g;
const NUMBER = /\d+(?:[.,]\d+)?/g;

function values(text) {
  const t = normalize(text);
  const quoted = t.match(QUOTED) || [];
  const rest = t.replace(QUOTED, ' ');
  return [...quoted, ...(rest.match(NUMBER) || [])].sort().join('\u0001');
}

function maskValues(text) {
  return normalize(text).replace(QUOTED, '"').replace(NUMBER, '0');
}

function quotedStrings(text) {
  return (String(text || '').normalize('NFKC').match(QUOTED) || []).map((q) => q.slice(1, -1));
}

// ---- 似ている度合い ----

function ngrams(text) {
  const t = text.replace(/\s+/g, '');
  const c = new Map();
  for (const n of [2, 3]) for (let i = 0; i + n <= t.length; i++) { const g = t.slice(i, i + n); c.set(g, (c.get(g) || 0) + 1); }
  if (t.length === 1) c.set(t, 1);
  return c;
}

// L2 正規化した n-gram のベクトル（Map）。IDF は掛けない。LLM が出すケースは同じ言い回しの繰り返しが
// 多く、IDF を掛けると共通部分が軽くなり、1 文字違いの重複まで「似ていない」と出てしまうため。
function vectorize(texts) {
  return texts.map((t) => {
    const v = new Map();
    let sq = 0;
    for (const [f, tf] of ngrams(t)) { const w = 1 + Math.log(tf); v.set(f, w); sq += w * w; }
    const norm = Math.sqrt(sq) || 1;
    for (const [f, w] of v) v.set(f, w / norm);
    return v;
  });
}

function cosine(a, b) {
  if (a.size > b.size) [a, b] = [b, a];
  let s = 0;
  for (const [f, w] of a) { const x = b.get(f); if (x) s += w * x; }
  return s;
}

// 余弦が threshold 以上の組 [i, j, 類似度] を、取りこぼしなく返す。
// 前置フィルタ（All-Pairs）: ベクトルは長さ 1 なので、よく出る特徴から並べた前半のノルムが threshold 未満なら、
// 前半だけで threshold に届くことはない（コーシー＝シュワルツ）。後半だけを索引に入れ、共有する特徴で集めた
// 部分和に前半のノルムを足しても届かない組は捨てる。残った候補は正確な余弦で確かめる。
function similarPairs(vecs, threshold) {
  const n = vecs.length;
  if (n < 2) return [];
  const df = new Map();
  for (const v of vecs) for (const f of v.keys()) df.set(f, (df.get(f) || 0) + 1);
  const postings = new Map();
  const prefixNorm = new Array(n).fill(0);
  vecs.forEach((v, j) => {
    const feats = [...v.entries()].sort((a, b) => (df.get(b[0]) - df.get(a[0])) || (a[0] < b[0] ? -1 : 1));
    let acc = 0; let k = 0;
    while (k < feats.length && Math.sqrt(acc + feats[k][1] ** 2) < threshold) { acc += feats[k][1] ** 2; k++; }
    prefixNorm[j] = Math.sqrt(acc);
    for (const [f, w] of feats.slice(k)) {
      if (!postings.has(f)) postings.set(f, []);
      postings.get(f).push([j, w]);
    }
  });
  const out = [];
  vecs.forEach((v, i) => {
    if (!v.size) return;
    const acc = new Map();
    for (const [f, w] of v) {
      for (const [j, wj] of postings.get(f) || []) if (j > i) acc.set(j, (acc.get(j) || 0) + w * wj);
    }
    for (const [j, partial] of acc) {
      if (partial + prefixNorm[j] >= threshold) {
        const s = cosine(v, vecs[j]);
        if (s >= threshold - 1e-9) out.push([i, j, s]);
      }
    }
  });
  return out;
}

// 2 つの文字列の近さ（0〜1）。編集距離から。仕様・実装の文言に近いものを示すのに使う
function editSimilarity(a, b) {
  if (a === b) return 1;
  const m = a.length; const n = b.length;
  if (!m || !n) return 0;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return 1 - prev[n] / Math.max(m, n);
}

// ---- 肯定・否定 ----

const FUNC_VERBS = new Set(['する', 'れる', 'られる', 'なる', 'いる', 'ある', 'できる', 'くる', 'いく', 'おる', 'せる',
  'させる', 'もらう', 'しまう', 'みる', 'おく', 'くれる', 'やる']);
const NEG_AUX = new Set(['ない', 'ぬ', 'ん']);
const NEG_PREFIX = new Set(['未', '不', '非', '無']);
const STOP_NOUNS = new Set(['こと', 'もの', 'よう', 'ため', 'とき', '時', '場合', 'の', 'ん', 'それ', 'これ', 'そう']);
// 反対語は [正の形, 否定か] にそろえる
const ANTONYMS = {
  無効: ['有効', true], 有効: ['有効', false], 非表示: ['表示', true], 失敗: ['成功', true], 成功: ['成功', false],
  不可: ['可能', true], 不可能: ['可能', true], 可能: ['可能', false], 拒否: ['許可', true], 許可: ['許可', false],
  不一致: ['一致', true], 非活性: ['活性', true], なし: ['あり', true], 無し: ['あり', true], あり: ['あり', false],
  有り: ['あり', false], 不正: ['正当', true], off: ['on', true], on: ['on', false], オフ: ['オン', true],
  オン: ['オン', false], hidden: ['visible', true], visible: ['visible', false], disabled: ['enabled', true],
  enabled: ['enabled', false], invisible: ['visible', true], fail: ['succeed', true], fails: ['succeed', true],
  failed: ['succeed', true], succeed: ['succeed', false], succeeds: ['succeed', false], success: ['succeed', false],
  not: [null, true], no: [null, true], cannot: [null, true], never: [null, true],
};
const CLAUSE_SPLIT = /[。;；\n]|(?<=[れしてりきけせい])[、,]/;

const clauseCache = new Map();
// 文を節に分け、節ごとに { text, core: Set, negative } を返す。
// 否定は偶奇で持つ（「未入力でエラーが表示されない」は 未 と ない で打ち消し合う）
function clauses(text) {
  const key = normalize(text);
  if (clauseCache.has(key)) return clauseCache.get(key);
  const out = [];
  for (let part of key.split(CLAUSE_SPLIT)) {
    part = (part || '').replace(/^[ 、,.]+|[ 、,.]+$/g, '');
    if (!part) continue;
    const core = new Set();
    let neg = 0; let prevPrefix = false;
    for (const t of tokens(part)) {
      const w = t.base;
      if (t.pos === '接頭詞' && NEG_PREFIX.has(t.surface)) { neg ^= 1; prevPrefix = true; continue; }
      if (Object.prototype.hasOwnProperty.call(ANTONYMS, w)) {
        const [canon, isNeg] = ANTONYMS[w];
        if (isNeg) neg ^= 1;
        if (canon) core.add(canon);
      } else if (t.pos === '助動詞' && NEG_AUX.has(w)) neg ^= 1;
      else if (t.pos === '形容詞' && (w === 'ない' || w === '無い')) neg ^= 1;
      else if (t.pos === '名詞' && !['非自立', '代名詞', '接尾'].includes(t.pos2) && !STOP_NOUNS.has(w)) core.add(w);
      else if (t.pos === '名詞' && t.pos2 === '接尾' && prevPrefix) core.add(w);
      else if (t.pos === '動詞' && t.pos2 === '自立' && !FUNC_VERBS.has(w)) core.add(w);
      else if (t.pos === '形容詞' && t.pos2 === '自立') core.add(w);
      prevPrefix = false;
    }
    if (core.size) out.push({ text: part, core, negative: Boolean(neg) });
  }
  if (clauseCache.size > 50000) clauseCache.clear();
  clauseCache.set(key, out);
  return out;
}

function sameCore(a, b) {
  if (a.size === b.size && [...a].every((x) => b.has(x))) return true;
  if (Math.min(a.size, b.size) < 3) return false;
  const inter = [...a].filter((x) => b.has(x)).length;
  return inter / (a.size + b.size - inter) >= 0.8;
}

// 2 つの文の中に、同じ中身で肯定と否定が逆の節があれば [節a, 節b] を返す
function opposite(aText, bText) {
  const ca = clauses(aText); const cb = clauses(bText);
  for (const x of ca) {
    for (const y of cb) {
      if (x.negative === y.negative || !sameCore(x.core, y.core)) continue;
      // 同じ文の中に同じ向きの節もあるなら、言い換えの重なりとみなして食い違いにしない
      if (cb.some((z) => z.negative === x.negative && sameCore(z.core, x.core))) continue;
      if (ca.some((z) => z.negative === y.negative && sameCore(z.core, y.core))) continue;
      return [x, y];
    }
  }
  return null;
}

// ---- 数値の条件 ----

const UNITS = ['文字', '桁', '件', '個', '回', '円', '秒', '分', '時間', '日', '週間', 'か月', 'ヶ月', 'カ月', '年', '歳', '才',
  '名', '人', '行', '列', 'ページ', '枚', '点', 'ポイント', 'バイト', 'byte', 'bytes', 'kb', 'mb', 'gb', 'px', '%', 'ms',
  'characters', 'chars', 'items', 'times', 'seconds'];
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const UNIT = [...UNITS].sort((a, b) => b.length - a.length).map(esc).join('|');
const BOUND = '以上|以下|未満|超え|超|を超える|より多い|より少ない|まで|以内|or more|or less|at least|at most';
const PAT_SUFFIX = new RegExp(`(\\d+)\\s*(${UNIT})?\\s*(${BOUND})`, 'g');
const PAT_PREFIX = new RegExp(`(最大|上限|最長|最多|最小|下限|最短|最少|max(?:imum)?|min(?:imum)?)\\s*(?:は|が|で|:|=)?\\s*(\\d+)\\s*(${UNIT})?`, 'g');
const PAT_POINT = new RegExp(`(\\d+)\\s*(${UNIT})`, 'g');
const UNIT_ONLY = new RegExp(`^(${UNIT}|${BOUND}|最大|最小|上限|下限|\\d+)$`);
const MAX_WORDS = new Set(['以下', '未満', 'まで', '以内', 'より少ない', 'or less', 'at most']);
const UNIT_ALIAS = { 才: '歳', ヶ月: 'か月', カ月: 'か月', chars: '文字', characters: '文字', bytes: 'バイト', byte: 'バイト' };
const unitOf = (u) => { const x = (u || '').trim(); return UNIT_ALIAS[x] || x; };

// 数値の手前の、いちばん近い名詞のまとまり（「パスワードは」なら パスワード）
function subjectOf(prefix) {
  const toks = tokens(prefix.slice(-40));
  const words = []; let started = false;
  for (let k = toks.length - 1; k >= 0; k--) {
    const t = toks[k];
    const afterNumber = k > 0 && toks[k - 1].pos2 === '数';
    const isUnit = UNIT_ONLY.test(t.surface) && (afterNumber || t.pos2 !== '接尾');
    const isNoun = t.pos === '名詞' && !['数', '非自立', '代名詞'].includes(t.pos2) && !['の', 'ん'].includes(t.surface) && !isUnit;
    if (isNoun) { words.push(t.surface); started = true; continue; }
    if (started) break;
    if (t.pos === '助詞' || t.pos === '記号' || t.pos2 === '数' || isUnit || ['の', 'で'].includes(t.surface) || t.pos === '名詞') continue;
    break;
  }
  return words.reverse().join('');
}

// 条件の一覧。kind は max / min（どちらも境界を含む整数に直す）
function constraints(text) {
  const t = normalize(text);
  const out = [];
  for (const m of t.matchAll(PAT_SUFFIX)) {
    const n = Number(m[1]); const unit = unitOf(m[2]); const bound = m[3];
    let kind; let value;
    if (bound === '未満' || bound === 'より少ない') { kind = 'max'; value = n - 1; } else if (MAX_WORDS.has(bound)) { kind = 'max'; value = n; } else if (['超', '超え', 'を超える', 'より多い'].includes(bound)) { kind = 'min'; value = n + 1; } else { kind = 'min'; value = n; }
    out.push({ subject: subjectOf(t.slice(0, m.index)), unit, kind, value, text: m[0] });
  }
  for (const m of t.matchAll(PAT_PREFIX)) {
    const w = m[1];
    const kind = ['最大', '上限', '最長', '最多'].includes(w) || w.startsWith('max') ? 'max' : 'min';
    out.push({ subject: subjectOf(t.slice(0, m.index)), unit: unitOf(m[3]), kind, value: Number(m[2]), text: m[0] });
  }
  const seen = new Set();
  return out.filter((c) => { const k = `${c.subject}|${c.unit}|${c.kind}|${c.value}`; if (seen.has(k)) return false; seen.add(k); return true; });
}

// 文に出てくる「N 単位」の N（条件として書かれたものは除く）
function testedPoints(text, unit) {
  const t = normalize(text);
  const spans = [...t.matchAll(PAT_SUFFIX), ...t.matchAll(PAT_PREFIX)].map((m) => [m.index, m.index + m[0].length]);
  const out = new Set();
  for (const m of t.matchAll(PAT_POINT)) {
    if (unitOf(m[2]) !== unit) continue;
    if (spans.some(([a, b]) => a <= m.index && m.index < b)) continue;
    out.add(Number(m[1]));
  }
  return out;
}

const quotedLengths = (text) => new Set(quotedStrings(text).map((q) => [...q].length));

module.exports = {
  init, normalize, tokens, values, maskValues, quotedStrings, vectorize, cosine, similarPairs, editSimilarity,
  clauses, sameCore, opposite, constraints, testedPoints, quotedLengths, unitOf, UNITS,
};
