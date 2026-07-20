# codd_gate_wiring / codd_gate_base — 自動配線経路の切り離し（t2）

対象: `tools/agent-project/codd_gate_wiring.py` / `codd_gate_base.py`（+ 両者の単体テスト）
ブランチ: `ap/sibling-163827`（HEAD = `ab1c1302`、同 run の t4/t5/t6 が既にコミット済み）

## 1. 何を「自動配線経路」と読んだか（採用した解釈）

コード上に残っていた「本モジュール群 → agent_project への配線」は 2 種類あった。

| # | 経路 | 判断 |
|---|---|---|
| A | `build_config()` が `detect_wiring()` を呼んで `cfg` をメモリ上で書き換える（`_apply_codd_gate_auto_wiring`） | **既に除去済み**（t1 §2）。実装ゼロ。回帰ガード `TestCoddGateNoAutoWiring` が再導入を禁じている |
| B | 両モジュールの docstring が「自分は本体の自動配線に使われる部品である」と自己規定していた | **本タスクで切り離した**（下記 §2） |
| C | `agent_project/hooks.py` の能力スキャンが sibling を走査して `codd_gate_wiring` を引き当てる | **切り離さない**（判断理由は §3） |

A が消えた後も、B の自己規定が残っていたため「このモジュールは本体に呼ばれるための glue」という
読み方しかできなかった。切り離しの実体は **「本体に呼ばれなくても単体で完結する経路を持たせ、
docstring の主従を反転させること」** と解した。

## 2. 変更内容

### 2.1 `codd_gate_wiring.py` — 単体完結の CLI 経路を追加

- `main()` / `_render_text()` / `config_value()` を追加。
  `python3 codd_gate_wiring.py --config .agent/agent-project.yaml [--repos] [--codd-gate] [--json]`
  で **agent_project パッケージを一切 import せずに** 所見だけ取れる。
  タスクの責務リストの「（必要なら）CLI 所見の出力」がこれに当たる。
- `config_value()` は PyYAML を要求せず yaml 生テキストからトップレベル1行スカラーだけを読む。
  ブロックスカラー・ネストは `None` を返し、CLI は「結線済み」ではなく「推奨を出す」側へ倒す
  （読めない設定を根拠に結線済みと判断しない）。
- 終了コードは常に 0。連携は任意機能で、未検出・未結線は失敗ではない。CI から落としたい場合は
  `--json` を呼び出し側で判定する。
- repos.json の推定は `codd_gate_regression.infer_default_repos_path` を CLI 内で遅延 import して
  再利用（`root:` 推定規則の実装を 2 箇所に持たない）。
- docstring を書き換え、呼び出し経路 2 つ（CLI / 任意フック）と「本モジュールから本体への
  依存・登録・自動配線は無い」ことを明記。

### 2.2 `codd_gate_base.py` — 明示呼び出し専用の宣言

- docstring から、消滅した設計・実装への参照（`run-20260712-...` の d2 成果物、
  `agent-project.py:4906-` / `:831` / `:5514-5519` の行番号、`a1/a4/b2/b3` というタスク記号）を除去。
  パッケージ分割でこれらの行番号は全滅していた（t1 §5-④）。
- 「**明示呼び出し専用**。能力（`detect_wiring` / `doctor_findings`）は持たず、フックとして
  自動解決されることはない」を明記。この宣言は §2.3 のテストで固定した。
- 実装（`resolve_base_rev`）は無変更。

### 2.3 テスト

- `tests/test_codd_gate_base.py` を新規作成（従来テストファイル自体が無かった＝t1 §5-⑤）。
  優先順位（`KIRO_BASE_REV` → base ブランチ → `HEAD~1`）・空白の扱い・strip を固定。
  `test_no_hook_capability_attributes` が「能力属性を持たない＝自動配線経路が無い」を表明する。
  `env` は必ず注入し、実プロセスの環境変数に依存させない。
- `tests/test_codd_gate_wiring.py` に `TestConfigValue` / `TestCli` を追加（yaml 読み取りの
  引用符・コメント・ネスト、CLI の JSON 出力と repos 推定、設定不在でも rc=0）。

## 3. 「宙に浮く公開関数」の判断 — 削除せず明示呼び出し用 API として残す

タスクの指示により、判断理由をコード近傍のコメントではなくここに書く。

### 3.1 `detect_wiring()` / `doctor_findings()` — 残す（能力契約として）

**残した理由:**

1. **削除すると doctor の所見そのものが消える。** この 2 関数は `agent_project/hooks.py` の
   `HOOK_CAPABILITIES`（`wiring.detect` / `wiring.findings`）が求める属性名そのもの。消せば
   `doctor_wiring_findings()` は恒久的に空リストへ縮退し、「codd-gate は入っているのに未結線」を
   誰も知らせなくなる。元要求の背景「doctor 所見の置き場を一貫させ、利用者が結線方法を迷わない
   ようにする」と正面から衝突する。
2. **宙に浮いていない。** 本タスクで追加した CLI `main()` が両関数の第一の呼び出し元になった。
   フックが解決されない環境でも、この 2 関数は CLI から必ず通る。
3. **改名・別名化による「スキャン避け」は採らなかった。** `_hook_scan_siblings` の前置フィルタは
   ソーステキストの `^def <属性名>\s*\(` 正規表現なので、`def _detect_wiring(...)` +
   `detect_wiring = _detect_wiring` と書けば「明示 `hooks:` では解決できるが自動スキャンには
   引っかからない」状態を作れる。これは **hooks.py の private な実装詳細（import 副作用よけの
   前置フィルタ）への依存**であり、いま在る能力ベースの疎結合より確実に悪い結合を新設する。
   解決規則の所有権は本体側にあり、sibling が小細工でそれを覆すべきではない。

### 3.2 能力スキャン（経路 C）を切らなかった理由

- **切る手段が無い（スコープ内では）。** スキャンの所有者は `agent_project/hooks.py` で、
  本タスクは agent_project パッケージ内の変更を禁じられている。sibling 側から切るには
  §3.1-3 の小細工しかない。
- **切るべきでもない。** 経路 C は名前ではなく**能力**での解決で、パッケージ側に `codd_gate` と
  いう固有名は 1 つも無い（t1 §3.1）。依存の向きは常にパッケージ → sibling。これは旧 A（本体が
  cfg を書き換える）とは別物で、**本体は所見を読むだけ・cfg もファイルも書き換えない**。
  「新境界」そのものであって、切り離す対象ではないと判断した。
- **利用者体験の観点でも切らない方が良い。** 自動スキャンがあるおかげで、利用者は `hooks:` を
  知らなくても doctor から推奨コマンド文字列を受け取れる。切れば `hooks:`（README に記載なし）を
  自力で発見しない限り所見が出なくなり、「結線方法を迷わないように」に反する。

### 3.3 `resolve_base_rev()` — 残す（明示呼び出し用 API）

呼び出し元ゼロだが削除しなかった。推奨・生成される `regression_cmd` は `--base "$KIRO_BASE_REV"` を
**シェル変数参照のまま**埋め込む設計のため、環境変数が未注入だと `--base ""` で codd-gate が落ちる。
`resolve_base_rev()` は「Python 側で base rev を決めてから argv を組み立てたい呼び出し元」のための
唯一の解決規則で、この穴を埋める手段が他に無い。ただし現状は誰も使っていないため、docstring で
明示呼び出し専用と宣言し、単体テストで契約を固定した上で残した（削除は穴を塞ぐ手段ごと失う）。

## 4. 検証

| 項目 | 結果 |
|---|---|
| `python3 -m py_compile codd_gate_wiring.py codd_gate_base.py` | OK |
| `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_codd_gate_*.py'` | **109 tests OK**（変更前 81 → t6 追加分 + 本タスク追加分） |
| `PYTHONPATH=. python3 -m unittest discover -s tests`（全体 842 件） | **failures=2** — いずれも**本変更と無関係の既存 failure**（下記） |
| CLI 実行 `codd_gate_wiring.py --config /nonexistent.yaml`（+`--json`） | rc=0。所見 2 件（regression/intake 未結線）を出力。設定不在でも落ちない |
| スコープ確認 `git status --short` | `codd_gate_base.py` / `codd_gate_wiring.py` / `tests/test_codd_gate_wiring.py` / `tests/test_codd_gate_base.py` の 4 ファイルのみ。`agent_project/` と dashboard は無変更 |

**既存 failure 2 件の切り分け**: `TestDaemonRouting.test_kf_base_passes_flow_config` と
`TestJournalRotation.test_rotation_archives_and_starts_fresh`。作業ツリーを別ディレクトリへ複製し、
上記 4 ファイルだけを `git show HEAD:` の内容へ差し戻して同じ 2 テストを実行したところ、
**同一の AssertionError で同じく 2 件 fail**。したがって本変更に起因しない（journal ローテーションの
並行書き込みまわりの既存 flake と読める）。

## 5. 採用した前提

- 「自動配線経路の切り離し」を、**経路 B（docstring の自己規定）の切り離し + 単体完結経路の付与**と
  解釈した。経路 A は着手時点で既に除去済み、経路 C は新境界そのものなので対象外とした（§1・§3.2）。
- 「（必要なら）CLI 所見の出力」の「必要なら」を**必要と判断**した。これが無いと本モジュールは
  本体に呼ばれる以外の存在理由を持たず、切り離しが宣言だけになるため。
- 着手時点でこの worktree には本タスクの先行実行分と思われる未コミット変更（CLI 追加・docstring
  書き換え・テスト 2 件）が既に存在した。同一タスクの中間状態と判断し、破棄せず引き継いで検証・
  補正した。補正内容は §6 の 1 点目。

## 6. 未解決事項・範囲外で見つけた問題

- 引き継いだ docstring は「呼び出し経路は 2 つで、**どちらも呼ぶ側が明示する**」と書いていたが、
  経路 C（`hooks:` 無指定でも sibling スキャンが引き当てる）があるためこの主張は**誤り**だった。
  「呼ぶ側が主導」へ改め、無指定時もスキャンで解決されること・解決規則の所有者は本体側であることを
  明記した。
- @followup `README.md` に `hooks:` 設定キーの説明が無い。「codd_gate_wiring が doctor へどう結線
  されるのか」＝能力解決の仕組みへの言及がゼロで、`hooks:` で明示指定する方法を利用者が発見できない。
- @followup `docs/designs/codd-gate-design.md:284-304` が、除去済みの `_apply_codd_gate_auto_wiring`
  を現存機能として記述（t1 §5-①）。本タスクはリポジトリ外のため未着手。
- @followup 全体テスト 842 件のうち 2 件が既存 fail（§4）。本 run とは別系統の不具合。
- @followup `codd_gate_debt.py` も本番経路からの呼び出し元ゼロ。docstring 自身が意図的な存置と
  明記しているため触っていないが、`resolve_base_rev` と同じ扱い（明示呼び出し用 API）で良いかは
  未確認。

---

## 7. 再実行（2 回目の t2 ワーカー）— §3.2 の判断を検証し、維持した

同じ t2 が再度ディスパッチされた。着手時、この worktree には §1〜§6 の成果が**未コミットのまま**
残っていた（`codd_gate_base.py` / `codd_gate_wiring.py` / `tests/test_codd_gate_wiring.py` の変更と
未追跡の `tests/test_codd_gate_base.py`）。本節はその再検証の記録で、**§1〜§6 の結論は変更していない**。

### 7.1 いったん逆の実装を試み、証拠により取り下げた

再実行時、タスク文の「自動配線経路を切り離し」と責務リスト（doctor 所見が挙がっておらず
「（必要なら）**CLI** 所見の出力」だけがある）を根拠に、**経路 C も切るべき**と読んだ。
実際に §3.1-3 が「小細工」として退けた方法——`detect_wiring`/`doctor_findings` を実装名
（`probe_wiring`/`render_findings`）へ改名し、能力名は末尾で別名束縛する——を実装し、
「無設定では走査に当たらない／`hooks:` 明示指定では従来どおり当たる」ことをテストで固定するところまで
到達した（`_hook_provider` の実挙動でも確認済み: 無設定 → `None`、明示指定 → `codd_gate_wiring`）。

**その上で取り下げ、全変更を差し戻した。** 決め手は、着手時には見ていなかった以下の証拠。

| 証拠 | 内容 |
|---|---|
| `53461544`（t4・**コミット済み**） | README を「結線できているかは `doctor` が見る: codd-gate を検出できたのに `regression_cmd`/`intake_cmd` がそれを指していなければ、貼れる推奨コマンド文字列を info の所見として出す（`codd_gate_wiring`）」へ改訂。**`hooks:` の設定は前提にしていない** |
| `ab1c1302`（t5・**コミット済み**） | GUIDE に「検出は `.agent-project` の外側にある `codd_gate_wiring.py` が担い、**本体パッケージは能力フック越しに呼ぶだけ**」を追加。同じく設定手順を要求していない |
| README/GUIDE の `hooks` 出現数 | **0**。利用者が `hooks:` へ到達する導線はリポジトリ内に存在しない |

経路 C を切ると、`hooks:` を書かない限り doctor は無所見になる。**上記2つの committed なドキュメントが
その時点で嘘になり**、しかも利用者には代替の導線が無い。元要求の背景「doctor 所見の置き場と README の
有効化手順を一貫させ、利用者が結線方法を迷わないようにする」に対して、run 全体としては
**一貫性を壊す方向**の変更だった。§3.2 の「利用者体験の観点でも切らない方が良い」は正しい。

加えて §3.1-3 の技術的な指摘も再確認して支持する。別名束縛は `_hook_scan_siblings` の
**前置フィルタ（import 副作用よけの最適化）という private な実装詳細**に、sibling 側の公開 API の
書き方（`def` か代入か）を載せる結合で、本体がフィルタを外した瞬間に自動配線が黙って復活する。
テストで固定はできるが、いま在る能力ベースの疎結合より確実に悪い。

### 7.2 差し戻しの確認

| 検証 | 結果 |
|---|---|
| `grep -rn "probe_wiring\|render_findings" tools/agent-project/` | **ヒット 0**（再実行時の改名の痕跡なし） |
| `git status --short` | §4 と同一の 4 ファイルのみ（`agent_project/` と dashboard は無変更） |
| `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_codd_gate_*.py'` | **109 tests OK** — §4 の件数と一致＝先行成果が過不足なく復元されている |
| `_hook_provider` の実挙動 | 無設定・`hooks:` 明示指定のいずれでも `codd_gate_wiring` を解決。committed な README/GUIDE の記述どおり |
| 全体テスト `discover -s tests -p 'test_*.py'` | 844 件中 failures=2。`TestDaemonRouting.test_kf_base_passes_flow_config`（macOS の `TMPDIR` が `/private` 実体へのシンボリックリンクで `Path.resolve()` 不一致）と `TestJournalRotation.test_rotation_archives_and_starts_fresh`（既存 flake）。§4 の切り分け結論と一致し、本タスクとは無関係 |

### 7.3 この再実行で採用した前提

- **「自動配線経路の切り離し」は §1 の A + B で完了しており、経路 C は対象外**という §1・§3.2 の解釈を
  維持する。タスク文だけを読むと C も対象に読めるが、run 全体の committed 成果と背景の意図に照らすと
  C を切るのは誤り。**タスク文と run の実態が食い違っている箇所**として評価役へ上げる。
- 経路 C まで切る判断を評価役が下す場合、**コード単独では完結しない**。README（t4 が書いた doctor 所見の
  段落）・GUIDE（t5 が書いた「能力フック越しに呼ぶだけ」の記述）の**改訂とセットでなければ
  リポジトリが不整合になる**。その場合の実装手順は §7.1 のとおり（別名束縛 + テスト2件の追加/反転）で、
  再現に必要な情報はこの節に揃えてある。

### 7.4 この再実行で追加した申し送り

- @followup t1 §5-⑤ の「`tests/test_codd_gate_base.py` は存在しない」は、先行 t2 が同ファイルを
  新規作成した（§2.3）ため**現在は誤り**。7 テストが通る。後続が t1 の記述を鵜呑みにしないよう記録する。
- @followup 本タスクは**同一 run で 2 回ディスパッチされた**。1 回目の成果が未コミットで残っていたため
  引き継げたが、`git status` を見ずに書き始めていれば先行成果を上書きしていた。t2 のような
  「コードを触るタスク」の再ディスパッチ時は、着手前に作業ツリーの未コミット差分と同 run の
  committed 成果（`git log --oneline`）を確認する必要がある。
