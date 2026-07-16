# t1: 検証コマンド: ラベル入力バリアント列挙

## (a) 成果

### 対象コード
- `tools/agent-project/agent_project/verify.py`
  - `_first_command_line`（431行目）: 合成出力から先頭のコマンド行を抽出するエントリポイント
  - `_strip_leading_command_label`（347行目）: 日本語ラベル『検証コマンド:』を剥がす本体
  - `_strip_leading_shell_prompt`（338行目）: 行頭 `$ ` を剥がす
  - `_code_fence_lines`（300行目）: Markdown コードフェンス内行の抽出
  - `_first_executable_line`（412行目）: 候補行から最初のコマンドを選ぶ
  - `synth_verify`（463行目）: `_first_command_line` の呼び出し元。合成 LLM 出力→候補抽出→
    Windows シェル判定→自然言語判定→恒真式判定、を経て verify コマンドを確定する合成フロー

### 抽出パイプライン（verify 合成フロー内での位置づけ）
1. `synth_verify` がエージェント CLI を呼び、自然言語の完了条件から verify コマンド案を含む
   自由形式テキスト（`out`）を得る。
2. `_first_command_line(out)` で候補行を1つに絞る:
   a. まず `strip_ansi` で ANSI エスケープを除去。
   b. コードフェンス（` ``` `）があれば最優先: フェンス内の最初の非空・非コメント行を
      `require_shell_syntax=False` で無条件採用（`_strip_leading_command_label` →
      `_strip_leading_shell_prompt` → `_strip_code` の順でラベル・プロンプト・バッククォートを
      剥がした後に判定）。
   c. フェンスが無ければフェンス外の全行に対し `_has_command_like_leading_token` で
      候補を絞り込み、`_first_executable_line`（`require_shell_syntax=True`、`sh -n` で構文検証）
      へフォールバック。
3. `synth_verify` は候補を Windows シェル判定 → 自然言語判定 → 恒真式判定に通し、いずれも
   通れば採用、落ちれば再合成（最大 `attempts` 回）。

### 「検証コマンド:」ラベル入力バリアント一覧（実装が対応する形式）
`_strip_leading_command_label` は正規表現 `^.*?検証コマンド\s*[:：]\s*` を「変化がなくなるまで」
繰り返し適用し、`_strip_leading_shell_prompt` の**後**（＝内側の呼び出し）に適用される
（`_strip_leading_command_label(_strip_leading_shell_prompt(line))` という合成順）。
実際に `_first_command_line` へ通して確認した結果（`CMD = 'codd-gate verify --base "$KIRO_BASE_REV"'`）:

| # | バリアント | 入力例 | 結果 |
|---|---|---|---|
| 1 | ラベル単独行＋コマンド次行 | `検証コマンド:\n{CMD}` | ✅ CMD |
| 2 | ラベルとコマンドが同一行（半角コロン） | `検証コマンド: {CMD}` | ✅ CMD |
| 3 | ラベルとコマンドが同一行（全角コロン `：`） | `検証コマンド：{CMD}` | ✅ CMD |
| 4 | ラベル前に散文が同居（同一行） | `以下を実行してください。検証コマンド: {CMD}` | ✅ CMD |
| 5 | ラベル前に散文が別行・複数行 | `これで確認できます。\n検証コマンド:\n{CMD}` | ✅ CMD |
| 6 | ラベル行の前後に空行 | `検証コマンド:\n\n{CMD}\n` | ✅ CMD（空行は候補フィルタで自然に脱落） |
| 7 | ラベルの二重・多重付与 | `検証コマンド: 検証コマンド: {CMD}` | ✅ CMD（while ループで収束） |
| 8 | ラベルとコマンドの間にコメント行 | `検証コマンド:\n# コメント\n{CMD}` | ✅ CMD（`#` 始まり行は候補から除外） |
| 9 | ラベル＋コードフェンス（フェンスが次行） | ``検証コマンド:\n```bash\n{CMD}\n```` | ✅ CMD（フェンス最優先パス） |
| 10 | ラベル＋フェンス開始が同一行 | ``検証コマンド: ```bash\n{CMD}\n```` | ✅ CMD（フェンス最優先パスがラベル行を素通り） |
| 11 | ラベル＋`$` プロンプト（別行） | `検証コマンド:\n$ {CMD}` | ✅ CMD |
| 12 | ラベル＋`$` プロンプトが**同一行** | `検証コマンド: $ {CMD}` | ❌ `None`（範囲外の既知の穴。下記参照） |
| 13 | フェンス内にラベル行がある | ``` ```\n検証コマンド: {CMD}\n``` ``` | ✅ CMD |
| 14 | コマンド行の後に説明文が続く | `検証コマンド:\n{CMD}\nこれでOKです。` | ✅ CMD（最初の実行可能行のみ採用） |
| 15 | `$KIRO_BASE_REV` を含む二重引用符の扱い | `codd-gate verify --base "$KIRO_BASE_REV"`（ラベル無し単体） | ✅ CMD（`"..."` 内の `$VAR` は `_strip_leading_shell_prompt` の対象外＝`^\$\s+` 限定のため無傷で保持される） |

`$KIRO_BASE_REV` の引用符について: コマンド文字列中の `"$KIRO_BASE_REV"` はダブルクォートで
囲まれた変数参照であり、`_strip_leading_shell_prompt` は行頭の `$ `（`$` 直後が空白）だけを
対象にした正規表現 `^\$\s+` のため、`"$KIRO_BASE_REV"` のような `$` 直後が引用符や変数名の
ケースには一切作用しない。またコマンド内の `:` はラベル固定文字列 `検証コマンド` を伴わない限り
`_strip_leading_command_label` の対象にならない（`git commit -m "note: fix bug"` のような
コロンを含む通常コマンドを誤って割らない）。

## (b) 検証内容と結果
- 上記 15 バリアントすべてを `PYTHONPATH=tools/agent-project python3` で実際に
  `_first_command_line` に通して確認した（結果は表内の ✅/❌ の通り）。
- 本タスクの完了条件コマンドを実行し、終了コード 0（成功）を確認済み:
  ```
  PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
  ```
  → 現行実装のまま成功。**コード変更は行っていない**（本タスクは読解・列挙のみが範囲）。
- 既存テスト（`tools/agent-project/tests/test_agent_project.py:5899-5943`）が上記バリアントの
  多く（同一行ラベル・全角コロン・二重ラベル・前置き散文・コメントのみ／散文のみで None）を
  既に個別ケースとしてカバーしていることを確認した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題
- **前提**: 本タスクの範囲は「現行実装の読解とバリアント列挙」であり、コード修正は行わない
  （worktree は変更なしのまま）。完了条件コマンドは現行実装で既に成功するため、追加実装は
  不要と判断した。
- **範囲外で見つけた問題**: バリアント #12 `検証コマンド: $ {CMD}`（ラベルと `$` プロンプトが
  同一行に同居）が `None` を返す。原因は `_strip_leading_shell_prompt` → `_strip_leading_command_label`
  という合成順（`_strip_leading_command_label(_strip_leading_shell_prompt(line))`）で、入力が
  `検証コマンド: $ ...` のように**ラベルが先頭にある**場合、内側の `_strip_leading_shell_prompt`
  は行頭が `検` であるため何もせず、外側でラベルを剥がした後の `$ ...` は再度プロンプト剥がしに
  かけられない（剥がし後の再帰的なプロンプト除去がない）ため。実際の LLM 出力で
  「ラベルと `$` プロンプトが同一行」という組み合わせは、別行パターン（#11、対応済み）に比べ
  出現頻度が低いと見られるが、`synth_verify` のプロンプト次第では起こり得る。修正要否・要否の
  判断は評価役に委ねる（本タスクは列挙のみが範囲のため未修正）。
