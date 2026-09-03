'use strict';

// 文書ルール（Document Rule）— 1 物理ファイル（Markdown）の読み書き。
//
// 書式の正典はここ 1 か所。画面もプロンプトも、この節の並びと見出しを引く。
//
//   ---
//   name: 提案書
//   formats: docx, pptx
//   ---
//   # 文書ルール: 提案書
//   ## 対象と目的
//   ## テンプレート
//   ## 定型と体裁
//   ## 記述内容
//   ## 注意点
//   ## 区分
//   - 概要 — 全体像を 1 段落で
//
// 「区分」は意味的区分（章立て）の一覧で、区分ごと作成モードの単位になる。
// 節が欠けたファイルも読める（欠けは missing に返す）——外部で書いたルールを弾かない。

const fs = require('fs');
const path = require('path');

const RULE_SECTIONS = [
  ['purpose', '対象と目的', 'この文書は誰に何のために読ませるか。読者・場面・達成したい効果。'],
  ['template', 'テンプレート', '雛形や既存文書があればその場所と使い方。無ければ構成の骨子。'],
  ['format', '定型と体裁', '書式・分量・用字用語・図表の扱いなど、見た目と形の決まり。'],
  ['content', '記述内容', '各部分に何を書くか。必ず書くこと、書いてはいけないこと。'],
  ['cautions', '注意点', '過去の指摘・つまずき・レビューで見られる観点。'],
  ['divisions', '区分', '意味的区分（章立て）の一覧。1 行 1 区分「- 名前 — 説明」。区分ごと作成の単位。'],
];

const formats = require('./formats');
const { FORMATS, normalizeFormats, formatLabel } = formats;

// ファイル名にできる識別子。日本語はそのまま残し、区切り・記号だけを '-' にする。
function slugify(name) {
  const s = String(name || '').trim()
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .replace(/-{2,}/g, '-');
  return s.slice(0, 80);
}

function parseFrontMatter(text) {
  const src = String(text || '').replace(/\r\n/g, '\n');
  const m = src.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return { meta: {}, body: src };
  const meta = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^([A-Za-z_][\w-]*)\s*:\s*(.*)$/);
    if (kv) meta[kv[1]] = kv[2].trim().replace(/^\[|\]$/g, '');
  }
  return { meta, body: src.slice(m[0].length) };
}

// 「- 名前 — 説明」「- 名前: 説明」「1. 名前 — 説明」を区分として読む。
function parseDivisions(text) {
  const out = [];
  for (const raw of String(text || '').split('\n')) {
    const m = raw.match(/^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$/);
    if (!m) continue;
    const item = m[1].replace(/^\*\*(.+?)\*\*/, '$1');
    const split = item.match(/^(.+?)\s*(?:[—–\-:：]|--)\s+(.+)$/);
    const title = (split ? split[1] : item).trim();
    if (!title) continue;
    out.push({ title, note: split ? split[2].trim() : '' });
  }
  return out;
}

function parseRule(text) {
  const { meta, body } = parseFrontMatter(text);
  const sections = {};
  const order = [];
  let current = null;
  let title = '';
  for (const line of body.split('\n')) {
    const h1 = line.match(/^#\s+(.+?)\s*$/);
    if (h1 && !current) {
      title = h1[1].replace(/^文書ルール\s*[:：]\s*/, '').trim();
      continue;
    }
    const h2 = line.match(/^##\s+(.+?)\s*$/);
    if (h2) {
      const heading = h2[1].trim();
      const row = RULE_SECTIONS.find(([, label]) => label === heading);
      current = row ? row[0] : `extra:${heading}`;
      if (!(current in sections)) {
        sections[current] = '';
        order.push(current);
      }
      continue;
    }
    if (current) sections[current] += `${line}\n`;
  }
  for (const key of Object.keys(sections)) sections[key] = sections[key].trim();
  const missing = RULE_SECTIONS.map(([k]) => k).filter((k) => !String(sections[k] || '').trim());
  const name = String(meta.name || title || '').trim();
  return {
    name,
    formats: normalizeFormats(meta.formats),
    sections,
    order,
    divisions: parseDivisions(sections.divisions || ''),
    missing,
    meta,
  };
}

// 画面の入力（name / formats / 節ごとの本文）からファイル本文を組む。
function serializeRule({ name, formats, sections }) {
  const n = String(name || '').trim();
  const f = normalizeFormats(formats);
  const lines = ['---', `name: ${n}`, `formats: ${f.join(', ')}`, '---', `# 文書ルール: ${n}`, ''];
  for (const [key, label, help] of RULE_SECTIONS) {
    const body = String((sections || {})[key] || '').trim();
    lines.push(`## ${label}`, '', body || `（未記入: ${help}）`, '');
  }
  return `${lines.join('\n').trimEnd()}\n`;
}

// エージェントの応答（ルール本文）を正規化する。front matter が無ければ足し、
// 節が欠けていれば空節を足す——欠けたまま保存すると画面の節表示が歯抜けになる。
function normalizeRuleText(text, { name = '', formats = [] } = {}) {
  const parsed = parseRule(text);
  const merged = { ...parsed.sections };
  for (const key of parsed.missing) if (!merged[key]) merged[key] = '';
  const extras = parsed.order.filter((k) => k.startsWith('extra:'));
  let out = serializeRule({
    name: parsed.name || name,
    formats: parsed.formats.length ? parsed.formats : formats,
    sections: merged,
  });
  for (const key of extras) {
    const body = String(merged[key] || '').trim();
    if (body) out += `\n## ${key.slice('extra:'.length)}\n\n${body}\n`;
  }
  return out;
}

function ruleSummary(file, text) {
  const parsed = parseRule(text);
  let mtime = '';
  try {
    mtime = fs.statSync(file).mtime.toISOString();
  } catch { /* 一覧の鮮度情報は無くても困らない */ }
  return {
    file,
    id: path.basename(file, '.md'),
    name: parsed.name || path.basename(file, '.md'),
    formats: parsed.formats,
    divisions: parsed.divisions.length,
    missing: parsed.missing,
    updatedAt: mtime,
  };
}

function listRules(dir) {
  let names;
  try {
    names = fs.readdirSync(dir).filter((n) => n.toLowerCase().endsWith('.md') && !n.startsWith('.'));
  } catch {
    return [];
  }
  const out = [];
  for (const n of names.sort((a, b) => a.localeCompare(b, 'ja'))) {
    const file = path.join(dir, n);
    try {
      out.push(ruleSummary(file, fs.readFileSync(file, 'utf8')));
    } catch { /* 読めないファイルは一覧から外す（OS で直す） */ }
  }
  return out;
}

// ルールファイルの置き場の外を指す参照を断る（画面から任意パスを読ませない）。
function resolveRuleFile(dir, file) {
  const value = String(file || '').trim();
  if (!value) throw new Error('文書ルールを選択してください');
  const full = path.isAbsolute(value) ? path.normalize(value) : path.join(dir, value);
  const base = path.normalize(dir);
  const inside = full === base || full.startsWith(base.endsWith(path.sep) ? base : base + path.sep);
  if (!inside || !full.toLowerCase().endsWith('.md')) {
    throw new Error(`文書ルールのフォルダにあるファイルだけを使えます: ${value}`);
  }
  return full;
}

function readRule(dir, file) {
  const full = resolveRuleFile(dir, file);
  const text = fs.readFileSync(full, 'utf8');
  return { ...ruleSummary(full, text), content: text, parsed: parseRule(text) };
}

// 空いているファイル名（同名があれば -2, -3 …）。
function availableRuleFile(dir, name) {
  const slug = slugify(name) || 'rule';
  let candidate = path.join(dir, `${slug}.md`);
  let i = 2;
  while (fs.existsSync(candidate)) {
    candidate = path.join(dir, `${slug}-${i}.md`);
    i += 1;
  }
  return candidate;
}

function saveRule(dir, { file, name, content }) {
  const text = String(content || '');
  const parsed = parseRule(text);
  const ruleName = String(name || parsed.name || '').trim();
  if (!ruleName) throw new Error('ルールの名前を入力してください');
  if (!text.trim()) throw new Error('ルールの本文を入力してください');
  fs.mkdirSync(dir, { recursive: true });
  const target = file ? resolveRuleFile(dir, file) : availableRuleFile(dir, ruleName);
  const normalized = normalizeRuleText(text, { name: ruleName, formats: parsed.formats });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, normalized, 'utf8');
  fs.renameSync(tmp, target);
  return { ...ruleSummary(target, normalized), content: normalized, created: !file };
}

module.exports = {
  RULE_SECTIONS,
  FORMATS,
  normalizeFormats,
  formatLabel,
  slugify,
  parseFrontMatter,
  parseDivisions,
  parseRule,
  serializeRule,
  normalizeRuleText,
  ruleSummary,
  listRules,
  resolveRuleFile,
  readRule,
  availableRuleFile,
  saveRule,
};
