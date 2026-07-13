# t4: kiro_project の acceptance（受入判定）— 判定関数・結果構造・拡張点

対象: `tools/kiro-project/kiro-project.py`（他タスクとの分割前の単一ファイル。行番号はこのファイル基準）。
「acceptance」はこのコードベースの用語で **charter（プロジェクト憲章）レベルの done 判定**を指し、
タスク個々の `- verify:`（regression/t3 の対象）とは別レイヤーである。両者は同じプリミティブ
（`run_verify_stable`）を共有するが、判定主体・結果の永続化先が異なる。

## 1. 判定関数（呼び出し順）

```
cmd_project()                              # ループ本体（1 charter・1 パス）
  ├─ parse_charter() ................... charter.md の `## acceptance` を Charter.acceptance へ
  ├─ [guard] not charter.acceptance → REASON_PROJECT_NO_ACCEPTANCE で人へ（9853-9862）
  ├─ resolve_charter_acceptance() ...... 自然言語を決定的コマンドへ合成・キャッシュ（9589-9615）
  │    └─ synth_verify()（task 用と共用。3401-3426）
  ├─ [guard] unresolved → REASON_PROJECT_NO_ACCEPTANCE（9891-9901）
  ├─ charter.acceptance = resolved ..... 以降は解決済みコマンドのみで評価（9902）
  ├─ run_loop()（②execute。バックログ消化。既存機構、無改造）
  └─ _project_evaluate()                # ③ evaluate（9753-9802）
       └─ evaluate_acceptance()         # 受入判定の中核（9546-9572）
            ├─ _acceptance_cwd() ...... 実行 cwd 解決（9524-9543）
            └─ run_verify_stable() → run_verify()（3018-3045）
```

主要関数（署名・役割）:

| 関数 | シグネチャ | 役割 |
|---|---|---|
| `parse_charter` | `(text: str) -> Charter` | `## acceptance` セクションを `_charter_bullets` で `list[str]` に（8350-8380、acceptance は 8370） |
| `_acceptance_kind` | `(line: str) -> (kind, text)` | 1行を `"command"`（シェルとして実行）か `"accept"`（自然言語→合成）に分類（9575-9586） |
| `resolve_charter_acceptance` | `(cfg, charter, state, kiro_run) -> (resolved: list[str], unresolved: list[str])` | 自然言語行を `synth_verify` で決定的コマンドへ合成。結果は `state["acceptance_synth"]` にキャッシュ（原文キー、サイクルをまたいで安定）（9589-9615） |
| `_acceptance_cwd` | `(cfg, charter) -> (Path, tmp: str\|None)` | 実行 cwd を決める。優先順位: `cfg.verify_cwd` 明示 &gt; charter の単一対象 repo（push 先）の一時 clone &gt; `cfg.workdir`（9524-9543） |
| `evaluate_acceptance` | `(cfg, charter) -> (passed: int, total: int, results: list[(cmd, ok, msg)])` | **受入判定の中核**。全 acceptance コマンドを実行し集計（9546-9572） |
| `run_verify_stable` | `(cmd, workdir, timeout, confirm, env) -> (ok, flaky, msg)` | 1コマンドの実行本体。`confirm>1` で PASS/FAIL 揺れを flaky 検出（3031-3045） |
| `_acceptance_specs` / `_failing_acceptance_specs` | `(cmds/results) -> list[dict]` | 未達 acceptance を `{"title":…, "verify": cmd, "source":"acceptance"}` の backlog タスク spec 化（改善タスクとして enqueue）（9618-9627） |
| `_project_evaluate` | `(cfg, charter, pid, state, cycle, cost_used, review_fn, charter_tag) -> (reason\|None, summary)` | `evaluate_acceptance` を呼び、`state["history"/"best"/"stall"]` 更新・未達の改善タスク化・収束/停滞/コスト判定（9753-9802） |
| `write_milestone` | `(cfg, charter, reason, summary, pid, version)` | `needs/<pid>.md` に人向けマイルストーンを書く（9630-9669） |
| `finalize_project` | `(cfg, state, reason, charter, charter_name)` | 人の承認後、`status=accepted` に確定・納品書を残す（9722-9743） |

task 側の対応物（同じ思想の別レイヤー。参考として明記、t3/t5 の対象ではない）:
`ensure_verify`（3429-3466）／`synth_verify`（3401-3426、charter 側と共用）／`Task.verify`。

## 2. 判定結果の構造

### 2.1 その場の返り値（プロセス内、非永続）

`evaluate_acceptance` の戻り値がすべての起点:

```python
(passed: int, total: int, results: list[tuple[cmd: str, ok: bool, msg: str]])
```

- `results` の各要素は `run_verify`（3018-3028）が作る `(ok: bool, msg: str)` に `cmd` を添えたもの。
  `msg` は `"exit={code} {stdout/stderr tail 400+400字}"`（500字に切り詰め）、または
  `"verify 未定義…"` / `"verify タイムアウト…"`。
- `_acceptance_cwd` が失敗（対象 repo の clone 失敗）した場合は **全件 NG** 扱いで返す
  （9553-9555）。「成果の無い場所で偽 PASS にしない」という kiro-project 全体の鉄則がここにも表れる。
- `evaluate_acceptance` は cwd が git 管理下なら HEAD を読み `KIRO_BASE_REV` を env に注入して
  各コマンドを実行する（9557-9561）。**ただし `(wd / ".git").exists()` が false（非 git ワークスペース）
  なら注入されず空文字のまま**——`codd_gate_base.resolve_base_rev`（同ディレクトリ、regression 用に
  実装済み）と同じ穴が acceptance 側にも存在する（§4 参照）。

### 2.2 永続化される表現（3系統）

1. **`project.json`（または複数 charter 運用時 `charters/<name>.state.json`）の `state` dict**
   `_project_evaluate` が更新するキー: `history`（各サイクルの `passed` の履歴配列）、
   `best`（過去最高 `passed`。停滞判定の基準）、`stall`（連続で `best` を更新できなかった回数）、
   `acceptance_synth`（自然言語→合成コマンドのキャッシュ）。`cmd_project` が設定するキー:
   `status`（`REASON_PROJECT_*` の1つ。文字列定数）、`acceptance_total`（`len(charter.acceptance)`。
   **`evaluate_acceptance` 呼び出し前、resolve 直後の値で固定**——評価のたびに再計算されない点に注意、
   §4 で拡張点として触れる）。
2. **`needs/<pid>.md`（milestone）** — `write_milestone` が書く人向け MADR ドキュメント。
   `REASON_PROJECT_*` → ラベル文字列（`labels` dict、9645-9652）、`summary`（`f"cycle {cycle}:
   acceptance {passed}/{total} PASS, 改善 {n} 件"` 等）、承認チェックボックス。
   `reconcile_milestones`（9685-9719）が `state["status"]` との整合を毎パス取り、
   古い milestone を消す唯一の GC 経路。
3. **journal.md / decisions/\<pid\>.md** — `append_journal`/`append_decision` による監査ログ
   （`"project cycle N: acceptance P/T PASS, …"`）。

### 2.3 状態遷移（`REASON_PROJECT_*`、8160-8174）

`no-acceptance`（未定義/未合成）→〔charter.acceptance が揃うと〕→ 評価ループ →
`converged`（全 PASS・改善ゼロ）or `no-progress`（stall 上限）or `project-cost`/`project-budget` →
人が `approve` →`accepted`。**done の唯一の根拠は `passed == total`**（9792）。

## 3. 受入基準を追加できる拡張点

### 3.1 データレベルの拡張点（コード変更ゼロ）— `charter.md` の `## acceptance` セクション

`parse_charter`/`_charter_bullets` が `## acceptance` の `- ` 箇条書きをそのまま
`Charter.acceptance: list[str]` にする。**この段より下（`resolve_charter_acceptance` /
`evaluate_acceptance` / `_project_evaluate` / milestone / state）は行数に依存しない汎用ループ**
なので、`## acceptance` に1行足すだけで kiro-project.py 本体を一切改造せずに受入基準が増える
（設計書 `docs/designs/codd-gate-design.md` §4 の E1 行が正典。同 §4.1 は
`kiro-project-design.md` の「フック契約カタログ」）。

行の書き方は2種類（`_acceptance_kind` が分類、9575-9586）:
- **`command`**: シェルコマンドに見える行（`_looks_like_shell_command` 判定）はそのまま実行。
  例: `- codd-gate verify --debt --max-broken 0 --repos ./.kiro-project/repos.json --repo-dir sandbox=.`
- **`accept`**: `accept:`/`受入:`/`受入条件:`/自然文 接頭辞、または散文はコマンドに見えない場合。
  `synth_verify`（LLM 合成）に回る。

**codd-gate のような決定的ツールは必ず `command` 側（生のシェル文）で書くべき**——`accept` 側は
LLM 合成の非決定性が挟まり、codd-gate 自身の設計原則（鉄則4「決定的・stdlib のみ・LLM 不要」）と
矛盾する。

env 面: `evaluate_acceptance` は cwd が git 管理下なら `KIRO_BASE_REV`（act 前 HEAD）を自動注入する
ので、`--base "$KIRO_BASE_REV"` と書くだけで正しい差分基準が渡る
（`docs/designs/codd-gate-design.md` 254行が明記する既存規約と一致）。
cwd/repo-dir は `_acceptance_cwd`（単一対象 repo なら一時 clone、複数/0 なら `cfg.workdir`）が決めるため、
`--repo-dir <charter の repo name>=.` の組み立てには既存の `codd_gate_routing.build_routing_args
(repos_path, name, vcwd)`（tools/kiro-project/codd_gate_routing.py、既に実装済み）がそのまま使える。

**この拡張点の限界**: `evaluate_acceptance` の1行1コマンドは **PASS/FAIL の2値**しか表現できない。
codd-gate 未インストール環境で「無害スキップ（合否に影響させない）」をこの経路だけで実現する方法は
無い——コマンドが見つからなければ shell が exit 127 を返し、そのまま **FAIL 扱いで `total` に
計上される**（`command()` の no-op 縮退のような「実行しない」選択肢が `run_verify` には無い）。
すなわち **t17 の要求（未検出時は当該基準を合否・スコアに影響させない）は、`## acceptance` に
生コマンドを書くだけでは満たせない**。

### 3.2 コードレベルの拡張点 — `evaluate_acceptance` 本体への項目追加（t16/t17 が要求する形）

`evaluate_acceptance`（9546-9572）の `results` 組み立てループの後（return 前）に、
codd-gate 用の「疑似 acceptance 項目」を条件付きで追加する形が、3値スキップを表現できる唯一の口。
既に実装済みの部品（本 run の別タスクが用意した値オブジェクト）をそのまま使える:

```python
status = codd_gate_status.detect_status(...)         # 既存: usable / command()
if <codd_gate 有効設定>(cfg/repos.json の _meta 等):   # t9 の設定読み込みで決める
    args = codd_gate_routing.build_routing_args(repos_path, name, wd, ".") + ["--base", base_rev, "--strict"]
    result = codd_gate_invoke.invoke_codd_gate(status, "verify", *args)
    if result.status != "skipped":                    # skipped は total に計上しない＝無害
        cmd_repr = " ".join(["codd-gate", "verify", *args])   # _failing_acceptance_specs が
                                                                # そのまま再実行可能な文字列にする
        results.append((cmd_repr, result.ok, result.reason or result.stdout[:500]))
passed = sum(1 for _, ok, _ in results if ok)
return passed, len(results), results
```

この形が既存構造と噛み合う理由:
- `results` の要素は `(cmd: str, ok: bool, msg: str)` という**ただのタプル**で、由来がユーザー記述
  （`## acceptance`）か codd-gate 由来かを区別しない。`_failing_acceptance_specs`（9625-9627）は
  `results` から NG だけを拾って `_acceptance_specs`（9618-9622）で
  `{"title": "受入条件を満たす: <cmd>", "verify": <cmd>, "source": "acceptance"}` の改善タスクへ
  変換する——`cmd_repr` を実際に再実行可能なシェル文字列にしておけば、この改善タスク化も**無改造で
  そのまま動く**。
- `status != "skipped"` の分岐だけで `total` への計上/非計上を切り替えられる
  （`CoddGateResult.status` の3値は `codd_gate_invoke.py` に既実装、`ok`/`failed`/`skipped`）。
  これが t17（無害スキップ）の唯一の実現経路。
- `passed == total` による収束判定（`_project_evaluate` 9792）・journal/milestone の
  `"acceptance {passed}/{total} PASS"` 表示は、いずれも `evaluate_acceptance` の返り値だけを見る
  ローカル変数計算（`_project_evaluate` 内の `total` はこの返り値そのもの）なので、
  ここに1項目足すだけで収束判定・改善タスク化・milestone 表示のすべてに正しく波及する。

**要注意点（見つけた小さな不整合）**: `cmd_project`（9917）は `state["acceptance_total"] =
len(charter.acceptance)` を **`evaluate_acceptance` 呼び出しより前**（`_project_evaluate` は
この後の `run_loop` の後に呼ばれる）にセットしている。codd-gate 項目を `evaluate_acceptance` 内で
動的に足す設計だと、`state["acceptance_total"]`（viewer 表示や `finalize_project` の納品サマリーが
参照する）は codd-gate 項目を含まない値のまま残り、`_project_evaluate` が実際に使う `total`
（`evaluate_acceptance` の返り値、codd-gate 項目を含む）と食い違う。t16 実装時は
`state["acceptance_total"]` を `_project_evaluate` 側の `total` で上書きするか、
`cmd_project` 側のセットを削除して `_project_evaluate` に一本化する必要がある
（範囲外・t16 の実装課題として記録。本タスクでは調査のみのため未修正）。

### 3.3 KIRO_BASE_REV 注入の欠落（regression 側と共通の既知の穴）

`evaluate_acceptance` は `(wd / ".git").exists()` の場合のみ `KIRO_BASE_REV` を注入する
（9557-9561）。`_acceptance_cwd` が返す `wd` が非 git（稀だが `cfg.workdir` フォールバック時に
あり得る）だと注入されず、codd-gate 項目の `--base "$KIRO_BASE_REV"` が空になる。
`codd_gate_base.resolve_base_rev(task_base_branch, env)`（tools/kiro-project/codd_gate_base.py、
regression フック=b3 用に実装済み、優先順位: env の `KIRO_BASE_REV` → charter repo の `base=` →
`HEAD~1`）が同じ穴を埋める設計で既に存在するため、**§3.2 の codd-gate 項目を組み立てる際は
`evaluate_acceptance` の生 env ではなく `resolve_base_rev` を経由すべき**
（`--base "${KIRO_BASE_REV:-HEAD~1}"` という完了条件コマンド自体がこのフォールバックを前提にしている）。

### 3.4 タスク個別の受入基準（`- verify:`。E1 のもう一方。参考）

charter acceptance とは別に、修復タスク個々の `- verify:` 行（`Task.verify`）も E1 の一部
（`kiro-project-design.md` §4.1）。codd-gate 設計書 254-256行の④は `codd-gate check …` を
ここに置く想定（修復タスクの done 根拠）。これは t3/enqueue 系タスクの対象であり、
`evaluate_acceptance`/charter レベルとは独立した経路（`ensure_verify`/`run_verify_stable` は
共有するが、`Charter.acceptance` は経由しない）。

## 4. 検証内容と結果

- `evaluate_acceptance`/`_project_evaluate`/`cmd_project`/`write_milestone` 等、本報告で参照した
  全関数はソースを直接読んで行番号・シグネチャ・戻り値型を確認した（推測・引用のみに頼っていない）。
- `docs/designs/codd-gate-design.md` §4（結合点表 E1/E2/E3）・`docs/designs/kiro-project-design.md`
  §4.1（フック契約カタログ）を正典として突き合わせ、両ドキュメントと実装（`Charter.acceptance`・
  `evaluate_acceptance` の args/戻り値）に矛盾がないことを確認した。
- 完了条件コマンドを worktree で実行し、**現時点で両方とも exit 0 で成功**することを確認済み
  （本タスクはコード変更を行っていないため、この結果は他タスク（t7/t9/t18 等、既に main へ
  マージ済み）の成果による。参考として記録）:
  - `python3 -m pytest tools/kiro-project/tests -q -k codd` → `47 passed, 579 deselected, 3 subtests passed`
  - `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → `OK: 一貫性ゲート通過`（exit 0）

## 5. 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- タスク文言「acceptance（受入判定）」は kiro-project の用語法どおり charter レベルの
  `evaluate_acceptance`/`Charter.acceptance` を指すと解釈した（task 個別の `- verify:` は
  §3.4 で参考として触れるに留め、詳細分析の主対象は charter acceptance とした）。
- 本タスクは調査専任（t7 が t1〜t6 を統合し t8 以降が実装する fan-out-and-synthesize 構成）と
  読み、**sandbox worktree のソースは一切変更していない**（完了条件のコマンドは他タスクの成果で
  既に exit 0 のため、本タスクとして追加の実装は不要と判断）。

**未解決事項 / 範囲外で見つけた問題（別タスク・評価役の判断に委ねる）**:
1. `state["acceptance_total"]`（`cmd_project` 9917）が `evaluate_acceptance` 呼び出し前の値で
   固定される件（§3.2 要注意点）。codd-gate 項目を `evaluate_acceptance` 内で動的追加する設計を
   採るなら、t16 実装時にこのズレの解消が必要。
2. `KIRO_BASE_REV` 注入が `evaluate_acceptance` 側でも非 git cwd では欠落する件（§3.3）。
   `codd_gate_base.resolve_base_rev` を acceptance 側の codd-gate 項目構築でも再利用することを推奨。
3. codd-gate を有効化するかどうかの設定（enabled/strict/max-broken 等）の読み込み・既定値決定は
   t9 の担当であり、本報告では触れていない（§3.2 の疑似コードは t9 が提供する設定判定を
   プレースホルダとして仮置きしている）。
