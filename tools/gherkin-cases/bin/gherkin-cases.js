#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { convert } = require('../src/convert');

function main(argv) {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, options: {
    out: { type: 'string', short: 'o' }, help: { type: 'boolean', short: 'h' },
  } });
  if (values.help) { process.stdout.write('gherkin-cases <file.feature ...> [-o cases.json]\n入力元のパスは出力JSONからの相対（標準出力なら現在のフォルダから）。\n'); return; }
  if (!positionals.length) throw new Error('入力の .feature ファイルを指定してください');
  const base = values.out ? path.dirname(path.resolve(values.out)) : process.cwd();
  const cases = []; const seen = new Set();
  for (const input of positionals) {
    const source = path.resolve(input);
    if (!/\.feature$/i.test(source)) throw new Error(`.feature を指定してください: ${input}`);
    if (values.out && path.resolve(values.out) === source) throw new Error('入力ファイルへは出力できません');
    if (seen.has(source)) throw new Error(`入力が重複しています: ${input}`);
    seen.add(source);
    const file = path.relative(base, source).split(path.sep).join('/');
    const result = convert(fs.readFileSync(source, 'utf8'), file);
    cases.push(...result.cases);
    result.warnings.forEach((warning) => process.stderr.write(`${warning}\n`));
  }
  const body = `${JSON.stringify({ cases }, null, 2)}\n`;
  if (values.out) {
    fs.mkdirSync(base, { recursive: true });
    fs.writeFileSync(values.out, body);
  } else process.stdout.write(body);
}

try { main(process.argv.slice(2)); } catch (e) { process.stderr.write(`gherkin-cases: ${e.message}\n`); process.exitCode = 2; }
