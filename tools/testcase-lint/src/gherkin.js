'use strict';
// Gherkin の .feature を、JSON / YAML のケースと同じ形のケースに展開する（公式のパーサーと展開処理を使う）。
// Feature / Rule はグループ、Background は前提・手順、Outline は例の行ごとのケース、Given / When / Then は
// 前提 / 手順 / 期待結果にする。指摘を元の行へ返すため、ケースと各ステップの行を lines に持つ。
const { createHash } = require('node:crypto');
const { AstBuilder, Parser, GherkinClassicTokenMatcher, compile } = require('@cucumber/gherkin');

const digest = (s) => createHash('sha256').update(s).digest('hex');
// 構文や展開の誤り。line は .feature の行（分からなければ 0）
const fail = (line, message) => Object.assign(new Error(message), { line });
const generatedId = (value) => `GH-${BigInt(`0x${digest(JSON.stringify(value)).slice(0, 16)}`).toString()}`;

function convert(text, file) {
  let next = 0;
  const nextId = () => String(++next);
  const parser = new Parser(new AstBuilder(nextId), new GherkinClassicTokenMatcher());
  let doc;
  try { doc = parser.parse(text.replace(/^\uFEFF/, '')); } catch (e) {
    const first = e.errors?.[0] || e;
    throw fail(first.location?.line || 0, first.message);
  }
  const nodes = new Map(); const groups = new Map(); const examples = new Map(); const scenarios = [];
  function walk(node, group = []) {
    if (!node || typeof node !== 'object') return;
    if (node.id) { nodes.set(node.id, node); groups.set(node.id, group); }
    if (node.tableHeader && node.tableBody) {
      const headers = node.tableHeader.cells.map((cell) => cell.value);
      if (new Set(headers).size !== headers.length) throw fail(node.location.line, `Examples の列名が重複しています`);
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
    if (!expanded.has(scenario.id)) throw fail(scenario.location.line, `展開できる例がないシナリオです: ${scenario.name}`);
  }
  const seen = new Set(); const warnings = [];
  const cases = pickles.map((pickle) => {
    const scenario = nodes.get(pickle.astNodeIds[0]);
    const params = examples.get(pickle.astNodeIds[1]);
    const tags = pickle.tags.map((tag) => tag.name);
    const named = tags.filter((tag) => tag.startsWith('@case:')).map((tag) => tag.slice(6));
    if (named.length > 1 || named.some((value) => !value)) throw fail(scenario.location.line, `@case:ID は1つにしてください`);
    if (params && Object.hasOwn(params, 'case_id') && !params.case_id.trim()) throw fail(pickle.location.line, `case_id が空です`);
    const group = groups.get(scenario.id) || [];
    const inputNames = new Set();
    for (const step of pickle.steps.filter((s) => ['Context', 'Action'].includes(s.type))) {
      const original = nodes.get(step.astNodeIds[0]);
      for (const match of JSON.stringify(original).matchAll(/<([^>]+)>/g)) inputNames.add(match[1]);
    }
    const conditions = params ? Object.fromEntries(Object.entries(params).filter(([key]) => inputNames.has(key))) : {};
    const explicit = params ? params.case_id : named[0];
    const id = explicit || generatedId([group, named[0] || scenario.name, params ? Object.entries(params).sort() : null]);
    if (seen.has(id)) throw fail(pickle.location.line, `ケースIDが重複しています: ${id}`);
    seen.add(id);
    if (!explicit) warnings.push({ line: pickle.location.line, id });
    const c = {
      id, title: pickle.name, group, preconditions: [], steps: [], expected: [], tags,
      ...(Object.keys(conditions).length ? { conditions } : {}),
      requirement: tags.flatMap((tag) => tag.startsWith('@requirement:') ? [tag.slice(13)]
        : /^@(?:REQ|FR|NFR)-\d+$/.test(tag) ? [tag.slice(1)] : []),
      lines: { line: pickle.location.line, fields: { preconditions: [], steps: [], expected: [] } },
    };
    for (const step of pickle.steps) {
      if (!['Context', 'Action', 'Outcome'].includes(step.type)) {
        throw fail(nodes.get(step.astNodeIds[0])?.location.line || c.lines.line, `Given/When/Then でステップの役割を指定してください`);
      }
      const field = step.type === 'Context' ? 'preconditions' : step.type === 'Outcome' ? 'expected' : 'steps';
      let body = step.text;
      if (step.argument?.dataTable) body += `\n${step.argument.dataTable.rows.map((r) => `| ${r.cells.map((cell) => JSON.stringify(cell.value)).join(' | ')} |`).join('\n')}`;
      if (step.argument?.docString) body += `\n${JSON.stringify(step.argument.docString)}`;
      c[field].push(body);
      c.lines.fields[field].push(nodes.get(step.astNodeIds[0])?.location.line || c.lines.line);
    }
    return c;
  });
  if (!cases.length) throw fail(0, '展開できるシナリオがありません');
  return { cases, warnings };
}

module.exports = { convert };
