'use strict';
// 実装との突き合わせ（I01・I02）。期待結果に書いた文言・エラーコードが、実装（コード・文言のリソース）のどこかにあるかを見る。
// アプリは動かさない。フォルダの文字を読むだけなので、別のリポジトリのフォルダも渡せる。
//
// 「{0}文字以内」「%d 件」「${n}件」のような差し込みのある文字列は、その部分を任意の値として照らし合わせる。

const fs = require('fs');
const path = require('path');
const tx = require('./text');
const { walkFiles } = require('./spec');
const { excerpt } = require('./findings');
const { expectedStrings, nearest, NOT_TEXT_KEYS } = require('./specCheck');

const EXTS = /\.(js|jsx|ts|tsx|mjs|cjs|vue|svelte|html?|erb|ejs|hbs|jsp|php|py|rb|java|kt|kts|swift|cs|go|rs|dart|json|ya?ml|properties|po|xml|strings|resx|arb|ini|toml)$/i;
// 短い文字列も 1 つの塊として読み飛ばす（読み飛ばさないと、閉じの引用符から次の文字列を読み違える）
const LITERAL = /"((?:[^"\\\n]|\\.){0,300})"|'((?:[^'\\\n]|\\.){0,300})'|`([^`]{0,300})`|>([^<>\n]{2,300})</g;
const PLACEHOLDER = /\{\{[^}]*\}\}|\$\{[^}]*\}|\{[\w.]*\}|%\d*\$?[sdif@]|:\w+/g;
const MAX_BYTES = 64 * 1024 * 1024;
// エラーコードの形（E1001・ERR-404・ORDER_NOT_FOUND）。注文番号 O-100 のようなデータの ID と分けるため、
// エラーを言う語のある期待結果だけで拾う
const CODE = /(?<![\w-])(?:[A-Z]{1,6}[-_]?\d{3,5}|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w-])/g;
const ERROR_CONTEXT = /エラー|コード|例外|error|code|exception/i;

function loadSource(dirs) {
  let corpus = ''; const literals = []; const templates = []; const errors = []; let files = 0;
  const seen = new Set(); // 同じファイルを 2 度読まない
  for (const dir of dirs) {
    let stat;
    try { stat = fs.statSync(dir); } catch { errors.push(`実装のフォルダがありません: ${dir}`); continue; }
    for (const file of stat.isDirectory() ? walkFiles(dir, EXTS) : [dir]) {
      if (seen.has(path.resolve(file))) continue;
      seen.add(path.resolve(file));
      let text;
      try { text = fs.readFileSync(file, 'utf8'); } catch { continue; }
      if (text.length > 2 * 1024 * 1024 || corpus.length > MAX_BYTES) continue; // 生成物・巨大ファイルは飛ばす
      files++;
      const n = tx.normalize(text);
      corpus += `\n${n}`;
      for (const m of text.matchAll(LITERAL)) {
        const s = (m[1] || m[2] || m[3] || m[4] || '').trim();
        if ([...s].length < 2 || (!/[^\x00-\x7f]/.test(s) && !/\s/.test(s))) continue; // 日本語も空白も無いものは識別子とみなす
        literals.push(s);
        if (PLACEHOLDER.test(s)) {
          PLACEHOLDER.lastIndex = 0;
          const parts = tx.normalize(s).split(PLACEHOLDER).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
          if (parts.join('').length >= 3) templates.push(new RegExp(`^${parts.join('.{1,40}?')}$`));
        }
        PLACEHOLDER.lastIndex = 0;
      }
    }
  }
  return { corpus, literals, templates, files, errors };
}

function checkSource(ctx) {
  const src = ctx.source;
  if (!src || !src.files) return;
  for (const c of ctx.cases) {
    for (const item of c.expected) {
      for (const q of expectedStrings(item.text)) {
        const n = tx.normalize(q);
        if (src.corpus.includes(n) || src.templates.some((re) => re.test(n))) continue;
        const near = nearest(n, src.literals);
        ctx.findings.add('I01', `期待する文言「${excerpt(q, 40)}」が実装のどこにもありません${near ? `（実装には「${excerpt(near, 40)}」）` : ''}`, { case: c, item, hint: near ? '文言の誤りか、実装・仕様の変更に追いついていないかを確かめる' : '実装が文言をどこから出しているか（サーバー・外部の辞書など）を確かめる' });
      }
      for (const code of expectedCodes(item.text)) {
        const n = tx.normalize(code);
        if (new RegExp(`(?<![\\w-])${n}(?![\\w-])`).test(src.corpus)) continue;
        ctx.findings.add('I02', `期待するエラーコード「${code}」が実装のどこにもありません`, { case: c, item, excerpt: excerpt(item.text), hint: 'コードの誤りか、実装の変更に追いついていないかを確かめる。外部のサービスが返すコードなら、この指摘は止めてよい' });
      }
    }
  }
}

// 期待結果の中のエラーコード。日本語を含む引用は文言（I01 が見る）、セレクタなどの値はコードでないので外す
function expectedCodes(text) {
  const t = String(text).normalize('NFKC').replace(NOT_TEXT_KEYS, ' ').replace(/「[^」]*[^\x00-\x7f][^」]*」|"[^"]*[^\x00-\x7f][^"]*"/g, ' ');
  if (!ERROR_CONTEXT.test(t)) return [];
  return [...new Set(t.match(CODE) || [])];
}

module.exports = { loadSource, checkSource, expectedCodes };
