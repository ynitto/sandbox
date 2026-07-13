# tools/kiro-project enqueue/backlog 調査報告

タスクID: kiro-project-codd-gate-171537（調査サブタスク）
対象: `tools/kiro-project/kiro-project.py`（単一ファイル実装、全 10580 行）

## (a) 成果サマリー

### 1. タスク追加 API

エントリポイントは3系統。すべて最終的に `enqueue_task(cfg, spec: dict) -> Task`（L290）に集約される。

| 経路 | 関数 | 契約 |
|---|---|---|
| CLI | `kiro-project enqueue --title ... [--verify/--accept/--verify-template] [--id/--priority/--source/--status/--after/--repos/--review/--note/--cohort-items]`（引数定義 L10418-10441, ハンドラ `cmd_enqueue` L7388） | 単発 spec を dict化して `enqueue_task` へ |
| CLI（JSON一括） | `kiro-project enqueue --json [--file path\|stdin]`（`cmd_enqueue` 内） | dict または dict の配列。配列なら複数タスクを一括生成 |
| 内部フック（inbox） | `ingest_inbox(cfg)`（L456）: `inbox/*.json`（オブジェクト/配列）→ 各要素を `enqueue_task` へ。`inbox/*.md`（.md/.markdown/.txt）は `parse_task` で直接 Task化 | 取り込み後、元ファイルは削除 |
| 内部フック（外部ゲート連携） | `run_intake(cfg)`（L502）: `cfg.intake_cmd`（シェルコマンド、例 `codd-gate tasks --debt`）を実行し、stdout の JSON（`enqueue --json` と同形式）を取り込む。**この run の目的である codd-gate 連携はこの汎用フックに接続する設計** | interval で律速、非0終了・非JSON・例外は journal に記録して無視（ループを殺さない） |

`enqueue_task` 自体の処理:
```python
def enqueue_task(cfg, spec):
    if spec.get("cohort_items"):
        t = create_cohort(cfg, spec)      # pilot-then-batch（繰り返しタスクの一括生成）
    else:
        t = task_from_spec(cfg, spec)     # 通常の単一タスク生成
    persist_task(cfg, t)                  # backlog/<id>.md へ書き出し
    return t
```

`task_from_spec`（L250）が spec の検証・既定値解決を担う:
- `title` 必須（無ければ `ValueError`）
- `id` 省略時はタイトルからスラグ生成 + 時刻サフィックス（`_gen_task_id` L239 → `_unique_task_id` L222、backlog 内衝突があれば `-2`, `-3`... を付番。**明示 id でも衝突すれば同様に自動リネームされる**、後述）
- `status` 省略時: `verify`/`accept`/`verify_template` のいずれかがあれば `ready`、無ければ `inbox`。ただし `cfg.plan_review`（既定 on）が有効なら常に `proposed`（人の承認待ち）で入る
- 既知フィールド（`after/review/note/accept/verify_template/repos/workspace/refs/paths/routed_by`）は `extra` へ、未知キーも取りこぼさず `extra` へ保持

### 2. タスクスキーマ

保存形式は **backlog/ 配下の 1タスク=1 Markdown ファイル**（`backlog/<id>.md`）。`Task` dataclass（L84-117）:

```python
@dataclass
class Task:
    id: str
    title: str
    status: str = "ready"      # VALID_STATUS = inbox/draft/proposed/ready/doing/done/
                                #   blocked/review/offloaded/rejected（L48-49）
    source: str = "human"      # enqueue/inbox/cohort/followup/human 等、由来の記録のみ
    priority: int = 0          # 大きいほど高優先
    verify: str = ""           # done 確定用シェルコマンド
    retries: int = 0
    extra: list[tuple[str, str]] = field(default_factory=list)  # 未知/追加キーの (key, value)
```

シリアライズ形式（`serialize_task` L175）:
```markdown
## <id>: <title>
- status: ready
- source: enqueue
- priority: 0
- verify: `<command>`
- retries: 0
- <extra-key>: <extra-value>
...
```
パースは正規表現ベース（`TASK_HEADER_RE`: `^##\s+(id):\s*(title)$`、`FIELD_RE`: `^-\s+(key):\s*(val)$`、L56-57）。`load_tasks`（L188）は `backlog/*.md` を mtime 昇順（最古優先）で全件ロードする素朴な実装で、専用の DB/index は無い。

`CONSUMABLE = ("ready", "todo")` のみが実行対象。`draft`/`proposed`/`blocked`/`review`/`offloaded` は消化されない。

### 3. 重複排除の有無 — **経路によって非対称。真の重複排除（skip）は `run_intake` のみ**

| 経路 | 挙動 | 実質 |
|---|---|---|
| `run_intake`（外部ゲート連携。**codd-gate 接続の想定経路**） | 取り込み前に `existing = {backlog/*.md の stem}` を作り、spec の `id`（`_slug_id` 正規化後）が既存にあれば **enqueue せず丸ごとスキップ**（L538-551, コメント「冪等: 現役 backlog に居る発見は再投入しない」） | **真の重複排除あり**。ただし判定は「id 文字列の一致」のみで、内容（verify/title等）の変更検知はしない。archive 済み（done後）の同一 id は対象外なので、一度 done→archive された発見が再発すれば新規タスクとして再投入される |
| CLI `enqueue`（単発/JSON一括）、inbox の `.json` | `_gen_task_id` → `_unique_task_id` が **常に呼ばれる**。明示 `id` を渡してもバックログ内に同名があれば黙って `-2`, `-3`… を付番し**別タスクとして作成**（スキップしない）。コメント上は「明示 id は冪等キー」とあるが、実装は「衝突回避（リネーム）」であって「重複拒否（skip）」ではない | **重複排除なし**（同一内容を複数回 enqueue すると id を変えて複数タスクが生まれる） |
| inbox の `.md`/`.markdown`/`.txt` | 同上、`_unique_task_id` でリネームのみ | **重複排除なし** |
| cohort（`create_cohort`） | `_unique_cohort_id` で cohort id 衝突は回避するが、cohort 内容の重複は見ない | **重複排除なし** |

近縁の仕組みとして `apply_intake_recall`（L1018）があるが、これは「タイトル類似度で過去の hold（却下）案件と一致するか」を見て人の判断へ差し戻す**予防リコール**であり、重複投入の抑止ではない（別目的）。

## (b) 検証内容と結果

- 該当ソースを `Read`/`grep` で全文照合し、`Task`/`enqueue_task`/`task_from_spec`/`run_intake`/`ingest_inbox`/`cmd_enqueue`/CLI引数定義の実装を直接確認（行番号を本報告に明記）。
- 完了条件のシェルコマンドを実行:
  - `python3 -m pytest tools/kiro-project/tests -q -k codd` → **exit 5**（`515 deselected`、`codd` にマッチするテストが1件も存在しない）。`tools/kiro-project/tests/test_kiro_project.py` に codd 関連テストは無い（"codd" はコメント中の2箇所のみで、実装・テストとも未接続）。
  - `codd-gate verify ...` は未実施（前段の pytest が exit 0 でないため合成コマンドはそこで失敗する。かつ本タスクは調査専任でありコード変更は行っていない）。
- 本タスクは「読み、特定する」調査タスクであり、ワークスペース規約の「調査のみなら何も書き換えない」に従い **ファイルは一切変更していない**（`git status` 相当の差分なし）。

## (c) 前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本タスクの完了条件（pytest -k codd && codd-gate verify --strict）は run 全体（または後続の実装・結線タスク）に対する共通ゲートであり、本調査サブタスク単体でこれを green にすることは求められていない（担当範囲外）と解釈した。現状 kiro-project 側に codd 統合コードもテストも存在しないため、調査のみでこの完了条件を満たすことは原理的に不可能。

**未解決事項（後続の実装タスクへの申し送り）**:
- codd-gate 自動検出を結線するなら、自然な接続点は `run_intake` / `cfg.intake_cmd`（例: `intake_cmd = "codd-gate tasks --debt"`）。この経路のみ id ベースの真の重複排除（skip）を持つため、他経路（直接 `enqueue`、inbox JSON）を使うと同じ発見が id を変えて何度も積まれるリスクがある。
- `run_intake` の重複判定は「backlog 内の id 文字列一致」のみで、内容差分（codd-gate 側で verify や優先度が変わった場合）を検知して更新する仕組みが無い。codd-gate からの再検出時に「内容が変わった同一発見」をどう扱うか（無視 vs 上書き vs 新規)は未実装。
- `Config.intake_cmd` / `intake_interval` の設定箇所・既定値は本調査の範囲外につき未確認（別タスクでの確認を推奨）。

**範囲外で見つけた問題（修正はしていない）**:
- CLI `enqueue --id` のコメント「明示 id は冪等キーなので改名しない」（L241-242）は実装（`_unique_task_id` によるリネーム）と矛盾する。ドキュメンテーションバグの可能性があり、別タスクでの確認を推奨。
