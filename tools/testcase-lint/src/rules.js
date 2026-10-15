'use strict';
// 規則の一覧。コード・分類・既定の重さ・短い名前は、出力・--rules・設定の上書きがすべてこの表を引く。
//
// コードの頭文字は、どの観点の規則かを表す。
//   D 書き方の不備 / A 曖昧（人によって実行・判定が変わる）/ U 重複 / C 矛盾 / G 漏れ（ケースの中だけで分かるもの）
//   O 判定の強さ / X 実行できるか / M 条件の組み合わせ（ケースから取り出した表）
//   S 仕様との突き合わせ（--spec） / I 実装との突き合わせ（--source）

const CATEGORY = {
  defect: '不備', ambiguity: '曖昧', duplicate: '重複', conflict: '矛盾', gap: '漏れ', oracle: '判定', exec: '実行',
};
const SEVERITY_ORDER = { info: 0, warning: 1, error: 2 };
const SEVERITY_LABEL = { error: 'エラー', warning: '警告', info: '参考' };

const D = CATEGORY.defect, A = CATEGORY.ambiguity, U = CATEGORY.duplicate, C = CATEGORY.conflict, G = CATEGORY.gap;
const O = CATEGORY.oracle, X = CATEGORY.exec;

const RULES = {
  D00: [D, 'error', 'ファイルを読めない'],
  D01: [D, 'warning', 'ID がない'],
  D02: [D, 'error', 'ID が重複している'],
  D03: [D, 'warning', 'タイトルがない'],
  D04: [D, 'warning', '手順がない'],
  D05: [D, 'error', '期待結果がない'],
  D07: [D, 'error', '書きかけ（TBD・要確認など）'],
  D09: [D, 'warning', '他のケースに依存している'],
  D10: [D, 'warning', '期待結果に操作が書かれている'],
  D11: [D, 'info', '手順に確認が混ざっている'],
  D12: [D, 'info', '1 つの手順に操作が多い'],
  D13: [D, 'warning', '要件の紐づけがない'],
  D14: [D, 'warning', '要件一覧にない要件を指している'],
  D15: [D, 'error', '同じキーが 2 回書かれている'],
  D16: [D, 'error', '指定した書式に合わない'],
  D17: [D, 'warning', 'ケースを 1 件も読み取れない'],
  D18: [D, 'warning', '実在しそうなメールアドレスを使っている'],
  D19: [D, 'error', '本物らしいカード番号・秘密の鍵が書かれている'],
  A01: [A, 'warning', '合否の基準があいまい'],
  A02: [A, 'warning', '使う値が決まっていない'],
  A03: [A, 'warning', '量・回数・時間があいまい'],
  A04: [A, 'warning', '指すものがあいまい'],
  A05: [A, 'warning', '期待結果に選択肢がある'],
  A06: [A, 'warning', '期待結果を言い切っていない'],
  A07: [A, 'warning', '比べる基準がない'],
  A08: [A, 'warning', '操作の対象があいまい'],
  A09: [A, 'info', '列挙が閉じていない'],
  U01: [U, 'error', '完全に同じケース'],
  U02: [U, 'warning', 'ほぼ同じケース'],
  U03: [U, 'info', '値だけが違うケース（表にまとめられる）'],
  U04: [U, 'info', '同じタイトルで中身が違う'],
  U05: [U, 'warning', '同じ条件の組で同じ結果を確かめている'],
  C01: [C, 'error', '同じ手順で逆の期待結果'],
  C02: [C, 'error', 'ケースの中で食い違う'],
  C03: [C, 'warning', '同じ手順で期待結果が違う'],
  C04: [C, 'warning', '数値の条件が食い違う'],
  C05: [C, 'error', '同じ条件の組で結果が逆'],
  C06: [C, 'warning', '期待する文言が、ほかのケースと句読点・空白だけ違う'],
  C07: [C, 'info', '期待する文言が、ほかのケースと 1〜2 文字だけ違う'],
  G02: [G, 'warning', '異常系がない'],
  G03: [G, 'warning', '境界値を試していない'],
  G04: [G, 'info', '対になる条件を試していない'],
  O01: [O, 'warning', '期待結果が見て確かめられない'],
  O02: [O, 'warning', '期待結果が弱い（何が出ても通る）'],
  O03: [O, 'info', '異常系で、変わらないことを確かめていない'],
  O04: [O, 'warning', 'エラーになることしか確かめていない'],
  X01: [X, 'warning', '前提にないデータを使っている'],
  X02: [X, 'warning', 'ログインしていないのにログイン後の操作をしている'],
  X03: [X, 'info', '始める画面が決まっていない'],
  M01: [G, 'info', '条件の組み合わせ（2 つずつ）を試していない'],
  M02: [G, 'warning', '無効な値を試していない条件'],
  S01: [G, 'error', '仕様の要件を確かめるケースがない'],
  S02: [G, 'warning', '仕様の項目の制約を試していない'],
  S03: [G, 'warning', '仕様の状態遷移を試していない'],
  S04: [G, 'warning', '仕様の権限の組み合わせを試していない'],
  S05: [G, 'warning', '仕様の API の応答を試していない'],
  S06: [C, 'warning', '期待する文言が仕様にない'],
  S07: [G, 'info', '仕様の文言をどのケースも確かめていない'],
  S08: [C, 'warning', '仕様にない状態遷移を期待している'],
  I01: [C, 'warning', '期待する文言が実装にない'],
  I02: [C, 'warning', '期待するエラーコードが実装にない'],
};

module.exports = { RULES, CATEGORY, SEVERITY_ORDER, SEVERITY_LABEL };
