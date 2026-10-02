## [collect: 比べる組を集める]

**いつからの変更を見るか:** {{since}}

この段では**どのファイルも変えないでください**（書くのは `.codd/drift-candidates.md` だけ）。

1. codd を通らずに入った変更を出します。

   ```bash
   python3 .statemachine/codd/codd.py lint --no-test --since "{{since}}"
   ```

   `.codd/lint.md` の「codd を通らなかった変更」が対象です（コミットとファイル）。無ければ 4 へ。
2. コミットごとに `git show -U0 コミット -- ファイル` で変わった行を見て、意味を持つもの（値・条件・順番・
   戻り値・メッセージ・見出しの中身）を選び、その名前（関数・設定・用語・見出し）を 1〜5 個取ります。
3. 相手の側で同じものを書いている箇所を探します。実装の変更なら参照先（設計書）を、設計書の変更なら自分を引きます。

   ```bash
   python3 .statemachine/codd/codd.py explore --term "名前"   # 参照先を引く
   python3 .statemachine/codd/codd.py impact --term "名前"    # 自分を引く
   ```

   一致した行の前後だけを読み、**同じことを両方が述べている**組だけを候補にします（名前が出てくるだけの行は外す）。
4. `.codd/drift-candidates.md` に、1 組 1 行で書きます。多くても 10 組。参照先のファイルは `名前:パス`
   （`.statemachine/codd/codd.json` の refs の name）、自分のファイルはパスだけ。

   ```markdown
   - src/app.py:2 ⇔ docs:docs/api.md:5 — hello の戻り値（abc1234 で変わった）
   ```

   候補が無ければ `なし` とだけ書きます。

**出力形式:** 第 1 行に、候補があれば `OK`、無ければ `NONE`。
