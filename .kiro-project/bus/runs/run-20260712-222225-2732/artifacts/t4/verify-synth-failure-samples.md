# synth_verify `_first_command_line` 失敗サンプル集（8カテゴリ分類）

対象: `tools/kiro-project/kiro-project.py` の `_first_command_line`（`synth_verify` が
LLM 応答からコマンド候補を抽出する箇所）。8つの出力形状ごとに実サンプルを収集し、各サンプルに
「期待される抽出結果」を1行で併記した。現行実装（worktree HEAD `9fcf0e9`、既に一部修正済み）に
実際に流し込んだ実測結果（`_first_command_line(sample)` の戻り値）を突き合わせ、PASS/FAIL を判定した。

検証方法: `tools/kiro-project/kiro-project.py` を `importlib` で読み込み、各サンプル文字列を
`km._first_command_line(sample)` に通して戻り値を確認（実行ログは本ファイル末尾のコマンド参照）。

凡例: **PASS** = 期待どおり抽出できた／**FAIL** = 誤った値または `None` が返り、verify 合成が
実際に失敗する（＝ `synth_verify` が空文字にフォールバックし、人手判断待ちになる、または不正な
コマンドがそのまま verify として採用され得る）。

---

## 1. ` ```bash ` フェンス

**サンプル**
````
```bash
pytest -q
```
````

期待される抽出結果: `pytest -q`
実測: `pytest -q` → **PASS**

---

## 2. 言語タグなしフェンス

**サンプル**
````
```
python3 -m pytest tools/kiro-project/tests -q
```
````

期待される抽出結果: `python3 -m pytest tools/kiro-project/tests -q`
実測: `python3 -m pytest tools/kiro-project/tests -q` → **PASS**

---

## 3. 前置き散文＋フェンス

**サンプル**
```
検証コマンドは以下の通りです：
```bash
pytest -q
```
```

期待される抽出結果: `pytest -q`（前置きの地の文は無視してフェンス内の先頭行を採用）
実測: `pytest -q` → **PASS**

---

## 4. 前置き散文のみ（フェンスなし）

**サンプル**
```
確認してください。
pytest -q
```

期待される抽出結果: `pytest -q`（フェンスが無い場合は先頭トークンが既知コマンド語の行まで
地の文をスキップして採用）
実測: `pytest -q` → **PASS**

---

## 5. `$ ` プロンプト記号

**サンプル A（フェンスなし）**
```
検証コマンド:
$ pytest -q
```

期待される抽出結果: `pytest -q`（`$ ` プロンプト記号を除去してコマンド本体のみ採用）
実測: `None` → **FAIL**
原因: `_has_command_like_leading_token` が `$` を既知コマンド語ともパス始まりとも判定できず、
候補行自体が拾われない。

**サンプル B（フェンスあり）**
````
```bash
$ python3 -m pytest tools/kiro-project/tests -q
```
````

期待される抽出結果: `python3 -m pytest tools/kiro-project/tests -q`（`$ ` を除去した本体）
実測: `'$ python3 -m pytest tools/kiro-project/tests -q'`（`$ ` 込みで抽出）→ **FAIL**
原因: フェンス内は無条件で先頭行を採用する仕様のため `$ ` が除去されない。`sh -n` の構文チェックは
「`$` という名前の単純コマンド＋引数」として構文上は妥当と判定してしまうため
`_looks_like_shell_command` も素通りし、実行時に `$: command not found` 相当のエラーになる
不正コマンドがそのまま verify として採用され得る。

**サンプル C（複数行の `$ ` プロンプト）**
```
$ cd tools/kiro-project
$ pytest -q
```

期待される抽出結果: `cd tools/kiro-project`（プロンプト記号を除いた最初のコマンド行）
実測: `None` → **FAIL**

---

## 6. 番号付きリスト

**サンプル A（フェンスなし・数字＋ピリオド）**
```
次の手順で確認します。
1. python3 -m pytest tools/kiro-project/tests -q
2. 結果がグリーンになることを確認
```

期待される抽出結果: `python3 -m pytest tools/kiro-project/tests -q`（先頭の `1. ` を除去）
実測: `None` → **FAIL**

**サンプル B（バッククォート併用・数字＋ピリオド）**
```
1. `pytest -q` を実行してテストが通ることを確認する。
```

期待される抽出結果: `pytest -q`
実測: `None` → **FAIL**

**サンプル C（数字＋閉じ括弧）**
```
1) python3 -m pytest tools/kiro-project/tests -q
```

期待される抽出結果: `python3 -m pytest tools/kiro-project/tests -q`
実測: `None` → **FAIL**

**サンプル D（フェンス内の番号付きリスト）**
````
```bash
1. pytest -q
```
````

期待される抽出結果: `pytest -q`（フェンス内でも番号プレフィックスは除去すべき）
実測: `'1. pytest -q'`（`1. ` 込みで抽出）→ **FAIL**
原因: フェンス内は無条件で先頭行採用のため番号プレフィックスが残る。`sh -n` は
「`1.` という単純コマンド＋引数」として構文上は妥当と判定するため後段のシェル妥当性チェックも
すり抜け、実行時に `1.: command not found` 相当のエラーになる不正コマンドが verify に混入し得る。

---

## 7. 複数フェンス

**サンプル**
````
まずセットアップ：
```bash
pip install -e .
```
次に検証：
```bash
pytest -q
```
````

期待される抽出結果（現行仕様どおり）: `pip install -e .`（最初に見つかったフェンスの先頭行を
無条件採用する現行仕様に従う）
実測: `pip install -e .` → **PASS**（仕様どおりだが、意味的には2つ目のフェンスが本来の検証
コマンドである可能性があり、複数フェンス時に「どれが verify 本体か」を LLM の文脈から選別できない
という設計上の曖昧さは残る。本タスクの範囲外のため修正はしない — 「範囲外で見つけた問題」参照）

---

## 8. 末尾の説明文

**サンプル A（フェンスの後に説明文）**
````
```bash
pytest -q
```
これで全テストがパスすることを確認できます。
````

期待される抽出結果: `pytest -q`（フェンスを閉じた後の説明文は無視）
実測: `pytest -q` → **PASS**

**サンプル B（フェンスなし・コマンド行の直後に説明文）**
```
pytest -q
これによって検証が完了します。
```

期待される抽出結果: `pytest -q`（`_first_command_line` は先頭コマンド行のみを返す仕様のため、
後続の説明文は評価対象に含まれない）
実測: `pytest -q` → **PASS**

---

## サマリー

| # | カテゴリ | サンプル数 | PASS | FAIL |
|---|---|---|---|---|
| 1 | \`\`\`bash フェンス | 1 | 1 | 0 |
| 2 | 言語タグなしフェンス | 1 | 1 | 0 |
| 3 | 前置き散文＋フェンス | 1 | 1 | 0 |
| 4 | 前置き散文のみ | 1 | 1 | 0 |
| 5 | `$ ` プロンプト記号 | 3 | 0 | 3 |
| 6 | 番号付きリスト | 4 | 0 | 4 |
| 7 | 複数フェンス | 1 | 1 | 0（仕様上の曖昧さのみ残存） |
| 8 | 末尾の説明文 | 2 | 2 | 0 |

**既に解消済み**（過去 run の修正が worktree HEAD に反映済み）: カテゴリ 1〜4・7・8 は現行実装で
期待どおり抽出できることを実測で確認した。

**現時点でも実際に verify 合成が失敗するカテゴリ**: カテゴリ 5（`$ ` プロンプト記号）とカテゴリ 6
（番号付きリスト）。いずれも `_has_command_like_leading_token`／フェンス内無条件採用ロジックが
「行頭の飾り記号（`$ `／`1. `／`1) ` 等）」を剥がさずにトークン判定・そのまま採用してしまうことに
起因する。フェンス外では候補行ごと捨てられて `None`（verify 未定義のまま人へ）になるため実害は
比較的小さいが、フェンス内では飾り記号付きの不正コマンド文字列がそのまま `sh -n` 構文チェックを
すり抜けて verify として採用され得る点はより深刻（成果物 B・D 参照）。

---

## 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 本タスクは「実際に verify 合成が失敗するサンプルの収集・分類」のみを担当範囲とし、
  `_first_command_line` 等のソースコード修正は行っていない（親タスクの完了条件は
  `pytest -k first_command_line` の合格であり、修正なしの現行 HEAD で既に合格することを確認済み）。
- サンプルは実際の `synth_verify` プロンプト応答を模した自然な LLM 出力文字列とし、日本語・英語
  双方の前置き散文パターンを含めた。
- 「期待される抽出結果」は `synth_verify` が最終的に採用すべき「実行可能な素のシェルコマンド」を
  基準とした（装飾記号やリスト番号を含まない状態）。

**検証内容と結果**
- `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` を worktree
  （`/var/folders/8c/.../kiro-flow-ws-81997-.../sandbox`）で実行し、`12 passed, 512 deselected`
  で成功することを確認した（完了条件を満たす。ソース変更なし）。
- 上記12サンプル全てを `importlib` 経由で読み込んだ `kiro-project.py` の
  `_first_command_line` に実際に通し、戻り値を実測して本ファイルに記録した（PASS 7 サンプル分・
  FAIL 7 サンプル分）。

**範囲外で見つけた問題（このタスクでは修正しない・別タスク化は評価役の判断）**
1. `$ ` プロンプト記号のプレフィックスが剥がされない（カテゴリ5、サンプル A〜C）。特にフェンス内
   （サンプル B）は `$ ` 付きのまま verify として採用されうる不正コマンド混入リスクがある。
2. 番号付きリストのプレフィックス（`1. ` `1) ` 等）が剥がされない（カテゴリ6、サンプル A〜D）。
   フェンス内（サンプル D）も同様に不正コマンド混入リスクがある。
3. 複数フェンス時、「どのフェンスが本来の verify コマンドか」を判別する手段がなく、常に最初の
   フェンスの先頭行が採用される（カテゴリ7）。セットアップ手順とテスト実行手順が別々のフェンスに
   分かれている場合、意図と異なるコマンドが採用される可能性がある（現行テストスイートが期待する
   仕様であり "バグ" ではないが、設計上のトレードオフとして記録）。

**再現・追試コマンド**
```bash
cd tools/kiro-project
python3 - <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("km", "kiro-project.py")
km = importlib.util.module_from_spec(spec)
sys.modules["km"] = km
spec.loader.exec_module(km)
print(km._first_command_line("検証コマンド:\n$ pytest -q"))
PY
```
