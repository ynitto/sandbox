'use strict';
// テストとして曖昧な点（A01〜A09）。読む人によって、使う値・操作・合否が変わってしまう書き方を拾う。
//
// 「」や "" で囲んだ値（画面の文言・入力する値）は伏せてから当てる。文言の中の「いずれか」などを拾わないため。
// 規則ごとに、当てる場所（前提・手順・期待結果）を決めている。同じ文に同じ規則は 1 回だけ出す。

const tx = require('./text');
const { excerpt } = require('./findings');

const rx = (patterns) => new RegExp(patterns.map((p) => `(?:${p})`).join('|'));

// A01 合否の基準があいまい（期待結果・チェック項目）
const VAGUE = [
  '正しく', '正常に', '適切(に|な|で)', '問題(なく|ない|がない|のない)', 'うまく', 'ちゃんと', 'きちんと', 'スムーズ', '速やかに',
  'すばやく|素早く', '十分(に|な)', '妥当(に|な)', '(想定|期待|仕様|意図|設計|要件)(通り|どおり)',
  '見やすい|わかりやすい|分かりやすい|使いやすい|きれいに|綺麗に|自然に|違和感', 'など', '(?<![平同均対])等(?![しく級分価号])',
  '適宜', '必要に応じ', '可能な限り|なるべく|できるだけ', 'ある程度', '高速に|軽快', '^.{0,8}(動作|機能)する(こと)?$',
  '(画面|表示|内容|結果|動作|挙動|状態|画面表示)を確認(する|します)?$', '崩れ(ない|ていない)', '不自然(でない|ではない)',
  '\\bcorrectly\\b', '\\bproperly\\b', '\\bappropriate(ly)?\\b', '\\bas expected\\b', 'works? (fine|well)', 'should work',
  'user[- ]friendly', '\\betc\\b', '\\bquickly\\b',
];
// A02 使う値が決まっていない（前提・手順）。値（「」・数値）が同じ文にあれば言わない
const UNDEFINED = [
  '任意の', '適当な', '適当に', '何か(の|を)', 'なんらかの', '何らかの', 'ランダムな', '適宜',
  '(有効|無効|正しい|誤った|間違った|不正な|存在する|存在しない|適切な|別の|他の|ほかの)(値|データ|パスワード|メールアドレス|ユーザー名?|id|アカウント|日付|金額|文字列|コード|番号|ファイル)',
  '\\bsome (value|data|text)\\b', '\\bany (value|data|text)\\b', '\\brandom\\b', '\\bvalid (value|data|input|password|email)\\b',
  '\\binvalid (value|data|input|password|email)\\b',
];
// A03 量・回数・時間があいまい（前提・手順・期待結果）。すぐ後ろに数値があれば言わない
const QUANTITY = [
  'いくつか', '複数(の|件|個|回|人|名)?', '数件', '数個', '数回', '数名', '何回か', '何度か', '何件か', '多数', '大量', '少量', '多め', '少なめ',
  'たくさん', '少し', 'しばらく', '数秒', '数分', '一定(時間|期間|数|回数|以上)', '長い(文字列|テキスト|名前|文章|値)', '短い(文字列|テキスト|名前|値)',
  '大きな?(ファイル|画像|データ|値|数)', '小さな?(ファイル|画像|データ|値|数)', '頻繁に', '時々', 'ときどき', '十分な?(数|量|時間|件数)',
  '\\bseveral\\b', '\\bmany\\b', '\\ba few\\b', '\\blarge\\b', '\\bsmall\\b', '\\bfor a while\\b',
];
// A04 指すものがあいまい（前提・手順・期待結果）
const REFERENCE = [
  '該当の', '当該', '対象の', '例の', '先ほどの', 'さきほどの', 'いつもの', '所定の', '規定の', '指定の', '特定の', '最新の', '直近の', '最近の',
  '一部の', '関連する', '必要な(情報|項目|値|データ|事項)', '必要事項', '各種', 'しかるべき',
];
// 文の頭の指示語（前の文がケースの中に無いときだけ）
const LEADING_DEMONSTRATIVE = /^(それ|これ|あれ|その|この|あの)(?![ぞれ])/;
// A05 期待結果に選択肢がある
const ALTERNATIVE = ['または', 'もしくは', 'あるいは', 'いずれか', 'どちらか', 'のどれか', 'か否か', '\\bor\\b', 'and/or', '及び/又は', 'または/及び'];
// A06 言い切っていない・二通りに読める（期待結果）
const HEDGE = [
  'ことがある', '場合がある', '場合もある', 'ことも(ある|あります)', 'かもしれない', 'はず(だ|です)?$', 'と思われる', '可能性が(ある|高い)', '想定される',
  'ことが多い', 'ほぼ', 'おおむね', '大体', 'だいたい', '基本的に', '原則(として)?', '多くの場合', '場合によ(り|って)', '状況に(より|応じて)',
  'ないことはない', 'なくはない', 'ないわけではない', 'ないとは限らない', '\\bmay\\b', '\\bmight\\b', '\\busually\\b', '\\bshould\\b',
];
// A07 比べる基準がない（期待結果）。数値があれば言わない
const COMPARATIVE = [
  '(より|以前より|前より|従来より|今までより|これまでより)(速|早|遅|大き|小さ|多|少|高|低|短|長|軽|重|良|よ)',
  '改善され', '向上(する|している|すること)', '(速|早)くなる', '軽くなる', '短縮され', '(良|よ)くなる', '悪化しない', '劣化しない',
  '\\b(faster|slower|better|worse)\\b',
];
// A08 操作の対象があいまい（手順）。何を・どの値で操作するかが無い
const TARGET = [
  '^(ボタン|リンク|項目|値|データ|メニュー|タブ|アイコン|チェックボックス|ファイル|画像|フォーム|欄|フィールド)を(押|クリック|入力|選択|タップ|アップロード|開|チェック|選)',
  '(各|全|すべての|全ての|全部の)(項目|欄|フィールド|入力欄)(を|に)', '必要(な)?(項目|事項|情報)を(入力|選択)', '^(値|データ|情報)を(入力|設定|選択)',
];
// A09 列挙が閉じていない（前提・手順）
const OPEN_LIST = ['など', '(?<![平同均対])等(?![しく級分価号])', 'ほか(の|に)?(項目|値|データ)', 'その他', '…', '\\.\\.\\.', '\\betc\\b'];

const QUOTED = /「[^」]*」|『[^』]*』|"[^"]*"|“[^”]*”/g;
const plain = (text) => tx.normalize(String(text)).replace(QUOTED, '「」');
const hasValue = (t) => /「」|\d/.test(t);

class Ambiguity {
  constructor(ctx) {
    this.ctx = ctx;
    this.f = ctx.findings;
    const w = ctx.config.words;
    this.re = {
      vague: rx([...VAGUE, ...w.vague]), undefined: rx(UNDEFINED), quantity: rx(QUANTITY), reference: rx(REFERENCE),
      alternative: rx(ALTERNATIVE), hedge: rx(HEDGE), comparative: rx(COMPARATIVE), target: rx(TARGET), openList: rx(OPEN_LIST),
    };
  }

  run() {
    for (const c of this.ctx.cases) this.check(c);
  }

  add(code, c, item, m, message, hint) {
    this.f.add(code, message.replace('%s', m[0]), { case: c, item, hint, excerpt: excerpt(item.text) });
  }

  check(c) {
    const titleItem = c.title ? { text: c.title, line: c.line, field: 'title' } : null;
    // 合否を決める文: ケースなら期待結果、チェックリストなら項目そのもの
    const judge = c.kind === 'case' ? c.expected : (titleItem ? [titleItem] : []);
    const setup = [...c.preconditions, ...c.steps];
    const all = [...setup, ...judge];

    for (const item of judge) {
      const t = plain(item.text);
      let m;
      if ((m = this.re.vague.exec(t))) this.add('A01', c, item, m, '「%s」では、人によって合否が変わります', '何が・どうなれば合格かを、見て判断できる形（表示される文言・値・件数・状態）で書く');
      if ((m = this.re.alternative.exec(t))) this.add('A05', c, item, m, '「%s」で結果を選べるので、どちらになっても合格になります', '結果を 1 つに決める。条件で結果が分かれるなら、条件ごとにケースを分ける');
      if ((m = this.re.hedge.exec(t))) this.add('A06', c, item, m, '「%s」と言い切っておらず、そうならなくても不合格にできません', '必ずそうなる結果だけを書く。そうならないこともあるなら、そうなる条件を前提に書く');
      if ((m = this.re.comparative.exec(t)) && !/\d/.test(t)) this.add('A07', c, item, m, '「%s」は何と比べるかが無く、どれだけなら合格かが決まりません', '比べる相手と数値（「3 秒以内」「前の版の半分以下」など）を書く');
    }
    for (const item of setup) {
      const t = plain(item.text);
      let m;
      if ((m = this.re.undefined.exec(t)) && !t.slice(m.index).includes('「」')) this.add('A02', c, item, m, '「%s」では、人によって使う値が変わります', '使う値を「」で具体的に書く（種類や境界を試すなら、値ごとにケースを分ける）');
      if ((m = this.re.openList.exec(t))) this.add('A09', c, item, m, '「%s」で、何を含むかが決まっていません', '対象をすべて書き出す');
    }
    for (const item of c.steps) {
      const t = plain(item.text);
      const m = this.re.target.exec(t);
      if (m && !hasValue(t)) this.add('A08', c, item, m, '「%s」では、どれを・どの値で操作するかが決まっていません', '操作する対象（ボタンの名前・項目名）と入れる値を書く');
    }
    for (const item of all) {
      const t = plain(item.text);
      let m;
      if ((m = this.re.quantity.exec(t)) && !/^\s*[（(]?\s*\d/.test(t.slice(m.index + m[0].length))) {
        this.add('A03', c, item, m, '「%s」では、量・回数・時間が人によって変わります', '数値で書く（「3 件」「5 回」「10 秒待つ」「101 文字」など）');
      }
      if ((m = this.re.reference.exec(t))) this.add('A04', c, item, m, '「%s」が何を指すか、このケースの中で決まっていません', '指すもの（ID・名前・値）を書く。決まった値なら、その値をここに書く');
    }
    // 最初の文が指示語で始まる（指す相手がケースの中に無い）
    const first = all[0];
    if (first) {
      const m = LEADING_DEMONSTRATIVE.exec(plain(first.text));
      if (m) this.add('A04', c, first, m, '「%s」が何を指すか、このケースの中で決まっていません', '指すもの（ID・名前・値）を書く');
    }
  }
}

module.exports = { Ambiguity, VAGUE };
