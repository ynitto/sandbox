# tools/kiro-project enqueue / backlog タスク schema 調査報告

タスクID: kiro-project-codd-gate-171537（run r9 / t5、調査サブタスク）
対象: `tools/kiro-project/kiro-project.py`（全 11219 行、行番号は本 run のワークツリー HEAD 時点）

## (a) 成果サマリー

### 1. enqueue のタスク生成関数

全経路は最終的に `enqueue_task(cfg, spec: dict) -> Task`（kiro-project.py:290）に集約される。

```python
def enqueue_task(cfg, spec):
    if spec.get("cohort_items"):
        t = create_cohort(cfg, spec)      # pilot-then-batch
    else:
        t = task_from_spec(cfg, spec)     # 通常の単一タスク生成 ← spec検証・既定値解決の本体
    persist_task(cfg, t)                  # backlog/<id>.md へ書き出し（= 投入の確定点）
    return t
```

呼び出し口は3系統：

| 経路 | 関数 | 契約 |
|---|---|---|
| CLI 単発/JSON一括 | `cmd_enqueue`（L7979） | `enqueue --title ...` または `enqueue --json` |
| inbox 取り込み | `ingest_inbox`（L456） | `inbox/*.json` の各要素を `enqueue_task` へ |
| 外部ゲート連携 | `run_intake`（L502） | `cfg.intake_cmd`（例 `codd-gate tasks --debt`）の stdout JSON を取り込む。**codd-gate 連携が接続すべき経路** |

`task_from_spec`（L250）が spec 検証・既定値解決を担う：`title` 必須／`id` 省略時は `_gen_task_id`（L239）でスラグ生成／`status` 省略時は verify(or accept/verify_template) 有無で ready/inbox、`plan_review` 有効時は常に `proposed`。

### 2. backlog タスク schema

正典は `schemas/task.schema.json`（JSON Schema。`enqueue --json` / inbox の `*.json` / `intake_cmd` の stdout / `codd-gate tasks` の出力に共通の契約。`additionalProperties: true` で前方互換）。実行時表現は `Task` dataclass（kiro-project.py:84-92）＋ backlog/ 配下 1タスク=1 Markdown（`backlog/<id>.md`、`serialize_task`/`parse_task` で相互変換）。

| フィールド | 型/既定 | 対応 |
|---|---|---|
| `id` | string, 48字・`[A-Za-z0-9_-]` 以内 | 省略時 title から自動生成。**intake_cmd 経路では冪等キー（重複判定キー）そのもの** |
| `title` | string, 必須 | — |
| `priority` | integer（既定 0、大きいほど高優先） | `Task.priority` |
| `after`（deps相当） | array\|string（依存タスク id、カンマ/空白区切り） | `Task` の第一級フィールドではなく `extra` に格納。`task_deps()`（L2472）で読み出し、`unmet_deps()`/`ready_after_deps()`（L2478-2486）が DAG 順に消化を制御 |
| `status`/`source`/`verify`/`accept`/`verify_template`/`review`/`note`/`workspace`/`refs`/`paths`/`routed_by`/`cohort_*` | 各種 | schema 上は既知だが `Task` dataclass の第一級は `status`/`source`/`verify`/`retries` のみ。残りは全部 `extra: list[tuple[str,str]]` |

### 3. 重複判定キー（dedup key）

**`id`（`_slug_id` 正規化後）のみ。判定は `run_intake` 経路にしかない。**

- `run_intake`（L502-554）: 取り込み前に `existing = {backlog/*.md の stem}` を作り、spec の `id` が既存にあれば **enqueue せず丸ごとスキップ**（L538-544）。done→archive 済みの同一 id は対象外（除外リストに含まれない）なので、再発した発見は新規タスクとして積み直される。
- CLI `enqueue` / inbox `.json`・`.md` 経路: `_gen_task_id`→`_unique_task_id` が常に呼ばれ、id 衝突時は `-2`,`-3`… に**改名**するだけ（スキップしない＝重複排除なし）。コード中コメント（L241-242）は「明示 id は冪等キーなので改名しない」と書くが、実装は改名する（ドキュメンテーションと実装の不一致。範囲外の既知の問題として下記に記載）。

### 4. codd-gate 側の対応状況（本 run で既に実装済みの隣接モジュール）

`tools/kiro-project/codd_gate_debt.py` の `DriftItem.to_spec()`（L45-51）が `codd-gate tasks --debt` の出力を `{title, id, ...fields}` の spec dict へ正規化し、`enqueue_task(cfg, spec)` / `run_intake` にそのまま渡せる形にしている。同モジュールの docstring は「`id` を e2 の重複投入防止キーとして直接使う想定」と明記しており、上記 (3) の `run_intake` の id ベース dedup と設計として整合している。ただし `cfg.intake_cmd`/`run_intake` への実結線自体は `codd_gate_invoke.py`/`codd_gate_debt.py` のいずれのdocstringでも明示的に「本モジュールの範囲外」とされている。

## (b) 検証内容と結果

- `Task`/`enqueue_task`/`task_from_spec`/`run_intake`/`ingest_inbox`/`cmd_enqueue`/`task_deps`/`_slug_id`/`_gen_task_id`/`_unique_task_id` を `Read`/`grep` で実ソース照合（行番号は上記の通り）。
- `schemas/task.schema.json` を読み、`id`/`priority`/`after`（deps）の型・説明を確認。
- 完了条件のシェルコマンドを実行し、両方とも成功を確認：
  - `python3 -m pytest tools/kiro-project/tests -q -k codd` → `47 passed, 579 deselected` (exit 0)
  - `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --strict` → `OK: 一貫性ゲート通過` (exit 0)
- 本タスクは「特定する」調査タスクのため、ワークスペース規約に従い**ファイルは一切変更していない**（`git status` 差分なし）。完了条件のコマンド自体は既に本 run の先行タスクによって green になっており、本タスクの成果（この報告）が別途 green 化する必要はなかった。

## (c) 前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本タスクは「enqueue タスク生成関数と backlog タスク schema を特定する」ことが完了条件であり、完了条件のシェルコマンドは run 全体の共通ゲートであって、本調査タスク自身の変更でこれを通す責務は負わないと解釈した（現に本タスクは無変更のまま両コマンドとも exit 0 だった）。

**未解決事項（申し送り）**:
- `cfg.intake_cmd`/`run_intake` と `codd_gate_debt.parse_debt_output`/`DriftItem.to_spec` の実結線自体（e2 系タスク）は、各モジュールの docstring 上「範囲外」と明記されたまま。結線タスクが別途あるなら、上記 (2)(3) のスキーマ・dedup 仕様をそのまま使える設計になっている。

**範囲外で見つけた問題（修正はしていない）**:
- `kiro-project.py:241-242` のコメント「明示 id は冪等キーなので改名しない」は実装（`_unique_task_id` によるリネーム）と矛盾する。dedup が効くのは `run_intake` 経路のみで、CLI `enqueue --id`/inbox `.json` では効かない。ドキュメンテーションバグの可能性があり、別タスクでの確認を推奨。
