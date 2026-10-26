'use strict';
// 設定に書いたテストのコマンドを実行し、JUnit XML を、対応表（ケース ↔ 個別テスト）と突き合わせた実行結果にする。
// ケースの点検（purpose: check）とは別の、テストを実際に実行した結果（purpose: execution）を書く。
//
//   { version: 1, purpose: "execution", run_id, inputs_digest, root, items: [
//     { id: <selector>, kind: "behavior", suite, file, status: passed|failed|skipped, test_sha256,
//       cases: [{ side, file, id, sha256 }], error?, path? } ] }
//
// 成功を取り違えないための約束:
// - 実行前にケースとテストのハッシュを控え、終了後に変わっていれば結果を書かない
// - ファイルに書く JUnit と以前の結果は実行前に消す（ランナーが何も書かなければ古い成功を使わない）
// - ランナーの失敗、対応先の結果の欠落、skip、未知の status は終了コード 0 にしない
// - 実行の識別（TEST_EVIDENCE_RUN_ID / TEST_EVIDENCE_INPUTS_DIGEST）が渡されれば引き継ぐ

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { createHash, randomBytes } = require('node:crypto');

const fail = (message) => Object.assign(new Error(message), { exitCode: 2 });
const sha = (file) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// リポジトリ内の相対パスだけを受ける
function local(root, value) {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes(':')) throw fail(`リポジトリ内の相対パスを指定してください: ${JSON.stringify(value)}`);
  const parts = value.split('/');
  if (value.startsWith('/') || parts.includes('..')) throw fail(`リポジトリ内の相対パスを指定してください: ${value}`);
  return path.resolve(root, value);
}

// 対応表を読む。B01・B02 の点検（bindings.js）と共有する
function readBindings(file) {
  let data;
  try { data = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, '')); } catch (e) { throw fail(`対応表を読めません: ${e.message}`); }
  if (!data || typeof data !== 'object' || data.version !== 1 || !Array.isArray(data.bindings)) throw fail('対応表は {"version": 1, "bindings": [...]} の形です');
  return data.bindings;
}

// --- JUnit XML（依存を増やさないための小さな読み手。testcase と、その直下の failure / error / skipped だけを見る）
const ENTITIES = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };
const decode = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => (e[0] === '#'
  ? String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : ENTITIES[e] ?? m));

function parseJUnit(xml) {
  if (/<!DOCTYPE|<!ENTITY/.test(xml)) throw fail('DTD/ENTITY を含む JUnit レポートには対応していません');
  const TOKEN = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[([\s\S]*?)\]\]>|<(\/?)([\w:.-]+)((?:\s+[\w:.-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>|([^<]+)|(<)/g;
  const stack = []; const records = []; let current = null; let roots = 0;
  for (const m of xml.matchAll(TOKEN)) {
    const [, cdata, closing, tag, attrText, selfClosing, text, stray] = m;
    if (stray) throw fail('JUnit レポートを XML として読めません');
    if (text != null || cdata != null) {
      if (!stack.length && (text || '').trim()) throw fail('JUnit レポートを XML として読めません');
      if (current && current.child) current.child.text += cdata != null ? cdata : decode(text);
      continue;
    }
    if (!tag) continue;
    const name = tag.split(':').pop();
    if (closing) {
      if (stack.pop() !== tag) throw fail('JUnit レポートを XML として読めません（タグの対応が崩れています）');
      if (current && stack.length === current.depth) { records.push(current); current = null; } else if (current && stack.length === current.depth + 1) current.child = null;
      continue;
    }
    const attrs = {};
    for (const a of (attrText || '').matchAll(/([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) attrs[a[1]] = decode(a[2] ?? a[3]);
    if (!stack.length) roots += 1;
    if (name === 'testcase' && !current) {
      current = { attrs, children: [], depth: stack.length, child: null };
      if (selfClosing) { records.push(current); current = null; continue; }
    } else if (current && stack.length === current.depth + 1) {
      const child = { name, attrs, text: '' };
      current.children.push(child);
      if (!selfClosing) current.child = child;
    }
    if (!selfClosing) stack.push(tag);
  }
  if (stack.length || roots !== 1) throw fail('JUnit レポートを XML として読めません');
  return records.map(({ attrs, children }) => {
    const reported = attrs.status;
    if (![undefined, 'run', 'passed', 'failed', 'skipped', 'notrun', 'disabled'].includes(reported)) throw fail(`JUnit の status が不正です: ${reported}`);
    const names = new Set(children.map((c) => c.name));
    const status = names.has('failure') || names.has('error') || reported === 'failed' ? 'failed'
      : names.has('skipped') || ['skipped', 'notrun', 'disabled'].includes(reported) ? 'skipped' : 'passed';
    const detail = children.filter((c) => c.name === 'failure' || c.name === 'error').map((c) => (c.attrs.message || c.text || '').trim()).join('\n');
    const selectors = new Set(attrs.classname ? [attrs.name || '', `${attrs.classname}::${attrs.name || ''}`] : [attrs.name || '']);
    return { selectors, file: attrs.file, status, detail };
  });
}

// --- 1 つの suite を実行する
// opts: { root, roots: {side: dir}, side, bindings: [...], bindingsFile, suite, command: [..], junit: file|null, output, timeout }
function runSuite(opts, io) {
  const { root, suite } = opts;
  const targets = new Map(); const inputs = new Map(); const identities = new Set();
  for (const binding of opts.bindings) {
    const c = binding && binding.case;
    if (!c || typeof c.id !== 'string' || !c.id) throw fail('対応表の case には空でない id が必要です');
    const side = c.side ?? opts.side;
    if (!(side in opts.roots)) throw fail(`case.side「${side}」のリポジトリが設定の repos にありません`);
    const casePath = local(opts.roots[side], c.file);
    for (const target of Array.isArray(binding.targets) ? binding.targets : []) {
      if (target.suite !== suite) continue;
      if (typeof target.selector !== 'string' || !target.selector) throw fail('対応表の target には selector が必要です');
      const file = local(root, target.file);
      const key = `${target.file}\u0000${target.selector}`;
      const identity = `${side}\u0000${c.file}\u0000${c.id}\u0000${key}`;
      if (identities.has(identity)) throw fail(`対応が重複しています: ${c.file} / ${c.id} → ${target.file} / ${target.selector}`);
      identities.add(identity);
      inputs.set(casePath, sha(casePath)); inputs.set(file, sha(file));
      if (!targets.has(key)) targets.set(key, { file: target.file, selector: target.selector, cases: [] });
      targets.get(key).cases.push({ side, file: c.file, id: c.id, sha256: inputs.get(casePath) });
    }
  }
  if (!targets.size) throw fail(`suite「${suite}」の対応が対応表にありません`);
  const output = path.resolve(opts.output);
  const report = opts.junit ? path.resolve(opts.junit) : null;
  const protectedFiles = new Set([...inputs.keys(), path.resolve(opts.bindingsFile)]);
  if (protectedFiles.has(output) || (report && (protectedFiles.has(report) || report === output))) throw fail('結果・JUnit レポートを入力ファイルと同じパスにはできません');
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.rmSync(output, { force: true });
  if (report) { fs.mkdirSync(path.dirname(report), { recursive: true }); fs.rmSync(report, { force: true }); }

  const [cmd, ...args] = opts.command;
  const proc = spawnSync(cmd, args, { cwd: root, encoding: 'utf8', timeout: opts.timeout * 1000, maxBuffer: 256 * 1024 * 1024, windowsHide: true });
  if (proc.error) throw fail(proc.error.code === 'ETIMEDOUT' ? `suite「${suite}」が ${opts.timeout} 秒で終わりませんでした` : `テストのコマンドを実行できません: ${proc.error.message}`);
  if (proc.stderr) io.err.write(proc.stderr);
  if (report && proc.stdout) io.out.write(proc.stdout);
  let xml;
  try { xml = report ? fs.readFileSync(report, 'utf8') : proc.stdout; } catch { throw fail(`JUnit レポートがありません: ${opts.junit}`); }
  const records = parseJUnit(xml);
  for (const [file, digest] of inputs) if (sha(file) !== digest) throw fail(`テスト実行中に入力が変わりました: ${file}`);

  const items = [];
  for (const { file, selector, cases } of targets.values()) {
    const matches = []; const details = [];
    for (const r of records) {
      if (!r.selectors.has(selector)) continue;
      if (r.file) {
        const reported = path.isAbsolute(r.file) ? path.resolve(r.file) : local(root, r.file);
        if (reported !== local(root, file)) continue;
      } else if ([...targets.values()].filter((t) => r.selectors.has(t.selector)).length > 1) {
        throw fail(`JUnit に file が無く、同名テストの対応を特定できません: ${selector}`);
      }
      matches.push(r.status);
      if (r.detail) details.push(r.detail);
    }
    if (!matches.length) continue;
    const status = matches.includes('failed') ? 'failed' : matches.includes('skipped') ? 'skipped' : 'passed';
    items.push({ id: selector, kind: 'behavior', suite, file, status, test_sha256: inputs.get(local(root, file)), cases,
      ...(details.length ? { error: details.join('\n'), path: file } : {}) });
  }
  const digestOfInputs = createHash('sha256').update(JSON.stringify(Object.fromEntries([...inputs].sort()))).digest('hex');
  const result = { version: 1, purpose: 'execution', run_id: process.env.TEST_EVIDENCE_RUN_ID || randomBytes(16).toString('hex'),
    inputs_digest: process.env.TEST_EVIDENCE_INPUTS_DIGEST || digestOfInputs,
    root: path.relative(path.dirname(output), root).split(path.sep).join('/') || '.', items };
  fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`);

  const missing = items.length !== targets.size || items.some((i) => i.status !== 'passed');
  if (missing) {
    const reported = new Set(items.map((i) => `${i.file}\u0000${i.id}`));
    for (const t of targets.values()) if (!reported.has(`${t.file}\u0000${t.selector}`)) io.err.write(`testcase-lint: 対応先の結果がありません: ${t.file} / ${t.selector}\n`);
    for (const i of items) if (i.status !== 'passed') io.err.write(`testcase-lint: ${i.status}: ${i.file} / ${i.id}${i.error ? `\n${i.error}` : ''}\n`);
  }
  if (proc.status > 0) return proc.status;
  return proc.status == null || missing ? 1 : 0;
}

// testcase-lint run [suite ...]
function runCmd(args, io, cfg) {
  if (!cfg.file) throw fail('testcase-lint run には設定ファイル（testcase-lint.config.yaml）の bindings と run が必要です');
  if (!cfg.bindings) throw fail('設定の bindings に対応表のファイルを書いてください');
  const names = Object.keys(cfg.run);
  if (!names.length) throw fail('設定の run に suite ごとのテストのコマンドを書いてください');
  const unknown = args.filter((a) => !names.includes(a));
  if (unknown.length) throw fail(`設定の run に無い suite です: ${unknown.join(', ')}（${names.join(', ')}）`);
  const bindings = readBindings(cfg.bindings);
  const roots = { [cfg.side]: cfg.dir, ...cfg.repos };
  let code = 0;
  for (const suite of args.length ? args : names) {
    const s = cfg.run[suite];
    io.err.write(`testcase-lint: ${suite} を実行します: ${s.command.join(' ')}\n`);
    const result = runSuite({ root: cfg.dir, roots, side: cfg.side, bindings, bindingsFile: cfg.bindings, suite, ...s }, io);
    io.err.write(`testcase-lint: ${suite} → ${path.relative(process.cwd(), s.output).split(path.sep).join('/')}${result ? `（終了コード ${result}）` : ''}\n`);
    if (!code) code = result;
  }
  return code;
}

module.exports = { runCmd, runSuite, parseJUnit, readBindings, local };
