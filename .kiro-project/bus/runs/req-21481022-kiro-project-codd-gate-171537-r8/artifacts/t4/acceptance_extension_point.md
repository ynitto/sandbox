# t4: acceptance（受入判定）実装箇所と拡張点の確定

対象: `tools/kiro-project/kiro-project.py`（worktree HEAD、6e21135）。読了範囲: `受入`/`acceptance`
全 grep ヒット（kiro-project.py 内 60 件超）の該当箇所全文 + 呼び出しチェーン
（`cmd_project` → `_project_evaluate` → `evaluate_acceptance`）+ `docs/designs/codd-gate-design.md`
§4（結合点表）+ 同一 run 内の先行タスク t2 成果物（`artifacts/t2/api_contract.md`）。

## (a) 成果 / サマリー

### 1. 実装箇所（受入判定の一連の実装、すべて `tools/kiro-project/kiro-project.py`）

| 役割 | 関数/フィールド | file:line |
|---|---|---|
| チェックリスト本体（拡張点） | `Charter.acceptance: list[str]` | `kiro-project.py:8184` |
| charter.md `## acceptance` 節 → リスト解析 | `parse_charter` 内 `ch.acceptance = _charter_bullets(...)` | `kiro-project.py:8370` |
| マスター憲章からの継承（空なら補完） | `_merge_master_charter` 内 `ch.acceptance = ch.acceptance or list(base.acceptance)` | `kiro-project.py:8648` |
| 行の種別判定（command／自然言語） | `_acceptance_kind` + `_ACCEPT_PREFIX_RE` | `kiro-project.py:9575-9586`（正規表現定義は `8198`） |
| 自然言語行 → 決定的コマンドへ合成 | `resolve_charter_acceptance`（`synth_verify` 呼び出し） | `kiro-project.py:9589-9615` |
| **受入可否の判定関数本体** | `evaluate_acceptance` | `kiro-project.py:9546-9568` |
| 実行 cwd 解決（明示 verify_cwd／単一 repo 一時 clone／workdir） | `_acceptance_cwd` | `kiro-project.py:9524-9543` |
| 判定結果 → 収束/継続/停滞/コスト上限の決定 | `_project_evaluate` | `kiro-project.py:9753-9802` |
| acceptance 未定義ガード（判定不能→人へ） | `cmd_project` 内 `if not charter.acceptance:` | `kiro-project.py:9853-9862` |
| 未達 acceptance → 改善タスク化 | `_acceptance_specs` / `_failing_acceptance_specs` | `kiro-project.py:9618-9627` |

### 2. 判定関数の入出力

```python
def evaluate_acceptance(cfg: "Config", charter: "Charter") -> "tuple[int, int, list]":
```
`kiro-project.py:9546`

- **入力**
  - `cfg: Config` — `cfg.verify_cwd` / `cfg.verify_timeout` / `cfg.verify_confirm`（`_acceptance_cwd`・
    `run_verify_stable` へ渡す実行パラメータ）を保持。
  - `charter: Charter` — 実質参照するのは `charter.acceptance: list[str]`（受入コマンドの配列。
    `resolve_charter_acceptance` で自然言語行が事前に決定的コマンドへ解決済みである前提。呼び出し元
    `cmd_project` は `9902` で `charter.acceptance = resolved` と**破壊的に置き換えてから**
    `_project_evaluate`→`evaluate_acceptance` を呼ぶ）。
- **出力**: `tuple[int, int, list[tuple[str, bool, str]]]`
  - `passed: int` — PASS したコマンド数
  - `total: int` — `len(charter.acceptance)`
  - `results: list[(cmd: str, ok: bool, msg: str)]` — 各コマンドの実行結果（`ok` が個々の合否、
    `msg` は失敗理由/出力抜粋）
  - 判定ロジックは単純多数決ではなく**全 PASS が唯一の done 根拠**（`9792`
    `if passed == total and not improved:` → `REASON_PROJECT_CONVERGED`）。
  - 各コマンドは `run_verify_stable(cmd, wd, cfg.verify_timeout, cfg.verify_confirm, env)`
    （`kiro-project.py:9564`、実体は `3031`）でシェル実行され、**exit code 0 が唯一の合否根拠**
    （kiro-project 全体の「done は verify の exit 0 のみ」の鉄則がここでも適用される）。

### 3. 外部チェック結果を受入可否へ合流させる既存の拡張点

**`Charter.acceptance: list[str]`（`kiro-project.py:8184`）そのものが拡張点**——専用の
`cfg.codd_gate_cmd` のような単一フィールドは無い。E2（`regression_cmd`, `kiro-project.py:4607`）・
E3（`intake_cmd`, `4609`）が「1 フィールド＝1 コマンド文字列」なのに対し、E1（acceptance）は
**複数行のチェックリスト自体が合流点**という構造の違いがある。

- charter.md の `## acceptance` セクションに 1 行足すだけで、その行は `_charter_bullets`
  （`8370` 経由）で `charter.acceptance` に追加され、`evaluate_acceptance`（`9546`）が他の受入行と
  **同列に**シェル実行し、exit code を `passed`/`total` の集計に合流させる。
  行の書式は 2 通り（`_acceptance_kind`, `9575`）:
  - シェルコマンドに見える行 → そのまま command として実行（**codd-gate 呼び出しはこちら**。
    `_looks_like_shell_command` が真になる書き方であれば追加コードなしに合流する）
  - `accept:`/`受入:` 接頭辞または散文 → 自然言語とみなし `resolve_charter_acceptance`（`9589`）が
    `synth_verify` でコマンドへ合成（LLM 経由・失敗すれば unresolved → 人へ）
- **設計上の正典**: `docs/designs/codd-gate-design.md:249`（§4 結合点表 ②）が
  「E1 charter `## acceptance`」への差し込みとして
  `codd-gate verify --debt --max-broken 0 …` を明記——「evaluate のたび負債ラチェットを決定的に
  判定」する想定で、まさにこの `charter.acceptance` リストへの追記が正式な拡張経路。
- **未着手の結線**（範囲外・c1-c2 の責務。t2 成果物 `api_contract.md` §5-1 と一致）:
  `charter.acceptance` へ codd-gate コマンド行を**自動で**注入するコードは
  `kiro-project.py` 本体に存在しない（`grep -n "import codd_gate" tools/kiro-project` は
  各モジュール自身のテストファイル以外ヒット 0 件、`codd_gate_status`/`codd_gate_routing` からの
  呼び出しも 0 件）。人が charter.md へ手で 1 行足せば今の実装でもそのまま合流する
  （decision: 手動なら未着手ではない）が、**自動検出→自動追記**の配線自体は無い。
  類似の「解析後に自動補完する」既存パターンとして `load_charter`（`8586`）内
  `_apply_repo_registry`（`8557`, `allow_export` で `<root>/repos.json` を自動生成）があり、
  もし acceptance 側にも自動注入フックを作るなら charter パース直後（`parse_charter` 後・
  `evaluate_acceptance` 呼び出し前）が既存の「charter 後処理」パターンと整合する挿入位置になる
  （このモジュールの新設・実装判断そのものは本タスクの範囲外＝後続タスクの責務）。

## (b) 検証内容と結果

- コード変更なし（調査のみ。`git status --porcelain` 出力ゼロを worktree で確認）。
- 完了条件コマンドをそのまま worktree で実行し、変更前から exit 0 であることを確認（このタスクの
  変更がゲートを壊していないことの確認）:
  ```
  $ python3 -m pytest tools/kiro-project/tests -q -k codd
  29 passed, 579 deselected in 0.05s
  $ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict
  OK: 一貫性ゲート通過
  $ echo $?
  0
  ```
- file:line はすべて worktree 内 `grep -n`/`Read` で実測（HEAD `6e21135`、ブランチ
  `kp/kiro-project-codd-gate-171537`）。行番号はこのコミット時点のもの。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文中の「受入可否を決める判定関数」は `evaluate_acceptance`（プロジェクト単位の
  1 回の判定。複数コマンドの集計）を指すと解釈した。個々の受入コマンド 1 本の合否判定自体は
  `run_verify_stable`（`3031`）が担うため、両方を「判定関数」の入出力として併記した。
- **前提**: 「外部チェック結果を受入可否へ合流させる拡張点」は、E2/E3 のような専用 config
  フィールドではなく `charter.acceptance` リスト自体であると判断した。根拠は
  `docs/designs/codd-gate-design.md` §4 表②が明示的に E1=`## acceptance` を差し込み先に指定して
  いること、および現状の実装（`evaluate_acceptance`）が `charter.acceptance` の各行を無差別に
  シェル実行する構造であること。
- **範囲外で見つけた問題（後続タスクへの申し送り）**: `charter.acceptance` への codd-gate
  コマンドの自動注入は未実装（t2 成果物と同一の指摘）。実装するタスク（c1/c2 相当）は
  「どこで注入するか」（`load_charter`/`_apply_repo_registry` 相当のパース後フック vs
  `evaluate_acceptance` 直前の一時的合成）と「冪等性」（charter.md 保存時に重複追記しないか、
  それとも保存せず評価直前にメモリ上でのみ合成するか）を決める必要があるが、これは本タスクの
  範囲外（判定・入出力・拡張点の特定のみが本タスクの完了条件）。
- 未解決事項なし。
