'use strict';
// 対応表（ケース ↔ 個別テスト）を、ケースとテストのファイルに照らして点検する。テストは実行しない。
//
//   B01 対応表のケース（ファイル・ID）が無い
//   B02 対応先のテストのファイルが無い・suite / selector が無い・対応表の書き方の誤り
//   B03 どのテストにも対応していないケース（チェックリストは除く）

const fs = require('fs');
const path = require('path');
const { parseText, plain, lineAt, loadFile } = require('./load');
const { local } = require('./run');

function bindingFindings(cfg, cases, fmap, f, display) {
  const file = display(cfg.bindings);
  const at = (line, hint) => ({ file, line, hint });
  let tree;
  try {
    tree = parseText(fs.readFileSync(cfg.bindings, 'utf8').replace(/^﻿/, '')).trees[0];
  } catch (e) {
    f.add('B02', `対応表を読めません: ${e.message.split('\n')[0]}`, at(e.line || 0, 'JSON の書き方を直す'));
    return;
  }
  const data = tree ? plain(tree) : null;
  if (!data || data.version !== 1 || !Array.isArray(data.bindings)) {
    f.add('B02', '対応表は {"version": 1, "bindings": [...]} の形です', at(1, 'version と bindings を書く'));
    return;
  }
  const roots = { [cfg.side]: cfg.dir, ...cfg.repos };
  const ids = new Map(); // 読んだケースのファイル（実パス）→ ID の集合
  for (const c of cases) {
    const p = path.resolve(c.file);
    if (!ids.has(p)) ids.set(p, new Set());
    if (c.id) ids.get(p).add(c.id);
  }
  const idsOf = (p) => {
    if (!ids.has(p)) ids.set(p, fs.existsSync(p) ? new Set(loadFile(p, display(p), fmap).cases.map((c) => c.id).filter(Boolean)) : null);
    return ids.get(p);
  };
  const bound = new Set();
  data.bindings.forEach((b, i) => {
    const line = lineAt(tree, ['bindings', String(i)]);
    const c = b && b.case;
    if (!c || typeof c.id !== 'string' || !c.id || typeof c.file !== 'string') {
      f.add('B02', 'case には file と id が必要です', at(line, '"case": {"file": "…", "id": "…"} と書く'));
      return;
    }
    const side = c.side ?? cfg.side;
    let casePath;
    try {
      if (!(side in roots)) throw new Error(`case.side「${side}」のリポジトリが設定の repos にありません`);
      casePath = local(roots[side], c.file);
    } catch (e) {
      f.add('B02', e.message, at(line, '設定の repos に名前とフォルダを書くか、case.file をリポジトリ内の相対パスにする'));
      return;
    }
    bound.add(`${casePath}\u0000${c.id}`);
    const known = idsOf(casePath);
    if (!known) f.add('B01', `ケースのファイル ${c.file} がありません（${c.id}）`, at(line, 'ケースのファイルの場所を直すか、廃止したケースの対応を消す'));
    else if (!known.has(c.id)) f.add('B01', `${c.file} にケース ${c.id} がありません`, at(line, 'ケースの ID を直すか、廃止したケースの対応を消す'));
    const targets = Array.isArray(b.targets) ? b.targets : [];
    if (!targets.length) f.add('B02', `${c.id} の対応先（targets）がありません`, at(line, '"targets": [{"suite", "file", "selector"}] を書く'));
    targets.forEach((t, k) => {
      const tline = lineAt(tree, ['bindings', String(i), 'targets', String(k)]);
      if (!t || typeof t.suite !== 'string' || !t.suite || typeof t.selector !== 'string' || !t.selector) {
        f.add('B02', `${c.id} の対応先には suite と selector が必要です`, at(tline, 'selector は JUnit の「classname::name」か「name」'));
        return;
      }
      let testPath;
      try { testPath = local(cfg.dir, t.file); } catch (e) { f.add('B02', e.message, at(tline, 'テストのファイルを、このリポジトリからの相対パスで書く')); return; }
      if (!fs.existsSync(testPath)) f.add('B02', `${c.id} の対応先のテスト ${t.file} がありません`, at(tline, 'テストのファイルの場所を直すか、対応を消す'));
      if (Object.keys(cfg.run).length && !cfg.run[t.suite]) f.add('B02', `suite「${t.suite}」が設定の run にありません（${c.id}）`, at(tline, `run に ${t.suite} のコマンドを書くか、suite の名前を直す`));
    });
  });
  for (const c of cases) {
    if (c.kind === 'case' && c.id && !bound.has(`${path.resolve(c.file)}\u0000${c.id}`)) {
      f.add('B03', 'どのテストにも対応していません', { case: c, hint: '対応表に、このケースを確かめる個別テストを書く。手で確かめるケースならこのままでよい' });
    }
  }
}

module.exports = { bindingFindings };
