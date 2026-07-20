# t3: codd_gate_regression.py の `--config` 結線導線を CLI として成立させる — 作業報告

対象: `tools/agent-project/codd_gate_regression.py` と `tests/test_codd_gate_regression.py` の2ファイルのみ。
`agent_project/` パッケージ内・dashboard・README・他の `codd_gate_*.py` は一切変更していない。

## (a) 成果

t2 で自動配線が切れた結果、この CLI は「検出 → 推奨文字列の生成 → yaml 冪等注入」へ到達する
唯一の書き込み経路になった。実際に叩いて壊れていた点・言語化されていなかった点を埋めた。

### 1. `--config` 不在時のエラー（本タスクの中心）

変更前は、存在しないパスを `--config` に渡すと**親ディレクトリごと新規作成し、`root:` も何も無い
`regression_cmd` 1行だけの yaml を書き出して exit 0** を返していた。agent-project 本体から読めない
設定ファイルを CLI が新造する挙動で、しかも成功として報告される。

「無ければ作る」をやめ、不在は前提条件エラーとして扱う（`_read_config_text`）。

```
error: 設定ファイルが見つかりません: /path/.agent/absent.yaml
       agent-project.yaml を先に用意するか、--config で実在するパスを指定してください（雛形: agent-project.yaml.example）。
```

エラーは stderr、成功時の JSON は stdout のまま。`--dry-run` でも同じ検査を通す——`--dry-run` は
「書かない」オプションであって前提条件の検査を飛ばす指定ではないため。ディレクトリを渡す等の
読めない指定（`OSError` / `UnicodeDecodeError`）もトレースバックを出さずに同じ経路へ畳む。

### 2. 終了コードの三分割

| コード | 意味 |
|---|---|
| 0 | 注入した／既に正しい値だった（`--dry-run` の「変わるはず」も 0） |
| 2 | 引数エラー、または `--config` が不在・読めない（何も書いていない） |
| 3 | codd-gate が使えない（未検出・バージョン/schema 非互換）ので no-op 縮退した |

`EXIT_OK` / `EXIT_CONFIG_ERROR` / `EXIT_CODD_GATE_UNAVAILABLE` として定数化した。2 を選んだのは
argparse 自身の usage エラーと同じ値だから（`--config` 不在は「引数の値が現実と合わない」で同種）。

3 を 0 と分けたのは、t2 の `codd_gate_wiring.py` CLI が「所見の有無を終了コードに反映しない」と
決めたのと**意図的に違える**判断。あちらは読むだけの報告 CLI で、未結線は壊れた状態ではない。
こちらは利用者が結線を頼んで起動する書き込み CLI であり、何も起きなかったことを呼び出し側が
判別できないと `codd_gate_regression.py --config ... && echo enabled` のような使い方が嘘をつく。
「壊れている」とは言わないよう stderr は `warning:` にし、理由（`status.reason`）を添える。

### 3. ヘルプ文言

`RawDescriptionHelpFormatter` + epilog で、貼って動く実行例3種・上記の終了コード表・
「所見だけ見たいなら `codd_gate_wiring.py`」の誘導を載せた。各 `--flag` の help も、
`--config` は実在必須、`--repos` は `root:` から `<root>/repos.json` を推定、
`--base` は `$KIRO_BASE_REV` をシェル変数参照のまま埋める、と挙動を明示した。

自動配線が無くなった以上、README を読まないと使えない CLI は「到達できない機能」と同じになる。

### 4. テスト（+5 ケース、1 ケース書き換え）

| ケース | 固定した契約 |
|---|---|
| `test_missing_config_is_an_error_and_creates_nothing` | rc=2、stdout 空、stderr にパスと `--config`、**親ディレクトリも作らない** |
| `test_missing_config_is_an_error_even_with_dry_run` | `--dry-run` でも前提条件検査は飛ばさない |
| `test_unreadable_config_is_reported_not_crashed` | ディレクトリ指定でトレースバックを出さない |
| `test_exit_codes_are_distinct` | 3 つの終了コードが衝突しない（分岐可能性そのもの） |
| `test_help_documents_flags_exit_codes_and_examples` | 全 flag・終了コード 0/2/3・実行例がヘルプに載る |
| `test_dry_run_reports_change_without_writing`（書き換え） | 不在パスではなく**実在する設定**に対して「変わるはずと報告するが書かない」 |
| `test_noop_when_codd_gate_not_detected`（追記） | rc=3 と `reason` の JSON 掲載を追加 |

## (b) 検証

### 単体テスト

| 対象 | 結果 |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_codd_gate_*.py'` | **110 tests OK**（t2 時点 105 → +5） |
| `python3 -m py_compile`（変更2ファイル） | OK |

### 実コマンドでの到達性確認（すべて実行済み）

「自動配線に頼らず CLI から到達できる」ことを、検出・推奨・yaml 注入の3機能それぞれで確認した。

| # | コマンド | 結果 |
|---|---|---|
| 1 | `--help` | 引数・終了コード表・実行例が表示。rc=0 |
| 2 | `--config <不在パス>` | エラーメッセージ、rc=**2**、ファイル・親ディレクトリとも未作成 |
| 3 | `--config <実在> --dry-run` | `changed:true` を報告、ファイルは無変更、rc=0 |
| 4 | `--config <実在>` | **検出** `usable:true` → **推奨生成** `codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json` → **yaml 注入**。人が書いたコメント・`agent_cli`・`model` は保持。rc=0 |
| 5 | 同じ引数で再実行 | `changed:false`、rc=0（冪等） |
| 6 | `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base'` | MATCH |
| 7 | PATH から codd-gate を外し同梱も無い環境で実行 | `warning: codd-gate が使えないため何も書きませんでした: codd-gate が見つからない…`、rc=**3**、ファイル無変更 |
| 8 | `--bogus` | argparse の usage エラー、rc=**2** |
| 9 | `--repos /srv/repos.json --base origin/main` | 明示指定が `root:` 推定に勝ち、その値が yaml に入る |
| 10 | `python3 codd_gate_wiring.py --config <未結線>` | **推奨文字列**を info 所見2件（regression/intake）として出力、rc=0 |
| 11 | 4 の注入後にもう一度 10 | `regression_wired=True`、所見は intake の1件だけに減る（regression CLI → wiring CLI が噛み合っている） |
| 12 | `_hook_provider('wiring.detect' / 'wiring.findings')` | 零設定 → **None / None**（自動配線は切れたまま）、明示 `hooks:` → `codd_gate_wiring` / `codd_gate_wiring` |

7 の再現手順（`--codd-gate /nonexistent` では再現しない。(c) 参照）:

```bash
T=$(mktemp -d); mkdir -p $T/tools/agent-project $T/proj/.agent $T/bin
cp tools/agent-project/codd_gate_*.py $T/tools/agent-project/
ln -s "$(command -v python3)" $T/bin/python3
printf 'root: .agent-project\nagent_cli: claude\n' > $T/proj/.agent/agent-project.yaml
( cd $T/proj && env PATH=$T/bin python3 $T/tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml )
```

12 は `agent_project.hooks` を単体 import すると `NameError: name 'Path' is not defined` になる。
バグではなく設計どおり（hooks.py は `__init__.py` が共有名前空間へ exec 合成する断片で、
ファイル冒頭に「単体 import しない」と明記されている）。`import agent_project` 経由で確認した。

### 全体スイート — 完走できていない（代替の確認で担保）

`python3 -m unittest discover -s tests` は**この作業時間内に完了しなかった**（バックグラウンドで
10 分以上、出力ゼロのまま。打ち切り）。t2 の報告では 832 tests が走り、環境依存 failure が 2 件
（macOS の `/var`→`/private/var` シンボリックリンク、journal アーカイブ連番の辞書順ソート）ある。

完走の代わりに、影響範囲を機械的に絞って確認した:

- `grep -rn codd_gate_regression`（`*.py`）の結果、`codd_gate_regression` を **import する
  プロダクションコードは `codd_gate_wiring.py:263` の `infer_default_repos_path` だけ**
  （本タスクで未変更の関数。`test_codd_gate_wiring.py` は緑）。テストからの import は
  `test_codd_gate_regression.py` のみ。`test_agent_project.py:4003` の言及は docstring で、
  import も呼び出しも無い。
- そのうえで `tests.test_agent_project.TestCoddGateNoAutoWiring`（有効化手順の回帰ガード）を
  個別実行 → **4 tests OK**。

つまり本変更を実行しうるテストは全て走らせてあり、未実行分は変更ファイルを読み込まない。

## (c) 採用した前提・未解決・範囲外の発見

### 採用した前提

- **「設定不在時のエラーメッセージ」を「不在なら作らずに落ちる」と解した。** タスクは文言だけを
  求めているとも読めるが、現状の「黙って作って exit 0」は文言を足す対象が無い（エラーではない）。
  注入先が無い状態で `regression_cmd` だけを書いても agent-project 本体が読めない成果物になるため、
  作らない方を既定にした。副作用として `test_dry_run_reports_change_without_writing` が
  不在パスに依存していたので、実在する設定に対する検証へ書き換えた（dry-run の契約自体は不変）。
- **exit 3 の導入は「終了コードまで含めて動作させる」の要求に含まれると解した。** 全経路 0 のままでは
  シェルから分岐できず、CLI として結線導線が成立しない。ただし「一貫性ゲートは任意機能」という
  設計方針は残すため、stderr は `error:` ではなく `warning:` にした。
- 共通指示にある `/caveman` と `/ponytail full` は、この実行環境の利用可能スキル一覧に存在せず
  **実行できなかった**（推測で代替スキルを当てはめる方がリスクが高いと判断）。
- `apply_to_file`（ライブラリ API）は**無変更**。親ディレクトリ作成も残した。不在を拒むのは
  「利用者が `--config` に何を渡したか」を検査する CLI の責務で、呼び出し元が自分で場所を決める
  ライブラリ経路まで縛る理由が無い。

### 未解決・範囲外で見つけた問題（手を出していない）

- @followup **`--codd-gate` に実在しないパスを渡しても `usable:true` になる。** `codd_gate_detect.resolve_codd_gate`
  が explicit 指定を実在確認せずそのまま argv にする設計のため、typo でも壊れた設定が書き込まれる。
  他モジュールの契約（「明示指定は人の意図として信じる」）であり、既存テストが `--codd-gate /opt/bin/codd-gate`
  という不在パスで決定的な `usable=True` を作ることに依存している。直すなら a1 側の設計判断として。
- @followup **README:279-283 が本 CLI の新しい挙動を書いていない。** 終了コード（0/2/3）と
  「`--config` は実在必須」に触れていない。README 更新は別タスクの担当と理解しているため触っていない。
  同節は t2 が指摘した `hooks:` 未説明の問題も抱えている。
- @followup **`intake_cmd` に対応する注入 CLI が無い**（README:282 が明記）。regression だけ CLI 経路があり
  intake は手書き、という非対称が残る。`upsert_config_text(text, cmd, key=...)` は既に key 引数を
  取るので、実装コストは CLI 表層だけ。
- @followup 全体スイートの環境依存 failure 2 件（t2 から継続）。
