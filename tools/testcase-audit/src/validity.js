'use strict';
// テストケースとしての妥当性（O / X / M と C05・U05）。書き方ではなく「このケースで不具合を見つけられるか」を見る。
//
// - 判定の強さ（O） … 期待結果が見て確かめられるか、何が出ても通ってしまわないか、異常系で副作用を見ているか
// - 実行できるか（X）… 使うデータ・ログイン状態・始める画面が、前提か前の手順で用意されているか
// - 条件の組み合わせ（M・C05・U05）… ケースから「条件 → 結果」の表を取り出し、空いている組・食い違い・重なりを見る

const tx = require('./text');
const { procedureText, expectedText, fullText, excerpt } = require('./findings');
const { NEGATIVE_WORDS, rx } = require('./lint');

// ---- 判定の強さ ----

// 見えるもの（期待結果にこれがあれば、何を見て確かめるかが書かれている）
const OBSERVABLE = /表示|画面|ページ|メッセージ|文言|一覧|件数|値|url|ステータス|status|レスポンス|応答|メール|通知|ダイアログ|ボタン|欄|ラベル|見出し|タイトル|アラート|トースト|ログに|ファイル|ダウンロード|text=|count=|visible|hidden|enabled|disabled|checked|title=|value=|遷移|移る|戻る|開く|閉じる|\d|「|"/;
// 中で起きること（見えるものが無いと確かめようがない）
const INTERNAL = /(保存|登録|更新|送信|処理|反映|記録|削除|書き込|作成|追加)(さ)?れ(る|ている|た)(こと)?$|(db|データベース|テーブル|内部|サーバー側|バックエンド|キャッシュ|セッション|メモリ)(に|の|が|へ)/;
// それだけでは中身の不具合を見逃す期待結果
const WEAK = [
  /^.{0,20}(画面|ページ)(が|に|へ)?(表示|遷移|開|移)/,
  /^.{0,12}(画面|ページ)遷移(する|します)?(こと)?$/,
  /(エラー|例外)(が)?(発生し|出|起き)?(ない|ません)(こと)?$/,
  /(落ち|固まら|クラッシュし|フリーズし)ない(こと)?$/,
  /^(処理が)?(正常)?(完了|終了)(する|します)(こと)?$/,
];
const SPECIFIC = /「[^」]+」|『[^』]+』|"[^"]+"|\d/;
const ERROR_ONLY = /^.{0,10}(エラー(メッセージ)?|警告)(が)?(表示され|出|になる|発生す)|^(失敗|エラーに)(する|なる)/;
const SIDE_EFFECT = /保存|登録|送信|削除|更新|購入|注文|支払|申請|確定|作成|追加|変更|アップロード/;
const UNCHANGED = /(保存|登録|送信|削除|更新|作成|追加|変更|反映)(さ)?れ(ない|ていない|ず)|変わら(ない|ず)|残(る|っている)|そのまま|元のまま|増え(ない|ていない)|減ら(ない|ず)|0\s?件|作られ(ない|ていない)|行われ(ない|ていない)/;

// ---- 実行できるか ----

const UI_NOUNS = /ボタン|画面|欄|リンク|メニュー|タブ|ダイアログ|ページ|フォーム|チェック|ボックス|アイコン|入力|値|内容|文字|テキスト|項目|設定|フィールド|モーダル|ウィンドウ|行|列|カード|パネル|エリア|ヘッダー|フッター|ナビ/;
const DATA_OP = /(を|の)(編集|削除|更新|変更|詳細|取消|取り消|キャンセル|複製|コピー|ダウンロード|承認|差し戻)/;
const CREATES = /作成|登録|追加|用意|存在|ある|あり|いる|投稿|購入|注文|インポート|作る|入れ/;
const AUTH_SCREENS = /マイページ|ログアウト|管理画面|会員情報|注文履歴|購入履歴|ダッシュボード|プロフィール|アカウント設定|お気に入り/;
const LOGIN_WORD = /ログイン|サインイン|sign ?in|log ?in|認証/;
const UNAUTH = /未ログイン|ログインしていない|ログイン前|ログアウト(した|後|の状態|状態)/;
const ENTRY = /開く|開いて|アクセス|起動|表示する|遷移|^goto\b|移動|ログインする|ログインして|サインイン|立ち上げ|url/;
const START_STATE = /画面|ページ|表示されている|開いている|url|アクセスしている|ログイン/;

// ---- 条件の組（ケースから取り出す） ----

// 「パスワードに「x」を入力する」「役割で「管理者」を選ぶ」→ { パスワード: x }
const INPUT_PATTERNS = [
  /(?<name>[^\s、。「」"]{1,20}?)(?:欄|フィールド|項目)?(?:に|へ)\s*(?<value>「[^」]*」|"[^"]*")\s*(?:と|を)?\s*(?:入力|入れ|設定|指定)/g,
  /(?<name>[^\s、。「」"]{1,20}?)(?:欄|フィールド|項目)?(?:で|から|に)\s*(?<value>「[^」]*」|"[^"]*")\s*(?:を)?\s*(?:選|選択|チェック)/g,
  /(?<name>[^\s、。「」"]{1,20}?)を\s*(?<value>「[^」]*」|"[^"]*")\s*(?:に)\s*(?:する|変更|設定)/g,
];
// 構造で書いた手順（fill label="パスワード" value="x"）
const FILL = /\bfill\b.*?(?:label|name|placeholder|testid|css)="(?<name>[^"]*)".*?value="(?<value>[^"]*)"/;

function inferredInputs(c) {
  const out = {};
  for (const item of c.steps) {
    const raw = String(item.text).normalize('NFKC');
    const f = FILL.exec(raw);
    if (f) { out[f.groups.name] = f.groups.value; continue; }
    for (const re of INPUT_PATTERNS) {
      for (const m of raw.matchAll(re)) out[m.groups.name.replace(/^(の|で|に)/, '')] = m.groups.value.slice(1, -1);
    }
  }
  return out;
}

class Validity {
  constructor(ctx) {
    this.ctx = ctx;
    this.cases = ctx.cases.filter((c) => c.kind === 'case');
    this.f = ctx.findings;
    this.negative = rx([...NEGATIVE_WORDS, ...ctx.config.words.negative]);
    this.hasLogin = ctx.cases.some((c) => LOGIN_WORD.test(tx.normalize(fullText(c))));
  }

  run() {
    for (const c of this.cases) {
      if (c.expected.length) this.oracle(c);
      if (c.steps.length) this.executable(c);
    }
    this.model();
  }

  isNegative(c) {
    if (this.negative.test(tx.normalize(fullText(c)))) return true;
    return tx.clauses([c.title, procedureText(c)].join('\n')).some((x) => x.negative);
  }

  oracle(c) {
    const exp = c.expected.map((i) => ({ item: i, n: tx.normalize(i.text) }));
    const anyObservable = exp.some((e) => OBSERVABLE.test(e.n));
    for (const e of exp) {
      if (INTERNAL.test(e.n) && !OBSERVABLE.test(e.n) && !anyObservable) {
        this.f.add('O01', '期待結果が、画面や応答で見て確かめられる形になっていません', { case: c, item: e.item, excerpt: excerpt(e.item.text), hint: 'どこを見れば分かるか（一覧に出る・詳細の値・API の応答・届くメールなど）を書く' });
      }
    }
    const weak = (n) => !SPECIFIC.test(n) && WEAK.some((re) => re.test(n));
    if (exp.length && exp.every((e) => weak(e.n))) {
      this.f.add('O02', '期待結果が「画面が出る」「エラーにならない」だけで、中身が壊れていても通ります', { case: c, item: exp[0].item, excerpt: excerpt(exp[0].item.text), hint: 'その画面で確かめる値（見出し・利用者名・件数・文言など）を足す' });
    }
    const negative = this.isNegative(c);
    if (negative && exp.length && exp.every((e) => ERROR_ONLY.test(e.n) && !SPECIFIC.test(e.n))) {
      this.f.add('O04', 'エラーになることしか確かめておらず、別の理由で失敗しても通ります', { case: c, item: exp[0].item, excerpt: excerpt(exp[0].item.text), hint: '表示されるメッセージの文言か、エラーの種類（コード）を書く' });
    }
    if (negative && SIDE_EFFECT.test(tx.normalize(c.steps.map((s) => s.text).join('\n'))) && !exp.some((e) => UNCHANGED.test(e.n))) {
      this.f.add('O03', '失敗したときに、データが変わっていない（保存・送信されていない）ことを確かめていません', { case: c, hint: '「保存されていない」「一覧の件数が変わらない」などを期待結果に足す' });
    }
  }

  executable(c) {
    const pre = tx.normalize(c.preconditions.map((i) => i.text).join('\n'));
    const cond = c.conditions ? tx.normalize(Object.entries(c.conditions).map(([k, v]) => `${k} ${v}`).join('\n')) : '';
    // 使うデータが前提か前の手順にあるか。対象に ID（O-100・「…」）があれば ID で、無ければ名詞で探す
    let earlier = `${pre}\n${cond}`;
    for (const item of c.steps) {
      const n = tx.normalize(item.text);
      const m = DATA_OP.exec(n);
      if (m) {
        const before = n.slice(0, m.index).split(/[、。,]|して|てから/).pop();
        const ids = before.match(/[a-z]+[-_]?\d+[\w-]*|「[^」]+」|"[^"]+"/g);
        const target = ids ? ids[ids.length - 1] : lastNoun(before);
        const pool = ids ? earlier : `${earlier}\n${tx.normalize(c.title)}`;
        if (target && (ids || !UI_NOUNS.test(target)) && !pool.includes(target)) {
          const shown = (String(item.text).normalize('NFKC').match(new RegExp(target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i')) || [target])[0];
          this.f.add('X01', `「${shown}」を${m[2]}していますが、その「${shown}」を前提でも前の手順でも用意していません`, { case: c, item, excerpt: excerpt(item.text), hint: `使う「${shown}」（あらかじめ作っておくデータ）を前提に書く` });
        }
      }
      earlier += `\n${n}`;
    }
    // ログインが要る画面
    if (this.hasLogin) {
      const all = tx.normalize([procedureText(c), expectedText(c)].join('\n'));
      const stepText = tx.normalize(c.steps.map((s) => s.text).join('\n'));
      const m = AUTH_SCREENS.exec(stepText);
      if (m && !LOGIN_WORD.test(all) && !UNAUTH.test(all)) {
        this.f.add('X02', `「${m[0]}」はログインしてから使うものですが、ログインする前提も手順もありません`, { case: c, hint: '前提に「ログイン済み（どの利用者か）」を書くか、ログインする手順を足す。ログインしていないときの動きを試すなら、前提にそう書く' });
      }
    }
    // 始める画面
    const first = tx.normalize(c.steps[0].text);
    if (!ENTRY.test(first) && !START_STATE.test(pre)) {
      this.f.add('X03', 'どの画面・状態から始めるかが書かれていません', { case: c, item: c.steps[0], excerpt: excerpt(c.steps[0].text), hint: '前提に始める画面を書くか、最初の手順を「〇〇画面を開く」にする' });
    }
  }

  // ---- 条件の組み合わせ ----

  model() {
    const rows = this.cases.map((c) => {
      const params = { ...inferredInputs(c), ...(c.conditions || {}) };
      return { c, params, explicit: c.conditions || {}, negative: this.isNegative(c) };
    }).filter((r) => Object.keys(r.params).length);
    this.decisions(rows);
    this.invalidClasses(rows);
    this.pairwise(rows);
  }

  // 同じ条件の組で結果が逆（C05）・同じ結果を重ねて確かめている（U05）
  decisions(rows) {
    const byKey = new Map();
    for (const r of rows) {
      const key = Object.keys(r.params).sort().map((k) => `${tx.normalize(k)}=${tx.normalize(r.params[k])}`).join('\u0001');
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(r);
    }
    const reported = this.ctx.reportedPairs || new Set();
    for (const [key, rs] of byKey) {
      if (rs.length < 2) continue;
      const shown = key.split('\u0001').slice(0, 4).join('・');
      for (let a = 0; a < rs.length; a++) {
        for (let b = a + 1; b < rs.length; b++) {
          const ca = rs[a].c; const cb = rs[b].c;
          const pairKey = [ca.index, cb.index].sort((x, y) => x - y).join(',');
          if (reported.has(pairKey)) continue;
          // 手順が別の画面・操作なら、同じ値でも別の振る舞いを見ている
          const steps = tx.vectorize([tx.maskValues(procedureText(ca)), tx.maskValues(procedureText(cb))]);
          if (tx.cosine(...steps) < 0.6) continue;
          const hit = tx.opposite(expectedText(ca), expectedText(cb));
          if (hit) {
            this.f.add('C05', `同じ条件（${shown}）なのに、期待結果が逆です（「${excerpt(hit[0].text, 30)}」と「${excerpt(hit[1].text, 30)}」）`, { case: ca, others: [cb], hint: 'どちらが正しいかを仕様で確かめる。結果を分ける条件があるなら、条件に足す' });
            reported.add(pairKey);
            continue;
          }
          const ev = tx.vectorize([tx.maskValues(expectedText(ca)), tx.maskValues(expectedText(cb))]);
          if (tx.cosine(...ev) >= 0.8 && rs[a].negative === rs[b].negative) {
            this.f.add('U05', `同じ条件（${shown}）で、同じ結果を確かめています`, { case: ca, others: [cb], hint: '1 件で足りる。別のことを確かめたいなら、条件か期待結果を変える' });
            reported.add(pairKey);
          }
        }
      }
    }
  }

  // 有効な値しか試していない条件（M02）。入力した値ごとに、異常系のケースがあるかを見る
  invalidClasses(rows) {
    const byParam = new Map();
    for (const r of rows) {
      for (const k of Object.keys(r.params)) {
        const name = tx.normalize(k);
        if (!byParam.has(name)) byParam.set(name, { name: k, rows: [] });
        byParam.get(name).rows.push(r);
      }
    }
    for (const { name, rows: rs } of byParam.values()) {
      if (rs.length < 2) continue;
      const values = new Set(rs.map((r) => tx.normalize(r.params[Object.keys(r.params).find((k) => tx.normalize(k) === tx.normalize(name))])));
      if (values.size < 2 && rs.length < 3) continue;
      if (rs.some((r) => r.negative)) continue;
      this.f.add('M02', `「${name}」は通る値しか試していません（${rs.length} 件・値 ${values.size} 種類）`, { case: rs[0].c, others: rs.slice(1, 4).map((r) => r.c), hint: `「${name}」に受け付けない値（形式違い・範囲外・空など）を入れたときのケースを足す` });
    }
  }

  // 2 つずつの組み合わせ（M01）。conditions に書いた条件だけを使う（手順から推した値は揺れるので使わない）
  pairwise(rows) {
    const byFile = new Map();
    for (const r of rows) {
      if (Object.keys(r.explicit).length < 2) continue;
      if (!byFile.has(r.c.file)) byFile.set(r.c.file, []);
      byFile.get(r.c.file).push(r);
    }
    for (const rs of byFile.values()) {
      const domains = new Map();
      for (const r of rs) for (const [k, v] of Object.entries(r.explicit)) { if (!domains.has(k)) domains.set(k, new Set()); domains.get(k).add(v); }
      const keys = [...domains.keys()].filter((k) => domains.get(k).size >= 2);
      if (keys.length < 3) continue; // 2 つだけなら全組み合わせの話になり、ペアの網羅としては扱わない
      const uncovered = [];
      for (let a = 0; a < keys.length; a++) {
        for (let b = a + 1; b < keys.length; b++) {
          for (const va of domains.get(keys[a])) {
            for (const vb of domains.get(keys[b])) {
              if (!rs.some((r) => r.explicit[keys[a]] === va && r.explicit[keys[b]] === vb)) uncovered.push([[keys[a], va], [keys[b], vb]]);
            }
          }
        }
      }
      if (!uncovered.length) continue;
      const extra = suggestRows(keys, domains, uncovered);
      const shown = uncovered.slice(0, 5).map(([[ka, va], [kb, vb]]) => `${ka}=${va}×${kb}=${vb}`).join('、');
      this.f.add('M01', `条件の組み合わせ（2 つずつ）のうち ${uncovered.length} 組を試していません: ${shown}${uncovered.length > 5 ? ' ほか' : ''}`, {
        case: rs[0].c,
        hint: `あと ${extra.length} 件で全部の組を試せる。例: ${extra.slice(0, 3).map((x) => `{${Object.entries(x).map(([k, v]) => `${k}: ${v}`).join(', ')}}`).join(' ')}`,
      });
    }
  }
}

// 名詞のまとまり（文の末尾側でいちばん近いもの）
function lastNoun(text) {
  const toks = tx.tokens(text);
  const words = []; let started = false;
  for (let k = toks.length - 1; k >= 0; k--) {
    const t = toks[k];
    if (t.pos === '名詞' && !['非自立', '代名詞', '数'].includes(t.pos2) && /[^\x00-\x7f]/.test(t.surface)) { words.push(t.surface); started = true; } else if (started) break;
  }
  return words.reverse().join('');
}

// 残った組を、少ない行で埋める（貪欲法）
function suggestRows(keys, domains, uncovered) {
  const left = new Set(uncovered.map(([[ka, va], [kb, vb]]) => `${ka}\u0001${va}\u0002${kb}\u0001${vb}`));
  const pairKey = (ka, va, kb, vb) => (keys.indexOf(ka) < keys.indexOf(kb) ? `${ka}\u0001${va}\u0002${kb}\u0001${vb}` : `${kb}\u0001${vb}\u0002${ka}\u0001${va}`);
  const rows = [];
  while (left.size && rows.length < 50) {
    const [first] = left;
    const [[ka, va], [kb, vb]] = first.split('\u0002').map((p) => p.split('\u0001'));
    const row = { [ka]: va, [kb]: vb };
    for (const k of keys) {
      if (k in row) continue;
      let best = null; let bestGain = -1;
      for (const v of domains.get(k)) {
        const gain = Object.entries(row).filter(([k2, v2]) => left.has(pairKey(k, v, k2, v2))).length;
        if (gain > bestGain) { best = v; bestGain = gain; }
      }
      row[k] = best;
    }
    const ks = Object.keys(row);
    for (let a = 0; a < ks.length; a++) for (let b = a + 1; b < ks.length; b++) left.delete(pairKey(ks[a], row[ks[a]], ks[b], row[ks[b]]));
    rows.push(Object.fromEntries(keys.map((k) => [k, row[k]])));
  }
  return rows;
}

module.exports = { Validity, inferredInputs, suggestRows, lastNoun };
