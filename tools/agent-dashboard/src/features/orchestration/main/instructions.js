'use strict';

// グローバル指示（agent-instructions 契約）の読み書きと決定的レンダリング、スキル棚卸し。
// 正典: schemas/agent-instructions.schema.json。実体は $AGENT_INSTRUCTIONS_DIR
// （既定 ~/.agents/instructions/）の instructions.json（管理面が原子書換）。
//
// dashboard は instructions.json に「全ノード共通の指示」を書き revision を単調増加させる。
// 各エンジンはこれを描画して実行エージェント（worker / 定常業務）のプロンプトへ前置する。
// agent-flow は run 作成時に描画済みブロックを meta.json へスナップショットし、委譲先ノードへ
// 伝播する（run 単位の一貫性基準）。ここでのレンダラは各エンジン（Python 側）と同一出力になる
// よう決定的に保つ（テストで突き合わせ）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const MARKER_PREFIX = '<!-- agent-instructions';
const HEADING = '## 共通指示（agent-dashboard 管理・全ノード共通）';
const HARD_MAX_CHARS = 8000;
const DEFAULT_MAX_CHARS = 2000;

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function resolveInstructionsDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  return expandHome(
    c.instructionsDir ||
      process.env.AGENT_INSTRUCTIONS_DIR ||
      agentHomeSubdir('instructions')
  );
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function nowStamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function atomicWriteJson(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, target);
}

function clampMaxChars(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MAX_CHARS;
  return Math.min(Math.floor(n), HARD_MAX_CHARS);
}

// instructions.json を読む。無ければ既定（version:1, revision:0, enabled:true, 空）。
function loadInstructions(dir) {
  const raw = readJson(path.join(dir, 'instructions.json'));
  if (!isPlainObject(raw)) {
    return { version: 1, revision: 0, enabled: true, text: '', skills: [], tools: {}, max_chars: DEFAULT_MAX_CHARS };
  }
  return {
    version: 1,
    revision: Number.isFinite(Number(raw.revision)) ? Number(raw.revision) : 0,
    enabled: raw.enabled !== false,
    text: typeof raw.text === 'string' ? raw.text : '',
    skills: Array.isArray(raw.skills) ? raw.skills : [],
    tools: isPlainObject(raw.tools) ? raw.tools : {},
    max_chars: clampMaxChars(raw.max_chars),
    updated_at: raw.updated_at,
    updated_by: raw.updated_by,
    _raw: raw, // additive: 未知キーを保持し、書換時に土台とする
  };
}

// スキル参照を {name, note} へ正規化。文字列 / {name,note} 以外は捨てる。
function normalizeSkill(s) {
  if (typeof s === 'string') {
    const name = s.trim();
    return name ? { name } : null;
  }
  if (isPlainObject(s)) {
    const name = String(s.name || '').trim();
    if (!name) return null;
    const note = String(s.note || '').trim();
    return note ? { name, note } : { name };
  }
  return null;
}

// 契約（agent-instructions.schema.json）→ 決定的テキストブロック。各エンジン（Python 側）と同一出力。
// data が空 / enabled=false / 中身なしのときは空文字（＝注入しない）。
function renderBlock(data, maxCharsOverride) {
  if (!isPlainObject(data) || data.enabled === false) return '';
  const text = String(data.text || '').trim();
  const skills = (Array.isArray(data.skills) ? data.skills : [])
    .map(normalizeSkill)
    .filter(Boolean);
  const tools = isPlainObject(data.tools) ? data.tools : {};
  const allow = Array.isArray(tools.allow) ? tools.allow.filter((t) => String(t || '').trim()) : [];
  const denyNote = String(tools.deny_note || '').trim();
  // 実質的な中身が無ければ注入しない（見出しだけのブロックを撒かない）。
  if (!text && !skills.length && !allow.length && !denyNote) return '';

  const rev = Number.isFinite(Number(data.revision)) ? Number(data.revision) : 0;
  const marker = `${MARKER_PREFIX} rev:${rev} -->`;
  const lines = [marker, HEADING];
  if (text) lines.push(text);
  if (skills.length) {
    lines.push('');
    lines.push('推奨スキル（ローカルに存在する場合のみ適用）:');
    for (const s of skills) lines.push(`- ${s.name}${s.note ? ` — ${s.note}` : ''}`);
  }
  if (allow.length) lines.push(`ツール（許可）: ${allow.join(', ')}`);
  if (denyNote) lines.push(`ツール方針: ${denyNote}`);

  let block = lines.join('\n');
  const cap = clampMaxChars(maxCharsOverride !== undefined ? maxCharsOverride : data.max_chars);
  if (block.length > cap) {
    // マーカー行は必ず残す。cap がマーカーより短い病的ケースはマーカーだけ返す。
    block = cap <= marker.length ? marker : `${block.slice(0, cap - 1).replace(/\s+$/, '')}…`;
  }
  return block;
}

// 既に注入済み（マーカーを含む）なら二重注入しない。target 先頭へブロック + 空行を前置。
function prependBlock(target, block) {
  const t = String(target || '');
  if (!block) return t;
  if (t.includes(MARKER_PREFIX)) return t;
  return t ? `${block}\n\n${t}` : block;
}

// patch をマージして instructions.json を書く。revision を +1 し updated_at/by を刻む。原子書換。
function saveInstructions(cfg, patch) {
  const dir = resolveInstructionsDir(cfg);
  const cur = loadInstructions(dir);
  const p = patch || {};
  const next = { ...(cur._raw || {}) }; // additive: 未知キーを保持
  next.version = 1;
  next.enabled = isPlainObject(cur._raw) ? cur.enabled : true;
  next.text = cur.text;
  next.skills = Array.isArray(cur.skills) ? cur.skills.slice() : [];
  next.tools = isPlainObject(cur.tools) ? { ...cur.tools } : {};
  next.max_chars = cur.max_chars;

  if (p.enabled !== undefined) next.enabled = !!p.enabled;
  if (p.text !== undefined) {
    if (typeof p.text !== 'string') throw new Error('text は文字列で指定してください');
    next.text = p.text;
  }
  if (p.skills !== undefined) {
    if (!Array.isArray(p.skills)) throw new Error('skills は配列で指定してください');
    const out = [];
    for (const s of p.skills) {
      const n = normalizeSkill(s);
      if (n) out.push(n);
    }
    next.skills = out;
  }
  if (p.tools !== undefined) {
    if (!isPlainObject(p.tools)) throw new Error('tools はオブジェクトで指定してください');
    const tools = {};
    if (p.tools.allow !== undefined) {
      if (!Array.isArray(p.tools.allow)) throw new Error('tools.allow は配列で指定してください');
      tools.allow = p.tools.allow.map((t) => String(t)).filter((t) => t.trim());
    } else if (Array.isArray(next.tools.allow)) {
      tools.allow = next.tools.allow;
    }
    if (p.tools.deny_note !== undefined) {
      tools.deny_note = String(p.tools.deny_note);
    } else if (next.tools.deny_note) {
      tools.deny_note = next.tools.deny_note;
    }
    next.tools = tools;
  }
  if (p.max_chars !== undefined) next.max_chars = clampMaxChars(p.max_chars);

  next.revision = cur.revision + 1;
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'instructions.json'), next);
  return loadInstructions(dir);
}

// スキル選択候補の棚卸し。エンジンと同じ探索順で SKILL.md を持つスキル名を列挙する
// （存在確認のみ・中身は読まない）。first-wins で同名は先に現れたディレクトリの定義が勝つ。
function skillsInventory(cfg) {
  const dirs = [];
  const roots = cfg && cfg.projects && Array.isArray(cfg.projects.roots) ? cfg.projects.roots : [];
  for (const root of roots) {
    if (root) dirs.push(path.join(expandHome(String(root)), '.github', 'skills'));
  }
  dirs.push(agentHomeSubdir('skills'));
  dirs.push(path.join(os.homedir(), '.kiro', 'skills'));
  const seen = new Set();
  const out = [];
  for (const dir of dirs) {
    let names;
    try {
      names = fs.readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory());
    } catch {
      continue;
    }
    for (const ent of names.sort((a, b) => a.name.localeCompare(b.name))) {
      const name = ent.name;
      if (seen.has(name)) continue;
      if (!fs.existsSync(path.join(dir, name, 'SKILL.md'))) continue;
      seen.add(name);
      out.push({ name, dir });
    }
  }
  return out;
}

module.exports = {
  resolveInstructionsDir,
  loadInstructions,
  saveInstructions,
  renderBlock,
  prependBlock,
  skillsInventory,
  MARKER_PREFIX,
  HEADING,
  DEFAULT_MAX_CHARS,
  HARD_MAX_CHARS,
};
