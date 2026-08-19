'use strict';

// 配布物の取りこぼし防止。
//
// `index.html` はバンドラを使わない方針（設計 §3.5）なので、CSS / JS を**相対パスで直接**
// 読む。中には `../../node_modules/diff2html/…` のようにアプリのソースツリーの外を指すものが
// あり、これが `package.json` の `build.files` に載っていないと**パッケージ版だけ**画面の一部が
// 動かない（開発起動では node_modules がそこに在るので気づけない）。壊れ方が配布後にしか
// 出ないので、参照と同梱指定の対応をここで機械的に突き合わせる。
//
// 追加依存なしで `node test/packaging-assets.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const ROOT = path.join(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const html = fs.readFileSync(path.join(ROOT, 'src', 'renderer', 'index.html'), 'utf8');

// `index.html` が読む相対アセット（href / src）。絶対 URL・データ URI・アンカーは対象外。
function referencedAssets() {
  const out = new Set();
  for (const m of html.matchAll(/(?:href|src)\s*=\s*"([^"]+)"/g)) {
    const ref = m[1].trim();
    if (!ref || /^[a-z][a-z0-9+.-]*:/i.test(ref) || ref.startsWith('#') || ref.startsWith('//')) continue;
    // index.html は src/renderer/ にあるので、そこからパッケージルート相対へ直す
    out.add(path.relative(ROOT, path.resolve(ROOT, 'src', 'renderer', ref)).split(path.sep).join('/'));
  }
  return [...out].sort();
}

// build.files の glob 判定。`**` と `*` だけを解釈する簡易実装で、このリポジトリが使う
// パターン（`src/**/*` / `package.json` / `node_modules/<pkg>/bundles/**`）に必要な範囲に絞る。
// 否定パターン（`!…`）や `{a,b}` を使い始めたら、この関数も一緒に育てること。
function matches(pattern, target) {
  assert.ok(!pattern.startsWith('!'), `否定パターンは未対応: ${pattern}`);
  assert.ok(!/[{}[\]]/.test(pattern), `複雑な glob は未対応: ${pattern}`);
  const re = new RegExp(
    `^${pattern
      .split('/')
      // `**` の目印は `\u0000`（**エスケープで書く**。生の NUL バイトをソースへ直接埋めると
      // git がファイルごとバイナリ扱いし、diff もレビューもマージもできなくなる）。
      .map((seg) => (seg === '**' ? '\u0000' : seg.replace(/[.+^$()|\\]/g, '\\$&').replace(/\*/g, '[^/]*')))
      .join('/')
      .replace(/\u0000\//g, '(?:.*/)?')
      .replace(/\/?\u0000/g, '(?:/.*)?')}$`
  );
  return re.test(target);
}

test('index.html が読む相対アセットは build.files に載っている', () => {
  const patterns = pkg.build.files;
  assert.ok(Array.isArray(patterns) && patterns.length, 'build.files が無い');
  const assets = referencedAssets();
  assert.ok(assets.length >= 2, `参照アセットを抽出できていない: ${assets}`);
  for (const asset of assets) {
    const covered = patterns.some((p) => matches(p, asset));
    assert.ok(covered, `${asset} が build.files に載っていない（パッケージ版で欠ける）`);
  }
});

test('index.html が読む相対アセットが実在する', () => {
  for (const asset of referencedAssets()) {
    const full = path.join(ROOT, asset);
    // 依存を入れていない環境（このリポジトリの CI・サンドボックス）では node_modules ごと
    // 無いので、そこは実在検査を飛ばす。src/ 配下は常に検査する。
    if (asset.startsWith('node_modules/') && !fs.existsSync(path.join(ROOT, 'node_modules'))) continue;
    assert.ok(fs.existsSync(full), `${asset} が存在しない（参照が古い）`);
  }
});

test('glob 判定そのものが効いている', () => {
  // 上のテストは「載っている」ことしか見ないので、判定が常に true を返す実装でも緑になる。
  assert.ok(matches('src/**/*', 'src/renderer/index.html'));
  assert.ok(matches('node_modules/diff2html/bundles/**', 'node_modules/diff2html/bundles/js/x.js'));
  assert.ok(matches('package.json', 'package.json'));
  assert.ok(!matches('src/**/*', 'node_modules/diff2html/bundles/js/x.js'));
  assert.ok(!matches('node_modules/diff2html/bundles/**', 'node_modules/other/x.js'));
  assert.ok(!matches('package.json', 'src/package.json'));
});

// 同梱の CLI 定義（agents/<name>.json）は **アプリのソースツリーの外**（リポジトリ直下）に
// あるので build.files では入らない。入っていないと、パッケージ版で ~/.agents/agents/ が
// 無い端末（install.py 未実行）ではどの CLI も解決できず、AI 機能が全滅する。
// 組み込みテーブルというフォールバックを持たない設計（S9-3.1）なので、ここは死活問題。
test('同梱の CLI 定義が extraResources でパッケージへ入る', () => {
  const extra = pkg.build.extraResources || [];
  const entry = extra.find((e) => String(e && e.to) === 'agents');
  assert.ok(entry, 'build.extraResources に agents/ の同梱指定が必要です');
  assert.ok(String(entry.from).endsWith('agents'), `from が agents/ を指していません: ${entry.from}`);
  const dir = path.resolve(ROOT, entry.from);
  assert.ok(fs.existsSync(path.join(dir, 'kiro.json')),
    `同梱元に定義がありません: ${dir}（agents/kiro.json が必要）`);
});

test('同梱の手法カタログが extraResources でパッケージへ入る', () => {
  const entry = (pkg.build.extraResources || []).find((e) => String(e && e.to) === 'methods');
  assert.ok(entry, 'build.extraResources に methods/ の同梱指定が必要です');
  const dir = path.resolve(ROOT, entry.from);
  assert.strictEqual(fs.readdirSync(dir).filter((name) => name.endsWith('.json')).length, 25);
  // ワークフロー機能が実行時に足す作業ルールと、設計書の書式も同梱カタログから引く
  for (const name of ['ui-consistency.json', 'test-green-evidence.json', 'integration-verify.json',
    'design-document-format.json']) {
    assert.ok(fs.existsSync(path.join(dir, name)), `同梱元に ${name} がありません: ${dir}`);
  }
});

// 同梱フロー（workflows/<id>.json）も同じ理由でリポジトリ直下にある。入っていないと
// パッケージ版で設計セッションが「フローが見つかりません」で始められない。
test('同梱フローが extraResources でパッケージへ入る', () => {
  const entry = (pkg.build.extraResources || []).find((e) => String(e && e.to) === 'workflows');
  assert.ok(entry, 'build.extraResources に workflows/ の同梱指定が必要です');
  const dir = path.resolve(ROOT, entry.from);
  for (const name of ['design-interactive.json', 'design-auto.json']) {
    assert.ok(fs.existsSync(path.join(dir, name)), `同梱元に ${name} がありません: ${dir}`);
  }
});

console.log(`\n${passed} passed`);
