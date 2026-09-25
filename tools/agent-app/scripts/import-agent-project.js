#!/usr/bin/env node
'use strict';

// agent-project の状態フォルダを、agent-app のプロジェクトとしてナレッジリポジトリへ取り込む（画面の
// 「agent-project から取り込む」と同じ処理）。コミットはしない。書いたファイルを表示するので、
// 中身を見てからナレッジリポジトリでコミットする。
//
//   node scripts/import-agent-project.js <状態フォルダ> --kb <ナレッジリポジトリ> [--name <名前>]
//                                        [--host-yaml <~/.agents/agent-project.host.yaml>] [--dry-run]

const fs = require('fs');
const os = require('os');
const path = require('path');
const projectImport = require('../src/main/projectImport');

function args(argv) {
  const out = { root: '', kb: '', name: '', hostYaml: '', dryRun: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--kb') out.kb = argv[++i] || '';
    else if (a === '--name') out.name = argv[++i] || '';
    else if (a === '--host-yaml') out.hostYaml = argv[++i] || '';
    else if (a === '--dry-run') out.dryRun = true;
    else if (a === '-h' || a === '--help') out.help = true;
    else if (!out.root) out.root = a;
    else throw new Error(`知らない引数です: ${a}`);
  }
  return out;
}

function main() {
  const opt = args(process.argv.slice(2));
  if (opt.help || !opt.root || (!opt.kb && !opt.dryRun)) {
    console.log('使い方: node scripts/import-agent-project.js <状態フォルダ> --kb <ナレッジリポジトリ> [--name <名前>] [--host-yaml <path>] [--dry-run]');
    process.exit(opt.help ? 0 : 2);
  }
  const fallback = path.join(os.homedir(), '.agents', 'agent-project.host.yaml');
  const hostYaml = opt.hostYaml || (fs.existsSync(fallback) ? fallback : '');
  const planned = projectImport.plan({ root: opt.root, hostYaml, name: opt.name });
  const summary = {
    name: planned.project.name, folder: planned.folder, source: planned.source,
    repos: planned.project.repos, localPaths: planned.repoPaths,
    copies: planned.copies.map((item) => item.to), leftBehind: planned.leftBehind,
  };
  if (opt.dryRun) { console.log(JSON.stringify(summary, null, 2)); return; }
  const done = projectImport.apply(path.resolve(opt.kb), planned);
  console.log(JSON.stringify({ ...summary, written: done.written, skipped: done.skipped }, null, 2));
  console.log(`\nナレッジリポジトリでコミットしてください: git -C ${path.resolve(opt.kb)} add projects/${planned.folder} && git -C ${path.resolve(opt.kb)} commit -m "agent-project「${planned.project.name}」を取り込み"`);
  if (Object.keys(planned.repoPaths).length) console.log('この PC のフォルダ（localPaths）は、agent-app でプロジェクトを選んだときに登録リポジトリの origin から自動で結ばれます');
}

try { main(); } catch (err) { console.error(err.message); process.exit(1); }
