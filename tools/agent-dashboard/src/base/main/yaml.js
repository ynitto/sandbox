'use strict';

// YAML の読み取り。**このアプリの YAML 読みはすべてここを通す。**
//
// もともと各機能が「決まった形だけ読む」小さな行指向パーサを持っていたが、インライン
// コメント（`repos:  # このPCのクローン`）や列 0 のコメント行で読み落としが出た。壊れ方が
// 「設定が無いことにされる」＝黙って機能が消える形だったので、読みは本物のパーサへ寄せる。
//
// **書き戻しは別**（cowork/main/writeback.js）。あちらはコメント・順序・他エントリを保つため
// に物理行を直接触る外科的な書換で、値ではなく位置が関心事なので、この層は使わない。

const YAML = require('yaml');

// パース結果か null（壊れた YAML）。**例外を投げない**——設定 1 つの破損で画面全体を
// 落とさず、「読めなかった＝宣言なし」に倒すのがこのアプリの既定の壊れ方。
//
// 構文エラーがある文書は**復旧値を採らずに null へ倒す**。ライブラリはタブインデントや
// 閉じ忘れからそれらしい値を復旧してくれるが、それを採ると「設定の半分だけが効いている」
// 状態を静かに作る。全部読めたか、何も読めなかったか、のどちらかにする。
function parseYaml(text) {
  const s = String(text == null ? '' : text);
  if (!s.trim()) return null;
  try {
    const doc = YAML.parseDocument(s, { logLevel: 'silent', uniqueKeys: false });
    if (doc.errors.length) return null;
    return doc.toJS();
  } catch {
    return null;
  }
}

function isPlainObject(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

// スカラを表示・比較用の文字列へ。真偽値・数値も文字列で受け取る呼び出し側の契約を保つ
// （移行前の行指向パーサは常に文字列を返していた）。スカラでなければ null。
function scalarString(v) {
  if (v == null) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (v instanceof Date) return v.toISOString();
  return null;
}

module.exports = { parseYaml, isPlainObject, scalarString };
