'use strict';
// 入力だけを変換する。テストの実行や、結果の読み手は知らない。
const { createHash } = require('node:crypto');
const { AstBuilder, Parser, GherkinClassicTokenMatcher, compile } = require('@cucumber/gherkin');

const digest = (s) => createHash('sha256').update(s).digest('hex');
const generatedId = (value) => `GH-${BigInt(`0x${digest(JSON.stringify(value)).slice(0, 16)}`).toString()}`;

function convert(text, file) {
  let next = 0;
  const nextId = () => String(++next);
  const parser = new Parser(new AstBuilder(nextId), new GherkinClassicTokenMatcher());
  let doc;
  try { doc = parser.parse(text.replace(/^\uFEFF/, '')); } catch (e) {
    const first = e.errors?.[0] || e;
    throw new Error(`${file}:${first.location?.line || 0}: ${first.message}`);
  }
  const nodes = new Map(); const groups = new Map(); const examples = new Map(); const scenarios = [];
  function walk(node, group = []) {
    if (!node || typeof node !== 'object') return;
    if (node.id) { nodes.set(node.id, node); groups.set(node.id, group); }
    if (node.tableHeader && node.tableBody) {
      const headers = node.tableHeader.cells.map((cell) => cell.value);
      if (new Set(headers).size !== headers.length) throw new Error(`${file}:${node.location.line}: Examples の列名が重複しています`);
      for (const row of node.tableBody) examples.set(row.id, Object.fromEntries(row.cells.map((cell, i) => [headers[i], cell.value])));
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === 'scenario') scenarios.push(value);
      const nested = ['feature', 'rule'].includes(key) ? [...group, value.name] : group;
      if (Array.isArray(value)) value.forEach((v) => walk(v, nested));
      else if (value && typeof value === 'object') walk(value, nested);
    }
  }
  walk(doc);
  const pickles = compile(doc, file, nextId);
  const expanded = new Set(pickles.map((pickle) => pickle.astNodeIds[0]));
  for (const scenario of scenarios) {
    if (!expanded.has(scenario.id)) throw new Error(`${file}:${scenario.location.line}: 展開できる例がないシナリオです: ${scenario.name}`);
  }
  const seen = new Set(); const warnings = [];
  const cases = pickles.map((pickle) => {
    const scenario = nodes.get(pickle.astNodeIds[0]);
    const params = examples.get(pickle.astNodeIds[1]);
    const tags = pickle.tags.map((tag) => tag.name);
    const named = tags.filter((tag) => tag.startsWith('@case:')).map((tag) => tag.slice(6));
    if (named.length > 1 || named.some((value) => !value)) throw new Error(`${file}:${scenario.location.line}: @case:ID は1つにしてください`);
    if (params && Object.hasOwn(params, 'case_id') && !params.case_id.trim()) throw new Error(`${file}:${pickle.location.line}: case_id が空です`);
    const group = groups.get(scenario.id) || [];
    const inputNames = new Set();
    for (const step of pickle.steps.filter((s) => ['Context', 'Action'].includes(s.type))) {
      const original = nodes.get(step.astNodeIds[0]);
      for (const match of JSON.stringify(original).matchAll(/<([^>]+)>/g)) inputNames.add(match[1]);
    }
    const conditions = params ? Object.fromEntries(Object.entries(params).filter(([key]) => inputNames.has(key))) : {};
    const explicit = params ? params.case_id : named[0];
    const id = explicit || generatedId([group, named[0] || scenario.name, params ? Object.entries(params).sort() : null]);
    if (seen.has(id)) throw new Error(`${file}:${pickle.location.line}: ケースIDが重複しています: ${id}`);
    seen.add(id);
    if (!explicit) warnings.push(`${file}:${pickle.location.line}: ${id} を生成しました。安定した対応には @case:ID または Examples の case_id を指定してください`);
    const c = {
      id, title: pickle.name, group, preconditions: [], steps: [], expected: [], tags,
      ...(Object.keys(conditions).length ? { conditions } : {}),
      requirement: tags.flatMap((tag) => tag.startsWith('@requirement:') ? [tag.slice(13)]
        : /^@(?:REQ|FR|NFR)-\d+$/.test(tag) ? [tag.slice(1)] : []),
      origin: { file, line: pickle.location.line, definition_line: scenario.location.line,
        ...(params ? { example_line: pickle.location.line } : {}),
        input_sha256: digest(text), fields: { preconditions: [], steps: [], expected: [] } },
    };
    for (const step of pickle.steps) {
      if (!['Context', 'Action', 'Outcome'].includes(step.type)) {
        throw new Error(`${file}:${nodes.get(step.astNodeIds[0])?.location.line || c.origin.line}: Given/When/Then でステップの役割を指定してください`);
      }
      const field = step.type === 'Context' ? 'preconditions' : step.type === 'Outcome' ? 'expected' : 'steps';
      let body = step.text;
      if (step.argument?.dataTable) body += `\n${step.argument.dataTable.rows.map((r) => `| ${r.cells.map((cell) => JSON.stringify(cell.value)).join(' | ')} |`).join('\n')}`;
      if (step.argument?.docString) body += `\n${JSON.stringify(step.argument.docString)}`;
      c[field].push(body);
      c.origin.fields[field].push(nodes.get(step.astNodeIds[0])?.location.line || c.origin.line);
    }
    return c;
  });
  if (!cases.length) throw new Error(`${file}: 展開できるシナリオがありません`);
  return { cases, warnings };
}

module.exports = { convert };
