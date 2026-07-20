# codd_gate_regression.py の `--config` 結線導線を CLI として成立させた（t3）

対象: `tools/agent-project/codd_gate_regression.py`／ブランチ `ap/sibling-163827`
変更は `tools/agent-project` 配下のみ。

## 1. 出発点 — 何が「成立していなかった」か

`main()` は着手時点で存在し、検出→推奨文字列→yaml 注入までは通っていた。欠けていたのは
**CLI としての外形**、つまりシェルから呼んだ側が結果を判断できる面だった。

| 面 | 着手時 | 変更後 |
|---|---|---|
| 終了コード | 常に `0`（未検出でも 0） | `0` / `1` / `2` / `3` を区別 |
| 設定不在時 | 空テキスト扱い → **`regression_cmd` 1行だけの yaml を新規作成** | stderr にエラー、`1` で停止、何も作らない |
| ヘルプ | フラグの列挙のみ | 終了コード表・実行例を epilog に追加 |
| stderr | 使わない（失敗も stdout の JSON だけ） | 失敗理由を日本語で出す（JSON は維持） |

最も実害があったのは設定不在時の挙動。`--config` のパスを間違える／別ディレクトリから叩く
という最も起きやすい誤りが、**エラーではなく「新規ファイル生成として成功」**していた。
できあがる yaml は `root:` も `agent_cli:` も持たず本体が起動できない半端な設定で、しかも
利用者は成功したと思って立ち去る。ここを「その場のエラー」に倒した。

## 2. 終了コードの設計

```
0  注入した、または既に同じ値が入っていた（冪等 no-op）
1  --config の設定ファイルが無い・読めない
2  引数の使い方が誤っている（argparse 既定）
3  codd-gate が使えず regression_cmd を組み立てられない（何も書いていない）
```

`1` と `3` を分けたのは、呼び出し側が `$?` だけで**「codd-gate 未導入だから飛ばす」**と
**「パスを間違えている」**を区別できるようにするため。前者は opt-in 機能の正常な不在で
`|| true` してよいが、後者は直させなければならない。値が同じだと両者が混ざる。

`2` を避けて `3` を使ったのは argparse の使用法エラーとの衝突回避（定数
`EXIT_OK` / `EXIT_CONFIG_MISSING` / `EXIT_UNUSABLE` をモジュールに公開し、テストと
epilog がリテラルを重複して持たないようにした）。

未検出時に非ゼロを返すのは、モジュール内部の no-op 縮退方針と矛盾しない。**書き込みは
従来どおり一切しない**（縮退の実体は不変）。変えたのは「利用者が明示的に注入を頼んだのに
それが起きなかった」事実を報告するかどうかで、ライブラリ層の `build_regression_cmd`
（`None` を返す）は無変更。

## 3. 変更の範囲

| ファイル | 変更 |
|---|---|
| `codd_gate_regression.py` | `EXIT_*` 定数、`_build_parser()` 分離、`main()` の設定読み込みを `try/except` 化、stderr 診断、epilog |
| `tests/test_codd_gate_regression.py` | `TestCliContract` 新設（7 tests）。既存 `test_dry_run_reports_change_without_writing` を既存ファイル前提へ修正 |
| `README.md` | 一貫性ゲート項に「`--config` は既存ファイルを指す」「終了コード表」を1文追加（この節が CLI 挙動の正本のため） |

ライブラリ関数（`build_regression_cmd` / `upsert_config_text` / `apply_to_file` /
`infer_default_repos_path`）は**シグネチャ・振る舞いとも無変更**。`apply_to_file` は今も
存在しないファイルを作れる（明示的にそう呼んだ場合の API であり、誤りようがない）。
存在チェックは CLI 層にだけ置いた。

`test_dry_run_reports_change_without_writing` の修正は仕様変更に伴う意図的なもの。
「書かない」ことの検証を「ファイルが作られない」から「既存ファイルが1バイトも変わらない」
へ移し、`--dry-run` でも設定不在はエラーになることを別テストで固定した。

## 4. 検証 — 実際に叩いた

`python3 -m unittest discover -s tests -p 'test_codd_gate_*.py'` → **111 tests OK**
（t2 の 104 + 本タスク 7）。全体スイート → **844 tests / failures=2**（後述の既知の環境依存）。

実コマンド実行（`codd-gate` は PATH 上の `/Users/…/.local/bin/codd-gate` を自動解決）:

| # | コマンド | 結果 |
|---|---|---|
| 1 | `--help` | フラグ5種＋終了コード表＋実行例を表示、rc=0 |
| 2 | `--config <存在しないパス>` | `error: 設定ファイルがありません: <パス>` を stderr へ、**rc=1**、ファイル未作成 |
| 3 | `--bogus` | argparse の usage エラー、rc=2 |
| 4 | `--dry-run` | `usable:true` / `cmd` 生成 / `changed:true` を JSON 出力、**md5 不変**、rc=0 |
| 5 | 素の実行 | `regression_cmd` を注入。人が書いたコメント・`model: auto` は保持、見出しコメント付きブロックが `agent_cli:` の直前に入る、rc=0 |
| 6 | 再実行 | `changed:false`、rc=0（冪等） |
| 7 | `--repos /srv/repos.json --base origin/main` | 明示値がそのまま埋まり既存行を in-place 置換、rc=0 |
| 8 | `detect_status` を未検出に固定 | JSON は `cmd:null` / `changed:false`、stderr に理由、**rc=3**、設定は不変 |
| 9 | `python3 codd_gate_wiring.py --config <同じ yaml>` | `regression_wired: true` を確認 |

**t2 で切り離した3機能へ自動配線を経ずに到達できることの確認**が #4〜#8。
検出（`codd_gate_status.detect_status` → `usable` フィールド）・推奨文字列の生成
（`build_regression_cmd` → `cmd` フィールド）・yaml 冪等注入（`upsert_config_text` →
`changed` フィールドと実ファイル）の3つが、`--config` 一本で順に踏まれている。
#9 は独立した経路（`codd_gate_wiring` の CLI）から注入結果が結線として認識されることの
突き合わせで、両 sibling CLI が同じ設定ファイルについて同じ判断をすることを示す。

## 5. 採用した前提

- **「CLI として成立させる」を「シェルの呼び出し側が結果を判断できる状態にする」と解した。**
  機能（検出・生成・注入）は t2 時点で動いていたため、追加すべきは機能ではなく契約と診断。
- **設定ファイル不在はエラー**（自動生成しない）。opt-in の一貫性ゲートを入れる相手は
  「既に agent-project を使っている人」であり、設定ファイルは必ず先に存在する。
- **`--dry-run` は「書かない」だけの指定**で、対象不在の誤りを見逃す指定ではない。
- README の一貫性ゲート項が CLI 挙動の正本（GUIDE.md がそう宣言している）なので、
  終了コードの記述は README 側に置き GUIDE には複製しなかった。

## 6. 範囲外で見つけた問題（手を出していない）

- **@followup `--codd-gate` に存在しないパスを渡しても `usable=true` になる。**
  `codd_gate_detect.resolve_codd_gate` は explicit 指定を実在確認せず信頼する設計で
  （`tests/test_codd_gate_regression.py` の `TestCliMain` はこの性質を使って
  `/opt/bin/codd-gate` という架空パスで決定的な `usable=True` を作っている）、結果として
  タイプミスした `--codd-gate` が黙って結線済み yaml を生む。CLI 層で存在チェックを足すのが
  素直だが、既存テスト5件以上の前提を壊すため本タスクでは触っていない。検出層の設計判断として
  別途決めるべき。
- **@followup `intake_cmd` には対応する注入 CLI が無い**（README も明記）。`regression_cmd` だけ
  CLI・yaml 直書きの2経路、`intake_cmd` は直書きのみという非対称が利用手順の分かりにくさとして残る。
- **@followup 全体スイートの環境依存 failure 2 件**（t2 報告と同一・本変更と無関係）。
  `TestJournalRotation` はアーカイブ連番の辞書順ソート（`.1 .10 … .2`）で古い行を取り違え、
  もう1件は macOS の `/var`→`/private/var` シンボリックリンク。どちらも `codd_gate_*` を import しない。
