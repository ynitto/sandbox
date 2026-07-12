# `_first_command_line` 現行実装調査

## (a) 成果／サマリー

対象: `tools/kiro-project/kiro-project.py`

関数本体（現行の行番号付き）:

```text
2883 def _first_command_line(out: str) -> str:
2884     """合成出力の先頭の「意味あるコマンド行」を取り出す（コメント/コードフェンス/空行を飛ばす）。"""
2885     for line in (out or "").splitlines():
2886         line = _strip_code(line.strip())
2887         if line and not line.startswith("#"):
2888             return line
2889     return ""
```

現在の行選択ロジックは次のとおり。

1. `out` が偽値なら空文字列に置換し、`splitlines()` で先頭から各行を走査する。
2. 各行の前後空白を `line.strip()` で除去する。
3. `_strip_code(...)` で ANSI エスケープを除去し、文字列全体がバッククォートで始まり終わる場合は、先頭と末尾を1文字ずつ除去する。
4. 正規化後の行が空でなく、かつ `#` で始まらない最初の行を即座に返す。
5. 該当行がなければ空文字列を返す。

`_first_command_line` 自体は正規表現を使用していない。間接的に `_strip_code` → `strip_ansi` が使用する正規表現と補助関数の現行実装は以下。

```text
119 _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

122 def strip_ansi(text: str) -> str:
123     """端末カラー等の ANSI エスケープを除去する。
124     kiro-cli の出力にはカラーコードが混ざるため、合成した verify を
125     シェルで実行する前に正規化しないと `\\x1b[..m` が混入してコマンドが壊れる。"""
126     return _ANSI_RE.sub("", text or "")

129 def _strip_code(val: str) -> str:
130     v = strip_ansi(val).strip()
131     if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
132         return v[1:-1]
133     return v
```

除外条件は、正規化後が空文字列であること、または正規化後の先頭文字が `#` であることだけ。コードフェンス専用の正規表現・状態管理・除外条件は存在しない。そのため、たとえば開始フェンス ` ```sh ` はそのまま非空行として選択される。閉じフェンス ` ``` ` も `_strip_code` により外側のバッククォート2文字だけが外れて `` ` `` となり、非空行として選択対象になる。docstring の「コードフェンスを飛ばす」は現行挙動と一致しない。また、LLM の前置きが非空かつ `#` 始まりでなければ、それがコマンドか否かをここでは判定せず最初の候補として返す。

ソースコードは改変していない。

## (b) 検証内容と結果

- 指定 worktree で対象関数、呼び出し元、補助関数を読み取り、行番号とロジックを確認した。
- 指定コマンド `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` は、システム Python では `No module named pytest` により終了コード 1。
- pytest 導入済み環境を PATH に指定して同じコマンドを再実行した結果は `512 deselected in 0.19s`、終了コード 5。現行テスト群には `first_command_line` に一致するテストが存在しないため、終了コード 0 の完了条件は満たせなかった。

## (c) 採用した前提・未解決事項・範囲外

- 前提: この調査タスクの成果は「指定 worktree の現行実装を改変せず、行番号付きで正確に転記し、選択ロジック・関与する正規表現・除外条件を明示すること」と解釈した。
- 前提: 「正規表現」には `_first_command_line` が直接使うものがないため、正規化経路で間接的に使われる `_ANSI_RE` を記載した。
- 未解決: 完了条件の pytest 終了コード 0 は、該当テストが存在しない現状と「改変はしない」という制約が両立せず、達成不能だった。後続の修正・テスト追加タスクで該当テストが追加された後に再実行が必要。
- 範囲外で確認した問題: docstring はコードフェンスを飛ばすと述べるが、実装はコードフェンスを除外しない。本タスクでは修正していない。
