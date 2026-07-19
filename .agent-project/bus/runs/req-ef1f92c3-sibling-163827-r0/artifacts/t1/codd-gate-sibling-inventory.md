# codd_gate_* sibling レイヤ 棚卸し表（t1）

対象コミット: `ap/sibling-163827`（main 分岐時点）／リポジトリ: ynitto/sandbox
調査範囲: `tools/agent-project/codd_gate_*.py` 7ファイル、`tools/agent-project/agent_project/` パッケージ、
`tools/agent-project/README.md`・`GUIDE.md`・`docs/designs/codd-gate-design.md` §4.1。

## 0. 結論（後続タスクが最初に知るべき2点）

1. **`build_config` によるメモリ上の自動配線は、コード上には既に一切残っていない。**
   `_apply_codd_gate_auto_wiring` は `agent_project/configfile.py` から除去済みで、リポジトリ全体の
   残存は「回帰ガードのテスト」と「未更新の設計書1行」だけ。README も新境界へ追随済み。
   **追随が残っているのは `docs/designs/codd-gate-design.md` §4.1（現在地の記述）と GUIDE.md（記述ゼロ）。**
2. **依存方向は完全に逆転済み。** `agent_project/` は `codd_gate_*` を import せず、固有名も持たない。
   `agent_project/hooks.py` の能力レジストリ（capability → 必須属性）で sibling を走査して引き当てる。
   よって「パッケージ → sibling」への静的依存は 0 本、「sibling → パッケージ」も 0 本。

---

## 1. 7モジュールの現在の責務・公開シンボル・依存

| ファイル | 行数 | 責務（現状） | 公開シンボル | sibling 内依存 | agent_project への依存 |
|---|---|---|---|---|---|
| `codd_gate_detect.py` | 142 | codd-gate 実体の解決と**生の検出値**。「使ってよいか」は判断しない | `BINARY_NAME` / `PROBE_TIMEOUT` / `resolve_codd_gate()` / `get_version()` / `check_repos_schema_compat()` / `detect_capabilities()` | なし（stdlib のみ） | **なし** |
| `codd_gate_status.py` | 138 | 検出結果の値オブジェクトと **no-op 縮退**（findings が1件でも `usable=False`） | `MIN_SUPPORTED_VERSION` / `CoddGateStatus` / `build_status()` / `detect_status()` | `codd_gate_detect` | **なし** |
| `codd_gate_routing.py` | 82 | `--repos` / `--repo-dir` の**実引数組み立て**（純粋関数） | `DEFAULT_REPO_DIR` / `resolve_repos_arg()` / `resolve_repo_dir_arg()` / `build_routing_args()` | なし（stdlib のみ） | **なし**（docstring で明示） |
| `codd_gate_base.py` | 54 | 差分ゲートの base rev 解決（`$KIRO_BASE_REV`→base ブランチ→`HEAD~1`） | `FALLBACK_BASE_REV` / `resolve_base_rev()` | なし（stdlib のみ） | **なし**（docstring で明示） |
| `codd_gate_debt.py` | 105 | `codd-gate tasks --debt` stdout の**レコード単位パース**と正規化 | `DriftItem` / `DebtParseResult` / `parse_debt_output()` | なし（stdlib のみ） | **なし**（docstring で「本体 intake はこの module に依存しない」と明記） |
| `codd_gate_wiring.py` | 198 | **実測配線 + 結線判定 + doctor 所見**。唯一の hook プロバイダ | `regression_wired()` / `intake_wired()` / `recommend_regression_cmd()` / `recommend_intake_cmd()` / `WiringJudgment` / `judge_wiring()` / **`detect_wiring()`** / **`doctor_findings()`** | `codd_gate_detect` / `codd_gate_routing` / `codd_gate_status` | **なし** |
| `codd_gate_regression.py` | 195 | `regression_cmd` の生成と **yaml への冪等注入**（唯一の書き込み CLI） | `KEY` / `DEFAULT_REPOS_PATH` / `build_regression_cmd()` / `render_line()` / `upsert_config_text()` / `_insert_new_line()` / `infer_default_repos_path()` / `apply_to_file()` / `main()` | `codd_gate_detect` / `codd_gate_routing` / `codd_gate_status` | **なし** |

sibling 内の依存グラフ（一方向・循環なし）:

```
codd_gate_detect ──┬──> codd_gate_status ──┬──> codd_gate_wiring
                   │                        └──> codd_gate_regression
codd_gate_routing ─┴──────────────────────────>  （同上 2つ）
codd_gate_base   （どこからも参照されない・孤立）
codd_gate_debt   （どこからも参照されない・孤立）
```

`codd_gate_wiring.py:36-38` のみ `sys.path.insert(0, <sibling dir>)` を自前で行う（zipapp / 直 import 両対応）。
他の5モジュールは素の `import codd_gate_*` で、呼び出し側の `sys.path` に依存する。

---

## 2. build_config によるメモリ上の自動配線 — 残存箇所の全数

grep 対象: `_apply_codd_gate_auto_wiring` / `build_config.*メモリ上で自動` をリポジトリ全体。

| # | 箇所 | 種別 | 状態 |
|---|---|---|---|
| 1 | `tools/agent-project/agent_project/configfile.py` | 実装 | **除去済み**（`build_config` は `configfile.py:215-` 開始。codd-gate 固有の分岐は 1 行も無い。`hooks=_normalize_hooks(...)` を `Config` に載せるのみ＝`configfile.py:316`） |
| 2 | `tools/agent-project/tests/test_agent_project.py:3998-4040` | テスト | **意図的に残す**。`TestCoddGateNoAutoWiring` が再導入を禁じる回帰ガード（`:4015` で `assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))`） |
| 3 | `docs/designs/codd-gate-design.md:284-295` | 設計書 | **要修正（stale）**。「`build_config()` が `detect_wiring()` を呼んで cfg を**メモリ上で**自動配線する（`_apply_codd_gate_auto_wiring`）」と現存機能として記述。実装と矛盾 |
| 4 | `docs/designs/codd-gate-design.md:297-304` | 設計書 | **要修正（stale）**。「build_config の自動配線とは独立に存在する（片方が無くても他方は動く）」＝自動配線の存在が前提。`_HUMAN_OWNED_STATE_FILES` の記述自体は現在も正しい |
| 5 | `tools/agent-project/README.md:279-288` | README | **追随済み**。「本体（configfile）に埋め込まない」「生成・結線は sibling へ外出しした」と新境界で記述 |

`.agent-project/backlog/*` `.agent-project/needs/*` にも文字列は出るが、これは本 run のタスク定義そのもの（対象外）。

---

## 3. 呼び出し元と依存方向（新境界）

### 3.1 パッケージ → sibling は「能力による間接解決」のみ

`agent_project/hooks.py` が唯一の入口。パッケージ側に `codd_gate` という固有名は**存在しない**。

| 要素 | 位置 | 内容 |
|---|---|---|
| 能力レジストリ | `agent_project/hooks.py:15-18` | `HOOK_CAPABILITIES = {"wiring.detect": ("detect_wiring",), "wiring.findings": ("doctor_findings",)}` |
| 解決の入口 | `agent_project/hooks.py:110-132` | `_hook_provider(capability, cfg)`。順序は **設定 `hooks:` の明示指定 → sibling ディレクトリの能力スキャン**。明示指定が解決できないときは自動検出へ落ちず `None`（人の意図を別物で置き換えない） |
| sibling 走査 | `agent_project/hooks.py:74-98` | `_hook_scan_siblings()`。`tools/agent-project/*.py` を**昇順**に、ソーステキストの `^def <必須属性>\s*\(` で前置フィルタ → import → 属性確認。`_` 始まり・非 identifier（`agent-project.py`）は除外 |
| キャッシュ | `agent_project/hooks.py:23` | `_HOOK_CACHE`（`None` もキャッシュ）。**cfg を差し替えるテストは `_HOOK_CACHE.clear()` が必要** |
| 設定の正規化 | `agent_project/configfile.py:171-` | `_normalize_hooks()`（能力キー → module 名。import 可否は見ない） |
| Config フィールド | `agent_project/config.py:128` | `hooks: dict = field(default_factory=dict)` |

**現在の当選モジュール**: `codd_gate_wiring.py` のみが `def detect_wiring(`（:139）と `def doctor_findings(`（:176）の
両方を持つ。昇順走査（base → debt → detect → regression → routing → status → wiring）で他6つは前置フィルタに
落ちるため、実 import されるのは `codd_gate_wiring` だけ（その import が推移的に detect/routing/status を引く）。

### 3.2 sibling の呼び出し元一覧

| 呼び出し元 | 位置 | 呼ぶもの | 経路 |
|---|---|---|---|
| `agent_project/doctor.py` | `:313-334` `doctor_wiring_findings()` | `detect_wiring(regression_cmd=, intake_cmd=, repos_path=, which=, run=)` → `doctor_findings(judgment)` | hooks 経由（固有名なし）。`judgment` は本体にとって不透明。例外・解決失敗はすべて空リストへ縮退 |
| `agent_project/doctor.py` | `:287-310` `_hook_misconfig_findings()` | （呼ばない）`_hook_resolution_error()` で設定ミスのみ warn 化 | hooks 経由 |
| `agent_project/doctor.py` | `:542` `cmd_doctor()` | `doctor_wiring_findings(cfg)` を決定的所見の一つとして合成 | — |
| 人・install 手順 | CLI | `python3 codd_gate_regression.py --config .agent/agent-project.yaml` | 直接実行（`main()` = `codd_gate_regression.py:162-191`） |
| 単体テスト | `tests/test_codd_gate_{detect,routing,debt,wiring,regression}.py` | 各モジュール直接 | `PYTHONPATH=tools/agent-project` |
| `install.sh` | `:46-50` | `codd_gate_*.py` を zipapp ルートへ同梱（glob。個別名を持たない） | 配布 |

**`codd_gate_base.py` と `codd_gate_debt.py` は本番経路からの呼び出し元がゼロ。** `codd_gate_debt` は
単体テスト（`test_codd_gate_debt.py`）があるが、`codd_gate_base` は**テストファイルも無い**（`tests/` に
`test_codd_gate_base.py` は存在しない）。

### 3.3 sibling → パッケージの依存

**0 本。** 7モジュールすべて stdlib と sibling 同士のみに依存し、`Config` / `Charter` / `Task` 型を import しない
（`codd_gate_base.py:13-16`・`codd_gate_routing.py:11-14` が設計判断として明記）。呼び出し側が
`cfg.backlog.parent` / `_task_verify_cwd()` / charter の repo spec から値を取り出して文字列・パスとして渡す規約。

### 3.4 codd-gate に触れるがモジュール依存ではない箇所（参考）

| 位置 | 内容 |
|---|---|
| `agent_project/charter.py:326` `repo_registry_path()` / `:370` `export_repo_registry()` | `<root>/repos.json` の生成。`:373` のコメントで「codd-gate 等の外部ツールへレジストリファイルとして渡す派生物」 |
| `agent_project/mr.py:546-556` | `cfg.regression_cmd` の実行。**常に workdir（git-bus ルート）で走らせる**理由として codd-gate の repos.json 解決を挙げる（`:550`） |
| `agent_project/model.py:497` / `:543` `run_intake()` | `intake_cmd` は検出器非依存。`_parse_intake_records` で汎用パース＋id 冪等 |
| `agent_project/verify.py:356` | verify コマンド allowlist に `"codd-gate"` を含む |
| `agent_project/doctor.py:167` | 無関係（`pytest -k codd` の収集件数に関するコメント） |

---

## 4. ドキュメント該当箇所（行番号）

### 4.1 `tools/agent-project/README.md`（全 77KB）

| 行 | 内容 | 追随状況 |
|---|---|---|
| 8 | 冒頭サマリ。`charter.md` / `repos.json` を入力と記述 | OK |
| 93 | 機能表: repos.json 自動生成（`_meta` 付き・正は charter に追従）＝codd-gate 等へ渡す | OK |
| **272-288** | **中核: 「一貫性ゲート（codd-gate 連携・オプション）」節** | 下記参照 |
| 272-278 | 有効化は設定だけ。`regression_cmd` / `intake_cmd` / acceptance の正準文字列3種 | OK |
| **279-281** | 「上記2行の生成は**本体（configfile）に埋め込まない**」「`build_config` は codd-gate 固有の実行時自動配線を持たず、差し込み点のみ」 | **新境界に追随済み** |
| **281-283** | 「生成・結線は sibling へ外出しした: repos.json が実在する環境で codd-gate を検出し、未結線なら `doctor` が推奨コマンド文字列を finding として提示する（`codd_gate_wiring`）」 | **要確認**: 「repos.json が実在する環境で」は `detect_wiring` の実挙動と食い違う（下記 §5-③） |
| 283-286 | 書き込み CLI は `regression_cmd` の1行だけ。`intake_cmd` の注入 CLI は無く yaml 直編集 | OK |
| 286-288 | 未検出・非互換なら両経路とも no-op。設計書 §4.1 へリンク | OK |
| 445 | 設定表: `intake_cmd: codd-gate tasks --debt` の例 | OK |
| 521 | repos.json 自動生成の詳細（`_meta` マーカー付き） | OK |

**README に無いもの**: `hooks:` 設定キー（能力 → module 名）の説明が**一切無い**。`codd_gate_wiring` という名前は
:283 に出るが、それが「どう本体に結線されるのか（hooks 経由の能力解決）」への言及が無い。

### 4.2 `tools/agent-project/GUIDE.md`（全 36KB）

**`codd` の出現数 = 0。`intake_cmd` の出現数 = 0。`hooks` の出現数 = 0。`sibling` の出現数 = 0。**

`regression_cmd` のみ4箇所:

| 行 | 内容 |
|---|---|
| 100 | 設定サンプル: `regression_cmd: "pytest -q"` # done 確定前のグローバル回帰検査 |
| 125 | `regression_cmd`=巻き込み検知 / `verify_confirm`=flake 隔離 / `require_progress`=偽 done 捕捉 |
| 313 | 表「回帰ゲート」行: `regression_cmd` (+`regression_revert`)、L2+ |
| 457 | 設定リファレンス表: `regression_cmd` / なし / L2+ / done 前のグローバル検査（例 `pytest -q`） |

→ 利用手順の観点では **GUIDE.md が最大の空白**。「一貫性ゲートをどう有効化するか」の導線が README にしか無い。

### 4.3 `docs/designs/codd-gate-design.md` §4.1（258-330 行）

| 行 | 内容 | 追随状況 |
|---|---|---|
| 258 | 見出し `### 4.1 自動検出レイヤ（tools/agent-project/codd_gate_*.py）` | OK |
| 260-263 | 前文。「補助モジュールが `tools/agent-project/` 直下に**部品として**存在する」「責務は3段」 | OK |
| **265-273** | **7モジュールの責務表**（detect / status / routing / base / debt / wiring / regression） | 内容は §1 の実装と一致。ただし `codd_gate_wiring` 行に `doctor_findings()` が hooks 能力として本体へ結線される事実の記載なし |
| 275-282 | データ契約（repos.schema.json 入力 / task.schema.json 出力 / CoddGateStatus は一過性） | OK |
| **284-295** | **「現在地（結線状況）」— `build_config()` が `detect_wiring()` を呼び `cfg` を**メモリ上で**自動配線する（`_apply_codd_gate_auto_wiring`）。発火条件は「明示されておらず」かつ「repos.json 実在」** | **stale。実装から除去済み** |
| **297-304** | `.agent/agent-project.yaml` は自動配線で書き換わらない（`_HUMAN_OWNED_STATE_FILES`）／`codd_gate_regression.py` は「build_config の自動配線とは独立に存在する」 | **前半（`_HUMAN_OWNED_STATE_FILES`）は現在も正。後半の「自動配線とは独立」は前提が消滅** |
| 306-329 | 差し込み点選択の妥当性（E1/E2/E3 の選定理由、E5/E6 不使用、agent-flow に差し込まない理由） | OK（境界変更の影響なし） |
| 246-252 | §4 の差し込み点表①〜④＋repos レジストリ（補） | OK |
| 254-256 | `$KIRO_BASE_REV` と `--repo-dir <name>=.` の規約 | OK |

**設計書に無いもの**: `agent_project/hooks.py` の能力レジストリ（`HOOK_CAPABILITIES`）と sibling 走査による
逆依存の仕組みへの言及が §4.1 に一切無い。新境界の中核なので、284-304 の差し替え先はここになる。

---

## 5. 後続タスク向けの申し送り（範囲外で見つけた不整合）

- **① 設計書 §4.1「現在地」が実装と矛盾**（`docs/designs/codd-gate-design.md:284-304`）。
  差し替えの骨子: 「build_config は差し込み点のみ／結線は `hooks.py` の能力レジストリ（`wiring.detect`＝
  `detect_wiring`、`wiring.findings`＝`doctor_findings`）で sibling を走査して解決／当選するのは
  `codd_gate_wiring`／所見は doctor が出すだけで cfg もファイルも書き換えない／永続化は
  `codd_gate_regression.py` の1キー冪等 upsert のみ」。
- **② GUIDE.md に一貫性ゲートの導線が無い**（codd / intake_cmd / hooks いずれも 0 件）。
  `regression_cmd` を扱う 4 箇所（:100 / :125 / :313 / :457）が追記候補。
- **③ README:281-283 の「repos.json が実在する環境で codd-gate を検出し」は実挙動とずれる。**
  現行 `doctor_wiring_findings`（`doctor.py:329-331`）は repos.json の有無に関わらず毎回
  `detect_wiring()` を呼び、`detect_wiring`（`codd_gate_wiring.py:167`）は repos.json が実ファイルのときだけ
  **schema 互換チェックを追加する**だけ。「repos.json 実在」は旧 build_config 自動配線の発火条件であって、
  現行 doctor 経路の条件ではない。設計書 :287-288 の同記述も同根。
- **④ モジュール docstring の行番号参照が全滅**。`agent-project.py` は 603 バイトの薄いエントリポイントへ
  分割済みだが、`codd_gate_base.py:7-11` が `agent-project.py:4906-` / `:831` / `:5514-5519`、
  `codd_gate_detect.py:5` が `agent-project.py:3477` を指す。現在地は
  `_settle_task`→`agent_project/mr.py:494`、`git_change_baseline`→`agent_project/policy.py:219`、
  `_task_verify_cwd`→`agent_project/verify.py:122`、`resolve_agent_flow`→`agent_project/request.py:4`。
- **⑤ `codd_gate_base.py` は呼び出し元も単体テストも無い**（`tests/test_codd_gate_base.py` が存在しない）。
  `resolve_base_rev()` が埋めるはずだった「`--base ""` で `_die` する穴」は、現行の推奨文字列
  （`recommend_regression_cmd` / `build_regression_cmd`）が `"$KIRO_BASE_REV"` をシェル変数参照のまま
  埋め込む設計のため、Python 側では誰も解決していない。存置か削除かの判断が要る（本タスクでは触らない）。
- **⑥ `codd_gate_debt.py` も本番経路からの呼び出し元ゼロ**。docstring 自身が「本体 intake はこの module に
  依存しない」「呼び出し側のための独立したアダプタとして残る」と明記しており、意図的な存置と読める。
- **⑦ hooks キャッシュのテスト注意**: `_HOOK_CACHE`（`hooks.py:23`）は `None` もキャッシュする。
  cfg を差し替えるテストを書く後続タスクは `_HOOK_CACHE.clear()` を忘れないこと。

## 6. 検証

- `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py'`
  → **81 tests, OK**（0.017s）。
- `grep -rn "_apply_codd_gate_auto_wiring" .` → 実装ファイルにヒット 0（テスト1件・設計書1件・backlog/needs 2件のみ）。
- `grep -rni "codd" tools/agent-project/agent_project/` → 9 件すべてコメント・allowlist 文字列・例示で、
  `import codd_gate_*` は 0 件。
- 本タスクはファイル変更なし（調査のみ）。
