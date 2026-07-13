# enqueue（負債取り込み）実装箇所と入出力契約（t5）

対象リポジトリ: https://github.com/ynitto/sandbox （worktree: kp/kiro-project-codd-gate-171537）
調査基準コミット: 本 worktree の HEAD（`git status --short` 差分ゼロ、コード変更なし）

## (a) 成果物 — 実装箇所と契約の確定

### 1. 実装箇所（file:line）

負債取り込み（enqueue）は3層構造で、上から順に「配線点（未実装）→ 定期実行フック（実装済み）→
タスク生成・永続化本体（実装済み）」となっている。

| 層 | 関数/フィールド | 場所 | 状態 |
|---|---|---|---|
| 配線点 | `cfg.intake_cmd` 自動設定 | `tools/kiro-project/kiro-project.py:4609`（フィールド定義） | **未実装**。手動設定（`--intake-cmd`）のみ可 |
| 定期実行フック | `run_intake(cfg: Config) -> list[Task]` | `tools/kiro-project/kiro-project.py:502-554` | 実装済み・稼働中 |
| タスク生成本体 | `enqueue_task(cfg: Config, spec: dict) -> Task` | `tools/kiro-project/kiro-project.py:290-298` | 実装済み・稼働中 |
| spec 検証/正規化 | `task_from_spec(cfg: Config, spec: dict) -> Task` | `tools/kiro-project/kiro-project.py:250-287` | 実装済み・稼働中 |
| ディスク書き込み | `persist_task` / `serialize_task` | `kiro-project.py:196-198` / `175-185` | 実装済み・稼働中 |
| CLI 手動投入口 | `cmd_enqueue(cfg, args)` | `kiro-project.py:7979-8023`（`enqueue` サブコマンド定義 `11057-11080`） | 実装済み（`--intake-cmd 'codd-gate tasks --debt'` は今日時点で人が手動設定すればそのまま動く。CLI ヘルプ `kiro-project.py:10602-10605` が既に例示済み） |
| （補助・未結線）レコード単位パーサ | `parse_debt_output` / `DriftItem.to_spec()` | `tools/kiro-project/codd_gate_debt.py`（全体） | 実装済みだが **どこからも呼ばれていない**（`grep -rn "codd_gate_debt" kiro-project.py` = ヒット0） |
| 負債側の id/タスク化 | `_task_id` / `tasks_from_debt` / `_emit_tasks` | `tools/codd-gate/codd-gate.py:737-743` / `790-831` / `1104-1114` | 実装済み（codd-gate 側。契約の生成元） |

**未実装の内訳**: 前タスク run（`run-20260712-213419-5922`）の設計文書 d2
（`.kiro-project/bus/runs/run-20260712-213419-5922/artifacts/d2/codd-gate-status-interface-design.md`
4.3節）が確定している自動配線コードそのもの:

```python
argv = status.command("tasks", "--debt", "--repos", str(repos_path), "--repo-dir", f"{name}={path}")
if argv and cfg.intake_cmd is None:
    cfg.intake_cmd = shlex.join(argv)
```

は `load_charter`（`kiro-project.py:7977` 付近、d2 の想定）にまだ挿入されていない。これは
本タスク（t5・調査のみ）の範囲外（実装は t16/t17 相当）。**「未設定のときだけ」自動設定**という
条件（既存の明示設定を上書きしない）を守ること。

### 2. 入出力契約（I/O）

#### `enqueue_task(cfg: Config, spec: dict) -> Task`
- **入力** `spec: dict` — `schemas/task.schema.json` 準拠。`enqueue --json` / inbox の `*.json` /
  `intake_cmd` の stdout / `codd-gate tasks` の出力すべてがこの1形式に合流する
  （`schemas/task.schema.json:5` の description に明記）。
- **出力** `Task`（dataclass。`id/title/status/source/priority/verify/retries/extra`）。
- **副作用** `cfg.backlog` ディレクトリ作成 + `backlog/<id>.md` 書き込み（`persist_task`）。
- **例外** `title` が空/欠落なら `ValueError`（`task_from_spec` 内、`kiro-project.py:253-254`）。
- `spec.get("cohort_items")` があれば通常の1タスクではなく pilot タスク（`create_cohort`,
  `kiro-project.py:352-391`）を生成する分岐がある（負債取り込みでは
  `tasks_from_debt(..., cohort=True)` の未文書化/未テスト系がこの経路を通り得る。
  `codd-gate.py:812-820`）。

#### `run_intake(cfg: Config) -> list[Task]`
- **入力** `cfg.intake_cmd`（シェルコマンド文字列）・`cfg.intake_interval`（既定600秒のレート制限）・
  `cfg.workdir`（cwd）・`cfg.verify_timeout`（subprocess タイムアウト）。
- **出力** 新規作成された `Task` のリスト（空リストは「取り込み対象なし」「スロットル中」
  「コマンド失敗」「非JSON」のいずれかを区別せず返す。詳細は `append_journal` 経由でのみわかる）。
- **処理**: `subprocess.run(cfg.intake_cmd, shell=True, cwd=cfg.workdir, timeout=cfg.verify_timeout)`
  → exit≠0/非JSON/空出力はすべて無視してループ継続 → stdout JSON を配列/単一オブジェクトいずれも
  吸収 → 各要素で下記の重複判定 → `enqueue_task` 呼び出し（`ValueError` は1件だけ握りつぶし journal
  へ）。

### 3. 必須フィールド

`schemas/task.schema.json` の `required` は **`title` のみ**（`additionalProperties: true`）。
実装側で実効的に意味を持つフィールド:

| フィールド | 必須性 | 役割 |
|---|---|---|
| `title` | 必須（無いと `ValueError`） | タスク見出し |
| `id` | 任意だが**冪等キーとして機能させるなら実質必須** | 省略時は title 由来の自動生成 id になり、再実行のたびに新規 id → 重複排除が効かない |
| `verify`/`accept`/`verify_template` | いずれか1つあれば `status` 既定が `ready`、無ければ `inbox`（`kiro-project.py:260-262`） |
| `status` | 省略可（上記ルールで自動決定。ただし `cfg.plan_review` が真なら明示指定が無い限り常に `proposed`、`kiro-project.py:265-266`） |
| その他（`priority`/`source`/`workspace`/`refs`/`paths`/`note`/`review` 等） | 任意。未知キーも `Task.extra` にすべて保持（前方互換、`kiro-project.py:279-281`） |

`codd-gate tasks --debt` が実際に埋めるフィールド（`codd-gate.py:790-831`）:
`id`（`_task_id` で決定的生成）・`title`・`verify` または `accept`・`paths`・`priority`・`note`・
（未文書化/未テストの cohort 化時のみ）`cohort_items`。→ 現行スキーマの必須項目を過不足なく満たす。

### 4. 重複判定キー（dedup key）

- **キー**: `spec["id"]` を `_slug_id()`（`[^A-Za-z0-9_-]→-` 置換・48字切り詰め、
  `kiro-project.py:217-219`）した文字列。これがそのまま backlog ファイル名 `<id>.md` になる。
- **判定範囲**: `cfg.backlog` 直下の `*.md` ファイル名 stem 集合
  （`run_intake` 内 `existing = {f.stem for f in cfg.backlog.glob("*.md")}`, `kiro-project.py:538`）。
  **archive ディレクトリは見ない**——done→archive 移動後に同じ id が再発見されたら新規タスクとして
  再投入される仕様（`run_intake` docstring, `kiro-project.py:507-508` に明記）。
- **判定タイミング**: `enqueue_task` 呼び出し**前**に `sid in existing` でスキップ判定
  （`kiro-project.py:542-544`）。`id` が空/未指定の spec は判定自体がスキップされ**常に新規投入**
  される＝冪等性が保証されない。
- **codd-gate 側の担保**: `_task_id(kind, *parts)`（`codd-gate.py:737-743`）が対象ノード名・トークン
  から SHA1 先頭6桁を含む決定的 id（`codd-<kind>-<slug28>-<hash6>`）を生成するため、同じ負債は
  `codd-gate tasks --debt` を何度実行しても同じ id になる → 上記の集合判定で確実に重複排除される
  （id 未指定という抜け穴には該当しない）。

## (b) 検証内容と結果

- `git status --short`（worktree） → 差分ゼロ。本タスクはコード変更を行っていない（調査・契約確定のみ）。
- `enqueue_task`/`task_from_spec`/`run_intake`/`persist_task`/`serialize_task`/`cmd_enqueue` の
  実装を `kiro-project.py` から直接読み、上記の file:line・分岐条件を実地確認した。
- `tools/kiro-project/codd_gate_debt.py`・`codd_gate_status.py`・`codd_gate_routing.py`・
  `codd_gate_base.py`・`codd_gate_detect.py` を全文読み、docstring が明記する責務境界
  （d1/d2 参照）を確認した。
- `grep -rn "codd_gate_" tools/kiro-project/kiro-project.py` → ヒット0（＝5モジュールとも
  まだ kiro-project.py 本体へ未結線であることを確認）。
- `tools/codd-gate/codd-gate.py` の `_task_id`/`tasks_from_debt`/`_emit_tasks`（負債→タスク化の
  送出側）を読み、id 生成の決定性と `schemas/task.schema.json` 準拠を確認した。
- `python3 -m pytest tools/kiro-project/tests -q -k codd` を実行 → **29 passed, 579 deselected**
  （既存の codd-gate 検出/ルーティング系テストは現状全て green。負債取り込み専用のテスト
  `test_codd_gate_enqueue_wiring.py` 等はまだ存在しない＝これから追加するテストが検証すべき対象
  はこの報告の契約そのものになる）。
- `codd-gate verify ...`（完了条件コマンド後段）は本タスクの範囲外のため実行していない
  （repos.json 復元・regression/acceptance 結線は他タスクの責務）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 前 run（`run-20260712-213419-5922`）の設計文書 d1/d2 を「まだ覆っていない既定路線」として
  採用した。d2 4.3節の自動配線コード案をそのまま「実装すべき対象」として本報告の契約に反映した。
- 「backlog タスク（markdown フロントマター）」という元タスクの表現は、実際のファイル形式
  （`## <id>: <title>` ヘッダ + `- key: value` の箇条書き。YAML `---` フロントマターではない）
  を指すものとして解釈した。正典は `tools/kiro-project/backlog.md.example` と
  `schemas/task.schema.json`（JSON表現）。

**未解決事項**（実装タスクが判断すべき点）:
- `codd_gate_debt.parse_debt_output`/`DriftItem` を実際に `run_intake` から呼ぶか
  （d2 5節が示唆する「spec ループに1行足す」経路）、それとも `run_intake` 既存のインライン
  JSON パース＋`enqueue_task` の `ValueError` 捕捉（機能的にほぼ同等）だけで済ませ
  `codd_gate_debt.py` を使わないままにするかが未確定。後者を選ぶ場合、`codd_gate_debt.py` は
  synth 段階で「未使用コード」として指摘され得る。
- `resolve_codd_gate_status` の計算結果（`CoddGateStatus`）をどこにキャッシュするか
  （d2 6節が2案を提示し未決定のまま）。

**範囲外で見つけた問題**（直さず報告のみ）:
- `cmd_enqueue`（CLI 手動投入）は投入直後に `apply_intake_recall`（過去の hold との類似検知）と
  `ensure_needs`（レビュー票生成）を呼ぶ（`kiro-project.py:8009, 8023` 付近）が、`run_intake`
  （自動取り込み経路）は**どちらも呼ばない**。codd-gate 由来の自動投入タスクが、CLI 経由と違って
  過去の却下パターンとの類似チェックを一切受けずに `ready` へ入り得る非対称性がある。
  意図的な設計か抜け漏れかは本タスクの範囲外のため判断していない。
