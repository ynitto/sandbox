'use strict';

// 配布物（electron-builder）の取りこぼし防止。agent-dashboard/test/packaging-assets.test.js と同じ狙い。
//
// index.html はバンドラを使わず CSS / JS を相対パスで直接読み、main 側は node_modules の
// statemachine-maker（file: リンク）を require する。これらが package.json の build.files に
// 載っていないと**パッケージ版だけ**壊れる（開発起動では node_modules がそこに在るので気づけない）。
// 壊れ方が配布後にしか出ないので、参照と同梱指定の対応をここで機械的に突き合わせる。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const build = pkg.build || {};
const patterns = build.files || [];

const posix = (p) => p.split(path.sep).join('/');

// electron-builder の files パターンのうち、このリポジトリで使う範囲（`**` / `*` / `{a,b}` / 先頭 `!`）を解釈する。
function globToRegExp(pattern) {
  let re = '';
  for (let i = 0; i < pattern.length; i += 1) {
    const c = pattern[i];
    if (c === '*') {
      if (pattern[i + 1] === '*') {
        i += 1;
        if (pattern[i + 1] === '/') { i += 1; re += '(?:.*/)?'; } else re += '.*';
      } else re += '[^/]*';
    } else if (c === '{') {
      const end = pattern.indexOf('}', i);
      assert.ok(end > i, `閉じていない {: ${pattern}`);
      re += `(?:${pattern.slice(i + 1, end).split(',').map((s) => s.replace(/[.+^$()|[\]\\]/g, '\\$&')).join('|')})`;
      i = end;
    } else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^$()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${re}$`);
}

// 同梱されるか: 肯定パターンのどれかに合い、否定パターンのどれにも合わない。
// electron-builder は package.json と本番依存の node_modules を自動で足すが、ここでは
// 「明示的に載っているか」を見る（自動同梱に頼ると file: リンクや除外の変更で崩れやすい）。
function included(rel) {
  const target = posix(rel);
  const hit = patterns.filter((p) => !p.startsWith('!')).some((p) => globToRegExp(p).test(target));
  const excluded = patterns.filter((p) => p.startsWith('!')).some((p) => globToRegExp(p.slice(1)).test(target));
  return hit && !excluded;
}

function relativeRequires(file) {
  const src = fs.readFileSync(file, 'utf8');
  return [...src.matchAll(/require\(\s*'(\.{1,2}\/[^']+)'\s*\)/g)].map((m) => m[1]);
}

function resolveRelative(fromFile, spec) {
  const base = path.resolve(path.dirname(fromFile), spec);
  for (const c of [base, `${base}.js`, `${base}.json`, path.join(base, 'index.js')]) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return null;
}

function jsFilesUnder(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...jsFilesUnder(full));
    else if (ent.isFile() && ent.name.endsWith('.js')) out.push(full);
  }
  return out;
}

test('build.files は src / package.json を同梱し、win の出力先とアイコンが決まっている', () => {
  assert.ok(patterns.includes('src/**/*'));
  assert.ok(patterns.includes('package.json'));
  assert.ok(included(pkg.main), `${pkg.main} が build.files に載っていない`);
  assert.ok(fs.existsSync(path.join(ROOT, pkg.main)));
  assert.deepStrictEqual(build.win && build.win.target, ['portable', 'nsis']);
  assert.ok(String(build.win.icon).endsWith('.ico'));
  assert.ok(fs.existsSync(path.join(ROOT, build.win.icon)), `アイコンが無い: ${build.win.icon}（npm run icon で生成）`);
  assert.strictEqual(build.directories && build.directories.output, 'release');
});

test('index.html が読む相対アセットは実在し、build.files に載っている', () => {
  const html = fs.readFileSync(path.join(ROOT, 'src', 'renderer', 'index.html'), 'utf8');
  const refs = new Set();
  for (const m of html.matchAll(/(?:href|src)\s*=\s*"([^"]+)"/g)) {
    const ref = m[1].trim();
    if (!ref || /^[a-z][a-z0-9+.-]*:/i.test(ref) || ref.startsWith('#') || ref.startsWith('//')) continue;
    refs.add(path.relative(ROOT, path.resolve(ROOT, 'src', 'renderer', ref)));
  }
  assert.ok(refs.size >= 20, `参照を抽出できていない: ${refs.size}`);
  for (const rel of refs) {
    assert.ok(fs.existsSync(path.join(ROOT, rel)), `index.html が読む ${posix(rel)} が無い（npm install で vendor/ を生成）`);
    assert.ok(included(rel), `index.html が読む ${posix(rel)} が build.files に載っていない`);
  }
});

test('src/ 配下の相対 require 先は実在し、build.files に載っている', () => {
  let checked = 0;
  for (const file of jsFilesUnder(path.join(ROOT, 'src'))) {
    for (const spec of relativeRequires(file)) {
      const target = resolveRelative(file, spec);
      const from = posix(path.relative(ROOT, file));
      assert.ok(target, `${from} の require('${spec}') が解決できない`);
      const rel = path.relative(ROOT, target);
      assert.ok(included(rel), `${from} が require する ${posix(rel)} が build.files に載っていない`);
      checked += 1;
    }
  }
  assert.ok(checked >= 20, `相対 require を抽出できていない: ${checked}`);
});

// statemachine-maker は file: リンク（node_modules/statemachine-maker → ../statemachine-maker）。
// electron-builder は実体を写すが、renderer 側は vendor/ に写した分だけ使うので main だけ同梱する。
// その main が使う yaml も要る（開発起動では ../statemachine-maker/node_modules から解決される）。
test('main が require する statemachine-maker（file: リンク）と yaml が build.files に載っている', () => {
  const specs = new Set();
  for (const file of jsFilesUnder(path.join(ROOT, 'src', 'main'))) {
    for (const m of fs.readFileSync(file, 'utf8').matchAll(/require\(\s*'(statemachine-maker\/[^']+)'\s*\)/g)) specs.add(m[1]);
  }
  assert.ok(specs.size >= 1, 'statemachine-maker の require を抽出できていない');
  const makerRoot = path.join(ROOT, '..', 'statemachine-maker');
  for (const spec of specs) {
    const rel = `node_modules/${spec}.js`;
    assert.ok(fs.existsSync(path.join(makerRoot, spec.replace(/^statemachine-maker\//, '') + '.js')), `${spec} が無い`);
    assert.ok(included(rel), `${rel} が build.files に載っていない`);
  }
  assert.ok(included('node_modules/statemachine-maker/package.json'));
  // maker の main 同士の相対 require も同梱範囲に収まる
  for (const file of jsFilesUnder(path.join(makerRoot, 'src', 'main'))) {
    for (const spec of relativeRequires(file)) {
      const target = resolveRelative(file, spec);
      assert.ok(target, `${posix(path.relative(makerRoot, file))} の require('${spec}') が解決できない`);
      const rel = path.join('node_modules', 'statemachine-maker', path.relative(makerRoot, target));
      assert.ok(included(rel), `${posix(rel)} が build.files に載っていない`);
    }
  }
  const makerPkg = JSON.parse(fs.readFileSync(path.join(makerRoot, 'package.json'), 'utf8'));
  for (const dep of Object.keys(makerPkg.dependencies || {})) {
    assert.ok(included(`node_modules/${dep}/package.json`), `statemachine-maker の依存 ${dep} が build.files に載っていない`);
  }
});

// electron-builder は dependencies（本番依存）の node_modules を推移的に自動同梱する。画面用ライブラリは
// npm install 時に vendor/ へ写した分だけ使うので devDependencies に置き、mermaid が引く d3 / katex …
// まで exe に入らないようにする（dependencies に戻すと asar が数十 MB 増える）。
test('画面用ライブラリは devDependencies（vendor/ に写す分だけ同梱）で、dependencies は statemachine-maker だけ', () => {
  const { FILES } = require('../scripts/vendor');
  const vendored = new Set(FILES.map(([from]) => (from.startsWith('@') ? from.split('/').slice(0, 2).join('/') : from.split('/')[0])));
  assert.deepStrictEqual(Object.keys(pkg.dependencies), ['statemachine-maker']);
  for (const dep of vendored) {
    if (dep === 'statemachine-maker') continue;
    assert.ok(pkg.devDependencies[dep], `${dep} は vendor/ に写すので devDependencies に置く`);
    assert.ok(!included(`node_modules/${dep}/package.json`), `${dep} は build.files に載せない`);
  }
});

// 同梱の CLI 定義（agents/<name>.json）はアプリのソースツリーの外（リポジトリ直下）にあるので
// build.files では入らない。src/main/agentCli.js が process.resourcesPath/agents を最後の候補にする。
test('同梱の CLI 定義が extraResources でパッケージへ入る', () => {
  const entry = (build.extraResources || []).find((e) => String(e && e.to) === 'agents');
  assert.ok(entry, 'build.extraResources に agents/ の同梱指定が必要です');
  const dir = path.resolve(ROOT, entry.from);
  assert.ok(fs.existsSync(path.join(dir, 'kiro.json')), `同梱元に定義がありません: ${dir}`);
  const src = fs.readFileSync(path.join(ROOT, 'src', 'main', 'agentCli.js'), 'utf8');
  assert.ok(src.includes("path.join(process.resourcesPath, 'agents')"));
});

// タスク（statemachine-maker の機能）は `.github/skills/statemachine-use/scripts/run_machine.py` を
// `appRoot/../../` から辿る。パッケージ版では resources/app-root/ にリポジトリ直下と同じ配置で同梱し、
// src/main/ipc.js がそこを appRoot として渡す。
test('statemachine-use スキルが extraResources でパッケージへ入り、ipc.js の appRoot から辿れる', () => {
  const to = 'app-root/.github/skills/statemachine-use';
  const entry = (build.extraResources || []).find((e) => String(e && e.to) === to);
  assert.ok(entry, `build.extraResources に ${to} の同梱指定が必要です`);
  const dir = path.resolve(ROOT, entry.from);
  assert.ok(fs.existsSync(path.join(dir, 'scripts', 'run_machine.py')), `同梱元にスクリプトがありません: ${dir}`);
  const src = fs.readFileSync(path.join(ROOT, 'src', 'main', 'ipc.js'), 'utf8');
  assert.ok(src.includes("path.join(process.resourcesPath, 'app-root')"));
  assert.ok(src.includes("path.join(packaged, 'tools', 'agent-app')"));
  // appRoot/../../.github/skills/statemachine-use == resources/<to>
  const appRoot = path.join('/resources', 'app-root', 'tools', 'agent-app');
  assert.strictEqual(posix(path.resolve(appRoot, '..', '..', '.github', 'skills', 'statemachine-use')), `/resources/${to}`);
});
