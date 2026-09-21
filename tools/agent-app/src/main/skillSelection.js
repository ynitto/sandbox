'use strict';

const MAX_SELECTED = 3;

function cleanName(value) {
  return String(value || '').trim().replace(/^[$/]+/, '').split(/\s+/, 1)[0];
}

function bigrams(value) {
  const text = String(value || '').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
  const set = new Set();
  for (let index = 0; index < text.length - 1; index += 1) set.add(text.slice(index, index + 2));
  return set;
}

function relevance(text, skill) {
  const source = `${skill.name || ''} ${skill.description || ''} ${(skill.tags || []).join(' ')}`.toLowerCase();
  const input = String(text || '').toLowerCase();
  if (!input.trim()) return 0;
  if (input.includes(String(skill.name || '').toLowerCase())) return 10;
  const wanted = bigrams(input);
  const offered = bigrams(source);
  let hits = 0;
  for (const token of wanted) if (offered.has(token)) hits += 1;
  return wanted.size ? hits / wanted.size : 0;
}

// 依頼の本文に名前がそのまま出ているスキル（「依頼で明示」。判定には訊かない）。
function mentioned(text, skills) {
  return (Array.isArray(skills) ? skills : []).filter((skill) => skill && new RegExp(`(?:^|\\s)[$/]?${String(skill.name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\s|$)`, 'i').test(String(text)));
}

function item(skill, role, reason) {
  return { name: skill.name, role, reason, content: String(skill.content || ''), path: String(skill.path || '') };
}

// judged … 振り分け（agent-herd route）が「添えると質が上がる」と判定したスキル [{ name, probability }]。
// 配列なら判定が決めたことになり、bigram の 1 位は使わない（判定が no と言った候補を拾い直さない）。
// null なら判定は無く、従来どおり文字列の一致で 1 件。
function select({ mode = 'auto', text = '', requested = [], candidates = [], catalog = [], judged = null } = {}) {
  const available = new Map((Array.isArray(catalog) ? catalog : []).map((skill) => [cleanName(skill && skill.name), skill]));
  const pool = [...new Set((Array.isArray(candidates) ? candidates : []).map(cleanName).filter(Boolean))]
    .map((name) => available.get(name)).filter(Boolean);
  const asked = [...new Set((Array.isArray(requested) ? requested : []).map(cleanName).filter(Boolean))];
  const missing = asked.filter((name) => !available.has(name));
  if (mode === 'manual' && missing.length) {
    const error = new Error(`選択したスキルが見つかりません: ${missing.join(', ')}`);
    error.code = 'SKILL_NOT_FOUND';
    throw error;
  }
  if (mode === 'off') return { mode: 'off', requested: asked, selected: [], omitted: [] };
  if (mode === 'manual') {
    return { mode, requested: asked, selected: asked.slice(0, MAX_SELECTED).map((name, index) => item(available.get(name), index ? 'support' : 'primary', '手動選択')), omitted: asked.slice(MAX_SELECTED).map((name) => ({ name, reason: '最大3件' })) };
  }
  const explicit = mentioned(text, [...available.values()]);
  const ranked = pool.filter((skill) => !explicit.includes(skill)).map((skill) => ({ skill, score: relevance(text, skill) }))
    .filter((entry) => entry.score >= 0.08).sort((a, b) => b.score - a.score || a.skill.name.localeCompare(b.skill.name));
  const chosen = [];
  for (const skill of explicit) if (chosen.length < MAX_SELECTED) chosen.push(item(skill, chosen.length ? 'support' : 'primary', '依頼で明示'));
  if (Array.isArray(judged)) {
    const byName = new Map(pool.map((skill) => [skill.name, skill]));
    for (const entry of [...judged].sort((a, b) => Number(b && b.probability) - Number(a && a.probability))) {
      const skill = byName.get(cleanName(entry && entry.name));
      if (!skill || chosen.some((picked) => picked.name === skill.name) || chosen.length >= MAX_SELECTED) continue;
      chosen.push(item(skill, chosen.length ? 'support' : 'primary', `判定で選択 ${Number(entry.probability || 0).toFixed(2)}`));
    }
  } else if (ranked.length && chosen.length < MAX_SELECTED) chosen.push(item(ranked[0].skill, chosen.length ? 'support' : 'primary', '依頼内容に一致'));
  const createsOutput = /(作|変更|修正|改善|実装|作成|build|fix|implement|edit)/i.test(String(text));
  const checking = pool.find((skill) => skill.name === 'self-checking');
  if (createsOutput && checking && !chosen.some((entry) => entry.name === checking.name) && chosen.length < MAX_SELECTED) {
    chosen.push(item(checking, 'support', '成果物の検証'));
  }
  return { mode: 'auto', requested: asked, selected: chosen, omitted: [] };
}

function deliver(selection, spec = {}, { budgetChars = 12000 } = {}) {
  const selected = Array.isArray(selection && selection.selected) ? selection.selected : [];
  const information = selected.map((entry) => ({
    type: 'skill', title: entry.name, status: 'success', detail: `${entry.role === 'primary' ? 'プライマリ' : '補助'} · ${entry.reason}`,
  }));
  if (spec.slashNative) {
    const prefix = spec.skillCommandPrefix || '/';
    return { commands: selected.map((entry) => `${prefix}${entry.name}`), instruction: '', information, omitted: selection.omitted || [], delivery: 'native-command' };
  }
  const included = [];
  const omitted = [...(selection.omitted || [])];
  let used = 0;
  for (const entry of selected) {
    const content = String(entry.content || '');
    if (!content || (used && used + content.length > budgetChars)) {
      omitted.push({ name: entry.name, reason: content ? 'コンテキスト予算' : '本文を読めない' });
      continue;
    }
    included.push(`## 適用スキル: ${entry.name}\n${content}`);
    used += content.length;
  }
  for (const entry of omitted) information.push({ type: 'skill', title: entry.name, status: 'error', detail: `省略: ${entry.reason}` });
  return {
    commands: [], instruction: included.join('\n\n'), information, omitted,
    delivery: included.length ? 'inline-context' : 'none',
  };
}

module.exports = { MAX_SELECTED, relevance, mentioned, select, deliver };
